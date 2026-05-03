"""Broker-agnostic execution adapter interfaces."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from scalper_ai.domain import OrderIntent, PositionState
from scalper_ai.execution.connectivity import BrokerConnectivitySnapshot
from scalper_ai.execution.models import ExecutionOrder, ExecutionQuote, ExecutionUpdate
from scalper_ai.execution.reconciliation import BrokerOrderSnapshot, BrokerPositionSnapshot


class ExecutionAdapter(Protocol):
    """Adapter boundary for paper or live order routing."""

    def submit_order(self, intent: OrderIntent, quote: ExecutionQuote) -> ExecutionUpdate:
        """Submit one order intent using the current market quote."""

    def process_quote(self, quote: ExecutionQuote) -> tuple[ExecutionUpdate, ...]:
        """Advance any open orders using a fresh quote update."""

    def cancel_order(self, broker_order_id: str, *, timestamp: datetime) -> ExecutionUpdate:
        """Cancel an accepted or triggered order."""

    def get_order(self, broker_order_id: str) -> ExecutionOrder | None:
        """Return the latest lifecycle state for one order if known."""

    def get_position(
        self,
        symbol: str,
        *,
        quote: ExecutionQuote | None = None,
    ) -> PositionState | None:
        """Return the current marked position for one symbol."""


class BrokerSnapshotProvider(Protocol):
    """Broker-facing contract that exposes normalized order and position snapshots."""

    def list_broker_orders(self) -> tuple[BrokerOrderSnapshot, ...]:
        """Return the latest broker-side order snapshots."""

    def list_broker_positions(self) -> tuple[BrokerPositionSnapshot, ...]:
        """Return the latest broker-side position snapshots."""


class BrokerConnectivityProvider(Protocol):
    """Broker-facing contract that exposes basic dependency/connectivity state."""

    def describe_broker_connectivity(self) -> BrokerConnectivitySnapshot:
        """Return the latest broker connectivity snapshot."""
