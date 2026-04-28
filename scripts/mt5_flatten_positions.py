"""Controlled MT5 demo-position flattening by position ticket."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scalper_ai.config import load_app_config
from scalper_ai.execution.mt5_client import load_metatrader5_module
from scalper_ai.utils import configure_logging


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Close MT5 demo positions by position ticket with strict safety gates.",
    )
    parser.add_argument("--config-name", default="mt5", help="Config overlay to load.")
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--expected-login", type=int, default=None)
    parser.add_argument("--expected-server", default=None)
    parser.add_argument(
        "--time-in-force",
        choices=("ioc", "fok"),
        default="ioc",
        help="Filling policy for close requests.",
    )
    parser.add_argument(
        "--i-understand-this-closes-demo-positions",
        action="store_true",
        help="Required explicit confirmation before position close order_send calls.",
    )
    parser.add_argument("--output-path", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""

    args = parse_args()
    config = load_app_config(config_name=args.config_name, config_dir=args.config_dir)
    configure_logging(config.logging)
    payload = flatten_mt5_demo_positions(
        config,
        symbol=str(args.symbol),
        expected_login=args.expected_login,
        expected_server=args.expected_server,
        time_in_force=str(args.time_in_force),
        operator_confirmation=bool(args.i_understand_this_closes_demo_positions),
    )
    if args.output_path is not None:
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        args.output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    if payload.get("blocked_reason") is not None:
        raise SystemExit(1)
    if payload.get("remaining_positions"):
        raise SystemExit(2)


def flatten_mt5_demo_positions(
    config: Any,
    *,
    symbol: str,
    expected_login: int | None,
    expected_server: str | None,
    time_in_force: str,
    operator_confirmation: bool,
    mt5_module: Any | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Close all current demo positions for one symbol by position ticket."""

    timestamp = generated_at or datetime.now(UTC)
    module = load_metatrader5_module() if mt5_module is None else mt5_module
    payload: dict[str, Any] = {
        "generated_at": timestamp.astimezone(UTC).isoformat(),
        "symbol": symbol.strip(),
        "operator_confirmation": operator_confirmation,
        "close_order_send_attempts": 0,
    }
    if not operator_confirmation:
        return _blocked(payload, "operator_confirmation_missing")
    if not symbol.strip():
        return _blocked(payload, "symbol_missing")

    initialize_kwargs = {"timeout": config.broker.mt5.timeout_milliseconds}
    if config.broker.mt5.terminal_path is not None:
        initialize_kwargs["path"] = str(config.broker.mt5.terminal_path)
    if config.broker.mt5.login is not None:
        initialize_kwargs["login"] = config.broker.mt5.login
    if config.broker.mt5.password is not None:
        initialize_kwargs["password"] = config.broker.mt5.password
    if config.broker.mt5.server is not None:
        initialize_kwargs["server"] = config.broker.mt5.server

    if not module.initialize(**initialize_kwargs):
        return _blocked(payload, f"initialize_failed:{module.last_error()}")
    try:
        account = _safe_mapping(module.account_info())
        terminal = _safe_mapping(module.terminal_info())
        payload["account"] = _account_summary(account)
        payload["terminal"] = _terminal_summary(terminal)
        block_reason = _safety_block_reason(
            account=account,
            terminal=terminal,
            expected_login=expected_login,
            expected_server=expected_server,
        )
        if block_reason is not None:
            return _blocked(payload, block_reason)

        positions = tuple(
            _safe_mapping(position)
            for position in _safe_sequence(module.positions_get(symbol=symbol.strip()))
        )
        payload["initial_positions"] = [_json_safe(position) for position in positions]
        close_results = []
        for position in positions:
            result = _close_one_position(
                module,
                position=position,
                magic_number=config.broker.mt5.magic_number,
                deviation_points=config.broker.mt5.deviation_points,
                time_in_force=time_in_force,
            )
            close_results.append(result)
            payload["close_order_send_attempts"] += int(result["order_send_attempted"])
        payload["close_results"] = close_results
        remaining = tuple(
            _safe_mapping(position)
            for position in _safe_sequence(module.positions_get(symbol=symbol.strip()))
        )
        payload["remaining_positions"] = [_json_safe(position) for position in remaining]
        return payload
    finally:
        module.shutdown()


def _close_one_position(
    module: Any,
    *,
    position: Mapping[str, Any],
    magic_number: int,
    deviation_points: int,
    time_in_force: str,
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
        "type_filling": _filling_code(module, time_in_force),
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
    send_result = module.order_send(request)
    return {
        "position": _json_safe(position),
        "request": _json_safe(request),
        "check": _json_safe(check_payload),
        "order_send_attempted": True,
        "send": _json_safe(_safe_mapping(send_result)),
        "last_error": module.last_error(),
    }


def _safety_block_reason(
    *,
    account: Mapping[str, Any],
    terminal: Mapping[str, Any],
    expected_login: int | None,
    expected_server: str | None,
) -> str | None:
    server = str(account.get("server") or "")
    if expected_login is not None and account.get("login") != expected_login:
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
    return None


def _filling_code(module: Any, time_in_force: str) -> int:
    if time_in_force == "ioc":
        return getattr(module, "ORDER_FILLING_IOC", 1)
    return getattr(module, "ORDER_FILLING_FOK", 0)


def _check_accepted(module: Any, retcode: Any) -> bool:
    return retcode in {
        0,
        getattr(module, "TRADE_RETCODE_DONE", 10009),
        getattr(module, "TRADE_RETCODE_DONE_PARTIAL", 10010),
        getattr(module, "TRADE_RETCODE_PLACED", 10008),
    }


def _blocked(payload: dict[str, Any], reason: str) -> dict[str, Any]:
    payload["blocked_reason"] = reason
    return payload


def _account_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "login": payload.get("login"),
        "server": payload.get("server"),
        "balance": payload.get("balance"),
        "equity": payload.get("equity"),
        "currency": payload.get("currency"),
        "trade_allowed": payload.get("trade_allowed"),
        "trade_expert": payload.get("trade_expert"),
    }


def _terminal_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": payload.get("name"),
        "build": payload.get("build"),
        "connected": payload.get("connected"),
        "trade_allowed": payload.get("trade_allowed"),
        "tradeapi_disabled": payload.get("tradeapi_disabled"),
    }


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
