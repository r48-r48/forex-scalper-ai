"""Snapshot contracts and state tracking helpers for reconciliation workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from scalper_ai.domain import PositionState
from scalper_ai.execution.models import ExecutionOrder, ExecutionUpdate
from scalper_ai.execution.reconciliation import (
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
    ReconciliationReport,
    build_reconciliation_report_for_positions,
)


class BrokerSnapshotProvider(Protocol):
    """Broker-facing contract that exposes normalized order and position snapshots."""

    def list_broker_orders(self) -> tuple[BrokerOrderSnapshot, ...]:
        """Return the latest broker-side order snapshots."""

    def list_broker_positions(self) -> tuple[BrokerPositionSnapshot, ...]:
        """Return the latest broker-side position snapshots."""


@dataclass
class ExecutionStateTracker:
    """Track internal execution state from emitted updates."""

    _orders: dict[str, ExecutionOrder]
    _positions: dict[tuple[bool, str], PositionState]

    def __init__(self) -> None:
        self._orders = {}
        self._positions = {}

    def apply_update(self, update: ExecutionUpdate) -> None:
        """Apply one execution update to the internal state cache."""

        self._orders[update.order.broker_order_id] = update.order
        self._positions[(update.order.intent.paper, update.position.symbol)] = update.position

    def apply_updates(self, updates: tuple[ExecutionUpdate, ...]) -> None:
        """Apply multiple execution updates in order."""

        for update in updates:
            self.apply_update(update)

    def list_orders(self, *, paper: bool | None = None) -> tuple[ExecutionOrder, ...]:
        """Return tracked orders, optionally filtered by paper/live routing."""

        orders = tuple(self._orders.values())
        if paper is None:
            return tuple(sorted(orders, key=lambda order: order.broker_order_id))
        filtered = [order for order in orders if order.intent.paper is paper]
        return tuple(sorted(filtered, key=lambda order: order.broker_order_id))

    def list_positions(self, *, paper: bool | None = None) -> tuple[PositionState, ...]:
        """Return tracked positions, optionally filtered by paper/live routing."""

        items = tuple(self._positions.items())
        if paper is not None:
            items = tuple((key, position) for key, position in items if key[0] is paper)
        positions = [position for _, position in items]
        return tuple(sorted(positions, key=lambda position: position.symbol))

    def clear(self) -> None:
        """Reset all tracked state."""

        self._orders.clear()
        self._positions.clear()


def build_snapshot_reconciliation_report(
    *,
    state_tracker: ExecutionStateTracker,
    snapshot_provider: BrokerSnapshotProvider,
    paper: bool,
    checked_at: datetime | None = None,
    allow_missing_terminal_orders: bool = True,
    require_position_stop_loss: bool = False,
    require_position_take_profit: bool = False,
) -> ReconciliationReport:
    """Build a reconciliation report from tracked internal state and broker snapshots."""

    internal_orders = state_tracker.list_orders(paper=paper)
    internal_positions = {
        position.symbol: position for position in state_tracker.list_positions(paper=paper)
    }
    broker_orders = {
        order.broker_order_id: order for order in snapshot_provider.list_broker_orders()
    }
    broker_positions = {
        position.symbol: position for position in snapshot_provider.list_broker_positions()
    }
    return build_reconciliation_report_for_positions(
        internal_orders=internal_orders,
        broker_orders=broker_orders,
        internal_positions=internal_positions,
        broker_positions=broker_positions,
        checked_at=checked_at or datetime.now(UTC),
        allow_missing_terminal_orders=allow_missing_terminal_orders,
        require_position_stop_loss=require_position_stop_loss,
        require_position_take_profit=require_position_take_profit,
    )
