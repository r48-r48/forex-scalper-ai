"""Execution-layer models for broker-agnostic routing and paper trading."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from scalper_ai.domain import FillEvent, OrderIntent, PositionState

_ZERO_TOLERANCE = 1e-12


class ExecutionOrderStatus(str, Enum):
    """Lifecycle states for submitted execution orders."""

    ACCEPTED = "accepted"
    TRIGGERED = "triggered"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ExecutionQuote:
    """Top-of-book quote used by execution adapters."""

    symbol: str
    event_timestamp: datetime
    received_timestamp: datetime
    bid: float
    ask: float
    venue: str = "paper"

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol must be non-empty.")
        if not self.venue.strip():
            raise ValueError("venue must be non-empty.")
        if self.event_timestamp.tzinfo is None or self.event_timestamp.utcoffset() is None:
            raise ValueError("event_timestamp must be timezone-aware.")
        if self.received_timestamp.tzinfo is None or self.received_timestamp.utcoffset() is None:
            raise ValueError("received_timestamp must be timezone-aware.")
        if self.bid <= 0:
            raise ValueError("bid must be greater than zero.")
        if self.ask <= 0:
            raise ValueError("ask must be greater than zero.")
        if self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid.")

    @property
    def mid_price(self) -> float:
        """Return the mark price implied by the top of book."""

        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        """Return the absolute bid/ask spread."""

        return self.ask - self.bid


@dataclass(frozen=True)
class ExecutionOrder:
    """Materialized execution-order lifecycle state."""

    intent: OrderIntent
    broker_order_id: str
    status: ExecutionOrderStatus
    submitted_at: datetime
    updated_at: datetime
    requested_quantity: float
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    fills: tuple[FillEvent, ...] = ()
    triggered_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    cancel_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.broker_order_id.strip():
            raise ValueError("broker_order_id must be non-empty.")
        if self.submitted_at.tzinfo is None or self.submitted_at.utcoffset() is None:
            raise ValueError("submitted_at must be timezone-aware.")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware.")
        if self.requested_quantity <= 0:
            raise ValueError("requested_quantity must be greater than zero.")
        if self.filled_quantity < 0:
            raise ValueError("filled_quantity must be non-negative.")
        if self.remaining_quantity < 0:
            raise ValueError("remaining_quantity must be non-negative.")
        if self.filled_quantity - self.requested_quantity > _ZERO_TOLERANCE:
            raise ValueError("filled_quantity must not exceed requested_quantity.")
        quantity_gap = (self.filled_quantity + self.remaining_quantity) - self.requested_quantity
        if not math.isclose(quantity_gap, 0.0, abs_tol=1e-9):
            raise ValueError("filled_quantity and remaining_quantity must reconcile to requested_quantity.")

    @property
    def is_open(self) -> bool:
        """Return whether the order can still change on future quotes."""

        return self.status in {
            ExecutionOrderStatus.ACCEPTED,
            ExecutionOrderStatus.TRIGGERED,
            ExecutionOrderStatus.PARTIALLY_FILLED,
        }


@dataclass(frozen=True)
class ExecutionUpdate:
    """Execution-layer state transition emitted by an adapter."""

    order: ExecutionOrder
    fills: tuple[FillEvent, ...]
    position: PositionState
    cash_balance: float
    equity: float
    quote: ExecutionQuote

    def __post_init__(self) -> None:
        if self.cash_balance <= 0 and self.equity <= 0:
            raise ValueError("cash_balance and equity cannot both be non-positive.")
