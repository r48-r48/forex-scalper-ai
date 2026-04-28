"""Runtime bootstrap entrypoint with operational summary, health, and metrics surfaces."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scalper_ai.deployment import bootstrap_runtime


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Bootstrap one deployment runtime and print its status surfaces.",
    )
    parser.add_argument(
        "action",
        choices=("describe", "health", "metrics"),
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
        print(runtime.metrics_text())
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
