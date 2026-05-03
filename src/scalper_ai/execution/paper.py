"""Deterministic paper execution adapter with explicit order lifecycle state."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime

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
    PositionState,
    TimeInForce,
)
from scalper_ai.execution.models import (
    ExecutionOrder,
    ExecutionOrderStatus,
    ExecutionQuote,
    ExecutionUpdate,
)

_ZERO_TOLERANCE = 1e-12


@dataclass(frozen=True)
class PaperExecutionConfig:
    """Configuration for the deterministic paper execution adapter."""

    initial_cash: float = 100_000.0
    slippage_bps: float = 0.0
    commission_bps: float = 0.0
    default_venue: str = "paper"

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be greater than zero.")
        if self.slippage_bps < 0:
            raise ValueError("slippage_bps must be non-negative.")
        if self.commission_bps < 0:
            raise ValueError("commission_bps must be non-negative.")
        if not self.default_venue.strip():
            raise ValueError("default_venue must be non-empty.")


class PaperExecutionAdapter:
    """Paper broker adapter with explicit order lifecycle and position updates."""

    def __init__(self, *, config: PaperExecutionConfig | None = None) -> None:
        self._config = config or PaperExecutionConfig()
        self._cash_balance = float(self._config.initial_cash)
        self._orders: dict[str, ExecutionOrder] = {}
        self._open_order_ids_by_symbol: dict[str, list[str]] = {}
        self._positions: dict[str, PositionState] = {}
        self._last_quotes: dict[str, ExecutionQuote] = {}
        self._next_order_id = 1
        self._next_fill_id = 1

    @property
    def cash_balance(self) -> float:
        """Return the current account cash balance."""

        return self._cash_balance

    def submit_order(self, intent: OrderIntent, quote: ExecutionQuote) -> ExecutionUpdate:
        """Submit one paper order using the current market quote."""

        if not intent.paper:
            raise ValueError("PaperExecutionAdapter only accepts order intents with paper=True.")
        if quote.symbol != intent.symbol:
            raise ValueError("ExecutionQuote symbol must match the submitted OrderIntent symbol.")

        self._remember_quote(quote)
        current_position = self._mark_current_position(intent.symbol, quote=quote)
        requested_quantity, rejection_reason = self._resolve_requested_quantity(
            intent,
            current_position=current_position,
        )

        broker_order_id = self._next_broker_order_id()
        if rejection_reason is not None:
            rejected_order = ExecutionOrder(
                intent=intent,
                broker_order_id=broker_order_id,
                status=ExecutionOrderStatus.REJECTED,
                submitted_at=quote.received_timestamp,
                updated_at=quote.received_timestamp,
                requested_quantity=1.0 if requested_quantity is None else requested_quantity,
                filled_quantity=0.0,
                remaining_quantity=1.0 if requested_quantity is None else requested_quantity,
                rejection_reason=rejection_reason,
            )
            self._orders[broker_order_id] = rejected_order
            return self._build_update(rejected_order, (), quote)

        order = ExecutionOrder(
            intent=intent,
            broker_order_id=broker_order_id,
            status=ExecutionOrderStatus.ACCEPTED,
            submitted_at=quote.received_timestamp,
            updated_at=quote.received_timestamp,
            requested_quantity=requested_quantity,
            filled_quantity=0.0,
            remaining_quantity=requested_quantity,
        )
        evaluated_order, fills = self._evaluate_order(order, quote=quote, submitted_now=True)
        final_order = evaluated_order
        if final_order.is_open and _requires_immediate_execution(intent.time_in_force):
            final_order = self._cancel_state(
                final_order,
                timestamp=quote.received_timestamp,
                reason="time_in_force_not_satisfied",
            )

        self._persist_order(final_order)
        return self._build_update(final_order, fills, quote)

    def process_quote(self, quote: ExecutionQuote) -> tuple[ExecutionUpdate, ...]:
        """Advance open paper orders using a new market quote."""

        self._remember_quote(quote)
        self._mark_current_position(quote.symbol, quote=quote)

        updates: list[ExecutionUpdate] = []
        open_order_ids = list(self._open_order_ids_by_symbol.get(quote.symbol, ()))
        for broker_order_id in open_order_ids:
            order = self._orders[broker_order_id]
            evaluated_order, fills = self._evaluate_order(order, quote=quote, submitted_now=False)
            if evaluated_order != order or fills:
                self._persist_order(evaluated_order)
                updates.append(self._build_update(evaluated_order, fills, quote))

        return tuple(updates)

    def cancel_order(self, broker_order_id: str, *, timestamp: datetime) -> ExecutionUpdate:
        """Cancel one accepted or triggered order."""

        order = self._orders.get(broker_order_id)
        if order is None:
            raise KeyError(f"Unknown broker_order_id: {broker_order_id}")
        if not order.is_open:
            raise ValueError("Only accepted or triggered orders can be canceled.")

        current_quote = self._last_quotes.get(order.intent.symbol)
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
        self._mark_current_position(order.intent.symbol, quote=quote)

        canceled_order = self._cancel_state(order, timestamp=timestamp, reason="user_requested")
        self._persist_order(canceled_order)
        return self._build_update(canceled_order, (), quote)

    def get_order(self, broker_order_id: str) -> ExecutionOrder | None:
        """Return the latest known state for one paper order."""

        return self._orders.get(broker_order_id)

    def get_position(
        self,
        symbol: str,
        *,
        quote: ExecutionQuote | None = None,
    ) -> PositionState | None:
        """Return the current marked position for one symbol."""

        if quote is not None:
            if quote.symbol != symbol:
                raise ValueError("ExecutionQuote symbol must match the requested position symbol.")
            self._remember_quote(quote)
            return self._mark_current_position(symbol, quote=quote)

        current_quote = self._last_quotes.get(symbol)
        if current_quote is None:
            return self._positions.get(symbol)
        return self._mark_current_position(symbol, quote=current_quote)

    def _evaluate_order(
        self,
        order: ExecutionOrder,
        *,
        quote: ExecutionQuote,
        submitted_now: bool,
    ) -> tuple[ExecutionOrder, tuple[FillEvent, ...]]:
        if order.intent.order_type is OrderType.MARKET:
            fill = self._build_market_fill(order, quote=quote)
            return self._apply_fill(order, fill=fill, quote=quote)

        if order.intent.order_type is OrderType.LIMIT:
            if self._limit_is_fillable(order, quote=quote):
                liquidity = LiquidityFlag.TAKER if submitted_now else LiquidityFlag.MAKER
                fill = self._build_limit_fill(order, quote=quote, liquidity_flag=liquidity)
                return self._apply_fill(order, fill=fill, quote=quote)
            return order, ()

        if order.intent.order_type is OrderType.STOP:
            if self._stop_is_triggered(order, quote=quote):
                fill = self._build_market_fill(order, quote=quote)
                return self._apply_fill(order, fill=fill, quote=quote)
            return order, ()

        if order.intent.order_type is OrderType.STOP_LIMIT:
            candidate = order
            if (
                candidate.status is not ExecutionOrderStatus.TRIGGERED
                and self._stop_is_triggered(candidate, quote=quote)
            ):
                candidate = replace(
                    candidate,
                    status=ExecutionOrderStatus.TRIGGERED,
                    updated_at=quote.received_timestamp,
                    triggered_at=quote.received_timestamp,
                )
            if (
                candidate.status is ExecutionOrderStatus.TRIGGERED
                and self._limit_is_fillable(candidate, quote=quote)
            ):
                liquidity = (
                    LiquidityFlag.TAKER
                    if candidate.triggered_at == quote.received_timestamp
                    else LiquidityFlag.MAKER
                )
                fill = self._build_limit_fill(candidate, quote=quote, liquidity_flag=liquidity)
                return self._apply_fill(candidate, fill=fill, quote=quote)
            return candidate, ()

        raise ValueError(f"Unsupported order type: {order.intent.order_type}")

    def _apply_fill(
        self,
        order: ExecutionOrder,
        *,
        fill: FillEvent,
        quote: ExecutionQuote,
    ) -> tuple[ExecutionOrder, tuple[FillEvent, ...]]:
        current_position = self._positions.get(order.intent.symbol)
        self._cash_balance = apply_fill_to_cash(self._cash_balance, fill)
        next_position = apply_fill_to_position(
            current_position,
            fill,
            mark_price=quote.mid_price,
        )
        self._positions[order.intent.symbol] = next_position
        filled_order = replace(
            order,
            status=ExecutionOrderStatus.FILLED,
            updated_at=fill.received_timestamp,
            filled_quantity=order.requested_quantity,
            remaining_quantity=0.0,
            fills=order.fills + (fill,),
        )
        return filled_order, (fill,)

    def _build_market_fill(self, order: ExecutionOrder, *, quote: ExecutionQuote) -> FillEvent:
        base_price = quote.ask if order.intent.side is OrderSide.BUY else quote.bid
        direction = 1.0 if order.intent.side is OrderSide.BUY else -1.0
        slippage_multiplier = self._config.slippage_bps / 10_000.0
        fill_price = base_price * (1.0 + (direction * slippage_multiplier))
        spread_cost = abs(base_price - quote.mid_price) * order.requested_quantity
        slippage_cost = base_price * order.requested_quantity * slippage_multiplier
        commission = (
            fill_price
            * order.requested_quantity
            * (self._config.commission_bps / 10_000.0)
        )
        return FillEvent(
            fill_id=self._next_fill_id_value(),
            intent_id=order.intent.intent_id,
            broker_order_id=order.broker_order_id,
            symbol=order.intent.symbol,
            event_timestamp=quote.event_timestamp,
            received_timestamp=quote.received_timestamp,
            side=order.intent.side,
            fill_price=fill_price,
            fill_quantity=order.requested_quantity,
            commission=commission,
            spread_cost=spread_cost,
            slippage_cost=slippage_cost,
            liquidity_flag=LiquidityFlag.TAKER,
            venue=quote.venue or self._config.default_venue,
        )

    def _build_limit_fill(
        self,
        order: ExecutionOrder,
        *,
        quote: ExecutionQuote,
        liquidity_flag: LiquidityFlag,
    ) -> FillEvent:
        if order.intent.limit_price is None:
            raise ValueError("Limit-style fills require limit_price.")

        if order.intent.side is OrderSide.BUY:
            base_price = min(float(order.intent.limit_price), quote.ask)
        else:
            base_price = max(float(order.intent.limit_price), quote.bid)

        spread_cost = abs(base_price - quote.mid_price) * order.requested_quantity
        commission = (
            base_price
            * order.requested_quantity
            * (self._config.commission_bps / 10_000.0)
        )
        return FillEvent(
            fill_id=self._next_fill_id_value(),
            intent_id=order.intent.intent_id,
            broker_order_id=order.broker_order_id,
            symbol=order.intent.symbol,
            event_timestamp=quote.event_timestamp,
            received_timestamp=quote.received_timestamp,
            side=order.intent.side,
            fill_price=base_price,
            fill_quantity=order.requested_quantity,
            commission=commission,
            spread_cost=spread_cost,
            slippage_cost=0.0,
            liquidity_flag=liquidity_flag,
            venue=quote.venue or self._config.default_venue,
        )

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

    @staticmethod
    def _limit_is_fillable(order: ExecutionOrder, *, quote: ExecutionQuote) -> bool:
        limit_price = order.intent.limit_price
        if limit_price is None:
            raise ValueError("Limit order evaluation requires limit_price.")
        if order.intent.side is OrderSide.BUY:
            return quote.ask <= float(limit_price)
        return quote.bid >= float(limit_price)

    @staticmethod
    def _stop_is_triggered(order: ExecutionOrder, *, quote: ExecutionQuote) -> bool:
        stop_price = order.intent.stop_price
        if stop_price is None:
            raise ValueError("Stop order evaluation requires stop_price.")
        if order.intent.side is OrderSide.BUY:
            return quote.ask >= float(stop_price)
        return quote.bid <= float(stop_price)

    def _mark_current_position(self, symbol: str, *, quote: ExecutionQuote) -> PositionState:
        current_position = self._positions.get(symbol)
        marked = mark_position(
            current_position,
            symbol=symbol,
            timestamp=quote.received_timestamp,
            mark_price=quote.mid_price,
        )
        self._positions[symbol] = marked
        return marked

    def _build_update(
        self,
        order: ExecutionOrder,
        fills: tuple[FillEvent, ...],
        quote: ExecutionQuote,
    ) -> ExecutionUpdate:
        position = self._mark_current_position(order.intent.symbol, quote=quote)
        equity = calculate_equity(self._cash_balance, position)
        return ExecutionUpdate(
            order=order,
            fills=fills,
            position=position,
            cash_balance=self._cash_balance,
            equity=equity,
            quote=quote,
        )

    def _persist_order(self, order: ExecutionOrder) -> None:
        self._orders[order.broker_order_id] = order
        symbol_open_orders = self._open_order_ids_by_symbol.setdefault(order.intent.symbol, [])
        if order.is_open:
            if order.broker_order_id not in symbol_open_orders:
                symbol_open_orders.append(order.broker_order_id)
        else:
            if order.broker_order_id in symbol_open_orders:
                symbol_open_orders.remove(order.broker_order_id)

    @staticmethod
    def _cancel_state(order: ExecutionOrder, *, timestamp: datetime, reason: str) -> ExecutionOrder:
        return replace(
            order,
            status=ExecutionOrderStatus.CANCELED,
            updated_at=timestamp,
            cancel_reason=reason,
        )

    def _remember_quote(self, quote: ExecutionQuote) -> None:
        self._last_quotes[quote.symbol] = quote

    def _next_broker_order_id(self) -> str:
        broker_order_id = f"paper-order-{self._next_order_id:06d}"
        self._next_order_id += 1
        return broker_order_id

    def _next_fill_id_value(self) -> str:
        fill_id = f"paper-fill-{self._next_fill_id:06d}"
        self._next_fill_id += 1
        return fill_id


def _requires_immediate_execution(time_in_force: TimeInForce | None) -> bool:
    return time_in_force in {TimeInForce.IOC, TimeInForce.FOK}
