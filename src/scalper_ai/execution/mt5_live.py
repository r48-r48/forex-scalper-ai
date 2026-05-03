"""MT5-oriented live execution adapter skeleton with normalized snapshots."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Protocol, cast

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
_MT5_TRADE_MODE_DISABLED = 0
_MT5_TRADE_MODE_LONG_ONLY = 1
_MT5_TRADE_MODE_SHORT_ONLY = 2
_MT5_TRADE_MODE_CLOSE_ONLY = 3
_MT5_TRADE_MODE_FULL = 4
_MT5_FILLING_FOK_FLAG = 1
_MT5_FILLING_IOC_FLAG = 2
_MT5_TRADE_EXECUTION_REQUEST = 0
_MT5_TRADE_EXECUTION_INSTANT = 1
_MT5_TRADE_EXECUTION_MARKET = 2
_MT5_TRADE_EXECUTION_EXCHANGE = 3


@dataclass(frozen=True)
class _ResolvedMt5Sizing:
    requested_quantity: float | None
    rejection_reason: str | None = None
    position_ticket: str | None = None


@dataclass(frozen=True)
class _PreparedMt5Prices:
    limit_price: float | None
    stop_price: float | None
    stop_loss_price: float | None
    take_profit_price: float | None
    time_in_force: TimeInForce | None


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
class Mt5SymbolSpec:
    """Broker symbol trading constraints used for conservative MT5 normalization."""

    broker_symbol: str
    base_units_per_lot: float = 100_000.0
    volume_min_lots: float = 0.01
    volume_step_lots: float = 0.01
    volume_max_lots: float | None = None
    digits: int | None = None
    point: float | None = None
    stops_level_points: int | None = None
    freeze_level_points: int | None = None
    trade_mode: int | None = None
    filling_mode: int | None = None
    trade_execution_mode: int | None = None

    def __post_init__(self) -> None:
        if not self.broker_symbol.strip():
            raise ValueError("broker_symbol must be non-empty.")
        if self.base_units_per_lot <= 0:
            raise ValueError("base_units_per_lot must be greater than zero.")
        if self.volume_min_lots <= 0:
            raise ValueError("volume_min_lots must be greater than zero.")
        if self.volume_step_lots <= 0:
            raise ValueError("volume_step_lots must be greater than zero.")
        if self.volume_max_lots is not None and self.volume_max_lots <= 0:
            raise ValueError("volume_max_lots must be greater than zero when provided.")
        if (
            self.volume_max_lots is not None
            and self.volume_max_lots < self.volume_min_lots
        ):
            raise ValueError("volume_max_lots must not be smaller than volume_min_lots.")
        if self.digits is not None and self.digits < 0:
            raise ValueError("digits must be non-negative when provided.")
        if self.point is not None and self.point <= 0:
            raise ValueError("point must be greater than zero when provided.")
        if self.stops_level_points is not None and self.stops_level_points < 0:
            raise ValueError("stops_level_points must be non-negative when provided.")
        if self.freeze_level_points is not None and self.freeze_level_points < 0:
            raise ValueError("freeze_level_points must be non-negative when provided.")
        for value_name, value in {
            "trade_mode": self.trade_mode,
            "filling_mode": self.filling_mode,
            "trade_execution_mode": self.trade_execution_mode,
        }.items():
            if value is not None and value < 0:
                raise ValueError(f"{value_name} must be non-negative when provided.")


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
class Mt5ProtectionUpdateRequest:
    """Broker-specific request to repair native SL/TP protection on one MT5 position."""

    broker_symbol: str
    position_ticket: str
    submitted_at: datetime
    stop_loss_price: float | None = None
    take_profit_price: float | None = None

    def __post_init__(self) -> None:
        if not self.broker_symbol.strip():
            raise ValueError("broker_symbol must be non-empty.")
        if not self.position_ticket.strip():
            raise ValueError("position_ticket must be non-empty.")
        if self.submitted_at.tzinfo is None or self.submitted_at.utcoffset() is None:
            raise ValueError("submitted_at must be timezone-aware.")
        if self.stop_loss_price is None and self.take_profit_price is None:
            raise ValueError("At least one protective price must be provided.")
        for value_name, value in {
            "stop_loss_price": self.stop_loss_price,
            "take_profit_price": self.take_profit_price,
        }.items():
            if value is not None and (value <= 0 or not math.isfinite(value)):
                raise ValueError(f"{value_name} must be positive and finite when provided.")


@dataclass(frozen=True)
class Mt5PendingOrderModifyRequest:
    """Broker-specific request to modify one open MT5 pending order."""

    broker_order_id: str
    broker_symbol: str
    side: OrderSide
    order_type: OrderType
    submitted_at: datetime
    time_in_force: TimeInForce | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None

    def __post_init__(self) -> None:
        if not self.broker_order_id.strip():
            raise ValueError("broker_order_id must be non-empty.")
        if not self.broker_symbol.strip():
            raise ValueError("broker_symbol must be non-empty.")
        if self.submitted_at.tzinfo is None or self.submitted_at.utcoffset() is None:
            raise ValueError("submitted_at must be timezone-aware.")
        if self.order_type is OrderType.MARKET:
            raise ValueError("Only pending orders can be modified with TRADE_ACTION_MODIFY.")
        for value_name, value in {
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
            "stop_loss_price": self.stop_loss_price,
            "take_profit_price": self.take_profit_price,
        }.items():
            if value is not None and (value <= 0 or not math.isfinite(value)):
                raise ValueError(f"{value_name} must be positive and finite when provided.")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("Limit order modify requests require limit_price.")
        if self.order_type is OrderType.STOP and self.stop_price is None:
            raise ValueError("Stop order modify requests require stop_price.")
        if self.order_type is OrderType.STOP_LIMIT and (
            self.stop_price is None or self.limit_price is None
        ):
            raise ValueError("Stop-limit modify requests require stop_price and limit_price.")


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
    limit_price: float | None = None
    stop_price: float | None = None
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
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
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
    stop_loss_price: float | None = None
    take_profit_price: float | None = None

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
        for value_name, value in {
            "stop_loss_price": self.stop_loss_price,
            "take_profit_price": self.take_profit_price,
        }.items():
            if value is not None and (value <= 0 or not math.isfinite(value)):
                raise ValueError(f"{value_name} must be a positive finite value when provided.")

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
        stop_loss_price=_shared_position_protective_price(
            selected,
            field_name="stop_loss_price",
        ),
        take_profit_price=_shared_position_protective_price(
            selected,
            field_name="take_profit_price",
        ),
    )


class Mt5ExecutionClientProtocol(Protocol):
    """Minimal MT5 client surface required by the live execution adapter skeleton."""

    def submit_order(self, request: Mt5OrderRequest) -> Mt5OrderState:
        """Submit one MT5 order request and return the normalized broker state."""

    def cancel_order(self, broker_order_id: str, *, timestamp: datetime) -> Mt5OrderState:
        """Cancel one live broker order and return the latest broker state."""

    def modify_position_protection(
        self,
        request: Mt5ProtectionUpdateRequest,
    ) -> Mt5PositionState:
        """Modify broker-side stop-loss / take-profit protection for one position."""

    def modify_pending_order(
        self,
        request: Mt5PendingOrderModifyRequest,
    ) -> Mt5OrderState:
        """Modify one open broker-side pending order."""

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


class _Mt5ConnectionSnapshotProtocol(Protocol):
    reconnect_enabled: bool
    reconnect_attempt_count: int
    circuit_breaker_open: bool
    last_reconnect_at: datetime | None
    last_error: str | None


def _describe_mt5_connection(
    client: object,
) -> _Mt5ConnectionSnapshotProtocol | None:
    describe_connection = getattr(client, "describe_connection", None)
    if not callable(describe_connection):
        return None
    return cast(_Mt5ConnectionSnapshotProtocol, describe_connection())


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
        self._symbol_specs: dict[str, Mt5SymbolSpec] = {}
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
        broker_symbol = self._broker_symbol_for(intent.symbol)
        broker_positions = self._broker_positions_for_symbol(intent.symbol)
        broker_position = aggregate_mt5_positions(
            broker_positions,
            broker_symbol=broker_symbol,
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

        spec = self._symbol_spec_for(broker_symbol)
        trade_rejection = self._symbol_trade_rejection(
            intent,
            spec,
            requested_quantity=sizing.requested_quantity,
            broker_position=broker_position,
        )
        if trade_rejection is not None:
            return self._build_rejected_update(
                intent,
                quote=quote,
                requested_quantity=sizing.requested_quantity,
                reason=trade_rejection,
            )
        try:
            prepared_prices = self._prepare_symbol_constrained_prices(
                intent,
                quote=quote,
                spec=spec,
            )
        except ValueError as exc:
            return self._build_rejected_update(
                intent,
                quote=quote,
                requested_quantity=sizing.requested_quantity,
                reason=str(exc),
            )

        try:
            volume_lots = self._base_units_to_lots(
                sizing.requested_quantity,
                broker_symbol=broker_symbol,
            )
        except ValueError as exc:
            return self._build_rejected_update(
                intent,
                quote=quote,
                requested_quantity=sizing.requested_quantity,
                reason=str(exc),
            )

        request = Mt5OrderRequest(
            client_order_id=intent.intent_id,
            broker_symbol=broker_symbol,
            side=intent.side,
            order_type=intent.order_type,
            submitted_at=quote.received_timestamp,
            volume_lots=volume_lots,
            time_in_force=prepared_prices.time_in_force,
            limit_price=prepared_prices.limit_price,
            stop_price=prepared_prices.stop_price,
            stop_loss_price=prepared_prices.stop_loss_price,
            take_profit_price=prepared_prices.take_profit_price,
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

    def modify_pending_order(
        self,
        broker_order_id: str,
        *,
        quote: ExecutionQuote,
        timestamp: datetime | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        stop_loss_price: float | None = None,
        take_profit_price: float | None = None,
        time_in_force: TimeInForce | None = None,
    ) -> ExecutionUpdate:
        """Modify an open MT5 pending order with broker stops/freeze guardrails."""

        existing_order = self._orders.get(broker_order_id)
        if existing_order is None:
            raise KeyError(f"Unknown broker_order_id: {broker_order_id}")
        if not existing_order.is_open:
            raise ValueError("Only open pending orders can be modified.")
        intent = existing_order.intent
        if intent.order_type is OrderType.MARKET:
            raise ValueError("Only pending orders can be modified.")
        if quote.symbol != intent.symbol:
            raise ValueError("Modify quote symbol must match the pending order symbol.")

        self._remember_quote(quote)
        broker_symbol = self._broker_symbol_for(intent.symbol)
        current_state = self._client.get_order(broker_order_id)
        if current_state is None:
            raise KeyError(f"MT5 broker no longer exposes pending order: {broker_order_id}")

        spec = self._symbol_spec_for(broker_symbol)
        broker_position = aggregate_mt5_positions(
            self._broker_positions_for_symbol(intent.symbol),
            broker_symbol=broker_symbol,
            position_mode=self._config.account_mode,
        )
        effective_intent = intent.model_copy(
            update={
                "limit_price": (
                    limit_price
                    if limit_price is not None
                    else current_state.limit_price or intent.limit_price
                ),
                "stop_price": (
                    stop_price
                    if stop_price is not None
                    else current_state.stop_price or intent.stop_price
                ),
                "stop_loss_price": (
                    stop_loss_price
                    if stop_loss_price is not None
                    else current_state.stop_loss_price or intent.stop_loss_price
                ),
                "take_profit_price": (
                    take_profit_price
                    if take_profit_price is not None
                    else current_state.take_profit_price or intent.take_profit_price
                ),
                "time_in_force": (
                    time_in_force if time_in_force is not None else intent.time_in_force
                ),
            }
        )
        trade_rejection = self._symbol_trade_rejection(
            effective_intent,
            spec,
            requested_quantity=existing_order.remaining_quantity,
            broker_position=broker_position,
        )
        if trade_rejection is not None:
            raise ValueError(trade_rejection)

        prepared_prices = self._prepare_symbol_constrained_prices(
            effective_intent,
            quote=quote,
            spec=spec,
        )
        _validate_pending_entry_distance_for_level(
            effective_intent,
            quote=quote,
            spec=spec,
            prices=prepared_prices,
            minimum_distance=_minimum_freeze_distance(spec),
            level_name="freeze",
        )
        _validate_protective_distances_for_side(
            effective_intent.side,
            quote=quote,
            spec=spec,
            stop_loss_price=prepared_prices.stop_loss_price,
            take_profit_price=prepared_prices.take_profit_price,
            minimum_distance=_minimum_freeze_distance(spec),
            level_name="freeze",
        )

        modify_request = Mt5PendingOrderModifyRequest(
            broker_order_id=broker_order_id,
            broker_symbol=broker_symbol,
            side=effective_intent.side,
            order_type=effective_intent.order_type,
            submitted_at=timestamp or quote.received_timestamp,
            time_in_force=prepared_prices.time_in_force,
            limit_price=prepared_prices.limit_price,
            stop_price=prepared_prices.stop_price,
            stop_loss_price=prepared_prices.stop_loss_price,
            take_profit_price=prepared_prices.take_profit_price,
        )
        state = self._client.modify_pending_order(modify_request)
        return self._sync_order_state(intent, state, quote=quote)

    def repair_position_protection(
        self,
        symbol: str,
        *,
        position_id: str,
        stop_loss_price: float | None,
        take_profit_price: float | None,
        quote: ExecutionQuote,
        timestamp: datetime | None = None,
    ) -> BrokerPositionSnapshot:
        """Repair native broker SL/TP protection for one known MT5 position ticket."""

        if quote.symbol != symbol:
            raise ValueError("Protection repair quote symbol must match the target symbol.")
        if not position_id.strip():
            raise ValueError("position_id must be non-empty for MT5 protection repair.")
        self._remember_quote(quote)

        broker_symbol = self._broker_symbol_for(symbol)
        position = self._broker_position_by_ticket(
            broker_symbol,
            position_id=position_id,
        )
        if position is None:
            raise KeyError(f"Unknown MT5 position ticket for protection repair: {position_id}")
        if math.isclose(position.net_volume_lots, 0.0, abs_tol=_ZERO_TOLERANCE):
            raise ValueError("Cannot repair protection for a flat MT5 position.")

        spec = self._symbol_spec_for(broker_symbol)
        _validate_symbol_allows_protection_update(spec)
        resolved_stop_loss = _normalize_optional_price(
            stop_loss_price if stop_loss_price is not None else position.stop_loss_price,
            spec=spec,
        )
        resolved_take_profit = _normalize_optional_price(
            (
                take_profit_price
                if take_profit_price is not None
                else position.take_profit_price
            ),
            spec=spec,
        )
        if resolved_stop_loss is None and resolved_take_profit is None:
            raise ValueError("Protection repair requires at least one SL/TP target.")

        side = OrderSide.BUY if position.net_volume_lots > 0 else OrderSide.SELL
        _validate_protective_distances_for_side(
            side,
            quote=quote,
            spec=spec,
            stop_loss_price=resolved_stop_loss,
            take_profit_price=resolved_take_profit,
            minimum_distance=_minimum_stop_distance(spec),
            level_name="stops",
        )
        _validate_protective_distances_for_side(
            side,
            quote=quote,
            spec=spec,
            stop_loss_price=resolved_stop_loss,
            take_profit_price=resolved_take_profit,
            minimum_distance=_minimum_freeze_distance(spec),
            level_name="freeze",
        )

        update_request = Mt5ProtectionUpdateRequest(
            broker_symbol=broker_symbol,
            position_ticket=position_id,
            submitted_at=timestamp or quote.received_timestamp,
            stop_loss_price=resolved_stop_loss,
            take_profit_price=resolved_take_profit,
        )
        updated_position = self._client.modify_position_protection(update_request)
        self._positions[symbol] = self._position_state_from_broker(
            symbol,
            updated_position,
            quote=quote,
        )
        return BrokerPositionSnapshot(
            symbol=symbol,
            timestamp=updated_position.timestamp,
            net_quantity=self._lots_to_base_units(
                updated_position.net_volume_lots,
                broker_symbol=updated_position.broker_symbol,
            ),
            average_entry_price=updated_position.average_entry_price,
            position_mode=updated_position.position_mode,
            position_id=updated_position.position_ticket,
            gross_quantity=self._lots_to_base_units(
                updated_position.gross_lots,
                broker_symbol=updated_position.broker_symbol,
            ),
            source_position_ids=updated_position.source_position_tickets,
            stop_loss_price=updated_position.stop_loss_price,
            take_profit_price=updated_position.take_profit_price,
        )

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
                requested_quantity=self._lots_to_base_units(
                    state.requested_volume_lots,
                    broker_symbol=state.broker_symbol,
                ),
                filled_quantity=self._lots_to_base_units(
                    state.filled_volume_lots,
                    broker_symbol=state.broker_symbol,
                ),
                remaining_quantity=self._lots_to_base_units(
                    state.remaining_volume_lots,
                    broker_symbol=state.broker_symbol,
                ),
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
                    net_quantity=self._lots_to_base_units(
                        position.net_volume_lots,
                        broker_symbol=position.broker_symbol,
                    ),
                    average_entry_price=position.average_entry_price,
                    position_mode=position.position_mode,
                    position_id=position.position_ticket,
                    gross_quantity=self._lots_to_base_units(
                        position.gross_lots,
                        broker_symbol=position.broker_symbol,
                    ),
                    source_position_ids=position.source_position_tickets,
                    stop_loss_price=position.stop_loss_price,
                    take_profit_price=position.take_profit_price,
                )
            )
        return tuple(sorted(snapshots, key=lambda snapshot: snapshot.symbol))

    def describe_broker_connectivity(self) -> BrokerConnectivitySnapshot:
        """Return the current MT5 dependency snapshot for runtime health checks."""

        checked_at = datetime.now(UTC)
        connected = self._client.is_connected()
        latest_broker_timestamp = self._latest_broker_snapshot_timestamp()
        connection_snapshot = _describe_mt5_connection(self._client)
        return BrokerConnectivitySnapshot(
            venue=self._config.default_venue,
            checked_at=checked_at,
            connected=connected,
            last_snapshot_at=latest_broker_timestamp,
            latency_ms=self._client.ping_latency_ms(),
            reconnect_enabled=(
                None if connection_snapshot is None else connection_snapshot.reconnect_enabled
            ),
            reconnect_attempt_count=(
                None
                if connection_snapshot is None
                else connection_snapshot.reconnect_attempt_count
            ),
            circuit_breaker_open=(
                None if connection_snapshot is None else connection_snapshot.circuit_breaker_open
            ),
            last_reconnect_at=(
                None if connection_snapshot is None else connection_snapshot.last_reconnect_at
            ),
            last_error=None if connection_snapshot is None else connection_snapshot.last_error,
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
        filled_quantity = self._lots_to_base_units(
            state.filled_volume_lots,
            broker_symbol=state.broker_symbol,
        )
        remaining_quantity = self._lots_to_base_units(
            state.remaining_volume_lots,
            broker_symbol=state.broker_symbol,
        )
        requested_quantity = self._lots_to_base_units(
            state.requested_volume_lots,
            broker_symbol=state.broker_symbol,
        )
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

        previous_filled_lots = previous_filled_quantity / self._symbol_spec_for(
            state.broker_symbol
        ).base_units_per_lot
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
        fill_quantity = self._lots_to_base_units(
            deal.volume_lots,
            broker_symbol=deal.broker_symbol,
        )
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
            broker_deal_id=deal.broker_deal_id,
            broker_symbol=deal.broker_symbol,
            broker_position_id=deal.position_ticket,
            broker_commission=deal.commission,
            broker_fee=deal.fee,
            broker_swap=deal.swap,
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
        net_quantity = self._lots_to_base_units(
            broker_position.net_volume_lots,
            broker_symbol=broker_position.broker_symbol,
        )
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
            else self._lots_to_base_units(
                broker_position.net_volume_lots,
                broker_symbol=broker_position.broker_symbol,
            )
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
        reducible_quantity = self._lots_to_base_units(
            abs(position.net_volume_lots),
            broker_symbol=position.broker_symbol,
        )
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
            else self._lots_to_base_units(
                broker_position.net_volume_lots,
                broker_symbol=broker_position.broker_symbol,
            )
        )
        signed_quantity = (
            requested_quantity if intent.side is OrderSide.BUY else -requested_quantity
        )
        next_quantity = current_quantity + signed_quantity
        return abs(next_quantity) - abs(current_quantity) > _ZERO_TOLERANCE

    def _symbol_trade_rejection(
        self,
        intent: OrderIntent,
        spec: Mt5SymbolSpec,
        *,
        requested_quantity: float,
        broker_position: Mt5PositionState | None,
    ) -> str | None:
        if spec.trade_mode is None:
            return None
        if spec.trade_mode == _MT5_TRADE_MODE_DISABLED:
            return "MT5 symbol trade_mode disables trading for this symbol."
        exposure_increasing = self._trade_increases_exposure(
            intent,
            requested_quantity=requested_quantity,
            broker_position=broker_position,
        )
        if (
            spec.trade_mode == _MT5_TRADE_MODE_LONG_ONLY
            and intent.side is OrderSide.SELL
            and exposure_increasing
        ):
            return "MT5 symbol trade_mode only allows long exposure increases."
        if (
            spec.trade_mode == _MT5_TRADE_MODE_SHORT_ONLY
            and intent.side is OrderSide.BUY
            and exposure_increasing
        ):
            return "MT5 symbol trade_mode only allows short exposure increases."
        if spec.trade_mode == _MT5_TRADE_MODE_CLOSE_ONLY and exposure_increasing:
            return "MT5 symbol trade_mode only allows position-closing orders."
        if spec.trade_mode in {
            _MT5_TRADE_MODE_LONG_ONLY,
            _MT5_TRADE_MODE_SHORT_ONLY,
            _MT5_TRADE_MODE_CLOSE_ONLY,
            _MT5_TRADE_MODE_FULL,
        }:
            return None
        return f"Unsupported MT5 symbol trade_mode: {spec.trade_mode}."

    def _prepare_symbol_constrained_prices(
        self,
        intent: OrderIntent,
        *,
        quote: ExecutionQuote,
        spec: Mt5SymbolSpec,
    ) -> _PreparedMt5Prices:
        prepared = _PreparedMt5Prices(
            limit_price=_normalize_optional_price(intent.limit_price, spec=spec),
            stop_price=_normalize_optional_price(intent.stop_price, spec=spec),
            stop_loss_price=_normalize_optional_price(intent.stop_loss_price, spec=spec),
            take_profit_price=_normalize_optional_price(intent.take_profit_price, spec=spec),
            time_in_force=_resolve_time_in_force(intent, spec=spec),
        )
        _validate_pending_entry_distance(intent, quote=quote, spec=spec, prices=prepared)
        _validate_protective_price_distance(intent, quote=quote, spec=spec, prices=prepared)
        return prepared

    @staticmethod
    def _reducible_quantity(current_quantity: float, *, side: OrderSide) -> float:
        if side is OrderSide.BUY and current_quantity < 0:
            return abs(current_quantity)
        if side is OrderSide.SELL and current_quantity > 0:
            return abs(current_quantity)
        return 0.0

    def _base_units_to_lots(self, quantity: float, *, broker_symbol: str) -> float:
        spec = self._symbol_spec_for(broker_symbol)
        raw_lots = Decimal(str(float(quantity))) / Decimal(str(spec.base_units_per_lot))
        normalized_lots = _quantize_lots_down(raw_lots, step=spec.volume_step_lots)
        if normalized_lots < Decimal(str(spec.volume_min_lots)):
            raise ValueError(
                "Requested base-unit quantity is smaller than the broker minimum lot size."
            )
        if (
            spec.volume_max_lots is not None
            and normalized_lots > Decimal(str(spec.volume_max_lots))
        ):
            raise ValueError("Requested base-unit quantity exceeds the broker maximum lot size.")
        return float(normalized_lots)

    def _lots_to_base_units(self, volume_lots: float, *, broker_symbol: str | None = None) -> float:
        base_units_per_lot = (
            self._config.base_units_per_lot
            if broker_symbol is None
            else self._symbol_spec_for(broker_symbol).base_units_per_lot
        )
        return float(volume_lots) * base_units_per_lot

    def _symbol_spec_for(self, broker_symbol: str) -> Mt5SymbolSpec:
        cached = self._symbol_specs.get(broker_symbol)
        if cached is not None:
            return cached
        spec_provider = getattr(self._client, "get_symbol_spec", None)
        if callable(spec_provider):
            spec = spec_provider(broker_symbol)
            if spec is not None:
                self._symbol_specs[broker_symbol] = spec
                return spec
        spec = Mt5SymbolSpec(
            broker_symbol=broker_symbol,
            base_units_per_lot=self._config.base_units_per_lot,
            volume_min_lots=self._config.min_volume_lots,
            volume_step_lots=self._config.volume_step_lots,
        )
        self._symbol_specs[broker_symbol] = spec
        return spec

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

    def _broker_position_by_ticket(
        self,
        broker_symbol: str,
        *,
        position_id: str,
    ) -> Mt5PositionState | None:
        for position in self._client.list_positions():
            if (
                position.broker_symbol == broker_symbol
                and position.position_ticket == position_id
            ):
                return position
        aggregate_position = self._client.get_position(broker_symbol)
        if (
            aggregate_position is not None
            and aggregate_position.position_ticket == position_id
        ):
            return aggregate_position
        return None

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


def _shared_position_protective_price(
    positions: Sequence[Mt5PositionState],
    *,
    field_name: str,
) -> float | None:
    if not positions:
        return None
    first_value = getattr(positions[0], field_name)
    if first_value is None:
        return None
    for position in positions[1:]:
        value = getattr(position, field_name)
        if value is None or not math.isclose(value, first_value, abs_tol=_ZERO_TOLERANCE):
            return None
    return float(first_value)


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


def _normalize_optional_price(value: float | None, *, spec: Mt5SymbolSpec) -> float | None:
    if value is None:
        return None
    price = float(value)
    if price <= 0 or not math.isfinite(price):
        raise ValueError("MT5 order prices must be positive and finite.")
    return _quantize_price(price, spec=spec)


def _quantize_price(price: float, *, spec: Mt5SymbolSpec) -> float:
    tick_size = _price_tick_size(spec)
    if tick_size is None:
        return price
    price_decimal = Decimal(str(price))
    ticks = (price_decimal / tick_size).to_integral_value(rounding=ROUND_HALF_UP)
    return float(ticks * tick_size)


def _price_tick_size(spec: Mt5SymbolSpec) -> Decimal | None:
    if spec.point is not None:
        return Decimal(str(spec.point))
    if spec.digits is not None:
        return Decimal("1").scaleb(-spec.digits)
    return None


def _resolve_time_in_force(
    intent: OrderIntent,
    *,
    spec: Mt5SymbolSpec,
) -> TimeInForce | None:
    requested = intent.time_in_force
    if intent.order_type is not OrderType.MARKET:
        if requested in {TimeInForce.FOK, TimeInForce.IOC}:
            raise ValueError(
                "MT5 pending orders require return filling; use GTC, DAY, or unset "
                "time_in_force."
            )
        return requested
    if requested in {TimeInForce.FOK, TimeInForce.IOC}:
        if not _symbol_supports_filling(spec, requested):
            raise ValueError(
                f"MT5 symbol filling_mode does not support {requested.value}."
            )
        return requested
    if requested is not None:
        return requested
    if spec.trade_execution_mode in {
        _MT5_TRADE_EXECUTION_REQUEST,
        _MT5_TRADE_EXECUTION_INSTANT,
        _MT5_TRADE_EXECUTION_EXCHANGE,
    }:
        return None
    if spec.filling_mode is not None:
        if _symbol_supports_filling(spec, TimeInForce.IOC):
            return TimeInForce.IOC
        if _symbol_supports_filling(spec, TimeInForce.FOK):
            return TimeInForce.FOK
    if (
        spec.trade_execution_mode == _MT5_TRADE_EXECUTION_MARKET
        and spec.filling_mode is not None
    ):
        raise ValueError(
            "MT5 market execution symbol requires a supported FOK or IOC filling mode."
        )
    return None


def _symbol_supports_filling(spec: Mt5SymbolSpec, time_in_force: TimeInForce) -> bool:
    if spec.trade_execution_mode in {
        _MT5_TRADE_EXECUTION_REQUEST,
        _MT5_TRADE_EXECUTION_INSTANT,
    }:
        return True
    if spec.filling_mode is None:
        return True
    if time_in_force is TimeInForce.FOK:
        return bool(spec.filling_mode & _MT5_FILLING_FOK_FLAG)
    if time_in_force is TimeInForce.IOC:
        return bool(spec.filling_mode & _MT5_FILLING_IOC_FLAG)
    return True


def _validate_symbol_allows_protection_update(spec: Mt5SymbolSpec) -> None:
    if spec.trade_mode is None:
        return
    if spec.trade_mode == _MT5_TRADE_MODE_DISABLED:
        raise ValueError("MT5 symbol trade_mode disables protection updates.")
    if spec.trade_mode in {
        _MT5_TRADE_MODE_LONG_ONLY,
        _MT5_TRADE_MODE_SHORT_ONLY,
        _MT5_TRADE_MODE_CLOSE_ONLY,
        _MT5_TRADE_MODE_FULL,
    }:
        return
    raise ValueError(f"Unsupported MT5 symbol trade_mode: {spec.trade_mode}.")


def _validate_pending_entry_distance(
    intent: OrderIntent,
    *,
    quote: ExecutionQuote,
    spec: Mt5SymbolSpec,
    prices: _PreparedMt5Prices,
) -> None:
    _validate_pending_entry_distance_for_level(
        intent,
        quote=quote,
        spec=spec,
        prices=prices,
        minimum_distance=_minimum_stop_distance(spec),
        level_name="stops",
    )


def _validate_pending_entry_distance_for_level(
    intent: OrderIntent,
    *,
    quote: ExecutionQuote,
    spec: Mt5SymbolSpec,
    prices: _PreparedMt5Prices,
    minimum_distance: float | None,
    level_name: str,
) -> None:
    if minimum_distance is None:
        return
    if intent.order_type is OrderType.LIMIT:
        if prices.limit_price is None:
            raise ValueError("Limit orders require limit_price.")
        distance = (
            quote.ask - prices.limit_price
            if intent.side is OrderSide.BUY
            else prices.limit_price - quote.bid
        )
        _raise_if_inside_broker_level(
            "limit_price",
            distance=distance,
            minimum_distance=minimum_distance,
            broker_symbol=spec.broker_symbol,
            level_name=level_name,
        )
    elif intent.order_type in {OrderType.STOP, OrderType.STOP_LIMIT}:
        if prices.stop_price is None:
            raise ValueError("Stop orders require stop_price.")
        distance = (
            prices.stop_price - quote.ask
            if intent.side is OrderSide.BUY
            else quote.bid - prices.stop_price
        )
        _raise_if_inside_broker_level(
            "stop_price",
            distance=distance,
            minimum_distance=minimum_distance,
            broker_symbol=spec.broker_symbol,
            level_name=level_name,
        )


def _validate_protective_price_distance(
    intent: OrderIntent,
    *,
    quote: ExecutionQuote,
    spec: Mt5SymbolSpec,
    prices: _PreparedMt5Prices,
) -> None:
    _validate_protective_distances_for_side(
        intent.side,
        quote=quote,
        spec=spec,
        stop_loss_price=prices.stop_loss_price,
        take_profit_price=prices.take_profit_price,
        minimum_distance=_minimum_stop_distance(spec),
        level_name="stops",
    )


def _validate_protective_distances_for_side(
    side: OrderSide,
    *,
    quote: ExecutionQuote,
    spec: Mt5SymbolSpec,
    stop_loss_price: float | None,
    take_profit_price: float | None,
    minimum_distance: float | None,
    level_name: str,
) -> None:
    if minimum_distance is None:
        return
    if stop_loss_price is not None:
        distance = (
            quote.bid - stop_loss_price
            if side is OrderSide.BUY
            else stop_loss_price - quote.ask
        )
        _raise_if_inside_broker_level(
            "stop_loss_price",
            distance=distance,
            minimum_distance=minimum_distance,
            broker_symbol=spec.broker_symbol,
            level_name=level_name,
        )
    if take_profit_price is not None:
        distance = (
            take_profit_price - quote.bid
            if side is OrderSide.BUY
            else quote.ask - take_profit_price
        )
        _raise_if_inside_broker_level(
            "take_profit_price",
            distance=distance,
            minimum_distance=minimum_distance,
            broker_symbol=spec.broker_symbol,
            level_name=level_name,
        )


def _minimum_stop_distance(spec: Mt5SymbolSpec) -> float | None:
    if spec.stops_level_points is None or spec.stops_level_points <= 0:
        return None
    if spec.point is None:
        return None
    return float(spec.stops_level_points) * spec.point


def _minimum_freeze_distance(spec: Mt5SymbolSpec) -> float | None:
    if spec.freeze_level_points is None or spec.freeze_level_points <= 0:
        return None
    if spec.point is None:
        return None
    return float(spec.freeze_level_points) * spec.point


def _raise_if_inside_stops_level(
    field_name: str,
    *,
    distance: float,
    minimum_distance: float,
    broker_symbol: str,
) -> None:
    _raise_if_inside_broker_level(
        field_name,
        distance=distance,
        minimum_distance=minimum_distance,
        broker_symbol=broker_symbol,
        level_name="stops",
    )


def _raise_if_inside_broker_level(
    field_name: str,
    *,
    distance: float,
    minimum_distance: float,
    broker_symbol: str,
    level_name: str,
) -> None:
    if distance + _ZERO_TOLERANCE >= minimum_distance:
        return
    raise ValueError(
        f"{field_name} is inside broker {level_name} level for {broker_symbol}: "
        f"distance={distance:.10g}, minimum={minimum_distance:.10g}."
    )


def _quantize_lots_down(raw_lots: Decimal, *, step: float) -> Decimal:
    if raw_lots <= 0:
        raise ValueError("Requested base-unit quantity must be greater than zero.")
    step_decimal = Decimal(str(step))
    return (raw_lots / step_decimal).to_integral_value(rounding=ROUND_DOWN) * step_decimal
