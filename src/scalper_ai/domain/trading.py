"""Canonical trading intent, fill, and position state models."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import FiniteFloat, model_validator

from scalper_ai.domain.base import DomainModel
from scalper_ai.domain.enums import LiquidityFlag, OrderSide, OrderType, PositionMode, TimeInForce
from scalper_ai.domain.validators import (
    NonEmptyStr,
    NonNegativeFiniteFloat,
    PositiveFiniteFloat,
    UtcDatetime,
)


class OrderIntent(DomainModel):
    """Broker-agnostic execution intent expressed in base units."""

    intent_id: NonEmptyStr
    strategy_id: NonEmptyStr
    symbol: NonEmptyStr
    created_at: UtcDatetime
    side: OrderSide
    order_type: OrderType
    time_in_force: Optional[TimeInForce] = None
    quantity: Optional[PositiveFiniteFloat] = None
    limit_price: Optional[PositiveFiniteFloat] = None
    stop_price: Optional[PositiveFiniteFloat] = None
    target_position: Optional[FiniteFloat] = None
    reduce_only: bool = False
    paper: bool = True
    metadata: Optional[dict[NonEmptyStr, Any]] = None

    @model_validator(mode="after")
    def validate_order_shape(self) -> "OrderIntent":
        objective_count = int(self.quantity is not None) + int(self.target_position is not None)
        if objective_count != 1:
            raise ValueError("Exactly one of quantity or target_position must be provided.")

        if self.order_type == OrderType.MARKET:
            if self.limit_price is not None or self.stop_price is not None:
                raise ValueError("Market orders must not include limit_price or stop_price.")
        elif self.order_type == OrderType.LIMIT:
            if self.limit_price is None or self.stop_price is not None:
                raise ValueError("Limit orders require limit_price and must not include stop_price.")
        elif self.order_type == OrderType.STOP:
            if self.stop_price is None or self.limit_price is not None:
                raise ValueError("Stop orders require stop_price and must not include limit_price.")
        elif self.order_type == OrderType.STOP_LIMIT:
            if self.stop_price is None or self.limit_price is None:
                raise ValueError("Stop-limit orders require both stop_price and limit_price.")

        return self


class FillEvent(DomainModel):
    """Executed fill with explicit cost decomposition."""

    fill_id: NonEmptyStr
    intent_id: NonEmptyStr
    broker_order_id: Optional[NonEmptyStr] = None
    symbol: NonEmptyStr
    event_timestamp: UtcDatetime
    received_timestamp: UtcDatetime
    side: OrderSide
    fill_price: PositiveFiniteFloat
    fill_quantity: PositiveFiniteFloat
    commission: NonNegativeFiniteFloat = 0.0
    spread_cost: NonNegativeFiniteFloat = 0.0
    slippage_cost: NonNegativeFiniteFloat = 0.0
    liquidity_flag: LiquidityFlag = LiquidityFlag.UNKNOWN
    venue: Optional[NonEmptyStr] = None


class PositionState(DomainModel):
    """Current marked position state in base units and quote exposure."""

    symbol: NonEmptyStr
    timestamp: UtcDatetime
    net_quantity: FiniteFloat
    average_entry_price: NonNegativeFiniteFloat
    mark_price: PositiveFiniteFloat
    realized_pnl: FiniteFloat
    unrealized_pnl: FiniteFloat
    exposure_quote: FiniteFloat
    last_fill_id: Optional[NonEmptyStr] = None
    position_mode: Optional[PositionMode] = None

    @model_validator(mode="after")
    def validate_entry_price(self) -> "PositionState":
        if self.net_quantity == 0:
            return self
        if self.average_entry_price <= 0:
            raise ValueError("Non-flat positions require a positive average_entry_price.")
        return self
