"""MT5-oriented live execution adapter skeleton with normalized snapshots."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Protocol, Sequence

from scalper_ai.backtesting.accounting import (
    apply_fill_to_cash,
    apply_fill_to_position,
    calculate_equity,
    mark_position,
)
from scalper_ai.domain import (
    FillEvent,
    LiquidityFlag,
    OrderIntent,
    OrderSide,
    OrderType,
    PositionMode,
    PositionState,
    TimeInForce,
)
from scalper_ai.execution.connectivity import BrokerConnectivitySnapshot
from scalper_ai.execution.models import (
    ExecutionOrder,
    ExecutionOrderStatus,
    ExecutionQuote,
    ExecutionUpdate,
)
from scalper_ai.execution.reconciliation import BrokerOrderSnapshot, BrokerPositionSnapshot

_ZERO_TOLERANCE = 1e-12


@dataclass(frozen=True)
class Mt5ExecutionConfig:
    """Configuration for the MT5 execution adapter skeleton."""

    initial_cash: float = 100_000.0
    default_venue: str = "MT5"
    base_units_per_lot: float = 100_000.0
    min_volume_lots: float = 0.01
    volume_step_lots: float = 0.01
    symbol_map: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be greater than zero.")
        if self.base_units_per_lot <= 0:
            raise ValueError("base_units_per_lot must be greater than zero.")
        if self.min_volume_lots <= 0:
            raise ValueError("min_volume_lots must be greater than zero.")
        if self.volume_step_lots <= 0:
            raise ValueError("volume_step_lots must be greater than zero.")
        if not self.default_venue.strip():
            raise ValueError("default_venue must be non-empty.")
        for internal_symbol, broker_symbol in self.symbol_map.items():
            if not internal_symbol.strip() or not broker_symbol.strip():
                raise ValueError("symbol_map keys and values must be non-empty.")


@dataclass(frozen=True)
class Mt5OrderRequest:
    """Normalized broker-specific order request expressed in MT5 lots."""

    client_order_id: str
    broker_symbol: str
    side: OrderSide
    order_type: OrderType
    submitted_at: datetime
    volume_lots: float
    time_in_force: TimeInForce | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    reduce_only: bool = False


@dataclass(frozen=True)
class Mt5OrderState:
    """Normalized MT5 order state returned by the broker client."""

    broker_order_id: str
    broker_symbol: str
    status: ExecutionOrderStatus
    submitted_at: datetime
    updated_at: datetime
    requested_volume_lots: float
    filled_volume_lots: float
    remaining_volume_lots: float
    average_fill_price: float | None = None
    rejection_reason: str | None = None
    cancel_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.broker_order_id.strip():
            raise ValueError("broker_order_id must be non-empty.")
        if not self.broker_symbol.strip():
            raise ValueError("broker_symbol must be non-empty.")
        if self.requested_volume_lots <= 0:
            raise ValueError("requested_volume_lots must be greater than zero.")
        if self.filled_volume_lots < 0:
            raise ValueError("filled_volume_lots must be non-negative.")
        if self.remaining_volume_lots < 0:
            raise ValueError("remaining_volume_lots must be non-negative.")
        if self.submitted_at.tzinfo is None or self.submitted_at.utcoffset() is None:
            raise ValueError("submitted_at must be timezone-aware.")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware.")


@dataclass(frozen=True)
class Mt5PositionState:
    """Normalized MT5 netting position state expressed in lots."""

    broker_symbol: str
    timestamp: datetime
    net_volume_lots: float
    average_entry_price: float

    def __post_init__(self) -> None:
        if not self.broker_symbol.strip():
            raise ValueError("broker_symbol must be non-empty.")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware.")
        if not math.isfinite(self.net_volume_lots):
            raise ValueError("net_volume_lots must be finite.")
        if self.average_entry_price < 0:
            raise ValueError("average_entry_price must be non-negative.")


class Mt5ExecutionClientProtocol(Protocol):
    """Minimal MT5 client surface required by the live execution adapter skeleton."""

    def submit_order(self, request: Mt5OrderRequest) -> Mt5OrderState:
        """Submit one MT5 order request and return the normalized broker state."""

    def cancel_order(self, broker_order_id: str, *, timestamp: datetime) -> Mt5OrderState:
        """Cancel one live broker order and return the latest broker state."""

    def get_order(self, broker_order_id: str) -> Mt5OrderState | None:
        """Return one order state if the broker still knows about it."""

    def list_orders(self) -> Sequence[Mt5OrderState]:
        """Return broker-visible order states for reconciliation."""

    def get_position(self, broker_symbol: str) -> Mt5PositionState | None:
        """Return one current broker position if present."""

    def list_positions(self) -> Sequence[Mt5PositionState]:
        """Return broker-visible positions for reconciliation."""

    def is_connected(self) -> bool:
        """Return whether the MT5 dependency currently looks reachable."""

    def ping_latency_ms(self) -> float | None:
        """Return an optional dependency round-trip latency estimate."""


class Mt5ExecutionAdapter:
    """Broker-specific live adapter skeleton that normalizes MT5 state into core models."""

    def __init__(self, client: Mt5ExecutionClientProtocol, *, config: Mt5ExecutionConfig | None = None) -> None:
        self._client = client
        self._config = config or Mt5ExecutionConfig()
        self._cash_balance = float(self._config.initial_cash)
        self._orders: dict[str, ExecutionOrder] = {}
        self._open_order_ids_by_symbol: dict[str, list[str]] = {}
        self._positions: dict[str, PositionState] = {}
        self._last_quotes: dict[str, ExecutionQuote] = {}
        self._next_fill_id = 1

    @property
    def cash_balance(self) -> float:
        """Return the tracked synthetic cash balance for normalized updates."""

        return self._cash_balance

    def submit_order(self, intent: OrderIntent, quote: ExecutionQuote) -> ExecutionUpdate:
        """Submit one live order intent through the MT5 client boundary."""

        if intent.paper:
            raise ValueError("Mt5ExecutionAdapter only accepts order intents with paper=False.")
        if quote.symbol != intent.symbol:
            raise ValueError("ExecutionQuote symbol must match the submitted OrderIntent symbol.")

        self._remember_quote(quote)
        current_position = self.get_position(intent.symbol, quote=quote) or self._mark_current_position(intent.symbol, quote)
        requested_quantity, rejection_reason = self._resolve_requested_quantity(intent, current_position=current_position)
        if rejection_reason is not None:
            return self._build_rejected_update(intent, quote=quote, requested_quantity=requested_quantity, reason=rejection_reason)

        request = Mt5OrderRequest(
            client_order_id=intent.intent_id,
            broker_symbol=self._broker_symbol_for(intent.symbol),
            side=intent.side,
            order_type=intent.order_type,
            submitted_at=quote.received_timestamp,
            volume_lots=self._base_units_to_lots(requested_quantity),
            time_in_force=intent.time_in_force,
            limit_price=None if intent.limit_price is None else float(intent.limit_price),
            stop_price=None if intent.stop_price is None else float(intent.stop_price),
            reduce_only=intent.reduce_only,
        )
        state = self._client.submit_order(request)
        return self._sync_order_state(intent, state, quote=quote)

    def process_quote(self, quote: ExecutionQuote) -> tuple[ExecutionUpdate, ...]:
        """Poll MT5 order state for open orders after a fresh quote update."""

        self._remember_quote(quote)
        self._mark_current_position(quote.symbol, quote)

        updates: list[ExecutionUpdate] = []
        for broker_order_id in list(self._open_order_ids_by_symbol.get(quote.symbol, ())):
            state = self._client.get_order(broker_order_id)
            if state is None:
                continue
            previous_order = self._orders.get(broker_order_id)
            update = self._sync_order_state(previous_order.intent, state, quote=quote)
            if previous_order is None or previous_order != update.order or update.fills:
                updates.append(update)
        return tuple(updates)

    def cancel_order(self, broker_order_id: str, *, timestamp: datetime) -> ExecutionUpdate:
        """Cancel one live broker order and return the normalized state transition."""

        existing_order = self._orders.get(broker_order_id)
        if existing_order is None:
            raise KeyError(f"Unknown broker_order_id: {broker_order_id}")
        current_quote = self._last_quotes.get(existing_order.intent.symbol)
        if current_quote is None:
            raise RuntimeError("Cannot cancel an order before at least one quote has been observed for its symbol.")

        quote = replace(
            current_quote,
            event_timestamp=timestamp,
            received_timestamp=timestamp,
        )
        self._remember_quote(quote)
        state = self._client.cancel_order(broker_order_id, timestamp=timestamp)
        return self._sync_order_state(existing_order.intent, state, quote=quote)

    def get_order(self, broker_order_id: str) -> ExecutionOrder | None:
        """Return the latest normalized order state tracked by the adapter."""

        return self._orders.get(broker_order_id)

    def get_position(self, symbol: str, *, quote: ExecutionQuote | None = None) -> PositionState | None:
        """Return the latest marked internal position for one symbol."""

        if quote is not None:
            if quote.symbol != symbol:
                raise ValueError("ExecutionQuote symbol must match the requested position symbol.")
            self._remember_quote(quote)

        latest_quote = quote or self._last_quotes.get(symbol)
        if latest_quote is None:
            return self._positions.get(symbol)
        return self._mark_current_position(symbol, latest_quote)

    def list_broker_orders(self) -> tuple[BrokerOrderSnapshot, ...]:
        """Return normalized broker-side order snapshots for reconciliation."""

        snapshots = [
            BrokerOrderSnapshot(
                broker_order_id=state.broker_order_id,
                symbol=self._internal_symbol_for(state.broker_symbol),
                status=state.status,
                updated_at=state.updated_at,
                requested_quantity=self._lots_to_base_units(state.requested_volume_lots),
                filled_quantity=self._lots_to_base_units(state.filled_volume_lots),
                remaining_quantity=self._lots_to_base_units(state.remaining_volume_lots),
            )
            for state in self._client.list_orders()
        ]
        return tuple(sorted(snapshots, key=lambda snapshot: snapshot.broker_order_id))

    def list_broker_positions(self) -> tuple[BrokerPositionSnapshot, ...]:
        """Return normalized broker-side net positions for reconciliation."""

        snapshots = [
            BrokerPositionSnapshot(
                symbol=self._internal_symbol_for(position.broker_symbol),
                timestamp=position.timestamp,
                net_quantity=self._lots_to_base_units(position.net_volume_lots),
                average_entry_price=position.average_entry_price,
                position_mode=PositionMode.NETTING,
            )
            for position in self._client.list_positions()
        ]
        return tuple(sorted(snapshots, key=lambda snapshot: snapshot.symbol))

    def describe_broker_connectivity(self) -> BrokerConnectivitySnapshot:
        """Return the current MT5 dependency snapshot for runtime health checks."""

        checked_at = datetime.now(timezone.utc)
        connected = self._client.is_connected()
        latest_broker_timestamp = self._latest_broker_snapshot_timestamp()
        return BrokerConnectivitySnapshot(
            venue=self._config.default_venue,
            checked_at=checked_at,
            connected=connected,
            last_snapshot_at=latest_broker_timestamp,
            latency_ms=self._client.ping_latency_ms(),
        )

    def close(self) -> None:
        """Close the underlying broker client if it exposes a shutdown hook."""

        close_method = getattr(self._client, "close", None)
        if callable(close_method):
            close_method()

    def _sync_order_state(
        self,
        intent: OrderIntent,
        state: Mt5OrderState,
        *,
        quote: ExecutionQuote,
    ) -> ExecutionUpdate:
        internal_symbol = self._internal_symbol_for(state.broker_symbol)
        if internal_symbol != intent.symbol:
            raise ValueError("Broker order symbol does not match the tracked OrderIntent symbol.")

        previous_order = self._orders.get(state.broker_order_id)
        previous_filled_quantity = 0.0 if previous_order is None else previous_order.filled_quantity
        filled_quantity = self._lots_to_base_units(state.filled_volume_lots)
        remaining_quantity = self._lots_to_base_units(state.remaining_volume_lots)
        requested_quantity = self._lots_to_base_units(state.requested_volume_lots)
        delta_filled_quantity = filled_quantity - previous_filled_quantity
        if delta_filled_quantity < -_ZERO_TOLERANCE:
            raise ValueError("Broker filled quantity moved backwards.")

        fills: tuple[FillEvent, ...] = ()
        order_fills: tuple[FillEvent, ...] = () if previous_order is None else previous_order.fills
        if delta_filled_quantity > _ZERO_TOLERANCE:
            fill = self._build_fill(
                intent,
                broker_order_id=state.broker_order_id,
                fill_quantity=delta_filled_quantity,
                state=state,
                quote=quote,
            )
            self._cash_balance = apply_fill_to_cash(self._cash_balance, fill)
            next_position = apply_fill_to_position(
                self._positions.get(intent.symbol),
                fill,
                mark_price=quote.mid_price,
            )
            self._positions[intent.symbol] = next_position
            fills = (fill,)
            order_fills = order_fills + fills
        else:
            self._mark_current_position(intent.symbol, quote)

        normalized_order = ExecutionOrder(
            intent=intent,
            broker_order_id=state.broker_order_id,
            status=state.status,
            submitted_at=state.submitted_at,
            updated_at=state.updated_at,
            requested_quantity=requested_quantity,
            filled_quantity=filled_quantity,
            remaining_quantity=remaining_quantity,
            fills=order_fills,
            rejection_reason=state.rejection_reason,
            cancel_reason=state.cancel_reason,
        )
        self._persist_order(normalized_order)
        return self._build_update(normalized_order, fills, quote=quote)

    def _build_rejected_update(
        self,
        intent: OrderIntent,
        *,
        quote: ExecutionQuote,
        requested_quantity: float | None,
        reason: str,
    ) -> ExecutionUpdate:
        quantity = 1.0 if requested_quantity is None else requested_quantity
        rejected_order = ExecutionOrder(
            intent=intent,
            broker_order_id=f"mt5-rejected-{intent.intent_id}",
            status=ExecutionOrderStatus.REJECTED,
            submitted_at=quote.received_timestamp,
            updated_at=quote.received_timestamp,
            requested_quantity=quantity,
            filled_quantity=0.0,
            remaining_quantity=quantity,
            rejection_reason=reason,
        )
        self._orders[rejected_order.broker_order_id] = rejected_order
        return self._build_update(rejected_order, (), quote=quote)

    def _build_fill(
        self,
        intent: OrderIntent,
        *,
        broker_order_id: str,
        fill_quantity: float,
        state: Mt5OrderState,
        quote: ExecutionQuote,
    ) -> FillEvent:
        if state.average_fill_price is not None:
            fill_price = float(state.average_fill_price)
        elif intent.side is OrderSide.BUY:
            fill_price = quote.ask
        else:
            fill_price = quote.bid
        return FillEvent(
            fill_id=self._next_fill_id_value(),
            intent_id=intent.intent_id,
            broker_order_id=broker_order_id,
            symbol=intent.symbol,
            event_timestamp=state.updated_at,
            received_timestamp=state.updated_at,
            side=intent.side,
            fill_price=fill_price,
            fill_quantity=fill_quantity,
            commission=0.0,
            spread_cost=abs(fill_price - quote.mid_price) * fill_quantity,
            slippage_cost=0.0,
            liquidity_flag=LiquidityFlag.UNKNOWN,
            venue=self._config.default_venue,
        )

    def _build_update(
        self,
        order: ExecutionOrder,
        fills: tuple[FillEvent, ...],
        *,
        quote: ExecutionQuote,
    ) -> ExecutionUpdate:
        position = self._mark_current_position(order.intent.symbol, quote)
        equity = calculate_equity(self._cash_balance, position)
        return ExecutionUpdate(
            order=order,
            fills=fills,
            position=position,
            cash_balance=self._cash_balance,
            equity=equity,
            quote=quote,
        )

    def _mark_current_position(self, symbol: str, quote: ExecutionQuote) -> PositionState:
        current_position = self._positions.get(symbol)
        marked = mark_position(
            current_position,
            symbol=symbol,
            timestamp=quote.received_timestamp,
            mark_price=quote.mid_price,
        )
        self._positions[symbol] = marked
        return marked

    def _persist_order(self, order: ExecutionOrder) -> None:
        self._orders[order.broker_order_id] = order
        symbol_open_orders = self._open_order_ids_by_symbol.setdefault(order.intent.symbol, [])
        if order.is_open:
            if order.broker_order_id not in symbol_open_orders:
                symbol_open_orders.append(order.broker_order_id)
        elif order.broker_order_id in symbol_open_orders:
            symbol_open_orders.remove(order.broker_order_id)

    def _remember_quote(self, quote: ExecutionQuote) -> None:
        self._last_quotes[quote.symbol] = quote

    def _resolve_requested_quantity(
        self,
        intent: OrderIntent,
        *,
        current_position: PositionState,
    ) -> tuple[float | None, str | None]:
        current_quantity = float(current_position.net_quantity)
        if intent.target_position is not None:
            delta_quantity = float(intent.target_position) - current_quantity
            if math.isclose(delta_quantity, 0.0, abs_tol=_ZERO_TOLERANCE):
                return None, "target_position resolves to zero trade quantity."
            implied_side = OrderSide.BUY if delta_quantity > 0 else OrderSide.SELL
            if intent.side is not implied_side:
                return None, "side is inconsistent with the requested target_position."
            requested_quantity = abs(delta_quantity)
        else:
            if intent.quantity is None:
                return None, "quantity must be provided when target_position is absent."
            requested_quantity = float(intent.quantity)

        if intent.reduce_only:
            reducible_quantity = self._reducible_quantity(current_quantity, side=intent.side)
            if reducible_quantity <= 0:
                return None, "reduce_only order would not reduce the current position."
            if requested_quantity - reducible_quantity > _ZERO_TOLERANCE:
                return None, "reduce_only order quantity exceeds the reducible position size."
        return requested_quantity, None

    @staticmethod
    def _reducible_quantity(current_quantity: float, *, side: OrderSide) -> float:
        if side is OrderSide.BUY and current_quantity < 0:
            return abs(current_quantity)
        if side is OrderSide.SELL and current_quantity > 0:
            return abs(current_quantity)
        return 0.0

    def _base_units_to_lots(self, quantity: float) -> float:
        raw_lots = float(quantity) / self._config.base_units_per_lot
        normalized_lots = round(round(raw_lots / self._config.volume_step_lots) * self._config.volume_step_lots, 8)
        if normalized_lots < self._config.min_volume_lots - _ZERO_TOLERANCE:
            raise ValueError("Requested base-unit quantity is smaller than the broker minimum lot size.")
        return normalized_lots

    def _lots_to_base_units(self, volume_lots: float) -> float:
        return float(volume_lots) * self._config.base_units_per_lot

    def _broker_symbol_for(self, internal_symbol: str) -> str:
        return self._config.symbol_map.get(internal_symbol, internal_symbol)

    def _internal_symbol_for(self, broker_symbol: str) -> str:
        for internal_symbol, mapped_symbol in self._config.symbol_map.items():
            if mapped_symbol == broker_symbol:
                return internal_symbol
        return broker_symbol

    def _latest_broker_snapshot_timestamp(self) -> datetime | None:
        timestamps = [state.updated_at for state in self._client.list_orders()]
        timestamps.extend(position.timestamp for position in self._client.list_positions())
        if not timestamps:
            return None
        return max(timestamps)

    def _next_fill_id_value(self) -> str:
        fill_id = f"mt5-fill-{self._next_fill_id:06d}"
        self._next_fill_id += 1
        return fill_id
