"""Runtime bootstrap entrypoint with operational summary, health, and metrics surfaces."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from time import sleep
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scalper_ai.deployment import (
    JsonlAlertTransport,
    RuntimeSupervisor,
    RuntimeSupervisorConfig,
    WebhookAlertTransport,
    bootstrap_runtime,
)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Bootstrap one deployment runtime and print its status surfaces.",
    )
    parser.add_argument(
        "action",
        choices=("describe", "health", "metrics", "supervise"),
        help="Which runtime surface to print.",
    )
    parser.add_argument(
        "--config-name",
        default="research",
        help="Config overlay to load.",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="Optional config directory override.",
    )
    parser.add_argument(
        "--live-confirmation",
        default=None,
        help="Explicit confirmation phrase required for live-safe startup.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Optional supervisor iteration cap. Omit for a long-running loop.",
    )
    parser.add_argument(
        "--health-interval-seconds",
        type=float,
        default=30.0,
        help="Supervisor health polling interval.",
    )
    parser.add_argument(
        "--reconciliation-interval-seconds",
        type=float,
        default=60.0,
        help="Supervisor reconciliation polling interval.",
    )
    parser.add_argument(
        "--idle-sleep-seconds",
        type=float,
        default=1.0,
        help="Supervisor sleep between iterations.",
    )
    parser.add_argument(
        "--alert-jsonl-path",
        type=Path,
        default=None,
        help="Optional local JSONL alert sink used by the supervisor.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""

    args = parse_args()
    live_confirmation = args.live_confirmation or os.environ.get("SCALPER_AI_LIVE_CONFIRMATION")
    runtime = bootstrap_runtime(
        config_name=args.config_name,
        config_dir=args.config_dir,
        live_confirmation_token=live_confirmation,
    )
    try:
        if args.action == "describe":
            print(json.dumps(runtime.summary().to_dict(), indent=2, sort_keys=True))
            return
        if args.action == "health":
            print(json.dumps(runtime.health_snapshot().to_dict(), indent=2, sort_keys=True))
            return
        if args.action == "metrics":
            print(runtime.metrics_text())
            return
        supervisor = RuntimeSupervisor(
            runtime,
            config=RuntimeSupervisorConfig(
                health_interval_seconds=args.health_interval_seconds,
                reconciliation_interval_seconds=args.reconciliation_interval_seconds,
                idle_sleep_seconds=args.idle_sleep_seconds,
                alert_include_warnings=runtime.config.monitoring.alert_include_warnings,
            ),
            alert_transport=_build_alert_transport(
                runtime.config.monitoring.alert_webhook_url,
                webhook_timeout_seconds=(
                    runtime.config.monitoring.alert_webhook_timeout_seconds
                ),
                jsonl_path=args.alert_jsonl_path,
            ),
        )
        if args.max_iterations is None:
            while True:
                iteration = supervisor.run_forever(max_iterations=1)[0]
                print(json.dumps(_iteration_to_dict(iteration), sort_keys=True), flush=True)
                sleep(args.idle_sleep_seconds)
        iterations = supervisor.run_forever(max_iterations=args.max_iterations)
        print(
            json.dumps(
                [_iteration_to_dict(iteration) for iteration in iterations],
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        runtime.stop()


class _CompositeAlertTransport:
    def __init__(self, transports: tuple[Any, ...]) -> None:
        self._transports = transports

    def write_alerts(self, alerts: tuple[Any, ...]) -> int:
        for transport in self._transports:
            transport.write_alerts(alerts)
        return len(alerts)


def _build_alert_transport(
    webhook_url: str | None,
    *,
    webhook_timeout_seconds: float,
    jsonl_path: Path | None,
) -> Any | None:
    transports: list[Any] = []
    if jsonl_path is not None:
        transports.append(JsonlAlertTransport(jsonl_path))
    if webhook_url is not None:
        transports.append(
            WebhookAlertTransport(
                webhook_url,
                timeout_seconds=webhook_timeout_seconds,
            )
        )
    if not transports:
        return None
    if len(transports) == 1:
        return transports[0]
    return _CompositeAlertTransport(tuple(transports))


def _iteration_to_dict(iteration: Any) -> dict[str, Any]:
    return {
        "checked_at": iteration.checked_at.isoformat(),
        "health_due": iteration.health_due,
        "reconciliation_due": iteration.reconciliation_due,
        "overall_status": (
            None if iteration.overall_status is None else iteration.overall_status.value
        ),
        "snapshot": None if iteration.snapshot is None else iteration.snapshot.to_dict(),
        "metrics_text": iteration.metrics_text,
        "alerts": [alert.to_dict() for alert in iteration.alerts],
        "alert_count": iteration.alert_count,
        "alert_error": iteration.alert_error,
        "error": iteration.error,
    }


if __name__ == "__main__":
    main()
