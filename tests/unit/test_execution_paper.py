"""Unit tests for paper execution and routing behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scalper_ai.domain import OrderIntent, OrderSide, OrderType, PositionMode, PositionState, TimeInForce
from scalper_ai.execution import (
    ExecutionOrder,
    ExecutionOrderStatus,
    ExecutionQuote,
    ExecutionRouter,
    ExecutionUpdate,
    PaperExecutionAdapter,
    PaperExecutionConfig,
)


def test_paper_market_order_fills_at_top_of_book_and_updates_position() -> None:
    adapter = PaperExecutionAdapter(
        config=PaperExecutionConfig(
            initial_cash=1_000.0,
            slippage_bps=10.0,
            commission_bps=20.0,
        )
    )

    update = adapter.submit_order(
        _order_intent(
            intent_id="market-buy",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1.0,
        ),
        _quote(bid=100.0, ask=101.0),
    )

    assert update.order.status is ExecutionOrderStatus.FILLED
    assert len(update.fills) == 1
    assert update.fills[0].fill_price == pytest.approx(101.101)
    assert update.fills[0].spread_cost == pytest.approx(0.5)
    assert update.fills[0].slippage_cost == pytest.approx(0.101)
    assert update.fills[0].commission == pytest.approx(0.202202)
    assert update.position.net_quantity == pytest.approx(1.0)
    assert update.position.average_entry_price == pytest.approx(101.101)
    assert update.cash_balance == pytest.approx(898.696798)


def test_paper_limit_order_rests_then_fills_on_later_quote() -> None:
    adapter = PaperExecutionAdapter(config=PaperExecutionConfig(initial_cash=1_000.0))

    accepted = adapter.submit_order(
        _order_intent(
            intent_id="limit-buy",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1.0,
            limit_price=100.0,
            time_in_force=TimeInForce.GTC,
        ),
        _quote(bid=99.0, ask=101.0),
    )

    assert accepted.order.status is ExecutionOrderStatus.ACCEPTED
    assert accepted.fills == ()

    updates = adapter.process_quote(_quote(minutes=1, bid=99.5, ask=100.0))

    assert len(updates) == 1
    filled = updates[0]
    assert filled.order.status is ExecutionOrderStatus.FILLED
    assert filled.fills[0].fill_price == pytest.approx(100.0)
    assert filled.fills[0].liquidity_flag.value == "maker"
    assert filled.position.net_quantity == pytest.approx(1.0)


def test_paper_ioc_limit_order_cancels_when_not_immediately_fillable() -> None:
    adapter = PaperExecutionAdapter(config=PaperExecutionConfig(initial_cash=1_000.0))

    update = adapter.submit_order(
        _order_intent(
            intent_id="ioc-limit",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1.0,
            limit_price=100.0,
            time_in_force=TimeInForce.IOC,
        ),
        _quote(bid=99.0, ask=101.0),
    )

    assert update.order.status is ExecutionOrderStatus.CANCELED
    assert update.order.cancel_reason == "time_in_force_not_satisfied"
    assert update.fills == ()


def test_execution_router_routes_paper_and_live_orders_to_separate_adapters() -> None:
    paper_adapter = _RecordingAdapter("paper")
    live_adapter = _RecordingAdapter("live")
    router = ExecutionRouter(
        paper_adapter=paper_adapter,
        live_adapter=live_adapter,
    )

    paper_update = router.submit_order(
        _order_intent(
            intent_id="paper-order",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1.0,
            paper=True,
        ),
        _quote(bid=100.0, ask=101.0),
    )
    live_update = router.submit_order(
        _order_intent(
            intent_id="live-order",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=1.0,
            paper=False,
        ),
        _quote(minutes=1, bid=100.0, ask=101.0),
    )

    assert paper_update.order.broker_order_id.startswith("paper-")
    assert live_update.order.broker_order_id.startswith("live-")
    assert len(paper_adapter.submitted_intents) == 1
    assert len(live_adapter.submitted_intents) == 1
    assert router.get_order(paper_update.order.broker_order_id) == paper_update.order
    assert router.get_order(live_update.order.broker_order_id) == live_update.order


def _order_intent(
    *,
    intent_id: str,
    side: OrderSide,
    order_type: OrderType,
    quantity: float | None = None,
    target_position: float | None = None,
    limit_price: float | None = None,
    stop_price: float | None = None,
    time_in_force: TimeInForce | None = None,
    paper: bool = True,
) -> OrderIntent:
    return OrderIntent(
        intent_id=intent_id,
        strategy_id="execution-test",
        symbol="EURUSD",
        created_at=_timestamp(0),
        side=side,
        order_type=order_type,
        time_in_force=time_in_force,
        quantity=quantity,
        target_position=target_position,
        limit_price=limit_price,
        stop_price=stop_price,
        paper=paper,
    )


def _quote(*, minutes: int = 0, bid: float, ask: float) -> ExecutionQuote:
    return ExecutionQuote(
        symbol="EURUSD",
        event_timestamp=_timestamp(minutes),
        received_timestamp=_timestamp(minutes),
        bid=bid,
        ask=ask,
        venue="paper",
    )


def _timestamp(minutes: int) -> datetime:
    return datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)


class _RecordingAdapter:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self.submitted_intents: list[OrderIntent] = []
        self._orders: dict[str, ExecutionOrder] = {}

    def submit_order(self, intent: OrderIntent, quote: ExecutionQuote) -> ExecutionUpdate:
        self.submitted_intents.append(intent)
        broker_order_id = f"{self._prefix}-{len(self.submitted_intents):06d}"
        order = ExecutionOrder(
            intent=intent,
            broker_order_id=broker_order_id,
            status=ExecutionOrderStatus.ACCEPTED,
            submitted_at=quote.received_timestamp,
            updated_at=quote.received_timestamp,
            requested_quantity=float(intent.quantity or 1.0),
            filled_quantity=0.0,
            remaining_quantity=float(intent.quantity or 1.0),
        )
        self._orders[broker_order_id] = order
        return ExecutionUpdate(
            order=order,
            fills=(),
            position=PositionState(
                symbol=intent.symbol,
                timestamp=quote.received_timestamp,
                net_quantity=0.0,
                average_entry_price=0.0,
                mark_price=quote.mid_price,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                exposure_quote=0.0,
                position_mode=PositionMode.NETTING,
            ),
            cash_balance=100_000.0,
            equity=100_000.0,
            quote=quote,
        )

    def process_quote(self, quote: ExecutionQuote) -> tuple[ExecutionUpdate, ...]:
        del quote
        return ()

    def cancel_order(self, broker_order_id: str, *, timestamp: datetime) -> ExecutionUpdate:
        order = self._orders[broker_order_id]
        updated = ExecutionOrder(
            intent=order.intent,
            broker_order_id=order.broker_order_id,
            status=ExecutionOrderStatus.CANCELED,
            submitted_at=order.submitted_at,
            updated_at=timestamp,
            requested_quantity=order.requested_quantity,
            filled_quantity=0.0,
            remaining_quantity=order.remaining_quantity,
            cancel_reason="user_requested",
        )
        self._orders[broker_order_id] = updated
        quote = _quote(bid=100.0, ask=101.0)
        return ExecutionUpdate(
            order=updated,
            fills=(),
            position=PositionState(
                symbol=order.intent.symbol,
                timestamp=timestamp,
                net_quantity=0.0,
                average_entry_price=0.0,
                mark_price=quote.mid_price,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                exposure_quote=0.0,
                position_mode=PositionMode.NETTING,
            ),
            cash_balance=100_000.0,
            equity=100_000.0,
            quote=quote,
        )

    def get_order(self, broker_order_id: str) -> ExecutionOrder | None:
        return self._orders.get(broker_order_id)

    def get_position(self, symbol: str, *, quote: ExecutionQuote | None = None) -> PositionState | None:
        mark_price = 100.5 if quote is None else quote.mid_price
        timestamp = _timestamp(0) if quote is None else quote.received_timestamp
        return PositionState(
            symbol=symbol,
            timestamp=timestamp,
            net_quantity=0.0,
            average_entry_price=0.0,
            mark_price=mark_price,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            exposure_quote=0.0,
            position_mode=PositionMode.NETTING,
        )
