"""Execution-aware backtesting simulator with latency, partial fills, and reject scenarios."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum

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
from scalper_ai.backtesting.engine import (
    BacktestEvent,
    BacktestState,
    TargetPositionStrategy,
    _build_backtest_event,
    _build_market_order,
    _coerce_target_position,
    _prepare_backtest_frame,
    _resolve_execution_costs,
    _resolve_strategy_id,
)
from scalper_ai.domain import FillEvent, OrderIntent, PositionState

_ZERO_TOLERANCE = 1e-12


class SimulatedOrderStatus(str, Enum):  # noqa: UP042 - keep local Python 3.9 compatibility.
    """Execution-aware simulated order lifecycle states."""

    QUEUED = "queued"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ExecutionSimulatorConfig:
    """Configuration for the execution-aware historical simulator."""

    base: BacktestConfig = field(default_factory=BacktestConfig)
    latency_steps: int = 0
    default_partial_fill_ratio: float = 1.0
    stale_after_seconds: float | None = None
    market_status_column: str = "market_status"
    partial_fill_ratio_column: str = "partial_fill_ratio"
    latency_steps_column: str = "latency_steps"
    available_liquidity_column: str = "available_liquidity"
    queue_ahead_quantity_column: str = "queue_ahead_quantity"
    force_reject_column: str = "force_reject"
    force_cancel_column: str = "force_cancel"
    cancel_replace_race_column: str = "cancel_replace_race"
    cancel_replace_race_fill_ratio: float = 0.0
    cancel_replace_on_new_target: bool = True

    def __post_init__(self) -> None:
        if self.latency_steps < 0:
            raise ValueError("latency_steps must be non-negative.")
        if not 0 <= self.default_partial_fill_ratio <= 1:
            raise ValueError("default_partial_fill_ratio must be between zero and one.")
        if self.stale_after_seconds is not None and self.stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be greater than zero when provided.")
        if not 0 <= self.cancel_replace_race_fill_ratio <= 1:
            raise ValueError("cancel_replace_race_fill_ratio must be between zero and one.")


@dataclass(frozen=True)
class SimulatedExecutionOrder:
    """Final state of one execution-aware simulated order."""

    intent: OrderIntent
    status: SimulatedOrderStatus
    submitted_at: pd.Timestamp
    updated_at: pd.Timestamp
    requested_quantity: float
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    latency_steps: int = 0
    queue_ahead_quantity: float = 0.0
    had_partial_fill: bool = False
    rejection_reason: str | None = None
    cancel_reason: str | None = None

    @property
    def is_open(self) -> bool:
        """Return whether future market events can still fill this order."""

        return self.status in {SimulatedOrderStatus.QUEUED, SimulatedOrderStatus.PARTIALLY_FILLED}


@dataclass(frozen=True)
class ExecutionQualityMetrics:
    """Execution-quality and PnL metrics from the execution-aware simulator."""

    total_pnl: float
    final_equity: float
    max_drawdown: float
    submitted_count: int
    filled_count: int
    partial_fill_count: int
    canceled_count: int
    rejected_count: int
    requested_quantity: float
    filled_quantity: float
    fill_ratio: float
    cancel_ratio: float
    reject_ratio: float
    turnover_quote: float
    spread_cost: float
    slippage_cost: float
    commission: float
    average_slippage_bps: float
    average_latency_steps: float


@dataclass(frozen=True)
class ExecutionBacktestResult:
    """Materialized outputs from an execution-aware replay run."""

    orders: tuple[OrderIntent, ...]
    fills: tuple[FillEvent, ...]
    execution_orders: tuple[SimulatedExecutionOrder, ...]
    position_history: tuple[PositionState, ...]
    equity_curve: pd.DataFrame
    metrics: ExecutionQualityMetrics


@dataclass(frozen=True)
class _OpenOrder:
    intent: OrderIntent
    status: SimulatedOrderStatus
    submitted_index: int
    submitted_at: pd.Timestamp
    updated_at: pd.Timestamp
    requested_quantity: float
    filled_quantity: float
    remaining_quantity: float
    latency_steps: int
    queue_ahead_quantity: float = 0.0
    had_partial_fill: bool = False


def run_execution_aware_backtest(
    frame: pd.DataFrame,
    strategy: TargetPositionStrategy,
    config: ExecutionSimulatorConfig | None = None,
) -> ExecutionBacktestResult:
    """Replay a target-position strategy through an execution-aware simulator."""

    resolved_config = config or ExecutionSimulatorConfig()
    market_frame = _prepare_backtest_frame(frame, config=resolved_config.base)
    symbol = str(market_frame.iloc[0][resolved_config.base.symbol_column])
    strategy_id = _resolve_strategy_id(strategy)

    cash_balance = float(resolved_config.base.initial_cash)
    peak_equity = float(resolved_config.base.initial_cash)
    position: PositionState | None = None
    turnover_quote = 0.0

    submitted_orders: list[OrderIntent] = []
    fills: list[FillEvent] = []
    closed_orders: list[SimulatedExecutionOrder] = []
    open_orders: list[_OpenOrder] = []
    position_history: list[PositionState] = []
    equity_rows: list[dict[str, object]] = []

    for row_index, row in market_frame.iterrows():
        event = _build_backtest_event(row, config=resolved_config.base)

        cash_balance, position, turnover_quote = _process_open_orders(
            event=event,
            row_index=int(row_index),
            open_orders=open_orders,
            closed_orders=closed_orders,
            fills=fills,
            position=position,
            cash_balance=cash_balance,
            turnover_quote=turnover_quote,
            config=resolved_config,
        )

        marked_position = mark_position(
            position,
            symbol=symbol,
            timestamp=event.available_timestamp.to_pydatetime(),
            mark_price=event.mark_price,
        )
        equity = calculate_equity(cash_balance, marked_position)
        peak_equity = max(peak_equity, equity)
        strategy_state = BacktestState(
            current_position=marked_position,
            cash_balance=cash_balance,
            equity=equity,
            peak_equity=peak_equity,
            drawdown=calculate_drawdown(equity, peak_equity),
            trade_count=len(fills),
            turnover_quote=turnover_quote,
        )
        target_position = strategy(event, strategy_state)

        if target_position is not None:
            target_position = _coerce_target_position(target_position)
            current_net_quantity = float(marked_position.net_quantity)
            active_quantity = sum(_signed_remaining_quantity(order) for order in open_orders)
            projected_quantity = current_net_quantity + active_quantity
            delta_quantity = target_position - projected_quantity
            if not math.isclose(delta_quantity, 0.0, abs_tol=_ZERO_TOLERANCE):
                if open_orders and resolved_config.cancel_replace_on_new_target:
                    cash_balance, position, turnover_quote = _cancel_open_orders(
                        event=event,
                        open_orders=open_orders,
                        closed_orders=closed_orders,
                        fills=fills,
                        position=position,
                        cash_balance=cash_balance,
                        turnover_quote=turnover_quote,
                        config=resolved_config,
                        reason="cancel_replace",
                    )
                    marked_position = mark_position(
                        position,
                        symbol=symbol,
                        timestamp=event.available_timestamp.to_pydatetime(),
                        mark_price=event.mark_price,
                    )
                    current_net_quantity = float(marked_position.net_quantity)
                    delta_quantity = target_position - current_net_quantity

                if not math.isclose(delta_quantity, 0.0, abs_tol=_ZERO_TOLERANCE):
                    order = _build_market_order(
                        event=event,
                        strategy_id=strategy_id,
                        symbol=symbol,
                        order_index=len(submitted_orders) + 1,
                        current_position=current_net_quantity,
                        target_position=float(target_position),
                        delta_quantity=delta_quantity,
                    )
                    submitted_orders.append(order)
                    open_orders.extend(
                        _submit_order(
                            order=order,
                            event=event,
                            row=row,
                            row_index=int(row_index),
                            closed_orders=closed_orders,
                            config=resolved_config,
                        )
                    )

        marked_position = mark_position(
            position,
            symbol=symbol,
            timestamp=event.available_timestamp.to_pydatetime(),
            mark_price=event.mark_price,
        )
        post_equity = calculate_equity(cash_balance, marked_position)
        peak_equity = max(peak_equity, post_equity)
        drawdown = calculate_drawdown(post_equity, peak_equity)
        position = marked_position
        position_history.append(marked_position)
        equity_rows.append(
            {
                "symbol": symbol,
                "timestamp": event.available_timestamp,
                "event_timestamp": event.event_timestamp,
                "mark_price": event.mark_price,
                "net_quantity": float(marked_position.net_quantity),
                "cash_balance": cash_balance,
                "equity": post_equity,
                "peak_equity": peak_equity,
                "realized_pnl": float(marked_position.realized_pnl),
                "unrealized_pnl": float(marked_position.unrealized_pnl),
                "drawdown": drawdown,
                "open_order_count": len(open_orders),
                "fill_count": len(fills),
                "turnover_quote": turnover_quote,
            }
        )

    for open_order in tuple(open_orders):
        closed_orders.append(
            _close_order(
                open_order,
                status=SimulatedOrderStatus.CANCELLED,
                updated_at=open_order.updated_at,
                cancel_reason="end_of_replay",
            )
        )
        open_orders.remove(open_order)

    equity_curve = pd.DataFrame.from_records(equity_rows)
    metrics = _build_execution_metrics(
        equity_curve=equity_curve,
        orders=submitted_orders,
        fills=fills,
        execution_orders=closed_orders,
        initial_cash=resolved_config.base.initial_cash,
        turnover_quote=turnover_quote,
    )
    return ExecutionBacktestResult(
        orders=tuple(submitted_orders),
        fills=tuple(fills),
        execution_orders=tuple(closed_orders),
        position_history=tuple(position_history),
        equity_curve=equity_curve,
        metrics=metrics,
    )


def _submit_order(
    *,
    order: OrderIntent,
    event: BacktestEvent,
    row: pd.Series,
    row_index: int,
    closed_orders: list[SimulatedExecutionOrder],
    config: ExecutionSimulatorConfig,
) -> tuple[_OpenOrder, ...]:
    if _row_bool(row, config.force_reject_column):
        closed_orders.append(
            SimulatedExecutionOrder(
                intent=order,
                status=SimulatedOrderStatus.REJECTED,
                submitted_at=event.available_timestamp,
                updated_at=event.available_timestamp,
                requested_quantity=float(order.quantity or 0.0),
                remaining_quantity=float(order.quantity or 0.0),
                rejection_reason="forced_reject",
            )
        )
        return ()

    rejection_reason = _submission_rejection_reason(event=event, row=row, config=config)
    if rejection_reason is not None:
        closed_orders.append(
            SimulatedExecutionOrder(
                intent=order,
                status=SimulatedOrderStatus.REJECTED,
                submitted_at=event.available_timestamp,
                updated_at=event.available_timestamp,
                requested_quantity=float(order.quantity or 0.0),
                remaining_quantity=float(order.quantity or 0.0),
                rejection_reason=rejection_reason,
            )
        )
        return ()

    quantity = float(order.quantity or 0.0)
    return (
        _OpenOrder(
            intent=order,
            status=SimulatedOrderStatus.QUEUED,
            submitted_index=row_index,
            submitted_at=event.available_timestamp,
            updated_at=event.available_timestamp,
            requested_quantity=quantity,
            filled_quantity=0.0,
            remaining_quantity=quantity,
            latency_steps=_row_int(row, config.latency_steps_column, default=config.latency_steps),
            queue_ahead_quantity=_row_float(row, config.queue_ahead_quantity_column, default=0.0),
            had_partial_fill=False,
        ),
    )


def _process_open_orders(
    *,
    event: BacktestEvent,
    row_index: int,
    open_orders: list[_OpenOrder],
    closed_orders: list[SimulatedExecutionOrder],
    fills: list[FillEvent],
    position: PositionState | None,
    cash_balance: float,
    turnover_quote: float,
    config: ExecutionSimulatorConfig,
) -> tuple[float, PositionState | None, float]:
    row = pd.Series(event.row_payload)
    if _row_bool(row, config.force_cancel_column):
        return _cancel_open_orders(
            event=event,
            open_orders=open_orders,
            closed_orders=closed_orders,
            fills=fills,
            position=position,
            cash_balance=cash_balance,
            turnover_quote=turnover_quote,
            config=config,
            reason="forced_cancel",
        )

    if _market_status(row, config=config) != "open" or _event_is_stale(event, config=config):
        return cash_balance, position, turnover_quote

    for open_order in tuple(open_orders):
        if row_index - open_order.submitted_index < open_order.latency_steps:
            continue
        fill_quantity, next_queue = _resolve_fill_quantity(
            open_order,
            row=row,
            config=config,
            forced_ratio=None,
        )
        open_order = replace(open_order, queue_ahead_quantity=next_queue)
        if fill_quantity <= _ZERO_TOLERANCE:
            _replace_open_order(open_orders, open_order)
            continue

        fill = _simulate_partial_fill(
            open_order.intent,
            fill_id=f"exec-fill-{len(fills) + 1:06d}",
            quantity=fill_quantity,
            event=event,
            config=config,
        )
        fills.append(fill)
        cash_balance = apply_fill_to_cash(cash_balance, fill)
        position = apply_fill_to_position(position, fill, mark_price=event.mark_price)
        turnover_quote += fill.fill_price * fill.fill_quantity

        remaining_quantity = max(0.0, open_order.remaining_quantity - fill.fill_quantity)
        filled_quantity = open_order.filled_quantity + fill.fill_quantity
        next_status = (
            SimulatedOrderStatus.FILLED
            if remaining_quantity <= _ZERO_TOLERANCE
            else SimulatedOrderStatus.PARTIALLY_FILLED
        )
        updated_order = replace(
            open_order,
            status=next_status,
            updated_at=event.available_timestamp,
            filled_quantity=filled_quantity,
            remaining_quantity=remaining_quantity,
            had_partial_fill=next_status is SimulatedOrderStatus.PARTIALLY_FILLED
            or open_order.had_partial_fill,
        )
        if next_status is SimulatedOrderStatus.FILLED:
            open_orders.remove(
                next(order for order in open_orders if order.intent == open_order.intent)
            )
            closed_orders.append(_close_order(updated_order, status=SimulatedOrderStatus.FILLED))
        else:
            _replace_open_order(open_orders, updated_order)

    return cash_balance, position, turnover_quote


def _cancel_open_orders(
    *,
    event: BacktestEvent,
    open_orders: list[_OpenOrder],
    closed_orders: list[SimulatedExecutionOrder],
    fills: list[FillEvent],
    position: PositionState | None,
    cash_balance: float,
    turnover_quote: float,
    config: ExecutionSimulatorConfig,
    reason: str,
) -> tuple[float, PositionState | None, float]:
    row = pd.Series(event.row_payload)
    for open_order in tuple(open_orders):
        if _row_bool(row, config.cancel_replace_race_column):
            fill_quantity, next_queue = _resolve_fill_quantity(
                open_order,
                row=row,
                config=config,
                forced_ratio=config.cancel_replace_race_fill_ratio,
            )
            open_order = replace(open_order, queue_ahead_quantity=next_queue)
            if fill_quantity > _ZERO_TOLERANCE:
                fill = _simulate_partial_fill(
                    open_order.intent,
                    fill_id=f"exec-fill-{len(fills) + 1:06d}",
                    quantity=fill_quantity,
                    event=event,
                    config=config,
                )
                fills.append(fill)
                cash_balance = apply_fill_to_cash(cash_balance, fill)
                position = apply_fill_to_position(position, fill, mark_price=event.mark_price)
                turnover_quote += fill.fill_price * fill.fill_quantity
                open_order = replace(
                    open_order,
                    status=SimulatedOrderStatus.PARTIALLY_FILLED,
                    updated_at=event.available_timestamp,
                    filled_quantity=open_order.filled_quantity + fill.fill_quantity,
                    remaining_quantity=max(0.0, open_order.remaining_quantity - fill.fill_quantity),
                    had_partial_fill=True,
                )
        closed_orders.append(
            _close_order(
                open_order,
                status=SimulatedOrderStatus.CANCELLED,
                updated_at=event.available_timestamp,
                cancel_reason=reason,
            )
        )
        open_orders.remove(
            next(order for order in open_orders if order.intent == open_order.intent)
        )
    return cash_balance, position, turnover_quote


def _resolve_fill_quantity(
    order: _OpenOrder,
    *,
    row: pd.Series,
    config: ExecutionSimulatorConfig,
    forced_ratio: float | None,
) -> tuple[float, float]:
    ratio = forced_ratio
    if ratio is None:
        ratio = _row_float(
            row,
            config.partial_fill_ratio_column,
            default=config.default_partial_fill_ratio,
        )
    if ratio < 0 or ratio > 1:
        raise ValueError("partial fill ratio must be between zero and one.")

    liquidity = _row_float(row, config.available_liquidity_column, default=math.inf)
    queue_ahead = max(0.0, order.queue_ahead_quantity)
    if math.isfinite(liquidity) and queue_ahead > 0:
        queue_consumed = min(queue_ahead, liquidity)
        queue_ahead -= queue_consumed
        liquidity -= queue_consumed
    if queue_ahead > _ZERO_TOLERANCE:
        return 0.0, queue_ahead

    candidate_quantity = order.remaining_quantity * ratio
    if math.isfinite(liquidity):
        candidate_quantity = min(candidate_quantity, liquidity)
    return min(order.remaining_quantity, max(0.0, candidate_quantity)), queue_ahead


def _simulate_partial_fill(
    intent: OrderIntent,
    *,
    fill_id: str,
    quantity: float,
    event: BacktestEvent,
    config: ExecutionSimulatorConfig,
) -> FillEvent:
    partial_intent = intent.model_copy(update={"quantity": quantity})
    execution_costs = _resolve_execution_costs(event, config=config.base)
    return simulate_market_fill(
        partial_intent,
        fill_id=fill_id,
        event_timestamp=event.available_timestamp.to_pydatetime(),
        received_timestamp=event.available_timestamp.to_pydatetime(),
        mark_price=event.mark_price,
        spread_bps=execution_costs.spread_bps,
        slippage_bps=execution_costs.slippage_bps,
        commission_bps=execution_costs.commission_bps,
    )


def _build_execution_metrics(
    *,
    equity_curve: pd.DataFrame,
    orders: list[OrderIntent],
    fills: list[FillEvent],
    execution_orders: list[SimulatedExecutionOrder],
    initial_cash: float,
    turnover_quote: float,
) -> ExecutionQualityMetrics:
    final_equity = float(equity_curve.iloc[-1]["equity"])
    requested_quantity = sum(float(order.quantity or 0.0) for order in orders)
    filled_quantity = sum(float(fill.fill_quantity) for fill in fills)
    submitted_count = len(orders)
    filled_count = sum(
        1 for order in execution_orders if order.status is SimulatedOrderStatus.FILLED
    )
    partial_fill_count = sum(1 for order in execution_orders if order.had_partial_fill)
    canceled_count = sum(
        1 for order in execution_orders if order.status is SimulatedOrderStatus.CANCELLED
    )
    rejected_count = sum(
        1 for order in execution_orders if order.status is SimulatedOrderStatus.REJECTED
    )
    spread_cost = sum(float(fill.spread_cost) for fill in fills)
    slippage_cost = sum(float(fill.slippage_cost) for fill in fills)
    commission = sum(float(fill.commission) for fill in fills)
    latency_steps = sum(float(order.latency_steps) for order in execution_orders)
    return ExecutionQualityMetrics(
        total_pnl=final_equity - initial_cash,
        final_equity=final_equity,
        max_drawdown=float(equity_curve["drawdown"].max()),
        submitted_count=submitted_count,
        filled_count=filled_count,
        partial_fill_count=partial_fill_count,
        canceled_count=canceled_count,
        rejected_count=rejected_count,
        requested_quantity=requested_quantity,
        filled_quantity=filled_quantity,
        fill_ratio=_safe_ratio(filled_quantity, requested_quantity),
        cancel_ratio=_safe_ratio(canceled_count, submitted_count),
        reject_ratio=_safe_ratio(rejected_count, submitted_count),
        turnover_quote=turnover_quote,
        spread_cost=spread_cost,
        slippage_cost=slippage_cost,
        commission=commission,
        average_slippage_bps=_safe_ratio(slippage_cost * 10_000.0, turnover_quote),
        average_latency_steps=_safe_ratio(latency_steps, len(execution_orders)),
    )


def _submission_rejection_reason(
    *,
    event: BacktestEvent,
    row: pd.Series,
    config: ExecutionSimulatorConfig,
) -> str | None:
    market_status = _market_status(row, config=config)
    if market_status != "open":
        return f"market_status:{market_status}"
    if _event_is_stale(event, config=config):
        return "stale_market_data"
    return None


def _event_is_stale(event: BacktestEvent, *, config: ExecutionSimulatorConfig) -> bool:
    if config.stale_after_seconds is None:
        return False
    age_seconds = (event.available_timestamp - event.event_timestamp).total_seconds()
    return bool(age_seconds > config.stale_after_seconds)


def _market_status(row: pd.Series, *, config: ExecutionSimulatorConfig) -> str:
    raw_value = row.get(config.market_status_column, "open")
    if raw_value is None or pd.isna(raw_value):
        return "open"
    normalized = str(raw_value).strip().lower()
    return normalized or "open"


def _close_order(
    order: _OpenOrder,
    *,
    status: SimulatedOrderStatus,
    updated_at: pd.Timestamp | None = None,
    rejection_reason: str | None = None,
    cancel_reason: str | None = None,
) -> SimulatedExecutionOrder:
    return SimulatedExecutionOrder(
        intent=order.intent,
        status=status,
        submitted_at=order.submitted_at,
        updated_at=updated_at or order.updated_at,
        requested_quantity=order.requested_quantity,
        filled_quantity=order.filled_quantity,
        remaining_quantity=order.remaining_quantity,
        latency_steps=order.latency_steps,
        queue_ahead_quantity=order.queue_ahead_quantity,
        had_partial_fill=order.had_partial_fill,
        rejection_reason=rejection_reason,
        cancel_reason=cancel_reason,
    )


def _replace_open_order(open_orders: list[_OpenOrder], updated_order: _OpenOrder) -> None:
    for index, current_order in enumerate(open_orders):
        if current_order.intent == updated_order.intent:
            open_orders[index] = updated_order
            return
    raise KeyError(f"Unknown open order: {updated_order.intent.intent_id}")


def _signed_remaining_quantity(order: _OpenOrder) -> float:
    direction = 1.0 if order.intent.side.value == "buy" else -1.0
    return direction * order.remaining_quantity


def _row_bool(row: pd.Series, column_name: str) -> bool:
    if column_name not in row or pd.isna(row[column_name]):
        return False
    value = row[column_name]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _row_float(row: pd.Series, column_name: str, *, default: float) -> float:
    if column_name not in row or pd.isna(row[column_name]):
        return default
    value = float(row[column_name])
    if value < 0:
        raise ValueError(f"{column_name} must be non-negative.")
    return value


def _row_int(row: pd.Series, column_name: str, *, default: int) -> int:
    if column_name not in row or pd.isna(row[column_name]):
        return default
    value = int(row[column_name])
    if value < 0:
        raise ValueError(f"{column_name} must be non-negative.")
    return value


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= _ZERO_TOLERANCE:
        return 0.0
    return numerator / denominator
