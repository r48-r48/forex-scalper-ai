"""Helpers for building live execution adapters from typed application config."""

from __future__ import annotations

from collections.abc import Callable

from scalper_ai.config import AppConfig
from scalper_ai.domain import PositionMode
from scalper_ai.execution import (
    ExecutionAdapter,
    LiveExecutionStubAdapter,
    Mt5ExecutionAdapter,
    Mt5ExecutionConfig,
)
from scalper_ai.execution.mt5_client import (
    MetaTrader5ModuleProtocol,
    Mt5TerminalClient,
    Mt5TerminalClientConfig,
    discover_mt5_terminal_path,
)


def build_mt5_terminal_client(
    config: AppConfig,
    *,
    mt5_module: MetaTrader5ModuleProtocol | None = None,
) -> Mt5TerminalClient:
    """Build one real MT5 terminal client from app config."""

    mt5_config = config.broker.mt5
    resolved_terminal_path = discover_mt5_terminal_path(mt5_config.terminal_path)
    return Mt5TerminalClient(
        config=Mt5TerminalClientConfig(
            terminal_path=resolved_terminal_path,
            login=mt5_config.login,
            password=mt5_config.password,
            server=mt5_config.server,
            timeout_milliseconds=mt5_config.timeout_milliseconds,
            magic_number=mt5_config.magic_number,
            deviation_points=mt5_config.deviation_points,
            history_lookback_hours=mt5_config.history_lookback_hours,
            account_mode=mt5_config.account_mode,
            order_comment_prefix=mt5_config.order_comment_prefix,
        ),
        module=mt5_module,
    )


def build_mt5_execution_adapter(
    config: AppConfig,
    *,
    mt5_module: MetaTrader5ModuleProtocol | None = None,
) -> Mt5ExecutionAdapter:
    """Build one MT5 execution adapter from app config."""

    mt5_config = config.broker.mt5
    client = build_mt5_terminal_client(config, mt5_module=mt5_module)
    account_snapshot = client.describe_account()
    initial_cash = (
        100_000.0
        if account_snapshot.balance is None
        else float(account_snapshot.balance)
    )
    return Mt5ExecutionAdapter(
        client,
        config=Mt5ExecutionConfig(
            initial_cash=initial_cash,
            default_venue="MT5",
            account_mode=PositionMode(mt5_config.account_mode),
            base_units_per_lot=mt5_config.base_units_per_lot,
            min_volume_lots=mt5_config.min_volume_lots,
            volume_step_lots=mt5_config.volume_step_lots,
            require_stop_loss=mt5_config.require_stop_loss,
            require_take_profit=mt5_config.require_take_profit,
            symbol_map=mt5_config.symbol_map,
        ),
    )


def resolve_live_adapter_factory(
    config: AppConfig,
    *,
    mt5_module: MetaTrader5ModuleProtocol | None = None,
) -> Callable[[], ExecutionAdapter] | None:
    """Return a configured live adapter factory when the config names one we can build."""

    adapter_name = config.broker.live_adapter.strip().lower()
    if adapter_name == "stub":
        return LiveExecutionStubAdapter
    if adapter_name == "mt5":
        return lambda: build_mt5_execution_adapter(config, mt5_module=mt5_module)
    return None
