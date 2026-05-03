"""Adapter router for paper and live execution backends."""

from __future__ import annotations

from datetime import datetime

from scalper_ai.domain import OrderIntent, PositionState
from scalper_ai.execution.interfaces import ExecutionAdapter
from scalper_ai.execution.models import ExecutionOrder, ExecutionQuote, ExecutionUpdate


class ExecutionRouter:
    """Route order intents to paper or live adapters without leaking adapter details."""

    def __init__(
        self,
        *,
        paper_adapter: ExecutionAdapter,
        live_adapter: ExecutionAdapter | None = None,
    ) -> None:
        self._paper_adapter = paper_adapter
        self._live_adapter = live_adapter
        self._order_adapters: dict[str, ExecutionAdapter] = {}

    def submit_order(self, intent: OrderIntent, quote: ExecutionQuote) -> ExecutionUpdate:
        """Submit an order to the appropriate adapter based on the paper flag."""

        adapter = self._paper_adapter if intent.paper else self._require_live_adapter()
        update = adapter.submit_order(intent, quote)
        self._order_adapters[update.order.broker_order_id] = adapter
        return update

    def process_quote(self, quote: ExecutionQuote) -> tuple[ExecutionUpdate, ...]:
        """Advance all configured adapters with a fresh quote."""

        updates: list[ExecutionUpdate] = []
        updates.extend(self._paper_adapter.process_quote(quote))
        if self._live_adapter is not None and self._live_adapter is not self._paper_adapter:
            updates.extend(self._live_adapter.process_quote(quote))
        return tuple(updates)

    def cancel_order(self, broker_order_id: str, *, timestamp: datetime) -> ExecutionUpdate:
        """Cancel a previously submitted order via the adapter that owns it."""

        adapter = self._order_adapters.get(broker_order_id)
        if adapter is None:
            raise KeyError(f"Unknown broker_order_id: {broker_order_id}")
        return adapter.cancel_order(broker_order_id, timestamp=timestamp)

    def get_order(self, broker_order_id: str) -> ExecutionOrder | None:
        """Return the latest known state for one order."""

        adapter = self._order_adapters.get(broker_order_id)
        if adapter is None:
            return None
        return adapter.get_order(broker_order_id)

    def get_position(
        self,
        symbol: str,
        *,
        quote: ExecutionQuote | None = None,
        paper: bool = True,
    ) -> PositionState | None:
        """Return the current marked position from the selected adapter."""

        adapter = self._paper_adapter if paper else self._require_live_adapter()
        return adapter.get_position(symbol, quote=quote)

    def _require_live_adapter(self) -> ExecutionAdapter:
        if self._live_adapter is None:
            raise ValueError("No live execution adapter is configured.")
        return self._live_adapter
