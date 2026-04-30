"""Controlled MT5 demo-order validation with strict safety gates."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
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
from scalper_ai.execution.mt5_client import MetaTrader5ModuleProtocol, load_metatrader5_module
from scalper_ai.execution.mt5_live import Mt5OrderRequest, Mt5OrderState, Mt5PositionState
from scalper_ai.utils import configure_logging


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Run one strictly gated MT5 demo-order validation. This script can call "
            "order_send only when explicit confirmation and terminal permissions are present."
        ),
    )
    parser.add_argument("--config-name", default="mt5", help="Config overlay to load.")
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="Optional config directory override.",
    )
    parser.add_argument("--symbol", default="EURUSD", help="Broker symbol to test.")
    parser.add_argument(
        "--side",
        choices=("buy", "sell"),
        default="buy",
        help="Initial side for the demo-order test.",
    )
    parser.add_argument(
        "--time-in-force",
        choices=("ioc", "fok"),
        default="ioc",
        help="Filling policy for the demo-order test.",
    )
    parser.add_argument(
        "--volume-lots",
        type=float,
        default=None,
        help="Lot volume. Defaults to broker.mt5.min_volume_lots and may not exceed it.",
    )
    parser.add_argument(
        "--expected-login",
        type=int,
        default=None,
        help="Optional account login that must match before sending.",
    )
    parser.add_argument(
        "--expected-server",
        default=None,
        help="Optional account server that must match before sending.",
    )
    parser.add_argument(
        "--history-lookback-hours",
        type=int,
        default=None,
        help="Raw history lookback window for post-trade diagnostics.",
    )
    parser.add_argument(
        "--no-auto-flatten",
        action="store_true",
        help="Leave the resulting demo position open instead of sending a flattening order.",
    )
    parser.add_argument(
        "--i-understand-this-sends-a-demo-order",
        action="store_true",
        help="Required explicit confirmation. Without it no order_send call is made.",
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
    payload = collect_mt5_demo_order_payload(
        config,
        symbol=str(args.symbol),
        side=OrderSide(args.side),
        time_in_force=_time_in_force_from_cli(str(args.time_in_force)),
        volume_lots=args.volume_lots,
        expected_login=args.expected_login,
        expected_server=args.expected_server,
        history_lookback_hours=args.history_lookback_hours,
        auto_flatten=not bool(args.no_auto_flatten),
        operator_confirmation=bool(args.i_understand_this_sends_a_demo_order),
    )
    if args.output_path is not None:
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        args.output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    if not payload.get("sent", False):
        raise SystemExit(1)


def collect_mt5_demo_order_payload(
    config: AppConfig,
    *,
    symbol: str,
    side: OrderSide,
    time_in_force: TimeInForce,
    volume_lots: float | None = None,
    expected_login: int | None = None,
    expected_server: str | None = None,
    history_lookback_hours: int | None = None,
    auto_flatten: bool = True,
    operator_confirmation: bool = False,
    mt5_module: MetaTrader5ModuleProtocol | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Run one gated MT5 demo-order validation and return a JSON-safe payload."""

    timestamp = generated_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware.")
    if config.broker.live_adapter.strip().lower() != "mt5":
        raise RuntimeError("mt5_demo_order.py requires broker.live_adapter=mt5.")
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
        "time_in_force": time_in_force.value,
        "volume_lots": resolved_volume,
        "auto_flatten": auto_flatten,
        "operator_confirmation": operator_confirmation,
        "preflight": preflight,
        "sent": False,
        "order_send_attempted": False,
        "flatten_order_send_attempted": False,
    }

    if not operator_confirmation:
        return _blocked(payload, "operator_confirmation_missing")
    if not preflight["ready_for_connection"]:
        return _blocked(payload, "mt5_preflight_not_ready")

    client = None
    try:
        client = build_mt5_terminal_client(config, mt5_module=module)
        account = client.describe_account()
        terminal = _safe_mapping(module.terminal_info())
        account_payload = _safe_mapping(module.account_info())
        current_orders = client.list_orders()
        current_positions = client.list_positions()
        payload.update(
            {
                "connected": client.is_connected(),
                "ping_latency_ms": client.ping_latency_ms(),
                "account": asdict(account),
                "terminal": _terminal_summary(terminal),
                "account_permissions": _account_permissions(account_payload),
                "initial_orders": [_order_state_to_dict(order) for order in current_orders],
                "initial_positions": [
                    _position_state_to_dict(position) for position in current_positions
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
            symbol=symbol.strip(),
        )
        if block_reason is not None:
            return _blocked(payload, block_reason)

        request = Mt5OrderRequest(
            client_order_id=f"demo_probe_{int(timestamp.timestamp())}",
            broker_symbol=symbol.strip(),
            side=side,
            order_type=OrderType.MARKET,
            submitted_at=timestamp.astimezone(UTC),
            volume_lots=resolved_volume,
            time_in_force=time_in_force,
        )
        order_check = client.check_order(request)
        payload["order_check"] = _order_check_to_dict(order_check)
        if not order_check.accepted:
            return _blocked(payload, "order_check_rejected")

        payload["order_send_attempted"] = True
        state = client.submit_order(request)
        payload["sent"] = True
        payload["submitted_order"] = _order_state_to_dict(state)
        payload["post_submit_orders"] = [
            _order_state_to_dict(order) for order in client.list_orders()
        ]
        payload["post_submit_positions"] = [
            _position_state_to_dict(position) for position in client.list_positions()
        ]

        if auto_flatten:
            flatten_results = _flatten_symbol_positions_by_ticket(
                module=module,
                symbol=symbol.strip(),
                magic_number=config.broker.mt5.magic_number,
                deviation_points=config.broker.mt5.deviation_points,
                time_in_force=time_in_force,
            )
            payload["flatten_order_send_attempted"] = any(
                result["order_send_attempted"] for result in flatten_results
            )
            payload["flatten_orders"] = flatten_results
            payload["post_flatten_positions"] = [
                _position_state_to_dict(position) for position in client.list_positions()
            ]

        payload["raw_history"] = _history_summary(
            module,
            generated_at=datetime.now(UTC),
            lookback_hours=history_lookback_hours or config.broker.mt5.history_lookback_hours,
            include_raw_samples=True,
        )
        return payload
    except Exception as exc:
        payload["error"] = str(exc)
        return payload
    finally:
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
        raise ValueError("controlled demo validation may not exceed broker.mt5.min_volume_lots.")
    return requested


def _safety_block_reason(
    *,
    payload: Mapping[str, Any],
    account: Mapping[str, Any],
    terminal: Mapping[str, Any],
    expected_login: int | None,
    expected_server: str | None,
    open_orders: Sequence[Mt5OrderState],
    open_positions: Sequence[Mt5PositionState],
    symbol: str,
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
    if any(position.broker_symbol == symbol for position in open_positions):
        return "open_symbol_position_present"
    return None


def _flatten_symbol_positions_by_ticket(
    *,
    module: MetaTrader5ModuleProtocol,
    symbol: str,
    magic_number: int,
    deviation_points: int,
    time_in_force: TimeInForce,
) -> list[dict[str, Any]]:
    positions = tuple(
        _safe_mapping(position) for position in _safe_sequence(module.positions_get(symbol=symbol))
    )
    return [
        _close_one_position(
            module,
            position=position,
            magic_number=magic_number,
            deviation_points=deviation_points,
            time_in_force=time_in_force,
        )
        for position in positions
    ]


def _close_one_position(
    module: MetaTrader5ModuleProtocol,
    *,
    position: Mapping[str, Any],
    magic_number: int,
    deviation_points: int,
    time_in_force: TimeInForce,
) -> dict[str, Any]:
    symbol = str(position["symbol"])
    volume = float(position["volume"])
    tick = _safe_mapping(module.symbol_info_tick(symbol))
    position_type_buy = getattr(module, "POSITION_TYPE_BUY", 0)
    order_type_buy = getattr(module, "ORDER_TYPE_BUY", 0)
    order_type_sell = getattr(module, "ORDER_TYPE_SELL", 1)
    close_type = order_type_sell if position.get("type") == position_type_buy else order_type_buy
    price = tick.get("bid") if close_type == order_type_sell else tick.get("ask")
    request = {
        "action": getattr(module, "TRADE_ACTION_DEAL", 1),
        "symbol": symbol,
        "volume": volume,
        "type": close_type,
        "position": position["ticket"],
        "price": price,
        "deviation": deviation_points,
        "magic": magic_number,
        "comment": "scalper_ai_flatten",
        "type_time": getattr(module, "ORDER_TIME_GTC", 0),
        "type_filling": _filling_policy_code(module, time_in_force),
    }
    check = module.order_check(request)
    check_payload = _safe_mapping(check)
    if not _check_accepted(module, check_payload.get("retcode")):
        return {
            "position": _json_safe(position),
            "request": _json_safe(request),
            "check": _json_safe(check_payload),
            "order_send_attempted": False,
            "send": None,
            "last_error": module.last_error(),
        }
    result = module.order_send(request)
    return {
        "position": _json_safe(position),
        "request": _json_safe(request),
        "check": _json_safe(check_payload),
        "order_send_attempted": True,
        "send": _json_safe(_safe_mapping(result)),
        "last_error": module.last_error(),
    }


def _filling_policy_code(module: MetaTrader5ModuleProtocol, time_in_force: TimeInForce) -> int:
    if time_in_force is TimeInForce.IOC:
        return getattr(module, "ORDER_FILLING_IOC", 1)
    return getattr(module, "ORDER_FILLING_FOK", 0)


def _check_accepted(module: MetaTrader5ModuleProtocol, retcode: Any) -> bool:
    return retcode in {
        0,
        getattr(module, "TRADE_RETCODE_DONE", 10009),
        getattr(module, "TRADE_RETCODE_DONE_PARTIAL", 10010),
        getattr(module, "TRADE_RETCODE_PLACED", 10008),
    }


def _blocked(payload: dict[str, Any], reason: str) -> dict[str, Any]:
    payload["blocked_reason"] = reason
    return payload


def _time_in_force_from_cli(value: str) -> TimeInForce:
    if value == "ioc":
        return TimeInForce.IOC
    return TimeInForce.FOK


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


def _history_summary(
    module: MetaTrader5ModuleProtocol,
    *,
    generated_at: datetime,
    lookback_hours: int,
    include_raw_samples: bool,
) -> dict[str, Any]:
    end_time = generated_at.astimezone(UTC)
    start_time = end_time - timedelta(hours=lookback_hours)
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
