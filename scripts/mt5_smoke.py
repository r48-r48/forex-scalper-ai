"""Read-only MT5 connectivity and account smoke check."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scalper_ai.config import load_app_config
from scalper_ai.deployment import build_mt5_preflight_report
from scalper_ai.deployment.live_factory import build_mt5_terminal_client
from scalper_ai.utils import configure_logging


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Run a read-only MT5 connectivity/account smoke check.",
    )
    parser.add_argument(
        "--config-name",
        default="mt5",
        help="Config overlay to load.",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="Optional config directory override.",
    )
    parser.add_argument(
        "--include-orders",
        action="store_true",
        help="Include normalized order snapshots in the JSON output.",
    )
    parser.add_argument(
        "--include-positions",
        action="store_true",
        help="Include normalized position snapshots in the JSON output.",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Print MT5 readiness diagnostics without attempting a terminal connection.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""

    args = parse_args()
    config = load_app_config(config_name=args.config_name, config_dir=args.config_dir)
    configure_logging(config.logging)
    if config.broker.live_adapter.strip().lower() != "mt5":
        raise RuntimeError("mt5_smoke.py requires broker.live_adapter=mt5 in the selected config.")

    preflight = build_mt5_preflight_report(config)
    payload: dict[str, object] = {
        "preflight": preflight.to_dict(),
    }

    if args.preflight_only:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    if not preflight.ready_for_connection:
        payload["connection_attempted"] = False
        payload["connection_error"] = (
            "MT5 preflight failed. Resolve the reported errors before retrying."
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        raise SystemExit(1)

    client = None
    try:
        client = build_mt5_terminal_client(config)
        account = client.describe_account()
        positions = client.list_positions()
        orders = client.list_orders()
        payload.update(
            {
                "connection_attempted": True,
                "connected": client.is_connected(),
                "ping_latency_ms": client.ping_latency_ms(),
                "account": {
                    "login": account.login,
                    "server": account.server,
                    "balance": account.balance,
                    "equity": account.equity,
                    "leverage": account.leverage,
                    "company": account.company,
                    "currency": account.currency,
                },
                "order_count": len(orders),
                "position_count": len(positions),
            }
        )
        if args.include_orders:
            payload["orders"] = [
                {
                    "broker_order_id": order.broker_order_id,
                    "broker_symbol": order.broker_symbol,
                    "status": order.status.value,
                    "submitted_at": order.submitted_at.isoformat(),
                    "updated_at": order.updated_at.isoformat(),
                    "requested_volume_lots": order.requested_volume_lots,
                    "filled_volume_lots": order.filled_volume_lots,
                    "remaining_volume_lots": order.remaining_volume_lots,
                    "average_fill_price": order.average_fill_price,
                }
                for order in orders
            ]
        if args.include_positions:
            payload["positions"] = [
                {
                    "broker_symbol": position.broker_symbol,
                    "timestamp": position.timestamp.isoformat(),
                    "net_volume_lots": position.net_volume_lots,
                    "average_entry_price": position.average_entry_price,
                }
                for position in positions
            ]
        print(json.dumps(payload, indent=2, sort_keys=True))
    except Exception as exc:
        payload["connection_attempted"] = True
        payload["connection_error"] = str(exc)
        print(json.dumps(payload, indent=2, sort_keys=True))
        raise SystemExit(1) from exc
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()
