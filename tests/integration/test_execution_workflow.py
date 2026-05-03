"""Integration tests for end-to-end paper execution workflows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scalper_ai.domain import OrderIntent, OrderSide, OrderType, TimeInForce
from scalper_ai.execution import (
    ExecutionOrderStatus,
    ExecutionQuote,
    ExecutionRouter,
    PaperExecutionAdapter,
    PaperExecutionConfig,
)


def test_paper_execution_workflow_handles_resting_limit_then_target_flatten() -> None:
    router = ExecutionRouter(
        paper_adapter=PaperExecutionAdapter(
            config=PaperExecutionConfig(
                initial_cash=1_000.0,
                slippage_bps=0.0,
                commission_bps=0.0,
            )
        )
    )

    accepted = router.submit_order(
        OrderIntent(
            intent_id="resting-buy",
            strategy_id="workflow-test",
            symbol="EURUSD",
            created_at=_timestamp(0),
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.GTC,
            quantity=1.0,
            limit_price=100.0,
            paper=True,
        ),
        _quote(minutes=0, bid=99.0, ask=101.0),
    )

    assert accepted.order.status is ExecutionOrderStatus.ACCEPTED
    assert accepted.fills == ()

    fill_updates = router.process_quote(_quote(minutes=1, bid=99.5, ask=100.0))

    assert len(fill_updates) == 1
    buy_fill = fill_updates[0]
    assert buy_fill.order.status is ExecutionOrderStatus.FILLED
    assert buy_fill.position.net_quantity == pytest.approx(1.0)
    assert buy_fill.fills[0].fill_price == pytest.approx(100.0)

    flatten = router.submit_order(
        OrderIntent(
            intent_id="flatten",
            strategy_id="workflow-test",
            symbol="EURUSD",
            created_at=_timestamp(2),
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            target_position=0.0,
            paper=True,
        ),
        _quote(minutes=2, bid=102.0, ask=103.0),
    )

    assert flatten.order.status is ExecutionOrderStatus.FILLED
    assert flatten.fills[0].fill_price == pytest.approx(102.0)
    assert flatten.position.net_quantity == pytest.approx(0.0)
    assert flatten.position.realized_pnl == pytest.approx(2.0)
    assert flatten.cash_balance == pytest.approx(1_002.0)
    assert flatten.equity == pytest.approx(1_002.0)


def _quote(*, minutes: int, bid: float, ask: float) -> ExecutionQuote:
    return ExecutionQuote(
        symbol="EURUSD",
        event_timestamp=_timestamp(minutes),
        received_timestamp=_timestamp(minutes),
        bid=bid,
        ask=ask,
        venue="paper",
    )


def _timestamp(minutes: int) -> datetime:
    return datetime(2026, 3, 27, 13, 0, tzinfo=UTC) + timedelta(minutes=minutes)
