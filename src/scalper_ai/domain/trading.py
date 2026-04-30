"""Canonical trading intent, fill, and position state models."""

from __future__ import annotations

from typing import Any

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
    time_in_force: TimeInForce | None = None
    quantity: PositiveFiniteFloat | None = None
    limit_price: PositiveFiniteFloat | None = None
    stop_price: PositiveFiniteFloat | None = None
    stop_loss_price: PositiveFiniteFloat | None = None
    take_profit_price: PositiveFiniteFloat | None = None
    target_position: FiniteFloat | None = None
    reduce_only: bool = False
    paper: bool = True
    metadata: dict[NonEmptyStr, Any] | None = None

    @model_validator(mode="after")
    def validate_order_shape(self) -> OrderIntent:
        objective_count = int(self.quantity is not None) + int(self.target_position is not None)
        if objective_count != 1:
            raise ValueError("Exactly one of quantity or target_position must be provided.")

        if self.order_type == OrderType.MARKET:
            if self.limit_price is not None or self.stop_price is not None:
                raise ValueError("Market orders must not include limit_price or stop_price.")
        elif self.order_type == OrderType.LIMIT:
            if self.limit_price is None or self.stop_price is not None:
                raise ValueError(
                    "Limit orders require limit_price and must not include stop_price."
                )
        elif self.order_type == OrderType.STOP:
            if self.stop_price is None or self.limit_price is not None:
                raise ValueError("Stop orders require stop_price and must not include limit_price.")
        elif self.order_type == OrderType.STOP_LIMIT:
            if self.stop_price is None or self.limit_price is None:
                raise ValueError("Stop-limit orders require both stop_price and limit_price.")

        reference_price = self._protective_reference_price()
        if reference_price is not None:
            self._validate_protective_prices(reference_price)

        return self

    def _protective_reference_price(self) -> float | None:
        if self.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT}:
            return None if self.limit_price is None else float(self.limit_price)
        if self.order_type == OrderType.STOP:
            return None if self.stop_price is None else float(self.stop_price)
        return None

    def _validate_protective_prices(self, reference_price: float) -> None:
        if self.side is OrderSide.BUY:
            if (
                self.stop_loss_price is not None
                and float(self.stop_loss_price) >= reference_price
            ):
                raise ValueError("Buy stop_loss_price must be below the entry reference price.")
            if (
                self.take_profit_price is not None
                and float(self.take_profit_price) <= reference_price
            ):
                raise ValueError("Buy take_profit_price must be above the entry reference price.")
        else:
            if (
                self.stop_loss_price is not None
                and float(self.stop_loss_price) <= reference_price
            ):
                raise ValueError("Sell stop_loss_price must be above the entry reference price.")
            if (
                self.take_profit_price is not None
                and float(self.take_profit_price) >= reference_price
            ):
                raise ValueError("Sell take_profit_price must be below the entry reference price.")


class FillEvent(DomainModel):
    """Executed fill with explicit cost decomposition."""

    fill_id: NonEmptyStr
    intent_id: NonEmptyStr
    broker_order_id: NonEmptyStr | None = None
    symbol: NonEmptyStr
    event_timestamp: UtcDatetime
    received_timestamp: UtcDatetime
    side: OrderSide
    fill_price: PositiveFiniteFloat
    fill_quantity: PositiveFiniteFloat
    commission: NonNegativeFiniteFloat = 0.0
    spread_cost: NonNegativeFiniteFloat = 0.0
    slippage_cost: NonNegativeFiniteFloat = 0.0
    broker_deal_id: NonEmptyStr | None = None
    broker_symbol: NonEmptyStr | None = None
    broker_position_id: NonEmptyStr | None = None
    broker_commission: FiniteFloat = 0.0
    broker_fee: FiniteFloat = 0.0
    broker_swap: FiniteFloat = 0.0
    liquidity_flag: LiquidityFlag = LiquidityFlag.UNKNOWN
    venue: NonEmptyStr | None = None


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
    last_fill_id: NonEmptyStr | None = None
    position_mode: PositionMode | None = None

    @model_validator(mode="after")
    def validate_entry_price(self) -> PositionState:
        if self.net_quantity == 0:
            return self
        if self.average_entry_price <= 0:
            raise ValueError("Non-flat positions require a positive average_entry_price.")
        return self
