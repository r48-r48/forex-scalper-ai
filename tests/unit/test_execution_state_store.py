"""Tests for durable execution state storage."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from scalper_ai.domain import OrderIntent, OrderSide, OrderType
from scalper_ai.execution import (
    ExecutionQuote,
    KillSwitchScope,
    KillSwitchState,
    PaperExecutionAdapter,
    SqliteExecutionStateStore,
)
from scalper_ai.risk import RiskDecision, RiskDecisionStatus
from scalper_ai.services import OmsOrderRecord, OmsOrderStatus, transition_order


def test_sqlite_execution_state_store_round_trips_runtime_records(tmp_path) -> None:
    store = SqliteExecutionStateStore(tmp_path / "runtime-state.sqlite")
    timestamp = datetime(2026, 4, 30, 11, 0, tzinfo=UTC)
    intent = OrderIntent(
        intent_id="state-store-intent",
        strategy_id="state-store-test",
        symbol="EURUSD",
        created_at=timestamp,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=2.0,
        paper=True,
    )
    quote = ExecutionQuote(
        symbol="EURUSD",
        event_timestamp=timestamp,
        received_timestamp=timestamp,
        bid=1.0999,
        ask=1.1001,
        venue="paper",
    )
    update = PaperExecutionAdapter().submit_order(intent, quote)
    attributed_fill = update.fills[0].model_copy(
        update={
            "broker_deal_id": "5001",
            "broker_symbol": "EURUSD",
            "broker_position_id": "7001",
            "broker_commission": -2.0,
            "broker_fee": -0.5,
            "broker_swap": 0.25,
            "commission": 2.5,
        }
    )
    update = replace(
        update,
        fills=(attributed_fill,),
        order=replace(update.order, fills=(attributed_fill,)),
    )
    decision = RiskDecision(
        status=RiskDecisionStatus.APPROVED,
        checked_at=timestamp,
        intent_id=intent.intent_id,
        symbol=intent.symbol,
        projected_position=2.0,
    )

    checked_record = transition_order(
        OmsOrderRecord.new(intent),
        OmsOrderStatus.CHECKED,
        updated_at=timestamp,
    )
    sent_record = transition_order(checked_record, OmsOrderStatus.SENT, updated_at=timestamp)
    ack_record = transition_order(
        sent_record,
        OmsOrderStatus.ACK,
        updated_at=timestamp,
        broker_order_id=update.order.broker_order_id,
        filled_quantity=update.order.filled_quantity,
    )
    filled_record = transition_order(
        ack_record,
        OmsOrderStatus.FILLED,
        updated_at=timestamp,
        broker_order_id=update.order.broker_order_id,
        filled_quantity=update.order.filled_quantity,
    )

    store.save_risk_decision(decision)
    for record in (checked_record, sent_record, ack_record, filled_record):
        store.save_oms_record(record)
    store.save_execution_update(update)
    store.save_kill_switch_state(
        KillSwitchState(
            scope=KillSwitchScope.SYMBOL,
            symbol="EURUSD",
            enabled=True,
            updated_at=timestamp,
            reason="manual_pause",
        )
    )

    assert store.count_rows("order_intents") == 1
    assert store.count_rows("risk_decisions") == 1
    assert store.count_rows("oms_transitions") == 4
    assert store.count_rows("execution_updates") == 1
    assert store.count_rows("fill_events") == 1
    assert store.count_rows("deal_attributions") == 1
    assert store.count_rows("position_states") == 1
    assert store.count_rows("kill_switch_states") == 1

    assert store.list_order_intents()[0].intent_id == intent.intent_id
    assert store.list_risk_decisions()[0].status is RiskDecisionStatus.APPROVED
    assert store.list_oms_records()[0].status is OmsOrderStatus.FILLED
    assert store.list_execution_updates()[0].order.broker_order_id == update.order.broker_order_id
    persisted_fill = store.list_fill_events()[0]
    assert persisted_fill.intent_id == intent.intent_id
    assert persisted_fill.broker_deal_id == "5001"
    deal_attribution = store.list_deal_attributions()[0]
    assert deal_attribution.broker_deal_id == "5001"
    assert deal_attribution.fill_id == persisted_fill.fill_id
    assert deal_attribution.broker_commission == -2.0
    assert deal_attribution.broker_fee == -0.5
    assert deal_attribution.broker_swap == 0.25
    assert deal_attribution.execution_cost == 2.5
    assert store.list_position_states()[0].net_quantity == 2.0
    assert store.list_kill_switch_states()[0].reason == "manual_pause"
