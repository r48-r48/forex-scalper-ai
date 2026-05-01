"""Controlled MT5 pending-order modify validation with strict safety gates."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
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
from scalper_ai.execution.models import ExecutionOrderStatus
from scalper_ai.execution.mt5_client import MetaTrader5ModuleProtocol, load_metatrader5_module
from scalper_ai.execution.mt5_live import (
    Mt5OrderRequest,
    Mt5OrderState,
    Mt5PendingOrderModifyRequest,
    Mt5PositionState,
    Mt5SymbolSpec,
)
from scalper_ai.utils import configure_logging


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Place, modify, and cancel one far-away MT5 demo pending limit order. "
            "This script can call order_send only when explicit confirmation and "
            "demo-account safety gates pass."
        ),
    )
    parser.add_argument("--config-name", default="mt5", help="Config overlay to load.")
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--symbol", default="EURUSD", help="Broker symbol to test.")
    parser.add_argument(
        "--side",
        choices=("buy", "sell"),
        default="buy",
        help="Pending limit side to test.",
    )
    parser.add_argument(
        "--volume-lots",
        type=float,
        default=None,
        help="Lot volume. Defaults to broker.mt5.min_volume_lots and may not exceed it.",
    )
    parser.add_argument(
        "--initial-offset-points",
        type=int,
        default=2000,
        help="Minimum distance from the current quote for the initial pending price.",
    )
    parser.add_argument(
        "--modify-extra-points",
        type=int,
        default=250,
        help="Additional distance used for the modify price, away from the market.",
    )
    parser.add_argument("--expected-login", type=int, default=None)
    parser.add_argument("--expected-server", default=None)
    parser.add_argument(
        "--i-understand-this-sends-a-demo-pending-order",
        action="store_true",
        help="Required explicit confirmation before any order_send call.",
    )
    parser.add_argument("--output-path", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""

    args = parse_args()
    config = load_app_config(config_name=args.config_name, config_dir=args.config_dir)
    configure_logging(config.logging)
    payload = collect_mt5_pending_modify_payload(
        config,
        symbol=str(args.symbol),
        side=OrderSide(str(args.side)),
        volume_lots=args.volume_lots,
        initial_offset_points=int(args.initial_offset_points),
        modify_extra_points=int(args.modify_extra_points),
        expected_login=args.expected_login,
        expected_server=args.expected_server,
        operator_confirmation=bool(args.i_understand_this_sends_a_demo_pending_order),
    )
    if args.output_path is not None:
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        args.output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    if payload.get("blocked_reason") is not None or payload.get("error") is not None:
        raise SystemExit(1)
    if payload.get("remaining_open_orders") or payload.get("remaining_positions"):
        raise SystemExit(2)


def collect_mt5_pending_modify_payload(
    config: AppConfig,
    *,
    symbol: str,
    side: OrderSide,
    volume_lots: float | None = None,
    initial_offset_points: int = 2000,
    modify_extra_points: int = 250,
    expected_login: int | None = None,
    expected_server: str | None = None,
    operator_confirmation: bool = False,
    mt5_module: MetaTrader5ModuleProtocol | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Run one controlled pending-order modify demo and return JSON-safe diagnostics."""

    timestamp = generated_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware.")
    if config.broker.live_adapter.strip().lower() != "mt5":
        raise RuntimeError("mt5_pending_modify_demo.py requires broker.live_adapter=mt5.")
    if not symbol.strip():
        raise ValueError("symbol must be non-empty.")

    resolved_volume = _resolve_safe_volume(config, volume_lots=volume_lots)
    module = load_metatrader5_module() if mt5_module is None else mt5_module
    module_loader = None if mt5_module is None else lambda: module
    preflight = build_mt5_preflight_report(config, module_loader=module_loader).to_dict()
    payload: dict[str, Any] = {
        "generated_at": timestamp.astimezone(UTC).isoformat(),
        "symbol": symbol.strip(),
        "side": side.value,
        "volume_lots": resolved_volume,
        "initial_offset_points": initial_offset_points,
        "modify_extra_points": modify_extra_points,
        "operator_confirmation": operator_confirmation,
        "preflight": preflight,
        "pending_order_send_attempted": False,
        "modify_attempted": False,
        "cancel_order_send_attempted": False,
    }

    if not operator_confirmation:
        return _blocked(payload, "operator_confirmation_missing")
    if not preflight["ready_for_connection"]:
        return _blocked(payload, "mt5_preflight_not_ready")

    client = None
    broker_order_id: str | None = None
    try:
        client = build_mt5_terminal_client(config, mt5_module=module)
        account = client.describe_account()
        terminal = _safe_mapping(module.terminal_info())
        account_payload = _safe_mapping(module.account_info())
        current_orders = _current_open_orders(module, symbol.strip())
        current_positions = _current_positions(module, symbol.strip())
        payload.update(
            {
                "connected": client.is_connected(),
                "ping_latency_ms": client.ping_latency_ms(),
                "account": asdict(account),
                "terminal": _terminal_summary(terminal),
                "account_permissions": _account_permissions(account_payload),
                "initial_open_orders": [_json_safe(order) for order in current_orders],
                "initial_positions": [
                    _json_safe(position) for position in current_positions
                ],
            }
        )

        block_reason = _safety_block_reason(
            payload=payload,
            account=account_payload,
            terminal=terminal,
            expected_login=expected_login,
            expected_server=expected_server,
            open_orders=current_orders,
            open_positions=current_positions,
        )
        if block_reason is not None:
            return _blocked(payload, block_reason)

        spec = client.get_symbol_spec(symbol.strip())
        tick = _safe_mapping(module.symbol_info_tick(symbol.strip()))
        pending_prices = _pending_limit_prices(
            side=side,
            tick=tick,
            spec=spec,
            initial_offset_points=initial_offset_points,
            modify_extra_points=modify_extra_points,
        )
        payload["pending_prices"] = pending_prices

        request = Mt5OrderRequest(
            client_order_id=f"pending_modify_{int(timestamp.timestamp())}",
            broker_symbol=symbol.strip(),
            side=side,
            order_type=OrderType.LIMIT,
            submitted_at=timestamp.astimezone(UTC),
            volume_lots=resolved_volume,
            time_in_force=TimeInForce.GTC,
            limit_price=pending_prices["initial_limit_price"],
        )
        place_check = client.check_order(request)
        payload["place_order_check"] = _order_check_to_dict(place_check)
        if not place_check.accepted:
            return _blocked(payload, "place_order_check_rejected")

        payload["pending_order_send_attempted"] = True
        placed_order = client.submit_order(request)
        broker_order_id = placed_order.broker_order_id
        payload["placed_order"] = _order_state_to_dict(placed_order)
        if placed_order.status not in {
            ExecutionOrderStatus.ACCEPTED,
            ExecutionOrderStatus.TRIGGERED,
            ExecutionOrderStatus.PARTIALLY_FILLED,
        } or placed_order.remaining_volume_lots <= 0:
            return _blocked(payload, "pending_order_not_open_after_place")

        modify_request = Mt5PendingOrderModifyRequest(
            broker_order_id=broker_order_id,
            broker_symbol=symbol.strip(),
            side=side,
            order_type=OrderType.LIMIT,
            submitted_at=datetime.now(UTC),
            time_in_force=TimeInForce.GTC,
            limit_price=pending_prices["modified_limit_price"],
        )
        payload["modify_attempted"] = True
        modified_order = client.modify_pending_order(modify_request)
        payload["modified_order"] = _order_state_to_dict(modified_order)
        return payload
    except Exception as exc:
        payload["error"] = str(exc)
        return payload
    finally:
        if client is not None and broker_order_id is not None:
            try:
                payload["cancel_order_send_attempted"] = True
                canceled_order = client.cancel_order(
                    broker_order_id,
                    timestamp=datetime.now(UTC),
                )
                payload["canceled_order"] = _order_state_to_dict(canceled_order)
            except Exception as exc:
                payload["cancel_error"] = str(exc)
            payload["remaining_open_orders"] = [
                _json_safe(order) for order in _current_open_orders(module, symbol.strip())
            ]
            payload["remaining_positions"] = [
                _json_safe(position) for position in _current_positions(module, symbol.strip())
            ]
        if client is not None:
            client.close()


def _resolve_safe_volume(config: AppConfig, *, volume_lots: float | None) -> float:
    min_volume = float(config.broker.mt5.min_volume_lots)
    if volume_lots is None:
        return min_volume
    requested = float(volume_lots)
    if requested <= 0:
        raise ValueError("volume_lots must be greater than zero.")
    if requested > min_volume:
        raise ValueError(
            "controlled pending modify validation may not exceed broker.mt5.min_volume_lots."
        )
    return requested


def _pending_limit_prices(
    *,
    side: OrderSide,
    tick: Mapping[str, Any],
    spec: Mt5SymbolSpec,
    initial_offset_points: int,
    modify_extra_points: int,
) -> dict[str, Any]:
    bid = _positive_float(tick.get("bid"), name="bid")
    ask = _positive_float(tick.get("ask"), name="ask")
    point = spec.point or 0.00001
    digits = spec.digits if spec.digits is not None else 5
    minimum_points = max(
        int(initial_offset_points),
        int(spec.stops_level_points or 0) + int(spec.freeze_level_points or 0) + 100,
    )
    extra_points = max(int(modify_extra_points), 1)
    if minimum_points <= 0:
        raise ValueError("initial_offset_points must be positive.")

    if side is OrderSide.BUY:
        initial_price = bid - (minimum_points * point)
        modified_price = bid - ((minimum_points + extra_points) * point)
    else:
        initial_price = ask + (minimum_points * point)
        modified_price = ask + ((minimum_points + extra_points) * point)
    if initial_price <= 0 or modified_price <= 0:
        raise ValueError("Calculated pending limit price must be positive.")
    return {
        "bid": bid,
        "ask": ask,
        "point": point,
        "digits": digits,
        "minimum_points": minimum_points,
        "extra_points": extra_points,
        "initial_limit_price": round(initial_price, digits),
        "modified_limit_price": round(modified_price, digits),
    }


def _safety_block_reason(
    *,
    payload: Mapping[str, Any],
    account: Mapping[str, Any],
    terminal: Mapping[str, Any],
    expected_login: int | None,
    expected_server: str | None,
    open_orders: Sequence[Mapping[str, Any]],
    open_positions: Sequence[Mapping[str, Any]],
) -> str | None:
    account_summary = payload.get("account")
    if not isinstance(account_summary, Mapping):
        return "account_snapshot_missing"
    login = account_summary.get("login")
    server = str(account_summary.get("server") or "")
    if expected_login is not None and login != expected_login:
        return "unexpected_account_login"
    if expected_server is not None and server != expected_server:
        return "unexpected_account_server"
    if "demo" not in server.lower():
        return "account_server_is_not_demo"
    if account.get("trade_allowed") is False:
        return "account_trade_not_allowed"
    if account.get("trade_expert") is False:
        return "account_expert_trading_not_allowed"
    if terminal.get("tradeapi_disabled") is True:
        return "terminal_tradeapi_disabled"
    if terminal.get("trade_allowed") is not True:
        return "terminal_trade_not_allowed"
    if open_orders:
        return "open_orders_present"
    if open_positions:
        return "open_symbol_position_present"
    return None


def _current_open_orders(
    module: MetaTrader5ModuleProtocol,
    symbol: str,
) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        _safe_mapping(order) for order in _safe_sequence(module.orders_get(symbol=symbol))
    )


def _current_positions(
    module: MetaTrader5ModuleProtocol,
    symbol: str,
) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        _safe_mapping(position) for position in _safe_sequence(module.positions_get(symbol=symbol))
    )


def _positive_float(value: Any, *, name: str) -> float:
    resolved = float(value)
    if resolved <= 0:
        raise ValueError(f"{name} must be positive.")
    return resolved


def _blocked(payload: dict[str, Any], reason: str) -> dict[str, Any]:
    payload["blocked_reason"] = reason
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


def _account_permissions(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "trade_allowed": payload.get("trade_allowed"),
        "trade_expert": payload.get("trade_expert"),
        "trade_mode": payload.get("trade_mode"),
    }


def _order_check_to_dict(check: Any) -> dict[str, Any]:
    payload = asdict(check)
    payload["checked_at"] = check.checked_at.isoformat()
    payload["rejection_reason"] = check.rejection_reason
    return payload


def _order_state_to_dict(order: Mt5OrderState) -> dict[str, Any]:
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


def _position_state_to_dict(position: Mt5PositionState) -> dict[str, Any]:
    payload = asdict(position)
    payload["timestamp"] = position.timestamp.isoformat()
    payload["position_mode"] = position.position_mode.value
    return payload


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
