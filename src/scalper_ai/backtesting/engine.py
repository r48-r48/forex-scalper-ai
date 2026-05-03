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


@dataclass(frozen=True)
class BacktestResult:
    """Materialized outputs from a complete replay run."""

    orders: tuple[OrderIntent, ...]
    fills: tuple[FillEvent, ...]
    position_history: tuple[PositionState, ...]
    equity_curve: pd.DataFrame
    metrics: BacktestMetrics


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
    position: PositionState | None = None

    orders: list[OrderIntent] = []
    fills: list[FillEvent] = []
    position_history: list[PositionState] = []
    equity_rows: list[dict[str, object]] = []

    for _, row in market_frame.iterrows():
        event = _build_backtest_event(row, config=resolved_config)

        pretrade_position = mark_position(
            position,
            symbol=symbol,
            timestamp=event.available_timestamp.to_pydatetime(),
            mark_price=event.mark_price,
        )
        pretrade_equity = calculate_equity(cash_balance, pretrade_position)
        pretrade_peak_equity = max(peak_equity, pretrade_equity)
        pretrade_drawdown = calculate_drawdown(pretrade_equity, pretrade_peak_equity)

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

        posttrade_position = pretrade_position
        if target_position is not None:
            delta_quantity = _coerce_target_position(target_position) - float(
                pretrade_position.net_quantity
            )
            if not math.isclose(delta_quantity, 0.0, abs_tol=_ZERO_TOLERANCE):
                order = _build_market_order(
                    event=event,
                    strategy_id=strategy_id,
                    symbol=symbol,
                    order_index=len(orders) + 1,
                    current_position=pretrade_position.net_quantity,
                    target_position=float(target_position),
                    delta_quantity=delta_quantity,
                )
                fill = simulate_market_fill(
                    order,
                    fill_id=f"bt-fill-{len(fills) + 1:06d}",
                    event_timestamp=event.available_timestamp.to_pydatetime(),
                    received_timestamp=event.available_timestamp.to_pydatetime(),
                    mark_price=event.mark_price,
                    spread_bps=resolved_config.spread_bps,
                    slippage_bps=resolved_config.slippage_bps,
                    commission_bps=resolved_config.commission_bps,
                )
                cash_balance = apply_fill_to_cash(cash_balance, fill)
                posttrade_position = apply_fill_to_position(
                    pretrade_position,
                    fill,
                    mark_price=event.mark_price,
                )
                trade_count += 1
                turnover_quote += fill.fill_price * fill.fill_quantity
                orders.append(order)
                fills.append(fill)

        posttrade_equity = calculate_equity(cash_balance, posttrade_position)
        peak_equity = max(pretrade_peak_equity, posttrade_equity)
        drawdown = calculate_drawdown(posttrade_equity, peak_equity)

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
                "trade_count": trade_count,
                "turnover_quote": turnover_quote,
            }
        )

    equity_curve = pd.DataFrame.from_records(equity_rows)
    final_equity = float(equity_curve.iloc[-1]["equity"])
    metrics = BacktestMetrics(
        total_pnl=final_equity - resolved_config.initial_cash,
        final_equity=final_equity,
        max_drawdown=float(equity_curve["drawdown"].max()),
        trade_count=trade_count,
        turnover_quote=turnover_quote,
    )
    return BacktestResult(
        orders=tuple(orders),
        fills=tuple(fills),
        position_history=tuple(position_history),
        equity_curve=equity_curve,
        metrics=metrics,
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

    prepared[config.price_column] = pd.to_numeric(prepared[config.price_column], errors="coerce")
    if prepared[config.price_column].isna().any():
        raise ValueError(f"{config.price_column} contains non-numeric values.")
    if not np.isfinite(prepared[config.price_column].to_numpy(dtype=float, copy=False)).all():
        raise ValueError(f"{config.price_column} contains non-finite values.")
    if (prepared[config.price_column] <= 0).any():
        raise ValueError(f"{config.price_column} must contain strictly positive prices.")

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
) -> OrderIntent:
    return OrderIntent(
        intent_id=f"bt-order-{order_index:06d}",
        strategy_id=strategy_id,
        symbol=symbol,
        created_at=event.available_timestamp.to_pydatetime(),
        side=OrderSide.BUY if delta_quantity > 0 else OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=abs(delta_quantity),
        paper=True,
        metadata={
            "current_position": current_position,
            "target_position": target_position,
            "mark_price": event.mark_price,
        },
    )


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
