"""Deployment bootstrap helpers used by scripts and future services."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from scalper_ai.config import load_app_config
from scalper_ai.deployment.health_providers import (
    DataFreshnessProvider,
    GuardStateProvider,
    ModelHealthProvider,
)
from scalper_ai.deployment.live_factory import resolve_live_adapter_factory
from scalper_ai.deployment.runtime import DeploymentRuntime
from scalper_ai.execution import (
    BrokerConnectivityProvider,
    BrokerSnapshotProvider,
    ExecutionAdapter,
)
from scalper_ai.features import OnlineFeatureCalculator
from scalper_ai.utils import configure_logging


def bootstrap_runtime(
    *,
    config_name: str = "research",
    config_dir: Path | None = None,
    paper_adapter_factory: Callable[[], ExecutionAdapter] | None = None,
    live_adapter_factory: Callable[[], ExecutionAdapter] | None = None,
    broker_snapshot_provider: BrokerSnapshotProvider | None = None,
    broker_connectivity_provider: BrokerConnectivityProvider | None = None,
    data_freshness_provider: DataFreshnessProvider | None = None,
    model_health_provider: ModelHealthProvider | None = None,
    guard_state_provider: GuardStateProvider | None = None,
    online_feature_calculator: OnlineFeatureCalculator | None = None,
    live_confirmation_token: str | None = None,
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
        data_freshness_provider=data_freshness_provider,
        model_health_provider=model_health_provider,
        guard_state_provider=guard_state_provider,
        online_feature_calculator=online_feature_calculator,
        live_confirmation_token=live_confirmation_token,
    )
    runtime.start()
    return runtime
