"""MT5-oriented live execution adapter skeleton with normalized snapshots."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol

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
class _ResolvedMt5Sizing:
    requested_quantity: float | None
    rejection_reason: str | None = None
    position_ticket: str | None = None


@dataclass(frozen=True)
class Mt5ExecutionConfig:
    """Configuration for the MT5 execution adapter skeleton."""

    initial_cash: float = 100_000.0
    default_venue: str = "MT5"
    account_mode: PositionMode = PositionMode.NETTING
    base_units_per_lot: float = 100_000.0
    min_volume_lots: float = 0.01
    volume_step_lots: float = 0.01
    require_stop_loss: bool = False
    require_take_profit: bool = False
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
        try:
            account_mode = (
                self.account_mode
                if isinstance(self.account_mode, PositionMode)
                else PositionMode(str(self.account_mode))
            )
        except ValueError as exc:
            raise ValueError("account_mode must be 'netting' or 'hedging'.") from exc
        object.__setattr__(self, "account_mode", account_mode)
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
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    reduce_only: bool = False
    position_ticket: str | None = None


@dataclass(frozen=True)
class Mt5DealState:
    """Normalized MT5 deal/fill record used for live accounting."""

    broker_deal_id: str
    broker_order_id: str
    broker_symbol: str
    timestamp: datetime
    side: OrderSide
    volume_lots: float
    price: float
    commission: float = 0.0
    fee: float = 0.0
    swap: float = 0.0
    position_ticket: str | None = None

    def __post_init__(self) -> None:
        if not self.broker_deal_id.strip():
            raise ValueError("broker_deal_id must be non-empty.")
        if not self.broker_order_id.strip():
            raise ValueError("broker_order_id must be non-empty.")
        if not self.broker_symbol.strip():
            raise ValueError("broker_symbol must be non-empty.")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware.")
        if self.volume_lots <= 0:
            raise ValueError("volume_lots must be greater than zero.")
        if self.price <= 0:
            raise ValueError("price must be greater than zero.")
        for value_name, value in {
            "commission": self.commission,
            "fee": self.fee,
            "swap": self.swap,
        }.items():
            if not math.isfinite(value):
                raise ValueError(f"{value_name} must be finite.")
        if self.position_ticket is not None and not self.position_ticket.strip():
            raise ValueError("position_ticket must be non-empty when provided.")

    @property
    def execution_cost(self) -> float:
        """Return non-negative deal costs representable by FillEvent.commission."""

        return sum(
            abs(value)
            for value in (self.commission, self.fee, self.swap)
            if value < 0
        )


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
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    average_fill_price: float | None = None
    rejection_reason: str | None = None
    cancel_reason: str | None = None
    deals: tuple[Mt5DealState, ...] = ()

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
        for value_name, value in {
            "stop_loss_price": self.stop_loss_price,
            "take_profit_price": self.take_profit_price,
        }.items():
            if value is not None and (value <= 0 or not math.isfinite(value)):
                raise ValueError(f"{value_name} must be a positive finite value when provided.")
        if self.submitted_at.tzinfo is None or self.submitted_at.utcoffset() is None:
            raise ValueError("submitted_at must be timezone-aware.")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware.")
        seen_deal_ids: set[str] = set()
        for deal in self.deals:
            if deal.broker_order_id != self.broker_order_id:
                raise ValueError("deals must reference the same broker_order_id.")
            if deal.broker_deal_id in seen_deal_ids:
                raise ValueError("deals must not contain duplicate broker_deal_id values.")
            seen_deal_ids.add(deal.broker_deal_id)


@dataclass(frozen=True)
class Mt5PositionState:
    """Normalized MT5 position state expressed in lots."""

    broker_symbol: str
    timestamp: datetime
    net_volume_lots: float
    average_entry_price: float
    position_ticket: str | None = None
    position_mode: PositionMode = PositionMode.NETTING
    gross_volume_lots: float | None = None
    source_position_tickets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.broker_symbol.strip():
            raise ValueError("broker_symbol must be non-empty.")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware.")
        if not math.isfinite(self.net_volume_lots):
            raise ValueError("net_volume_lots must be finite.")
        if self.average_entry_price < 0:
            raise ValueError("average_entry_price must be non-negative.")
        if self.position_ticket is not None and not self.position_ticket.strip():
            raise ValueError("position_ticket must be non-empty when provided.")
        if self.gross_volume_lots is not None:
            if self.gross_volume_lots < 0:
                raise ValueError("gross_volume_lots must be non-negative when provided.")
            if abs(self.net_volume_lots) - self.gross_volume_lots > _ZERO_TOLERANCE:
                raise ValueError("gross_volume_lots must not be smaller than absolute net volume.")
        for ticket in self.source_position_tickets:
            if not ticket.strip():
                raise ValueError("source_position_tickets must contain non-empty values.")

    @property
    def gross_lots(self) -> float:
        """Return gross lot exposure when available."""

        return (
            abs(self.net_volume_lots)
            if self.gross_volume_lots is None
            else self.gross_volume_lots
        )


def aggregate_mt5_positions(
    positions: Sequence[Mt5PositionState],
    *,
    broker_symbol: str | None = None,
    position_mode: PositionMode | None = None,
) -> Mt5PositionState | None:
    """Aggregate one or more MT5 position tickets into a broker-source snapshot."""

    selected = tuple(
        position
        for position in positions
        if broker_symbol is None or position.broker_symbol == broker_symbol
    )
    if not selected:
        return None
    if len(selected) == 1:
        position = selected[0]
        source_tickets = _source_position_tickets(selected)
        return replace(
            position,
            position_mode=position_mode or position.position_mode,
            gross_volume_lots=position.gross_lots,
            source_position_tickets=source_tickets,
        )

    net_volume_lots = sum(position.net_volume_lots for position in selected)
    gross_volume_lots = sum(position.gross_lots for position in selected)
    timestamp = max(position.timestamp for position in selected)
    resolved_mode = position_mode or (
        PositionMode.HEDGING
        if any(position.position_mode is PositionMode.HEDGING for position in selected)
        else PositionMode.NETTING
    )
    return Mt5PositionState(
        broker_symbol=selected[0].broker_symbol,
        timestamp=timestamp,
        net_volume_lots=net_volume_lots,
        average_entry_price=_weighted_entry_price(selected, net_volume_lots=net_volume_lots),
        position_ticket=None,
        position_mode=resolved_mode,
        gross_volume_lots=gross_volume_lots,
        source_position_tickets=_source_position_tickets(selected),
    )


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

    def __init__(
        self,
        client: Mt5ExecutionClientProtocol,
        *,
        config: Mt5ExecutionConfig | None = None,
    ) -> None:
        self._client = client
        self._config = config or Mt5ExecutionConfig()
        self._cash_balance = float(self._config.initial_cash)
        self._orders: dict[str, ExecutionOrder] = {}
        self._open_order_ids_by_symbol: dict[str, list[str]] = {}
        self._positions: dict[str, PositionState] = {}
        self._last_quotes: dict[str, ExecutionQuote] = {}
        self._seen_deal_ids: set[str] = set()
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
        broker_positions = self._broker_positions_for_symbol(intent.symbol)
        broker_position = aggregate_mt5_positions(
            broker_positions,
            broker_symbol=self._broker_symbol_for(intent.symbol),
            position_mode=self._config.account_mode,
        )
        if broker_position is not None:
            self._positions[intent.symbol] = self._position_state_from_broker(
                intent.symbol,
                broker_position,
                quote=quote,
            )
        else:
            self._positions[intent.symbol] = self._flat_position(intent.symbol, quote=quote)

        sizing = self._resolve_requested_quantity(
            intent,
            broker_position=broker_position,
            broker_positions=broker_positions,
        )
        if sizing.rejection_reason is not None:
            return self._build_rejected_update(
                intent,
                quote=quote,
                requested_quantity=sizing.requested_quantity,
                reason=sizing.rejection_reason,
            )
        if sizing.requested_quantity is None:
            return self._build_rejected_update(
                intent,
                quote=quote,
                requested_quantity=None,
                reason="MT5 sizing did not produce a trade quantity.",
            )

        missing_protection = self._missing_required_protection(
            intent,
            requested_quantity=sizing.requested_quantity,
            broker_position=broker_position,
        )
        if missing_protection:
            return self._build_rejected_update(
                intent,
                quote=quote,
                requested_quantity=sizing.requested_quantity,
                reason=(
                    "required protective prices are missing for exposure-increasing MT5 order: "
                    f"{', '.join(missing_protection)}."
                ),
            )

        request = Mt5OrderRequest(
            client_order_id=intent.intent_id,
            broker_symbol=self._broker_symbol_for(intent.symbol),
            side=intent.side,
            order_type=intent.order_type,
            submitted_at=quote.received_timestamp,
            volume_lots=self._base_units_to_lots(sizing.requested_quantity),
            time_in_force=intent.time_in_force,
            limit_price=None if intent.limit_price is None else float(intent.limit_price),
            stop_price=None if intent.stop_price is None else float(intent.stop_price),
            stop_loss_price=(
                None if intent.stop_loss_price is None else float(intent.stop_loss_price)
            ),
            take_profit_price=(
                None if intent.take_profit_price is None else float(intent.take_profit_price)
            ),
            reduce_only=intent.reduce_only,
            position_ticket=sizing.position_ticket,
        )
        state = self._client.submit_order(request)
        return self._sync_order_state(intent, state, quote=quote)

    def process_quote(self, quote: ExecutionQuote) -> tuple[ExecutionUpdate, ...]:
        """Poll MT5 order state for open orders after a fresh quote update."""

        self._remember_quote(quote)
        self.get_position(quote.symbol, quote=quote)

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
            raise RuntimeError(
                "Cannot cancel an order before at least one quote has been observed "
                "for its symbol."
            )

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

    def get_position(
        self,
        symbol: str,
        *,
        quote: ExecutionQuote | None = None,
    ) -> PositionState | None:
        """Return the latest broker-source-of-truth position for one symbol."""

        if quote is not None:
            if quote.symbol != symbol:
                raise ValueError("ExecutionQuote symbol must match the requested position symbol.")
            self._remember_quote(quote)

        latest_quote = quote or self._last_quotes.get(symbol)
        if latest_quote is None:
            return self._positions.get(symbol)

        broker_position = aggregate_mt5_positions(
            self._broker_positions_for_symbol(symbol),
            broker_symbol=self._broker_symbol_for(symbol),
            position_mode=self._config.account_mode,
        )
        if broker_position is None:
            position = self._flat_position(symbol, quote=latest_quote)
        else:
            position = self._position_state_from_broker(symbol, broker_position, quote=latest_quote)
        self._positions[symbol] = position
        return position

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
                stop_loss_price=state.stop_loss_price,
                take_profit_price=state.take_profit_price,
            )
            for state in self._client.list_orders()
        ]
        return tuple(sorted(snapshots, key=lambda snapshot: snapshot.broker_order_id))

    def list_broker_positions(self) -> tuple[BrokerPositionSnapshot, ...]:
        """Return normalized broker-side net positions for reconciliation."""

        positions_by_symbol: dict[str, list[Mt5PositionState]] = {}
        for position in self._client.list_positions():
            positions_by_symbol.setdefault(position.broker_symbol, []).append(position)

        snapshots: list[BrokerPositionSnapshot] = []
        for broker_symbol, positions in positions_by_symbol.items():
            position = aggregate_mt5_positions(
                positions,
                broker_symbol=broker_symbol,
                position_mode=self._config.account_mode,
            )
            if position is None:
                continue
            snapshots.append(
                BrokerPositionSnapshot(
                    symbol=self._internal_symbol_for(position.broker_symbol),
                    timestamp=position.timestamp,
                    net_quantity=self._lots_to_base_units(position.net_volume_lots),
                    average_entry_price=position.average_entry_price,
                    position_mode=position.position_mode,
                    position_id=position.position_ticket,
                    gross_quantity=self._lots_to_base_units(position.gross_lots),
                    source_position_ids=position.source_position_tickets,
                )
            )
        return tuple(sorted(snapshots, key=lambda snapshot: snapshot.symbol))

    def describe_broker_connectivity(self) -> BrokerConnectivitySnapshot:
        """Return the current MT5 dependency snapshot for runtime health checks."""

        checked_at = datetime.now(UTC)
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
        deal_fills = self._build_deal_fills(
            intent,
            state=state,
            previous_filled_quantity=previous_filled_quantity,
            quote=quote,
        )
        if deal_fills:
            for fill in deal_fills:
                self._cash_balance = apply_fill_to_cash(self._cash_balance, fill)
                next_position = apply_fill_to_position(
                    self._positions.get(intent.symbol),
                    fill,
                    mark_price=quote.mid_price,
                )
                self._positions[intent.symbol] = next_position
            fills = deal_fills
            order_fills = order_fills + fills
        elif delta_filled_quantity > _ZERO_TOLERANCE:
            fill = self._build_fallback_fill(
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
            self.get_position(intent.symbol, quote=quote)

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

    def _build_deal_fills(
        self,
        intent: OrderIntent,
        *,
        state: Mt5OrderState,
        previous_filled_quantity: float,
        quote: ExecutionQuote,
    ) -> tuple[FillEvent, ...]:
        if not state.deals:
            return ()

        previous_filled_lots = previous_filled_quantity / self._config.base_units_per_lot
        cumulative_lots = 0.0
        fills: list[FillEvent] = []
        for deal in sorted(state.deals, key=lambda item: (item.timestamp, item.broker_deal_id)):
            cumulative_lots += deal.volume_lots
            if cumulative_lots <= previous_filled_lots + _ZERO_TOLERANCE:
                self._seen_deal_ids.add(deal.broker_deal_id)
                continue
            if deal.broker_deal_id in self._seen_deal_ids:
                continue
            self._seen_deal_ids.add(deal.broker_deal_id)
            fills.append(self._build_deal_fill(intent, deal=deal, quote=quote))
        return tuple(fills)

    def _build_deal_fill(
        self,
        intent: OrderIntent,
        *,
        deal: Mt5DealState,
        quote: ExecutionQuote,
    ) -> FillEvent:
        fill_quantity = self._lots_to_base_units(deal.volume_lots)
        return FillEvent(
            fill_id=f"mt5-deal-{deal.broker_deal_id}",
            intent_id=intent.intent_id,
            broker_order_id=deal.broker_order_id,
            symbol=intent.symbol,
            event_timestamp=deal.timestamp,
            received_timestamp=deal.timestamp,
            side=deal.side,
            fill_price=deal.price,
            fill_quantity=fill_quantity,
            commission=deal.execution_cost,
            spread_cost=abs(deal.price - quote.mid_price) * fill_quantity,
            slippage_cost=0.0,
            liquidity_flag=LiquidityFlag.UNKNOWN,
            venue=self._config.default_venue,
        )

    def _build_fallback_fill(
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
        position = self.get_position(order.intent.symbol, quote=quote)
        if position is None:
            position = self._flat_position(order.intent.symbol, quote=quote)
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

    def _flat_position(self, symbol: str, *, quote: ExecutionQuote) -> PositionState:
        return PositionState(
            symbol=symbol,
            timestamp=quote.received_timestamp,
            net_quantity=0.0,
            average_entry_price=0.0,
            mark_price=quote.mid_price,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            exposure_quote=0.0,
            position_mode=self._config.account_mode,
        )

    def _position_state_from_broker(
        self,
        symbol: str,
        broker_position: Mt5PositionState,
        *,
        quote: ExecutionQuote,
    ) -> PositionState:
        net_quantity = self._lots_to_base_units(broker_position.net_volume_lots)
        average_entry_price = (
            0.0
            if math.isclose(net_quantity, 0.0, abs_tol=_ZERO_TOLERANCE)
            else broker_position.average_entry_price
        )
        unrealized_pnl = (
            0.0
            if math.isclose(net_quantity, 0.0, abs_tol=_ZERO_TOLERANCE)
            else (quote.mid_price - average_entry_price) * net_quantity
        )
        current_position = self._positions.get(symbol)
        return PositionState(
            symbol=symbol,
            timestamp=quote.received_timestamp,
            net_quantity=net_quantity,
            average_entry_price=average_entry_price,
            mark_price=quote.mid_price,
            realized_pnl=0.0 if current_position is None else float(current_position.realized_pnl),
            unrealized_pnl=unrealized_pnl,
            exposure_quote=net_quantity * quote.mid_price,
            last_fill_id=None if current_position is None else current_position.last_fill_id,
            position_mode=broker_position.position_mode,
        )

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
        broker_position: Mt5PositionState | None,
        broker_positions: Sequence[Mt5PositionState],
    ) -> _ResolvedMt5Sizing:
        current_quantity = (
            0.0
            if broker_position is None
            else self._lots_to_base_units(broker_position.net_volume_lots)
        )
        if intent.target_position is not None:
            delta_quantity = float(intent.target_position) - current_quantity
            if math.isclose(delta_quantity, 0.0, abs_tol=_ZERO_TOLERANCE):
                return _ResolvedMt5Sizing(
                    requested_quantity=None,
                    rejection_reason="target_position resolves to zero trade quantity.",
                )
            implied_side = OrderSide.BUY if delta_quantity > 0 else OrderSide.SELL
            if intent.side is not implied_side:
                return _ResolvedMt5Sizing(
                    requested_quantity=None,
                    rejection_reason="side is inconsistent with the requested target_position.",
                )
            requested_quantity = abs(delta_quantity)
        else:
            if intent.quantity is None:
                return _ResolvedMt5Sizing(
                    requested_quantity=None,
                    rejection_reason="quantity must be provided when target_position is absent.",
                )
            requested_quantity = float(intent.quantity)

        if self._config.account_mode is PositionMode.HEDGING:
            return self._resolve_hedging_sizing(
                intent,
                requested_quantity=requested_quantity,
                current_quantity=current_quantity,
                broker_positions=broker_positions,
            )

        if intent.reduce_only:
            reducible_quantity = self._reducible_quantity(current_quantity, side=intent.side)
            if reducible_quantity <= 0:
                return _ResolvedMt5Sizing(
                    requested_quantity=None,
                    rejection_reason="reduce_only order would not reduce the current position.",
                )
            if requested_quantity - reducible_quantity > _ZERO_TOLERANCE:
                return _ResolvedMt5Sizing(
                    requested_quantity=requested_quantity,
                    rejection_reason=(
                        "reduce_only order quantity exceeds the reducible position size."
                    ),
                )
        return _ResolvedMt5Sizing(requested_quantity=requested_quantity)

    def _resolve_hedging_sizing(
        self,
        intent: OrderIntent,
        *,
        requested_quantity: float,
        current_quantity: float,
        broker_positions: Sequence[Mt5PositionState],
    ) -> _ResolvedMt5Sizing:
        reduces_net = self._reducible_quantity(current_quantity, side=intent.side) > 0
        if not intent.reduce_only:
            if intent.target_position is not None and reduces_net:
                return _ResolvedMt5Sizing(
                    requested_quantity=requested_quantity,
                    rejection_reason=(
                        "hedging target_position reductions require reduce_only so MT5 can "
                        "close a broker position ticket instead of opening an opposite hedge."
                    ),
                )
            return _ResolvedMt5Sizing(requested_quantity=requested_quantity)

        reducible_positions = tuple(
            position
            for position in broker_positions
            if _position_is_reduced_by(position, intent.side)
        )
        if not reducible_positions:
            return _ResolvedMt5Sizing(
                requested_quantity=None,
                rejection_reason="reduce_only order would not reduce any broker position ticket.",
            )
        if len(reducible_positions) > 1:
            return _ResolvedMt5Sizing(
                requested_quantity=requested_quantity,
                rejection_reason=(
                    "reduce_only hedging order is ambiguous across multiple broker position "
                    "tickets."
                ),
            )

        position = reducible_positions[0]
        if position.position_ticket is None:
            return _ResolvedMt5Sizing(
                requested_quantity=requested_quantity,
                rejection_reason="reduce_only hedging order requires a broker position ticket.",
            )
        reducible_quantity = self._lots_to_base_units(abs(position.net_volume_lots))
        if requested_quantity - reducible_quantity > _ZERO_TOLERANCE:
            return _ResolvedMt5Sizing(
                requested_quantity=requested_quantity,
                rejection_reason="reduce_only order quantity exceeds the broker position ticket.",
            )
        return _ResolvedMt5Sizing(
            requested_quantity=requested_quantity,
            position_ticket=position.position_ticket,
        )

    def _missing_required_protection(
        self,
        intent: OrderIntent,
        *,
        requested_quantity: float,
        broker_position: Mt5PositionState | None,
    ) -> tuple[str, ...]:
        if not self._trade_increases_exposure(
            intent,
            requested_quantity=requested_quantity,
            broker_position=broker_position,
        ):
            return ()

        missing: list[str] = []
        if self._config.require_stop_loss and intent.stop_loss_price is None:
            missing.append("stop_loss_price")
        if self._config.require_take_profit and intent.take_profit_price is None:
            missing.append("take_profit_price")
        return tuple(missing)

    def _trade_increases_exposure(
        self,
        intent: OrderIntent,
        *,
        requested_quantity: float,
        broker_position: Mt5PositionState | None,
    ) -> bool:
        if intent.reduce_only:
            return False
        if requested_quantity <= _ZERO_TOLERANCE:
            return False
        if self._config.account_mode is PositionMode.HEDGING:
            return True

        current_quantity = (
            0.0
            if broker_position is None
            else self._lots_to_base_units(broker_position.net_volume_lots)
        )
        signed_quantity = (
            requested_quantity if intent.side is OrderSide.BUY else -requested_quantity
        )
        next_quantity = current_quantity + signed_quantity
        return abs(next_quantity) - abs(current_quantity) > _ZERO_TOLERANCE

    @staticmethod
    def _reducible_quantity(current_quantity: float, *, side: OrderSide) -> float:
        if side is OrderSide.BUY and current_quantity < 0:
            return abs(current_quantity)
        if side is OrderSide.SELL and current_quantity > 0:
            return abs(current_quantity)
        return 0.0

    def _base_units_to_lots(self, quantity: float) -> float:
        raw_lots = float(quantity) / self._config.base_units_per_lot
        step_count = math.floor((raw_lots + _ZERO_TOLERANCE) / self._config.volume_step_lots)
        normalized_lots = round(step_count * self._config.volume_step_lots, 8)
        if normalized_lots < self._config.min_volume_lots - _ZERO_TOLERANCE:
            raise ValueError(
                "Requested base-unit quantity is smaller than the broker minimum lot size."
            )
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

    def _broker_positions_for_symbol(self, symbol: str) -> tuple[Mt5PositionState, ...]:
        broker_symbol = self._broker_symbol_for(symbol)
        positions = tuple(
            position
            for position in self._client.list_positions()
            if position.broker_symbol == broker_symbol
        )
        if positions:
            return positions
        aggregate_position = self._client.get_position(broker_symbol)
        return () if aggregate_position is None else (aggregate_position,)

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


def _source_position_tickets(positions: Sequence[Mt5PositionState]) -> tuple[str, ...]:
    tickets: list[str] = []
    for position in positions:
        if position.position_ticket is not None:
            tickets.append(position.position_ticket)
        tickets.extend(position.source_position_tickets)
    return tuple(sorted(set(tickets)))


def _weighted_entry_price(
    positions: Sequence[Mt5PositionState],
    *,
    net_volume_lots: float,
) -> float:
    if math.isclose(net_volume_lots, 0.0, abs_tol=_ZERO_TOLERANCE):
        return 0.0
    net_sign = 1.0 if net_volume_lots > 0 else -1.0
    same_direction = [
        position
        for position in positions
        if not math.isclose(position.net_volume_lots, 0.0, abs_tol=_ZERO_TOLERANCE)
        and math.copysign(1.0, position.net_volume_lots) == net_sign
    ]
    total_volume = sum(abs(position.net_volume_lots) for position in same_direction)
    if total_volume <= 0:
        return 0.0
    weighted_notional = sum(
        abs(position.net_volume_lots) * position.average_entry_price
        for position in same_direction
    )
    return weighted_notional / total_volume


def _position_is_reduced_by(position: Mt5PositionState, side: OrderSide) -> bool:
    if side is OrderSide.BUY and position.net_volume_lots < 0:
        return True
    if side is OrderSide.SELL and position.net_volume_lots > 0:
        return True
    return False
