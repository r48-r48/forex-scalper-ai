"""Event-driven historical backtesting engine for target-position strategies."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
import pandas as pd

from scalper_ai.backtesting.accounting import (
    apply_fill_to_cash,
    apply_fill_to_position,
    calculate_drawdown,
    calculate_equity,
    mark_position,
    simulate_market_fill,
)
from scalper_ai.backtesting.config import BacktestConfig
from scalper_ai.domain import FillEvent, OrderIntent, OrderSide, OrderType, PositionState

_ZERO_TOLERANCE = 1e-12


@dataclass(frozen=True)
class BacktestEvent:
    """One replayable market event visible to the strategy."""

    symbol: str
    event_timestamp: pd.Timestamp
    available_timestamp: pd.Timestamp
    mark_price: float
    row_payload: dict[str, Any]


@dataclass(frozen=True)
class BacktestState:
    """Current marked portfolio state exposed to the strategy."""

    current_position: PositionState | None
    cash_balance: float
    equity: float
    peak_equity: float
    drawdown: float
    trade_count: int
    turnover_quote: float


class TargetPositionStrategy(Protocol):
    """Strategy interface that returns a target net position in base units."""

    def __call__(self, event: BacktestEvent, state: BacktestState) -> float | None:
        """Return a target net position or None to leave the position unchanged."""


@dataclass(frozen=True)
class BacktestMetrics:
    """Aggregate metrics emitted by the backtesting engine."""

    total_pnl: float
    final_equity: float
    max_drawdown: float
    trade_count: int
    turnover_quote: float
    pip_value_per_unit: float = 0.0
    pip_value_per_lot: float = 0.0
    max_margin_required: float = 0.0
    max_margin_utilization: float = 0.0
    min_margin_level: float = 0.0
    max_effective_leverage: float = 0.0
    margin_call_count: int = 0
    liquidation_count: int = 0
    protective_exit_count: int = 0
    stop_loss_count: int = 0
    take_profit_count: int = 0
    swap_cost: float = 0.0


@dataclass(frozen=True)
class BacktestResult:
    """Materialized outputs from a complete replay run."""

    orders: tuple[OrderIntent, ...]
    fills: tuple[FillEvent, ...]
    position_history: tuple[PositionState, ...]
    equity_curve: pd.DataFrame
    metrics: BacktestMetrics


@dataclass(frozen=True)
class _ExecutionCostBps:
    spread_bps: float
    slippage_bps: float
    commission_bps: float


@dataclass(frozen=True)
class _MarginObservation:
    margin_required: float
    margin_utilization: float
    margin_level: float
    effective_leverage: float


@dataclass(frozen=True)
class _MarketDeltaResult:
    order: OrderIntent
    fill: FillEvent
    position: PositionState
    cash_balance: float
    turnover_quote: float


@dataclass(frozen=True)
class _ProtectiveState:
    stop_loss_price: float | None = None
    take_profit_price: float | None = None


@dataclass(frozen=True)
class _ProtectiveExit:
    exit_type: str
    trigger_price: float


def run_backtest(
    frame: pd.DataFrame,
    strategy: TargetPositionStrategy,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Replay a historical frame through a target-position strategy."""

    resolved_config = config or BacktestConfig()
    market_frame = _prepare_backtest_frame(frame, config=resolved_config)
    symbol = str(market_frame.iloc[0][resolved_config.symbol_column])
    strategy_id = _resolve_strategy_id(strategy)

    cash_balance = float(resolved_config.initial_cash)
    peak_equity = float(resolved_config.initial_cash)
    trade_count = 0
    turnover_quote = 0.0
    swap_cost = 0.0
    margin_call_count = 0
    liquidation_count = 0
    protective_exit_count = 0
    stop_loss_count = 0
    take_profit_count = 0
    max_margin_required = 0.0
    max_margin_utilization = 0.0
    max_effective_leverage = 0.0
    min_margin_level: float | None = None
    position: PositionState | None = None
    active_protection = _ProtectiveState()
    previous_timestamp: pd.Timestamp | None = None

    orders: list[OrderIntent] = []
    fills: list[FillEvent] = []
    position_history: list[PositionState] = []
    equity_rows: list[dict[str, object]] = []

    for _, row in market_frame.iterrows():
        event = _build_backtest_event(row, config=resolved_config)
        event_swap_cost = _calculate_swap_cost(
            position,
            previous_timestamp=previous_timestamp,
            current_timestamp=event.available_timestamp,
            config=resolved_config,
        )
        if not math.isclose(event_swap_cost, 0.0, abs_tol=_ZERO_TOLERANCE):
            cash_balance -= event_swap_cost
            swap_cost += event_swap_cost

        pretrade_position = mark_position(
            position,
            symbol=symbol,
            timestamp=event.available_timestamp.to_pydatetime(),
            mark_price=event.mark_price,
        )
        pretrade_equity = calculate_equity(cash_balance, pretrade_position)
        pretrade_peak_equity = max(peak_equity, pretrade_equity)
        pretrade_drawdown = calculate_drawdown(pretrade_equity, pretrade_peak_equity)
        pretrade_margin = _observe_margin_state(
            pretrade_position,
            equity=pretrade_equity,
            config=resolved_config,
        )
        max_margin_required = max(max_margin_required, pretrade_margin.margin_required)
        max_margin_utilization = max(
            max_margin_utilization,
            pretrade_margin.margin_utilization,
        )
        max_effective_leverage = max(
            max_effective_leverage,
            pretrade_margin.effective_leverage,
        )
        min_margin_level = _update_min_margin_level(min_margin_level, pretrade_margin)

        posttrade_position = pretrade_position
        liquidated_on_margin_call = False
        protective_exit_type: str | None = None
        protective_exit = _resolve_protective_exit(
            event,
            position=pretrade_position,
            protection=active_protection,
            config=resolved_config,
        )
        if protective_exit is not None:
            executed_exit = _execute_market_delta(
                event=event,
                symbol=symbol,
                strategy_id="protective_exit",
                order_index=len(orders) + 1,
                fill_index=len(fills) + 1,
                current_position=pretrade_position,
                target_position=0.0,
                delta_quantity=-float(pretrade_position.net_quantity),
                cash_balance=cash_balance,
                config=resolved_config,
                execution_reference_price=protective_exit.trigger_price,
                reason=protective_exit.exit_type,
                metadata_extra={
                    "protective_exit_type": protective_exit.exit_type,
                    "trigger_price": protective_exit.trigger_price,
                },
            )
            cash_balance = executed_exit.cash_balance
            posttrade_position = executed_exit.position
            trade_count += 1
            turnover_quote += executed_exit.turnover_quote
            protective_exit_count += 1
            if protective_exit.exit_type == "stop_loss":
                stop_loss_count += 1
            else:
                take_profit_count += 1
            protective_exit_type = protective_exit.exit_type
            active_protection = _ProtectiveState()
            orders.append(executed_exit.order)
            fills.append(executed_exit.fill)
        elif _should_liquidate_for_margin(
            pretrade_position,
            margin=pretrade_margin,
            config=resolved_config,
        ):
            liquidation = _execute_market_delta(
                event=event,
                symbol=symbol,
                strategy_id="broker_margin_call",
                order_index=len(orders) + 1,
                fill_index=len(fills) + 1,
                current_position=pretrade_position,
                target_position=0.0,
                delta_quantity=-float(pretrade_position.net_quantity),
                cash_balance=cash_balance,
                config=resolved_config,
                reason="margin_call",
            )
            cash_balance = liquidation.cash_balance
            posttrade_position = liquidation.position
            trade_count += 1
            turnover_quote += liquidation.turnover_quote
            margin_call_count += 1
            liquidation_count += 1
            liquidated_on_margin_call = True
            active_protection = _ProtectiveState()
            orders.append(liquidation.order)
            fills.append(liquidation.fill)

        if protective_exit_type is None and not liquidated_on_margin_call:
            strategy_state = BacktestState(
                current_position=pretrade_position,
                cash_balance=cash_balance,
                equity=pretrade_equity,
                peak_equity=pretrade_peak_equity,
                drawdown=pretrade_drawdown,
                trade_count=trade_count,
                turnover_quote=turnover_quote,
            )
            target_position = strategy(event, strategy_state)
            if target_position is not None:
                target_position_value = _coerce_target_position(target_position)
                delta_quantity = target_position_value - float(pretrade_position.net_quantity)
                if not math.isclose(delta_quantity, 0.0, abs_tol=_ZERO_TOLERANCE):
                    executed = _execute_market_delta(
                        event=event,
                        symbol=symbol,
                        strategy_id=strategy_id,
                        order_index=len(orders) + 1,
                        fill_index=len(fills) + 1,
                        current_position=pretrade_position,
                        target_position=target_position_value,
                        delta_quantity=delta_quantity,
                        cash_balance=cash_balance,
                        config=resolved_config,
                    )
                    cash_balance = executed.cash_balance
                    posttrade_position = executed.position
                    trade_count += 1
                    turnover_quote += executed.turnover_quote
                    orders.append(executed.order)
                    fills.append(executed.fill)

            post_strategy_equity = calculate_equity(cash_balance, posttrade_position)
            post_strategy_margin = _observe_margin_state(
                posttrade_position,
                equity=post_strategy_equity,
                config=resolved_config,
            )
            max_margin_required = max(
                max_margin_required,
                post_strategy_margin.margin_required,
            )
            max_margin_utilization = max(
                max_margin_utilization,
                post_strategy_margin.margin_utilization,
            )
            max_effective_leverage = max(
                max_effective_leverage,
                post_strategy_margin.effective_leverage,
            )
            min_margin_level = _update_min_margin_level(
                min_margin_level,
                post_strategy_margin,
            )
            if _should_liquidate_for_margin(
                posttrade_position,
                margin=post_strategy_margin,
                config=resolved_config,
            ):
                liquidation = _execute_market_delta(
                    event=event,
                    symbol=symbol,
                    strategy_id="broker_margin_call",
                    order_index=len(orders) + 1,
                    fill_index=len(fills) + 1,
                    current_position=posttrade_position,
                    target_position=0.0,
                    delta_quantity=-float(posttrade_position.net_quantity),
                    cash_balance=cash_balance,
                    config=resolved_config,
                    reason="margin_call",
                )
                cash_balance = liquidation.cash_balance
                posttrade_position = liquidation.position
                trade_count += 1
                turnover_quote += liquidation.turnover_quote
                margin_call_count += 1
                liquidation_count += 1
                liquidated_on_margin_call = True
                active_protection = _ProtectiveState()
                orders.append(liquidation.order)
                fills.append(liquidation.fill)

        if not liquidated_on_margin_call and protective_exit_type is None:
            active_protection = _next_protective_state(
                previous_position=pretrade_position,
                current_position=posttrade_position,
                current_protection=active_protection,
                event=event,
                config=resolved_config,
            )
        elif _is_flat_position(posttrade_position):
            active_protection = _ProtectiveState()

        posttrade_equity = calculate_equity(cash_balance, posttrade_position)
        peak_equity = max(pretrade_peak_equity, posttrade_equity)
        drawdown = calculate_drawdown(posttrade_equity, peak_equity)
        posttrade_margin = _observe_margin_state(
            posttrade_position,
            equity=posttrade_equity,
            config=resolved_config,
        )
        max_margin_required = max(max_margin_required, posttrade_margin.margin_required)
        max_margin_utilization = max(
            max_margin_utilization,
            posttrade_margin.margin_utilization,
        )
        max_effective_leverage = max(
            max_effective_leverage,
            posttrade_margin.effective_leverage,
        )
        min_margin_level = _update_min_margin_level(min_margin_level, posttrade_margin)

        position = posttrade_position
        position_history.append(posttrade_position)
        equity_rows.append(
            {
                "symbol": symbol,
                "timestamp": event.available_timestamp,
                "event_timestamp": event.event_timestamp,
                "mark_price": event.mark_price,
                "net_quantity": float(posttrade_position.net_quantity),
                "cash_balance": cash_balance,
                "equity": posttrade_equity,
                "peak_equity": peak_equity,
                "realized_pnl": float(posttrade_position.realized_pnl),
                "unrealized_pnl": float(posttrade_position.unrealized_pnl),
                "drawdown": drawdown,
                "margin_required": posttrade_margin.margin_required,
                "margin_utilization": posttrade_margin.margin_utilization,
                "margin_level": posttrade_margin.margin_level,
                "effective_leverage": posttrade_margin.effective_leverage,
                "margin_call_count": margin_call_count,
                "liquidation_count": liquidation_count,
                "liquidated_on_margin_call": liquidated_on_margin_call,
                "protective_exit_count": protective_exit_count,
                "stop_loss_count": stop_loss_count,
                "take_profit_count": take_profit_count,
                "protective_exit_type": protective_exit_type,
                "active_stop_loss_price": active_protection.stop_loss_price,
                "active_take_profit_price": active_protection.take_profit_price,
                "swap_cost": swap_cost,
                "trade_count": trade_count,
                "turnover_quote": turnover_quote,
            }
        )
        previous_timestamp = event.available_timestamp

    equity_curve = pd.DataFrame.from_records(equity_rows)
    final_equity = float(equity_curve.iloc[-1]["equity"])
    pip_value_per_unit, pip_value_per_lot = _pip_values(resolved_config)
    metrics = BacktestMetrics(
        total_pnl=final_equity - resolved_config.initial_cash,
        final_equity=final_equity,
        max_drawdown=float(equity_curve["drawdown"].max()),
        trade_count=trade_count,
        turnover_quote=turnover_quote,
        pip_value_per_unit=pip_value_per_unit,
        pip_value_per_lot=pip_value_per_lot,
        max_margin_required=max_margin_required,
        max_margin_utilization=max_margin_utilization,
        min_margin_level=0.0 if min_margin_level is None else min_margin_level,
        max_effective_leverage=max_effective_leverage,
        margin_call_count=margin_call_count,
        liquidation_count=liquidation_count,
        protective_exit_count=protective_exit_count,
        stop_loss_count=stop_loss_count,
        take_profit_count=take_profit_count,
        swap_cost=swap_cost,
    )
    return BacktestResult(
        orders=tuple(orders),
        fills=tuple(fills),
        position_history=tuple(position_history),
        equity_curve=equity_curve,
        metrics=metrics,
    )


def _execute_market_delta(
    *,
    event: BacktestEvent,
    symbol: str,
    strategy_id: str,
    order_index: int,
    fill_index: int,
    current_position: PositionState,
    target_position: float,
    delta_quantity: float,
    cash_balance: float,
    config: BacktestConfig,
    execution_reference_price: float | None = None,
    reason: str | None = None,
    metadata_extra: dict[str, object] | None = None,
) -> _MarketDeltaResult:
    order_side = OrderSide.BUY if delta_quantity > 0 else OrderSide.SELL
    resolved_execution_reference_price = (
        _execution_reference_price(
            event,
            side=order_side,
            config=config,
        )
        if execution_reference_price is None
        else execution_reference_price
    )
    execution_costs = _resolve_execution_costs(event, config=config)
    order = _build_market_order(
        event=event,
        strategy_id=strategy_id,
        symbol=symbol,
        order_index=order_index,
        current_position=float(current_position.net_quantity),
        target_position=target_position,
        delta_quantity=delta_quantity,
        execution_reference_price=resolved_execution_reference_price,
        reason=reason,
        metadata_extra=metadata_extra,
    )
    fill = simulate_market_fill(
        order,
        fill_id=f"bt-fill-{fill_index:06d}",
        event_timestamp=event.available_timestamp.to_pydatetime(),
        received_timestamp=event.available_timestamp.to_pydatetime(),
        mark_price=resolved_execution_reference_price,
        spread_bps=execution_costs.spread_bps,
        slippage_bps=execution_costs.slippage_bps,
        commission_bps=execution_costs.commission_bps,
    )
    next_cash_balance = apply_fill_to_cash(cash_balance, fill)
    next_position = apply_fill_to_position(
        current_position,
        fill,
        mark_price=event.mark_price,
    )
    return _MarketDeltaResult(
        order=order,
        fill=fill,
        position=next_position,
        cash_balance=next_cash_balance,
        turnover_quote=fill.fill_price * fill.fill_quantity,
    )


def _prepare_backtest_frame(frame: pd.DataFrame, *, config: BacktestConfig) -> pd.DataFrame:
    if frame.empty:
        raise ValueError("Backtest frame must contain at least one row.")

    required_columns = {
        config.symbol_column,
        config.event_timestamp_column,
        config.available_timestamp_column,
        config.price_column,
    }
    if config.uses_bid_ask_execution:
        required_columns.update(
            {
                str(config.bid_price_column),
                str(config.ask_price_column),
            }
        )
    if config.uses_bar_path:
        required_columns.update(
            {
                str(config.high_price_column),
                str(config.low_price_column),
            }
        )
    for column_name in (
        config.spread_bps_column,
        config.slippage_bps_column,
        config.commission_bps_column,
        config.stop_loss_price_column,
        config.take_profit_price_column,
    ):
        if column_name is not None:
            required_columns.add(column_name)
    missing_columns = required_columns.difference(frame.columns)
    if missing_columns:
        raise ValueError(
            "Backtest frame is missing required columns: "
            f"{', '.join(sorted(missing_columns))}"
        )

    prepared = frame.copy()
    prepared[config.event_timestamp_column] = _normalize_timestamp_column(
        prepared[config.event_timestamp_column],
        column_name=config.event_timestamp_column,
    )
    prepared[config.available_timestamp_column] = _normalize_timestamp_column(
        prepared[config.available_timestamp_column],
        column_name=config.available_timestamp_column,
    )
    if (
        prepared[config.available_timestamp_column]
        < prepared[config.event_timestamp_column]
    ).any():
        raise ValueError("available_timestamp_column must not precede event_timestamp_column.")

    _validate_positive_price_column(prepared, column_name=config.price_column)
    if config.uses_bid_ask_execution:
        bid_column = str(config.bid_price_column)
        ask_column = str(config.ask_price_column)
        _validate_positive_price_column(prepared, column_name=bid_column)
        _validate_positive_price_column(prepared, column_name=ask_column)
        if (prepared[bid_column] > prepared[ask_column]).any():
            raise ValueError("bid_price_column must not exceed ask_price_column.")
    if config.uses_bar_path:
        high_column = str(config.high_price_column)
        low_column = str(config.low_price_column)
        _validate_positive_price_column(prepared, column_name=high_column)
        _validate_positive_price_column(prepared, column_name=low_column)
        if (prepared[high_column] < prepared[low_column]).any():
            raise ValueError("high_price_column must not be below low_price_column.")
        if (
            (prepared[config.price_column] > prepared[high_column])
            | (prepared[config.price_column] < prepared[low_column])
        ).any():
            raise ValueError("price_column must be between low_price_column and high_price_column.")
    for column_name in (
        config.spread_bps_column,
        config.slippage_bps_column,
        config.commission_bps_column,
    ):
        if column_name is not None:
            _validate_non_negative_numeric_column(prepared, column_name=column_name)
    for column_name in (
        config.stop_loss_price_column,
        config.take_profit_price_column,
    ):
        if column_name is not None:
            _validate_optional_positive_price_column(prepared, column_name=column_name)

    if prepared[config.symbol_column].isna().any():
        raise ValueError(f"{config.symbol_column} must not contain null symbols.")
    prepared[config.symbol_column] = prepared[config.symbol_column].map(
        lambda value: str(value).strip()
    )
    if (prepared[config.symbol_column] == "").any():
        raise ValueError(f"{config.symbol_column} must not contain empty symbols.")
    if prepared[config.symbol_column].nunique(dropna=False) != 1:
        raise ValueError("Backtest frame must contain exactly one symbol.")

    prepared.sort_values(
        by=[config.available_timestamp_column, config.event_timestamp_column],
        inplace=True,
        kind="stable",
    )
    prepared.reset_index(drop=True, inplace=True)
    return prepared


def _normalize_timestamp_column(series: pd.Series, *, column_name: str) -> pd.Series:
    if isinstance(series.dtype, pd.DatetimeTZDtype):
        normalized = pd.to_datetime(series, utc=True, errors="raise")
        return pd.Series(normalized, index=series.index, name=series.name)

    normalized_values: list[pd.Timestamp] = []
    for value in series:
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp):
            raise ValueError(f"{column_name} contains invalid timestamps.")
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError(f"{column_name} must contain UTC-aware timestamps.")
        normalized_values.append(timestamp.tz_convert("UTC"))

    return pd.Series(
        normalized_values,
        index=series.index,
        name=series.name,
        dtype="datetime64[ns, UTC]",
    )


def _build_backtest_event(row: pd.Series, *, config: BacktestConfig) -> BacktestEvent:
    return BacktestEvent(
        symbol=str(row[config.symbol_column]),
        event_timestamp=pd.Timestamp(row[config.event_timestamp_column]),
        available_timestamp=pd.Timestamp(row[config.available_timestamp_column]),
        mark_price=float(row[config.price_column]),
        row_payload=row.to_dict(),
    )


def _build_market_order(
    *,
    event: BacktestEvent,
    strategy_id: str,
    symbol: str,
    order_index: int,
    current_position: float,
    target_position: float,
    delta_quantity: float,
    execution_reference_price: float | None = None,
    reason: str | None = None,
    metadata_extra: dict[str, object] | None = None,
) -> OrderIntent:
    resolved_execution_reference_price = (
        event.mark_price
        if execution_reference_price is None
        else execution_reference_price
    )
    metadata: dict[str, object] = {
        "current_position": current_position,
        "target_position": target_position,
        "mark_price": event.mark_price,
        "execution_reference_price": resolved_execution_reference_price,
    }
    if reason is not None:
        metadata["reason"] = reason
    if metadata_extra is not None:
        metadata.update(metadata_extra)
    return OrderIntent(
        intent_id=f"bt-order-{order_index:06d}",
        strategy_id=strategy_id,
        symbol=symbol,
        created_at=event.available_timestamp.to_pydatetime(),
        side=OrderSide.BUY if delta_quantity > 0 else OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=abs(delta_quantity),
        paper=True,
        metadata=metadata,
    )


def _execution_reference_price(
    event: BacktestEvent,
    *,
    side: OrderSide,
    config: BacktestConfig,
) -> float:
    if not config.uses_bid_ask_execution:
        return event.mark_price

    column_name = (
        str(config.ask_price_column)
        if side is OrderSide.BUY
        else str(config.bid_price_column)
    )
    return float(event.row_payload[column_name])


def _resolve_execution_costs(
    event: BacktestEvent,
    *,
    config: BacktestConfig,
) -> _ExecutionCostBps:
    return _ExecutionCostBps(
        spread_bps=_resolve_event_bps(
            event,
            column_name=config.spread_bps_column,
            default=config.spread_bps,
        ),
        slippage_bps=_resolve_event_bps(
            event,
            column_name=config.slippage_bps_column,
            default=config.slippage_bps,
        ),
        commission_bps=_resolve_event_bps(
            event,
            column_name=config.commission_bps_column,
            default=config.commission_bps,
        ),
    )


def _resolve_event_bps(
    event: BacktestEvent,
    *,
    column_name: str | None,
    default: float,
) -> float:
    if column_name is None:
        return default
    value = float(event.row_payload[column_name])
    if value < 0:
        raise ValueError(f"{column_name} must be non-negative.")
    return value


def _calculate_swap_cost(
    position: PositionState | None,
    *,
    previous_timestamp: pd.Timestamp | None,
    current_timestamp: pd.Timestamp,
    config: BacktestConfig,
) -> float:
    symbol_spec = config.fx_symbol
    if symbol_spec is None or previous_timestamp is None or position is None:
        return 0.0
    net_quantity = float(position.net_quantity)
    if math.isclose(net_quantity, 0.0, abs_tol=_ZERO_TOLERANCE):
        return 0.0
    rollover_count = _rollover_count(
        previous_timestamp,
        current_timestamp,
        rollover_hour_utc=symbol_spec.rollover_hour_utc,
    )
    if rollover_count <= 0:
        return 0.0
    rate = (
        symbol_spec.swap_long_per_lot
        if net_quantity > 0
        else symbol_spec.swap_short_per_lot
    )
    lots = abs(net_quantity) / symbol_spec.contract_size
    return lots * rate * rollover_count


def _rollover_count(
    previous_timestamp: pd.Timestamp,
    current_timestamp: pd.Timestamp,
    *,
    rollover_hour_utc: int,
) -> int:
    previous_utc = pd.Timestamp(previous_timestamp).tz_convert("UTC")
    current_utc = pd.Timestamp(current_timestamp).tz_convert("UTC")
    if current_utc <= previous_utc:
        return 0

    current_day = previous_utc.normalize()
    final_day = current_utc.normalize()
    count = 0
    while current_day <= final_day:
        rollover = current_day + pd.Timedelta(hours=rollover_hour_utc)
        if previous_utc < rollover <= current_utc:
            count += 1
        current_day += pd.Timedelta(days=1)
    return count


def _calculate_margin_required(
    position: PositionState,
    *,
    config: BacktestConfig,
) -> float:
    symbol_spec = config.fx_symbol
    if symbol_spec is None or symbol_spec.margin_rate <= 0:
        return 0.0
    return abs(float(position.exposure_quote)) * symbol_spec.margin_rate * (
        symbol_spec.quote_to_account_rate
    )


def _observe_margin_state(
    position: PositionState,
    *,
    equity: float,
    config: BacktestConfig,
) -> _MarginObservation:
    margin_required = _calculate_margin_required(position, config=config)
    return _MarginObservation(
        margin_required=margin_required,
        margin_utilization=_safe_ratio(margin_required, equity),
        margin_level=_calculate_margin_level(
            equity=equity,
            margin_required=margin_required,
        ),
        effective_leverage=_calculate_effective_leverage(
            position,
            equity=equity,
            config=config,
        ),
    )


def _calculate_margin_level(*, equity: float, margin_required: float) -> float:
    if margin_required <= _ZERO_TOLERANCE:
        return 0.0
    return equity / margin_required


def _calculate_effective_leverage(
    position: PositionState,
    *,
    equity: float,
    config: BacktestConfig,
) -> float:
    exposure_account = abs(float(position.exposure_quote))
    if config.fx_symbol is not None:
        exposure_account *= config.fx_symbol.quote_to_account_rate
    return _safe_ratio(exposure_account, equity)


def _update_min_margin_level(
    current: float | None,
    observation: _MarginObservation,
) -> float | None:
    if observation.margin_required <= _ZERO_TOLERANCE:
        return current
    if current is None:
        return observation.margin_level
    return min(current, observation.margin_level)


def _should_liquidate_for_margin(
    position: PositionState,
    *,
    margin: _MarginObservation,
    config: BacktestConfig,
) -> bool:
    if config.margin_call_level is None:
        return False
    if _is_flat_position(position):
        return False
    if margin.margin_required <= _ZERO_TOLERANCE:
        return False
    return margin.margin_level <= config.margin_call_level


def _resolve_protective_exit(
    event: BacktestEvent,
    *,
    position: PositionState,
    protection: _ProtectiveState,
    config: BacktestConfig,
) -> _ProtectiveExit | None:
    if not config.uses_protective_exit_simulation or not config.uses_bar_path:
        return None
    if _is_flat_position(position):
        return None

    high_price = float(event.row_payload[str(config.high_price_column)])
    low_price = float(event.row_payload[str(config.low_price_column)])
    net_quantity = float(position.net_quantity)
    if net_quantity > 0:
        stop_loss_hit = (
            protection.stop_loss_price is not None
            and low_price <= protection.stop_loss_price
        )
        take_profit_hit = (
            protection.take_profit_price is not None
            and high_price >= protection.take_profit_price
        )
    else:
        stop_loss_hit = (
            protection.stop_loss_price is not None
            and high_price >= protection.stop_loss_price
        )
        take_profit_hit = (
            protection.take_profit_price is not None
            and low_price <= protection.take_profit_price
        )

    if stop_loss_hit and take_profit_hit:
        exit_type = config.protective_exit_priority
    elif stop_loss_hit:
        exit_type = "stop_loss"
    elif take_profit_hit:
        exit_type = "take_profit"
    else:
        return None

    trigger_price = (
        protection.stop_loss_price
        if exit_type == "stop_loss"
        else protection.take_profit_price
    )
    if trigger_price is None:
        return None
    return _ProtectiveExit(exit_type=exit_type, trigger_price=trigger_price)


def _next_protective_state(
    *,
    previous_position: PositionState,
    current_position: PositionState,
    current_protection: _ProtectiveState,
    event: BacktestEvent,
    config: BacktestConfig,
) -> _ProtectiveState:
    if not config.uses_protective_exit_simulation:
        return _ProtectiveState()
    if _is_flat_position(current_position):
        return _ProtectiveState()

    previous_direction = _position_direction(float(previous_position.net_quantity))
    current_direction = _position_direction(float(current_position.net_quantity))
    base_protection = (
        current_protection
        if previous_direction == current_direction and previous_direction != 0
        else _ProtectiveState()
    )
    stop_loss_price = _resolve_optional_price(
        event,
        column_name=config.stop_loss_price_column,
    )
    take_profit_price = _resolve_optional_price(
        event,
        column_name=config.take_profit_price_column,
    )
    return _ProtectiveState(
        stop_loss_price=(
            base_protection.stop_loss_price
            if stop_loss_price is None
            else stop_loss_price
        ),
        take_profit_price=(
            base_protection.take_profit_price
            if take_profit_price is None
            else take_profit_price
        ),
    )


def _resolve_optional_price(
    event: BacktestEvent,
    *,
    column_name: str | None,
) -> float | None:
    if column_name is None:
        return None
    value = event.row_payload[column_name]
    if pd.isna(value):
        return None
    return float(value)


def _is_flat_position(position: PositionState) -> bool:
    return math.isclose(float(position.net_quantity), 0.0, abs_tol=_ZERO_TOLERANCE)


def _position_direction(quantity: float) -> int:
    if math.isclose(quantity, 0.0, abs_tol=_ZERO_TOLERANCE):
        return 0
    return 1 if quantity > 0 else -1


def _pip_values(config: BacktestConfig) -> tuple[float, float]:
    if config.fx_symbol is None:
        return 0.0, 0.0
    return config.fx_symbol.pip_value_per_unit, config.fx_symbol.pip_value_per_lot


def _validate_positive_price_column(frame: pd.DataFrame, *, column_name: str) -> None:
    frame[column_name] = pd.to_numeric(frame[column_name], errors="coerce")
    if frame[column_name].isna().any():
        raise ValueError(f"{column_name} contains non-numeric values.")
    if not np.isfinite(frame[column_name].to_numpy(dtype=float, copy=False)).all():
        raise ValueError(f"{column_name} contains non-finite values.")
    if (frame[column_name] <= 0).any():
        raise ValueError(f"{column_name} must contain strictly positive prices.")


def _validate_non_negative_numeric_column(
    frame: pd.DataFrame,
    *,
    column_name: str,
) -> None:
    frame[column_name] = pd.to_numeric(frame[column_name], errors="coerce")
    if frame[column_name].isna().any():
        raise ValueError(f"{column_name} contains non-numeric values.")
    if not np.isfinite(frame[column_name].to_numpy(dtype=float, copy=False)).all():
        raise ValueError(f"{column_name} contains non-finite values.")
    if (frame[column_name] < 0).any():
        raise ValueError(f"{column_name} must contain non-negative values.")


def _validate_optional_positive_price_column(
    frame: pd.DataFrame,
    *,
    column_name: str,
) -> None:
    original = frame[column_name]
    numeric = pd.to_numeric(original, errors="coerce")
    invalid_non_null = numeric.isna() & original.notna()
    if invalid_non_null.any():
        raise ValueError(f"{column_name} contains non-numeric values.")
    non_null = numeric.notna()
    if not np.isfinite(numeric[non_null].to_numpy(dtype=float, copy=False)).all():
        raise ValueError(f"{column_name} contains non-finite values.")
    if (numeric[non_null] <= 0).any():
        raise ValueError(f"{column_name} must contain strictly positive prices.")
    frame[column_name] = numeric


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= _ZERO_TOLERANCE:
        return 0.0
    return numerator / denominator


def _resolve_strategy_id(strategy: TargetPositionStrategy) -> str:
    strategy_id = getattr(strategy, "strategy_id", None)
    if isinstance(strategy_id, str) and strategy_id.strip():
        return strategy_id.strip()

    strategy_name = getattr(strategy, "__name__", "")
    if isinstance(strategy_name, str) and strategy_name and strategy_name != "<lambda>":
        return strategy_name

    class_name = strategy.__class__.__name__.strip()
    return class_name or "strategy"


def _coerce_target_position(value: float | None) -> float:
    if value is None:
        raise ValueError("target_position cannot be None.")

    target_position = float(value)
    if not math.isfinite(target_position):
        raise ValueError("target_position must be finite.")
    return target_position
