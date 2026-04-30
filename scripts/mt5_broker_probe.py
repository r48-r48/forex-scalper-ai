"""Safe MT5 broker probe with symbol, history, and order_check diagnostics."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scalper_ai.config import AppConfig, load_app_config
from scalper_ai.deployment import build_mt5_preflight_report
from scalper_ai.deployment.live_factory import build_mt5_terminal_client
from scalper_ai.domain import OrderSide, OrderType, TimeInForce
from scalper_ai.execution.mt5_client import (
    MetaTrader5ModuleProtocol,
    Mt5TerminalClient,
    load_metatrader5_module,
)
from scalper_ai.execution.mt5_live import Mt5OrderRequest
from scalper_ai.utils import configure_logging


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Run a safe MT5 broker probe. This script never calls order_send; "
            "it only reads broker state and optionally calls order_check."
        ),
    )
    parser.add_argument("--config-name", default="mt5", help="Config overlay to load.")
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="Optional config directory override.",
    )
    parser.add_argument("--symbol", default="EURUSD", help="Broker symbol to probe.")
    parser.add_argument(
        "--side",
        choices=("buy", "sell"),
        default="buy",
        help="Side used only for order_check.",
    )
    parser.add_argument(
        "--time-in-force",
        choices=("fok", "ioc", "gtc"),
        default="fok",
        help="Filling/time policy used only for order_check.",
    )
    parser.add_argument(
        "--volume-lots",
        type=float,
        default=None,
        help="Lot volume used only for order_check; defaults to config MT5 minimum.",
    )
    parser.add_argument(
        "--history-lookback-hours",
        type=int,
        default=None,
        help="History polling window override for raw orders/deals counts.",
    )
    parser.add_argument(
        "--skip-order-check",
        action="store_true",
        help="Skip broker order_check and keep the probe strictly read-only.",
    )
    parser.add_argument(
        "--include-raw-samples",
        action="store_true",
        help="Include one raw history order/deal sample when present.",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Optional JSON artifact path.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""

    args = parse_args()
    config = load_app_config(config_name=args.config_name, config_dir=args.config_dir)
    configure_logging(config.logging)

    payload = collect_mt5_broker_probe_payload(
        config,
        symbol=str(args.symbol),
        side=OrderSide(args.side),
        time_in_force=_time_in_force_from_cli(str(args.time_in_force)),
        volume_lots=args.volume_lots,
        history_lookback_hours=args.history_lookback_hours,
        include_order_check=not args.skip_order_check,
        include_raw_samples=bool(args.include_raw_samples),
    )

    if args.output_path is not None:
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        args.output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(json.dumps(payload, indent=2, sort_keys=True))


def collect_mt5_broker_probe_payload(
    config: AppConfig,
    *,
    symbol: str,
    side: OrderSide,
    time_in_force: TimeInForce,
    volume_lots: float | None = None,
    history_lookback_hours: int | None = None,
    include_order_check: bool = True,
    include_raw_samples: bool = False,
    mt5_module: MetaTrader5ModuleProtocol | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Collect safe MT5 broker diagnostics without submitting orders."""

    if config.broker.live_adapter.strip().lower() != "mt5":
        raise RuntimeError("mt5_broker_probe.py requires broker.live_adapter=mt5.")
    if not symbol.strip():
        raise ValueError("symbol must be non-empty.")
    if volume_lots is not None and volume_lots <= 0:
        raise ValueError("volume_lots must be greater than zero when provided.")
    if history_lookback_hours is not None and history_lookback_hours <= 0:
        raise ValueError("history_lookback_hours must be greater than zero when provided.")

    timestamp = generated_at or datetime.now(timezone.utc)  # noqa: UP017
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware.")

    module = load_metatrader5_module() if mt5_module is None else mt5_module
    module_loader = None if mt5_module is None else lambda: module
    preflight = build_mt5_preflight_report(config, module_loader=module_loader).to_dict()
    payload: dict[str, Any] = {
        "generated_at": timestamp.astimezone(timezone.utc).isoformat(),  # noqa: UP017
        "preflight": preflight,
        "order_send_called": False,
        "symbol": symbol.strip(),
    }

    if not preflight["ready_for_connection"]:
        payload["connection_attempted"] = False
        payload["connection_error"] = "MT5 preflight failed."
        return payload

    client: Mt5TerminalClient | None = None
    try:
        client = build_mt5_terminal_client(config, mt5_module=module)
        account = client.describe_account()
        normalized_orders = client.list_orders()
        normalized_positions = client.list_positions()
        payload.update(
            {
                "connection_attempted": True,
                "connected": client.is_connected(),
                "ping_latency_ms": client.ping_latency_ms(),
                "account": asdict(account),
                "normalized_order_count": len(normalized_orders),
                "normalized_position_count": len(normalized_positions),
                "normalized_orders": [_order_state_to_dict(order) for order in normalized_orders],
                "normalized_positions": [
                    _position_state_to_dict(position) for position in normalized_positions
                ],
            }
        )
        payload["terminal"] = _terminal_summary(_safe_mapping(module.terminal_info()))
        payload["symbol_info"] = _symbol_summary(
            _safe_mapping(_call_module(module, "symbol_info", symbol.strip()))
        )
        payload["tick"] = _tick_summary(
            _safe_mapping(_call_module(module, "symbol_info_tick", symbol.strip()))
        )
        payload["raw_history"] = _history_summary(
            module,
            generated_at=timestamp,
            lookback_hours=history_lookback_hours or config.broker.mt5.history_lookback_hours,
            include_raw_samples=include_raw_samples,
        )
        if include_order_check:
            resolved_volume = (
                float(volume_lots)
                if volume_lots is not None
                else float(config.broker.mt5.min_volume_lots)
            )
            order_check = client.check_order(
                Mt5OrderRequest(
                    client_order_id=f"broker-probe-{int(timestamp.timestamp())}",
                    broker_symbol=symbol.strip(),
                    side=side,
                    order_type=OrderType.MARKET,
                    submitted_at=timestamp.astimezone(timezone.utc),  # noqa: UP017
                    volume_lots=resolved_volume,
                    time_in_force=time_in_force,
                )
            )
            payload["order_check"] = asdict(order_check) | {
                "checked_at": order_check.checked_at.isoformat(),
                "rejection_reason": order_check.rejection_reason,
                "side": side.value,
                "time_in_force": time_in_force.value,
                "volume_lots": resolved_volume,
            }
        return payload
    except Exception as exc:
        payload["connection_attempted"] = True
        payload["connection_error"] = str(exc)
        return payload
    finally:
        if client is not None:
            client.close()


def _time_in_force_from_cli(value: str) -> TimeInForce:
    if value == "fok":
        return TimeInForce.FOK
    if value == "ioc":
        return TimeInForce.IOC
    return TimeInForce.GTC


def _order_state_to_dict(order: Any) -> dict[str, Any]:
    payload = asdict(order)
    payload["status"] = order.status.value
    payload["submitted_at"] = order.submitted_at.isoformat()
    payload["updated_at"] = order.updated_at.isoformat()
    payload["deals"] = [_deal_state_to_dict(deal) for deal in order.deals]
    return payload


def _deal_state_to_dict(deal: Any) -> dict[str, Any]:
    payload = asdict(deal)
    payload["timestamp"] = deal.timestamp.isoformat()
    payload["side"] = deal.side.value
    payload["execution_cost"] = deal.execution_cost
    return payload


def _position_state_to_dict(position: Any) -> dict[str, Any]:
    payload = asdict(position)
    payload["timestamp"] = position.timestamp.isoformat()
    payload["position_mode"] = position.position_mode.value
    return payload


def _terminal_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": payload.get("name"),
        "company": payload.get("company"),
        "connected": payload.get("connected"),
        "trade_allowed": payload.get("trade_allowed"),
        "tradeapi_disabled": payload.get("tradeapi_disabled"),
        "path": payload.get("path"),
        "data_path": payload.get("data_path"),
        "build": payload.get("build"),
    }


def _symbol_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": payload.get("name"),
        "description": payload.get("description"),
        "visible": payload.get("visible"),
        "trade_mode": payload.get("trade_mode"),
        "filling_mode": payload.get("filling_mode"),
        "spread": payload.get("spread"),
        "spread_float": payload.get("spread_float"),
        "digits": payload.get("digits"),
        "volume_min": payload.get("volume_min"),
        "volume_max": payload.get("volume_max"),
        "volume_step": payload.get("volume_step"),
        "trade_contract_size": payload.get("trade_contract_size"),
        "currency_base": payload.get("currency_base"),
        "currency_profit": payload.get("currency_profit"),
        "currency_margin": payload.get("currency_margin"),
    }


def _tick_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "time": payload.get("time"),
        "time_msc": payload.get("time_msc"),
        "bid": payload.get("bid"),
        "ask": payload.get("ask"),
        "last": payload.get("last"),
        "volume": payload.get("volume"),
        "flags": payload.get("flags"),
    }


def _history_summary(
    module: MetaTrader5ModuleProtocol,
    *,
    generated_at: datetime,
    lookback_hours: int,
    include_raw_samples: bool,
) -> dict[str, Any]:
    start_time = generated_at.astimezone(timezone.utc) - timedelta(hours=lookback_hours)  # noqa: UP017
    end_time = generated_at.astimezone(timezone.utc)  # noqa: UP017
    orders = _safe_sequence(module.history_orders_get(start_time, end_time))
    deals = _safe_sequence(module.history_deals_get(start_time, end_time))
    payload: dict[str, Any] = {
        "lookback_hours": lookback_hours,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "raw_order_count": len(orders),
        "raw_deal_count": len(deals),
    }
    if include_raw_samples:
        payload["first_order"] = _json_safe(_safe_mapping(orders[0])) if orders else None
        payload["first_deal"] = _json_safe(_safe_mapping(deals[0])) if deals else None
    return payload


def _call_module(module: MetaTrader5ModuleProtocol, name: str, *args: Any) -> Any:
    callable_member = getattr(module, name, None)
    if callable_member is None:
        return None
    return callable_member(*args)


def _safe_mapping(raw_payload: Any) -> Mapping[str, Any]:
    if raw_payload is None:
        return {}
    if isinstance(raw_payload, Mapping):
        return raw_payload
    if hasattr(raw_payload, "_asdict"):
        payload = raw_payload._asdict()
        if isinstance(payload, Mapping):
            return payload
    return {
        key: getattr(raw_payload, key)
        for key in dir(raw_payload)
        if not key.startswith("_") and not callable(getattr(raw_payload, key))
    }


def _safe_sequence(raw_payload: Any) -> tuple[Any, ...]:
    if raw_payload is None:
        return ()
    if isinstance(raw_payload, tuple):
        return raw_payload
    if isinstance(raw_payload, Sequence) and not isinstance(raw_payload, (str, bytes)):
        return tuple(raw_payload)
    return tuple(raw_payload)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


if __name__ == "__main__":
    main()
