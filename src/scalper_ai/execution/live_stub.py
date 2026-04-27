"""Concrete live-facing execution stub with broker snapshot export."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone

from scalper_ai.domain import OrderIntent, PositionMode
from scalper_ai.execution.connectivity import BrokerConnectivitySnapshot
from scalper_ai.execution.paper import PaperExecutionAdapter, PaperExecutionConfig
from scalper_ai.execution.reconciliation import BrokerOrderSnapshot, BrokerPositionSnapshot
from scalper_ai.execution.models import ExecutionOrder, ExecutionQuote, ExecutionUpdate


@dataclass(frozen=True)
class LiveExecutionStubConfig:
    """Configuration for the live execution stub."""

    initial_cash: float = 100_000.0
    slippage_bps: float = 0.0
    commission_bps: float = 0.0
    default_venue: str = "live_stub"

    def to_paper_config(self) -> PaperExecutionConfig:
        """Convert the live stub config into the shared paper execution config."""

        return PaperExecutionConfig(
            initial_cash=self.initial_cash,
            slippage_bps=self.slippage_bps,
            commission_bps=self.commission_bps,
            default_venue=self.default_venue,
        )


class LiveExecutionStubAdapter(PaperExecutionAdapter):
    """Live-facing adapter stub backed by deterministic paper execution math."""

    def __init__(self, *, config: LiveExecutionStubConfig | None = None) -> None:
        self._live_config = config or LiveExecutionStubConfig()
        self._original_intents: dict[str, OrderIntent] = {}
        super().__init__(config=self._live_config.to_paper_config())

    def submit_order(self, intent: OrderIntent, quote: ExecutionQuote) -> ExecutionUpdate:
        """Submit one live-facing order through the deterministic stub."""

        if intent.paper:
            raise ValueError("LiveExecutionStubAdapter only accepts order intents with paper=False.")
        shadow_intent = intent.model_copy(update={"paper": True})
        update = super().submit_order(shadow_intent, self._normalize_quote(quote))
        self._original_intents[update.order.broker_order_id] = intent
        return self._restore_update(update)

    def process_quote(self, quote: ExecutionQuote) -> tuple[ExecutionUpdate, ...]:
        """Advance open live-stub orders using a fresh quote."""

        updates = super().process_quote(self._normalize_quote(quote))
        return tuple(self._restore_update(update) for update in updates)

    def cancel_order(self, broker_order_id: str, *, timestamp: datetime) -> ExecutionUpdate:
        """Cancel one open live-stub order."""

        update = super().cancel_order(broker_order_id, timestamp=timestamp)
        return self._restore_update(update)

    def get_order(self, broker_order_id: str) -> ExecutionOrder | None:
        """Return the latest live-facing order state."""

        order = super().get_order(broker_order_id)
        if order is None:
            return None
        return self._restore_order(order)

    def list_broker_orders(self) -> tuple[BrokerOrderSnapshot, ...]:
        """Return broker-style order snapshots for reconciliation."""

        snapshots = [
            BrokerOrderSnapshot(
                broker_order_id=order.broker_order_id,
                symbol=order.intent.symbol,
                status=order.status,
                updated_at=order.updated_at,
                requested_quantity=order.requested_quantity,
                filled_quantity=order.filled_quantity,
                remaining_quantity=order.remaining_quantity,
            )
            for order in self._orders.values()
        ]
        return tuple(sorted(snapshots, key=lambda snapshot: snapshot.broker_order_id))

    def list_broker_positions(self) -> tuple[BrokerPositionSnapshot, ...]:
        """Return broker-style position snapshots for reconciliation."""

        snapshots = [
            BrokerPositionSnapshot(
                symbol=position.symbol,
                timestamp=position.timestamp,
                net_quantity=position.net_quantity,
                average_entry_price=position.average_entry_price,
                position_mode=position.position_mode or PositionMode.NETTING,
            )
            for position in self._positions.values()
        ]
        return tuple(sorted(snapshots, key=lambda snapshot: snapshot.symbol))

    def describe_broker_connectivity(self) -> BrokerConnectivitySnapshot:
        """Return one healthy broker connectivity snapshot for the live stub."""

        checked_at = datetime.now(timezone.utc)
        latest_state_timestamp = max(
            (
                *[order.updated_at for order in self._orders.values()],
                *[position.timestamp for position in self._positions.values()],
            ),
            default=checked_at,
        )
        return BrokerConnectivitySnapshot(
            venue=self._live_config.default_venue,
            checked_at=checked_at,
            connected=True,
            last_snapshot_at=latest_state_timestamp,
            latency_ms=0.0,
        )

    def _normalize_quote(self, quote: ExecutionQuote) -> ExecutionQuote:
        return replace(quote, venue=self._live_config.default_venue)

    def _restore_update(self, update: ExecutionUpdate) -> ExecutionUpdate:
        restored_order = self._restore_order(update.order)
        return replace(update, order=restored_order)

    def _restore_order(self, order: ExecutionOrder) -> ExecutionOrder:
        original_intent = self._original_intents.get(order.broker_order_id)
        if original_intent is None:
            return order
        if order.intent == original_intent:
            return order
        return replace(order, intent=original_intent)
