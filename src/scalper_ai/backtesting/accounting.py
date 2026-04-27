"""Pure helpers for market-fill simulation and netting position accounting."""

from __future__ import annotations

import math
from datetime import datetime

from scalper_ai.domain import (
    FillEvent,
    LiquidityFlag,
    OrderIntent,
    OrderSide,
    OrderType,
    PositionMode,
    PositionState,
)

_ZERO_TOLERANCE = 1e-12


def simulate_market_fill(
    intent: OrderIntent,
    *,
    fill_id: str,
    event_timestamp: datetime,
    received_timestamp: datetime,
    mark_price: float,
    spread_bps: float = 0.0,
    slippage_bps: float = 0.0,
    commission_bps: float = 0.0,
) -> FillEvent:
    """Simulate a taker market fill from a backtest order intent."""

    if intent.order_type is not OrderType.MARKET:
        raise ValueError("simulate_market_fill only supports market orders.")
    if intent.quantity is None:
        raise ValueError("Market fill simulation requires an order intent with quantity.")
    if mark_price <= 0:
        raise ValueError("mark_price must be greater than zero.")

    direction = 1.0 if intent.side is OrderSide.BUY else -1.0
    total_impact_bps = (spread_bps + slippage_bps) / 10_000.0
    fill_price = mark_price * (1.0 + (direction * total_impact_bps))
    spread_cost = mark_price * intent.quantity * (spread_bps / 10_000.0)
    slippage_cost = mark_price * intent.quantity * (slippage_bps / 10_000.0)
    commission = fill_price * intent.quantity * (commission_bps / 10_000.0)

    return FillEvent(
        fill_id=fill_id,
        intent_id=intent.intent_id,
        symbol=intent.symbol,
        event_timestamp=event_timestamp,
        received_timestamp=received_timestamp,
        side=intent.side,
        fill_price=fill_price,
        fill_quantity=intent.quantity,
        commission=commission,
        spread_cost=spread_cost,
        slippage_cost=slippage_cost,
        liquidity_flag=LiquidityFlag.TAKER,
    )


def apply_fill_to_cash(cash_balance: float, fill: FillEvent) -> float:
    """Return the next cash balance after booking a fill and its commission."""

    signed_quantity = _signed_fill_quantity(fill)
    return cash_balance - (signed_quantity * fill.fill_price) - fill.commission


def mark_position(
    position: PositionState | None,
    *,
    symbol: str,
    timestamp: datetime,
    mark_price: float,
) -> PositionState:
    """Mark the current net position to the latest price without changing inventory."""

    if mark_price <= 0:
        raise ValueError("mark_price must be greater than zero.")

    if position is None:
        net_quantity = 0.0
        average_entry_price = 0.0
        realized_pnl = 0.0
        last_fill_id = None
    else:
        net_quantity = float(position.net_quantity)
        average_entry_price = float(position.average_entry_price)
        realized_pnl = float(position.realized_pnl)
        last_fill_id = position.last_fill_id

    unrealized_pnl = 0.0 if _is_flat(net_quantity) else (mark_price - average_entry_price) * net_quantity
    exposure_quote = net_quantity * mark_price
    return PositionState(
        symbol=symbol,
        timestamp=timestamp,
        net_quantity=0.0 if _is_flat(net_quantity) else net_quantity,
        average_entry_price=0.0 if _is_flat(net_quantity) else average_entry_price,
        mark_price=mark_price,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        exposure_quote=exposure_quote,
        last_fill_id=last_fill_id,
        position_mode=PositionMode.NETTING,
    )


def apply_fill_to_position(position: PositionState | None, fill: FillEvent, *, mark_price: float) -> PositionState:
    """Apply a fill to a netting position and immediately mark the result to market."""

    if mark_price <= 0:
        raise ValueError("mark_price must be greater than zero.")

    current = position or mark_position(
        None,
        symbol=fill.symbol,
        timestamp=fill.received_timestamp,
        mark_price=mark_price,
    )
    current_quantity = float(current.net_quantity)
    current_average_entry = float(current.average_entry_price)
    realized_pnl = float(current.realized_pnl) - float(fill.commission)

    signed_quantity = _signed_fill_quantity(fill)
    next_quantity = current_quantity + signed_quantity

    if _is_flat(current_quantity) or _same_direction(current_quantity, signed_quantity):
        next_average_entry = (
            fill.fill_price
            if _is_flat(current_quantity)
            else (
                ((abs(current_quantity) * current_average_entry) + (abs(signed_quantity) * fill.fill_price))
                / abs(next_quantity)
            )
        )
    else:
        closed_quantity = min(abs(current_quantity), abs(signed_quantity))
        realized_pnl += (fill.fill_price - current_average_entry) * closed_quantity * _position_sign(
            current_quantity
        )

        if _is_flat(next_quantity):
            next_quantity = 0.0
            next_average_entry = 0.0
        elif _same_direction(next_quantity, current_quantity):
            next_average_entry = current_average_entry
        else:
            next_average_entry = fill.fill_price

    if _is_flat(next_quantity):
        next_quantity = 0.0
        next_average_entry = 0.0

    unrealized_pnl = 0.0 if _is_flat(next_quantity) else (mark_price - next_average_entry) * next_quantity
    exposure_quote = next_quantity * mark_price

    return PositionState(
        symbol=fill.symbol,
        timestamp=fill.received_timestamp,
        net_quantity=next_quantity,
        average_entry_price=next_average_entry,
        mark_price=mark_price,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        exposure_quote=exposure_quote,
        last_fill_id=fill.fill_id,
        position_mode=PositionMode.NETTING,
    )


def calculate_equity(cash_balance: float, position: PositionState) -> float:
    """Return marked equity from cash and net exposure."""

    return cash_balance + position.exposure_quote


def calculate_drawdown(equity: float, peak_equity: float) -> float:
    """Return current drawdown as a fraction of the running equity peak."""

    if peak_equity <= 0:
        raise ValueError("peak_equity must be greater than zero.")
    return max(0.0, (peak_equity - equity) / peak_equity)


def _signed_fill_quantity(fill: FillEvent) -> float:
    return fill.fill_quantity if fill.side is OrderSide.BUY else -fill.fill_quantity


def _same_direction(left: float, right: float) -> bool:
    return _position_sign(left) == _position_sign(right) and not _is_flat(left) and not _is_flat(right)


def _position_sign(quantity: float) -> int:
    if _is_flat(quantity):
        return 0
    return 1 if quantity > 0 else -1


def _is_flat(quantity: float) -> bool:
    return math.isclose(quantity, 0.0, abs_tol=_ZERO_TOLERANCE)
