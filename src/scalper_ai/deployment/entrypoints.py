"""Deployment bootstrap helpers used by scripts and future services."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from scalper_ai.config import load_app_config
from scalper_ai.deployment.runtime import DeploymentRuntime
from scalper_ai.deployment.live_factory import resolve_live_adapter_factory
from scalper_ai.execution import (
    BrokerConnectivityProvider,
    BrokerSnapshotProvider,
    ExecutionAdapter,
)
from scalper_ai.utils import configure_logging


def bootstrap_runtime(
    *,
    config_name: str = "research",
    config_dir: Optional[Path] = None,
    paper_adapter_factory: Optional[Callable[[], ExecutionAdapter]] = None,
    live_adapter_factory: Optional[Callable[[], ExecutionAdapter]] = None,
    broker_snapshot_provider: Optional[BrokerSnapshotProvider] = None,
    broker_connectivity_provider: Optional[BrokerConnectivityProvider] = None,
    live_confirmation_token: Optional[str] = None,
) -> DeploymentRuntime:
    """Load config, configure logging, build the runtime, and start it."""

    config = load_app_config(config_name=config_name, config_dir=config_dir)
    configure_logging(config.logging)
    resolved_live_adapter_factory = live_adapter_factory or resolve_live_adapter_factory(config)
    runtime = DeploymentRuntime(
        config,
        paper_adapter_factory=paper_adapter_factory,
        live_adapter_factory=resolved_live_adapter_factory,
        broker_snapshot_provider=broker_snapshot_provider,
        broker_connectivity_provider=broker_connectivity_provider,
        live_confirmation_token=live_confirmation_token,
    )
    runtime.start()
    return runtime
