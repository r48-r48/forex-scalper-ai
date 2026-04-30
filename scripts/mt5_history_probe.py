"""Read-only MT5 history/deal API investigation probe."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
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
from scalper_ai.execution.mt5_client import (
    MetaTrader5ModuleProtocol,
    Mt5TerminalClient,
    load_metatrader5_module,
)
from scalper_ai.utils import configure_logging


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Run read-only MT5 order/deal history diagnostics. "
            "This script never calls order_send."
        ),
    )
    parser.add_argument("--config-name", default="mt5", help="Config overlay to load.")
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--symbol", default=None, help="Optional broker symbol/group focus.")
    parser.add_argument("--order-ticket", type=int, default=None)
    parser.add_argument("--position-ticket", type=int, default=None)
    parser.add_argument("--lookback-hours", type=int, default=None)
    parser.add_argument("--include-raw-samples", action="store_true")
    parser.add_argument("--output-path", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""

    args = parse_args()
    config = load_app_config(config_name=args.config_name, config_dir=args.config_dir)
    configure_logging(config.logging)
    payload = collect_mt5_history_probe_payload(
        config,
        symbol=args.symbol,
        order_ticket=args.order_ticket,
        position_ticket=args.position_ticket,
        lookback_hours=args.lookback_hours,
        include_raw_samples=bool(args.include_raw_samples),
    )
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output_path is not None:
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        args.output_path.write_text(rendered, encoding="utf-8")
    print(rendered)


def collect_mt5_history_probe_payload(
    config: AppConfig,
    *,
    symbol: str | None = None,
    order_ticket: int | None = None,
    position_ticket: int | None = None,
    lookback_hours: int | None = None,
    include_raw_samples: bool = False,
    mt5_module: MetaTrader5ModuleProtocol | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Collect read-only MT5 history diagnostics across known Python API call shapes."""

    if config.broker.live_adapter.strip().lower() != "mt5":
        raise RuntimeError("mt5_history_probe.py requires broker.live_adapter=mt5.")
    if order_ticket is not None and order_ticket <= 0:
        raise ValueError("order_ticket must be positive when provided.")
    if position_ticket is not None and position_ticket <= 0:
        raise ValueError("position_ticket must be positive when provided.")
    if lookback_hours is not None and lookback_hours <= 0:
        raise ValueError("lookback_hours must be positive when provided.")

    timestamp = generated_at or datetime.now(timezone.utc)  # noqa: UP017
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware.")
    normalized_symbol = symbol.strip() if symbol is not None and symbol.strip() else None
    resolved_lookback_hours = lookback_hours or config.broker.mt5.history_lookback_hours
    end_time = timestamp.astimezone(timezone.utc)  # noqa: UP017
    start_time = end_time - timedelta(hours=resolved_lookback_hours)

    module = load_metatrader5_module() if mt5_module is None else mt5_module
    module_loader = None if mt5_module is None else lambda: module
    preflight = build_mt5_preflight_report(config, module_loader=module_loader).to_dict()
    payload: dict[str, Any] = {
        "generated_at": end_time.isoformat(),
        "preflight": preflight,
        "order_send_called": False,
        "window": {
            "lookback_hours": resolved_lookback_hours,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        },
        "symbol": normalized_symbol,
        "order_ticket": order_ticket,
        "position_ticket": position_ticket,
    }
    if not preflight["ready_for_connection"]:
        payload["connection_attempted"] = False
        payload["connection_error"] = "MT5 preflight failed."
        return payload

    client: Mt5TerminalClient | None = None
    try:
        client = build_mt5_terminal_client(config, mt5_module=module)
        payload["connection_attempted"] = True
        payload["connected"] = client.is_connected()
        payload["account"] = client.describe_account().__dict__
        payload["terminal"] = _json_safe(_safe_mapping(module.terminal_info()))
        payload["current_orders"] = _history_call(
            module,
            label="orders_get",
            call=lambda: module.orders_get(),
            include_raw_samples=include_raw_samples,
        )
        payload["current_positions"] = _history_call(
            module,
            label="positions_get",
            call=lambda: module.positions_get(),
            include_raw_samples=include_raw_samples,
        )
        payload["history_calls"] = _history_calls(
            module,
            start_time=start_time,
            end_time=end_time,
            symbol=normalized_symbol,
            order_ticket=order_ticket,
            position_ticket=position_ticket,
            include_raw_samples=include_raw_samples,
        )
        return payload
    except Exception as exc:
        payload["connection_attempted"] = True
        payload["connection_error"] = str(exc)
        return payload
    finally:
        if client is not None:
            client.close()


def _history_calls(
    module: MetaTrader5ModuleProtocol,
    *,
    start_time: datetime,
    end_time: datetime,
    symbol: str | None,
    order_ticket: int | None,
    position_ticket: int | None,
    include_raw_samples: bool,
) -> dict[str, dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {
        "orders_window": _history_call(
            module,
            label="history_orders_get_window",
            call=lambda: module.history_orders_get(start_time, end_time),
            include_raw_samples=include_raw_samples,
        ),
        "deals_window": _history_call(
            module,
            label="history_deals_get_window",
            call=lambda: module.history_deals_get(start_time, end_time),
            include_raw_samples=include_raw_samples,
        ),
    }
    if symbol is not None:
        group = f"*{symbol}*"
        calls["orders_window_group"] = _history_call(
            module,
            label="history_orders_get_window_group",
            call=lambda: module.history_orders_get(start_time, end_time, group=group),
            include_raw_samples=include_raw_samples,
        )
        calls["deals_window_group"] = _history_call(
            module,
            label="history_deals_get_window_group",
            call=lambda: module.history_deals_get(start_time, end_time, group=group),
            include_raw_samples=include_raw_samples,
        )
    if order_ticket is not None:
        calls["orders_ticket"] = _history_call(
            module,
            label="history_orders_get_ticket",
            call=lambda: module.history_orders_get(ticket=order_ticket),
            include_raw_samples=include_raw_samples,
        )
        calls["deals_ticket"] = _history_call(
            module,
            label="history_deals_get_ticket",
            call=lambda: module.history_deals_get(ticket=order_ticket),
            include_raw_samples=include_raw_samples,
        )
    if position_ticket is not None:
        calls["deals_position"] = _history_call(
            module,
            label="history_deals_get_position",
            call=lambda: module.history_deals_get(position=position_ticket),
            include_raw_samples=include_raw_samples,
        )
    return calls


def _history_call(
    module: MetaTrader5ModuleProtocol,
    *,
    label: str,
    call: Callable[[], Any],
    include_raw_samples: bool,
) -> dict[str, Any]:
    try:
        records = _safe_sequence(call())
        payload: dict[str, Any] = {
            "label": label,
            "count": len(records),
            "error": None,
            "last_error": _json_safe(module.last_error()),
        }
        if include_raw_samples:
            payload["first"] = _json_safe(_safe_mapping(records[0])) if records else None
        return payload
    except Exception as exc:
        return {
            "label": label,
            "count": 0,
            "error": str(exc),
            "last_error": _json_safe(module.last_error()),
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
