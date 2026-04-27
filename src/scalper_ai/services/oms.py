"""Order-management state machine and emergency order helpers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Optional

from scalper_ai.domain import OrderIntent, OrderSide, OrderType


class OmsOrderStatus(str, Enum):
    """Canonical OMS lifecycle states."""

    NEW = "new"
    CHECKED = "checked"
    SENT = "sent"
    ACK = "ack"
    PARTIAL = "partial"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    RECONCILED = "reconciled"


TERMINAL_ORDER_STATUSES = {
    OmsOrderStatus.FILLED,
    OmsOrderStatus.REJECTED,
    OmsOrderStatus.CANCELLED,
    OmsOrderStatus.RECONCILED,
}

ALLOWED_ORDER_TRANSITIONS: dict[OmsOrderStatus, set[OmsOrderStatus]] = {
    OmsOrderStatus.NEW: {
        OmsOrderStatus.CHECKED,
        OmsOrderStatus.REJECTED,
        OmsOrderStatus.CANCELLED,
    },
    OmsOrderStatus.CHECKED: {
        OmsOrderStatus.SENT,
        OmsOrderStatus.REJECTED,
        OmsOrderStatus.CANCELLED,
    },
    OmsOrderStatus.SENT: {
        OmsOrderStatus.ACK,
        OmsOrderStatus.REJECTED,
        OmsOrderStatus.CANCELLED,
    },
    OmsOrderStatus.ACK: {
        OmsOrderStatus.PARTIAL,
        OmsOrderStatus.FILLED,
        OmsOrderStatus.REJECTED,
        OmsOrderStatus.CANCELLED,
    },
    OmsOrderStatus.PARTIAL: {
        OmsOrderStatus.PARTIAL,
        OmsOrderStatus.FILLED,
        OmsOrderStatus.CANCELLED,
    },
    OmsOrderStatus.FILLED: {
        OmsOrderStatus.RECONCILED,
    },
    OmsOrderStatus.REJECTED: {
        OmsOrderStatus.RECONCILED,
    },
    OmsOrderStatus.CANCELLED: {
        OmsOrderStatus.RECONCILED,
    },
    OmsOrderStatus.RECONCILED: set(),
}


@dataclass(frozen=True)
class OmsOrderRecord:
    """Immutable OMS lifecycle record for one order intent."""

    intent: OrderIntent
    status: OmsOrderStatus
    created_at: datetime
    updated_at: datetime
    broker_order_id: Optional[str] = None
    filled_quantity: float = 0.0
    rejection_reason: Optional[str] = None
    cancel_reason: Optional[str] = None

    def __post_init__(self) -> None:
        _ensure_aware(self.created_at, field_name="created_at")
        _ensure_aware(self.updated_at, field_name="updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must be greater than or equal to created_at.")
        if self.filled_quantity < 0:
            raise ValueError("filled_quantity must be non-negative.")
        if self.broker_order_id is not None and not self.broker_order_id.strip():
            raise ValueError("broker_order_id must be non-empty when provided.")

    @classmethod
    def new(cls, intent: OrderIntent) -> "OmsOrderRecord":
        """Create a NEW OMS record from an order intent."""

        return cls(
            intent=intent,
            status=OmsOrderStatus.NEW,
            created_at=intent.created_at,
            updated_at=intent.created_at,
        )

    @property
    def is_terminal(self) -> bool:
        """Return whether this order cannot be acted on except reconciliation."""

        return self.status in TERMINAL_ORDER_STATUSES


def is_valid_order_transition(current: OmsOrderStatus, target: OmsOrderStatus) -> bool:
    """Return whether the OMS lifecycle permits one state transition."""

    return target in ALLOWED_ORDER_TRANSITIONS[current]


def transition_order(
    record: OmsOrderRecord,
    target: OmsOrderStatus,
    *,
    updated_at: datetime,
    broker_order_id: str | None = None,
    filled_quantity: float | None = None,
    rejection_reason: str | None = None,
    cancel_reason: str | None = None,
) -> OmsOrderRecord:
    """Return an updated order record after validating one lifecycle transition."""

    _ensure_aware(updated_at, field_name="updated_at")
    if updated_at < record.updated_at:
        raise ValueError("updated_at must not move backwards.")
    if not is_valid_order_transition(record.status, target):
        raise ValueError(f"Invalid OMS transition: {record.status.value} -> {target.value}.")

    next_filled_quantity = record.filled_quantity if filled_quantity is None else filled_quantity
    if next_filled_quantity < record.filled_quantity:
        raise ValueError("filled_quantity must not decrease.")
    rejection_text = rejection_reason or record.rejection_reason
    if target is OmsOrderStatus.REJECTED and not _has_text(rejection_text):
        raise ValueError("rejection_reason is required for rejected orders.")
    if target is OmsOrderStatus.CANCELLED and not _has_text(cancel_reason or record.cancel_reason):
        raise ValueError("cancel_reason is required for cancelled orders.")

    return replace(
        record,
        status=target,
        updated_at=updated_at,
        broker_order_id=_first_text(broker_order_id, record.broker_order_id),
        filled_quantity=next_filled_quantity,
        rejection_reason=_first_text(rejection_reason, record.rejection_reason),
        cancel_reason=_first_text(cancel_reason, record.cancel_reason),
    )


def build_emergency_flatten_intent(
    *,
    intent_id: str,
    strategy_id: str,
    symbol: str,
    current_net_quantity: float,
    created_at: datetime,
    paper: bool = True,
) -> OrderIntent | None:
    """Build a reduce-only market intent that flattens the current net position."""

    _ensure_aware(created_at, field_name="created_at")
    if current_net_quantity == 0:
        return None
    side = OrderSide.SELL if current_net_quantity > 0 else OrderSide.BUY
    return OrderIntent(
        intent_id=intent_id,
        strategy_id=strategy_id,
        symbol=symbol,
        created_at=created_at,
        side=side,
        order_type=OrderType.MARKET,
        quantity=abs(float(current_net_quantity)),
        reduce_only=True,
        paper=paper,
        metadata={"reason": "emergency_flatten"},
    )


def _ensure_aware(timestamp: datetime, *, field_name: str) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")


def _has_text(value: str | None) -> bool:
    return value is not None and bool(value.strip())


def _first_text(candidate: str | None, fallback: str | None) -> str | None:
    if candidate is not None and candidate.strip():
        return candidate
    return fallback
