"""Tests for the MT5 live execution adapter skeleton."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scalper_ai.domain import OrderIntent, OrderSide, OrderType, PositionMode
from scalper_ai.execution import (
    ExecutionOrderStatus,
    ExecutionQuote,
    Mt5DealState,
    Mt5ExecutionAdapter,
    Mt5ExecutionConfig,
    Mt5OrderRequest,
    Mt5OrderState,
    Mt5PositionState,
)
from scalper_ai.execution.mt5_live import aggregate_mt5_positions


def test_mt5_execution_adapter_submits_live_market_order_and_exports_snapshots() -> None:
    client = _ImmediateFillMt5Client()
    adapter = Mt5ExecutionAdapter(
        client,
        config=Mt5ExecutionConfig(
            initial_cash=1_000_000.0,
            base_units_per_lot=100_000.0,
            symbol_map={"EURUSD": "EURUSD.a"},
        ),
    )
    timestamp = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)
    quote = ExecutionQuote(
        symbol="EURUSD",
        event_timestamp=timestamp,
        received_timestamp=timestamp,
        bid=1.1000,
        ask=1.1002,
        venue="broker-feed",
    )

    update = adapter.submit_order(
        OrderIntent(
            intent_id="mt5-intent-1",
            strategy_id="mt5-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100_000.0,
            paper=False,
        ),
        quote,
    )

    assert client.requests[0] == Mt5OrderRequest(
        client_order_id="mt5-intent-1",
        broker_symbol="EURUSD.a",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        submitted_at=timestamp,
        volume_lots=1.0,
        time_in_force=None,
        limit_price=None,
        stop_price=None,
        reduce_only=False,
    )
    assert update.order.status is ExecutionOrderStatus.FILLED
    assert update.order.intent.paper is False
    assert update.fills[0].fill_quantity == pytest.approx(100_000.0)
    assert update.fills[0].venue == "MT5"
    assert update.position.net_quantity == pytest.approx(100_000.0)

    broker_orders = adapter.list_broker_orders()
    broker_positions = adapter.list_broker_positions()
    connectivity = adapter.describe_broker_connectivity()
    assert broker_orders[0].symbol == "EURUSD"
    assert broker_orders[0].requested_quantity == pytest.approx(100_000.0)
    assert broker_positions[0].net_quantity == pytest.approx(100_000.0)
    assert connectivity.connected is True
    assert connectivity.venue == "MT5"
    assert connectivity.last_snapshot_at == timestamp


def test_mt5_execution_adapter_builds_fills_from_deal_records_with_costs() -> None:
    client = _DealFillMt5Client()
    adapter = Mt5ExecutionAdapter(
        client,
        config=Mt5ExecutionConfig(
            initial_cash=1_000_000.0,
            base_units_per_lot=100_000.0,
        ),
    )
    timestamp = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)

    update = adapter.submit_order(
        OrderIntent(
            intent_id="mt5-deal-intent",
            strategy_id="mt5-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100_000.0,
            paper=False,
        ),
        ExecutionQuote(
            symbol="EURUSD",
            event_timestamp=timestamp,
            received_timestamp=timestamp,
            bid=1.1000,
            ask=1.1002,
            venue="broker-feed",
        ),
    )

    assert update.order.status is ExecutionOrderStatus.FILLED
    assert len(update.fills) == 1
    assert update.fills[0].fill_id == "mt5-deal-5001"
    assert update.fills[0].fill_quantity == pytest.approx(100_000.0)
    assert update.fills[0].fill_price == pytest.approx(1.1002)
    assert update.fills[0].commission == pytest.approx(2.5)
    assert update.order.fills == update.fills
    assert update.position.last_fill_id == "mt5-deal-5001"


def test_mt5_execution_adapter_process_quote_polls_open_orders_until_fill() -> None:
    client = _PollingFillMt5Client()
    adapter = Mt5ExecutionAdapter(
        client,
        config=Mt5ExecutionConfig(base_units_per_lot=100_000.0),
    )
    submitted_at = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)
    submit_quote = ExecutionQuote(
        symbol="EURUSD",
        event_timestamp=submitted_at,
        received_timestamp=submitted_at,
        bid=1.0998,
        ask=1.1000,
        venue="broker-feed",
    )

    accepted = adapter.submit_order(
        OrderIntent(
            intent_id="mt5-intent-2",
            strategy_id="mt5-test",
            symbol="EURUSD",
            created_at=submitted_at,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100_000.0,
            limit_price=1.0999,
            paper=False,
        ),
        submit_quote,
    )

    assert accepted.order.status is ExecutionOrderStatus.ACCEPTED
    assert accepted.fills == ()

    polled_updates = adapter.process_quote(
        ExecutionQuote(
            symbol="EURUSD",
            event_timestamp=submitted_at + timedelta(seconds=1),
            received_timestamp=submitted_at + timedelta(seconds=1),
            bid=1.0998,
            ask=1.0999,
            venue="broker-feed",
        )
    )

    assert len(polled_updates) == 1
    fill_update = polled_updates[0]
    assert fill_update.order.status is ExecutionOrderStatus.FILLED
    assert fill_update.fills[0].fill_quantity == pytest.approx(100_000.0)
    assert fill_update.position.net_quantity == pytest.approx(100_000.0)
    assert (
        adapter.get_order(fill_update.order.broker_order_id).status
        is ExecutionOrderStatus.FILLED
    )


def test_mt5_execution_adapter_sizes_target_from_broker_position() -> None:
    client = _BrokerPositionMt5Client(
        Mt5PositionState(
            broker_symbol="EURUSD",
            timestamp=datetime(2026, 3, 28, 13, 0, tzinfo=UTC),
            net_volume_lots=-0.5,
            average_entry_price=1.1010,
        )
    )
    adapter = Mt5ExecutionAdapter(
        client,
        config=Mt5ExecutionConfig(base_units_per_lot=100_000.0),
    )
    timestamp = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)

    update = adapter.submit_order(
        OrderIntent(
            intent_id="mt5-target-flat",
            strategy_id="mt5-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            target_position=0.0,
            reduce_only=True,
            paper=False,
        ),
        ExecutionQuote(
            symbol="EURUSD",
            event_timestamp=timestamp,
            received_timestamp=timestamp,
            bid=1.0998,
            ask=1.1000,
            venue="broker-feed",
        ),
    )

    assert client.requests[0].volume_lots == pytest.approx(0.5)
    assert update.order.status is ExecutionOrderStatus.FILLED


def test_mt5_execution_adapter_hedging_reduce_only_uses_single_position_ticket() -> None:
    client = _BrokerPositionMt5Client(
        Mt5PositionState(
            broker_symbol="EURUSD",
            timestamp=datetime(2026, 3, 28, 13, 0, tzinfo=UTC),
            net_volume_lots=0.02,
            average_entry_price=1.1000,
            position_ticket="111",
            position_mode=PositionMode.HEDGING,
            gross_volume_lots=0.02,
            source_position_tickets=("111",),
        )
    )
    adapter = Mt5ExecutionAdapter(
        client,
        config=Mt5ExecutionConfig(
            account_mode=PositionMode.HEDGING,
            base_units_per_lot=100_000.0,
        ),
    )
    timestamp = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)

    update = adapter.submit_order(
        OrderIntent(
            intent_id="mt5-close-ticket",
            strategy_id="mt5-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=1_000.0,
            reduce_only=True,
            paper=False,
        ),
        ExecutionQuote(
            symbol="EURUSD",
            event_timestamp=timestamp,
            received_timestamp=timestamp,
            bid=1.0998,
            ask=1.1000,
            venue="broker-feed",
        ),
    )

    assert client.requests[0].position_ticket == "111"
    assert client.requests[0].volume_lots == pytest.approx(0.01)
    assert update.order.status is ExecutionOrderStatus.FILLED


def test_mt5_execution_adapter_rejects_ambiguous_hedging_reduce_only() -> None:
    client = _BrokerPositionMt5Client(
        Mt5PositionState(
            broker_symbol="EURUSD",
            timestamp=datetime(2026, 3, 28, 13, 0, tzinfo=UTC),
            net_volume_lots=0.01,
            average_entry_price=1.1000,
            position_ticket="111",
            position_mode=PositionMode.HEDGING,
            gross_volume_lots=0.01,
            source_position_tickets=("111",),
        ),
        Mt5PositionState(
            broker_symbol="EURUSD",
            timestamp=datetime(2026, 3, 28, 13, 0, tzinfo=UTC),
            net_volume_lots=0.02,
            average_entry_price=1.1010,
            position_ticket="222",
            position_mode=PositionMode.HEDGING,
            gross_volume_lots=0.02,
            source_position_tickets=("222",),
        ),
    )
    adapter = Mt5ExecutionAdapter(
        client,
        config=Mt5ExecutionConfig(
            account_mode=PositionMode.HEDGING,
            base_units_per_lot=100_000.0,
        ),
    )
    timestamp = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)

    update = adapter.submit_order(
        OrderIntent(
            intent_id="mt5-ambiguous-close",
            strategy_id="mt5-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=1_000.0,
            reduce_only=True,
            paper=False,
        ),
        ExecutionQuote(
            symbol="EURUSD",
            event_timestamp=timestamp,
            received_timestamp=timestamp,
            bid=1.0998,
            ask=1.1000,
            venue="broker-feed",
        ),
    )

    assert client.requests == []
    assert update.order.status is ExecutionOrderStatus.REJECTED
    assert update.order.rejection_reason == (
        "reduce_only hedging order is ambiguous across multiple broker position tickets."
    )


def test_mt5_execution_adapter_rejects_paper_orders() -> None:
    adapter = Mt5ExecutionAdapter(_ImmediateFillMt5Client())
    timestamp = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="paper=False"):
        adapter.submit_order(
            OrderIntent(
                intent_id="wrong-order",
                strategy_id="mt5-test",
                symbol="EURUSD",
                created_at=timestamp,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=100_000.0,
                paper=True,
            ),
            ExecutionQuote(
                symbol="EURUSD",
                event_timestamp=timestamp,
                received_timestamp=timestamp,
                bid=1.1000,
                ask=1.1002,
                venue="broker-feed",
            ),
        )


class _ImmediateFillMt5Client:
    def __init__(self) -> None:
        self.requests: list[Mt5OrderRequest] = []
        self._orders: dict[str, Mt5OrderState] = {}
        self._positions: dict[str, Mt5PositionState] = {}

    def submit_order(self, request: Mt5OrderRequest) -> Mt5OrderState:
        self.requests.append(request)
        state = Mt5OrderState(
            broker_order_id="mt5-order-1",
            broker_symbol=request.broker_symbol,
            status=ExecutionOrderStatus.FILLED,
            submitted_at=request.submitted_at,
            updated_at=request.submitted_at,
            requested_volume_lots=request.volume_lots,
            filled_volume_lots=request.volume_lots,
            remaining_volume_lots=0.0,
            average_fill_price=1.1002,
        )
        self._orders[state.broker_order_id] = state
        signed_volume = (
            request.volume_lots
            if request.side is OrderSide.BUY
            else -request.volume_lots
        )
        self._positions[request.broker_symbol] = Mt5PositionState(
            broker_symbol=request.broker_symbol,
            timestamp=request.submitted_at,
            net_volume_lots=signed_volume,
            average_entry_price=1.1002,
        )
        return state

    def cancel_order(self, broker_order_id: str, *, timestamp: datetime) -> Mt5OrderState:
        raise NotImplementedError

    def get_order(self, broker_order_id: str) -> Mt5OrderState | None:
        return self._orders.get(broker_order_id)

    def list_orders(self) -> tuple[Mt5OrderState, ...]:
        return tuple(self._orders.values())

    def get_position(self, broker_symbol: str) -> Mt5PositionState | None:
        return self._positions.get(broker_symbol)

    def list_positions(self) -> tuple[Mt5PositionState, ...]:
        return tuple(self._positions.values())

    def is_connected(self) -> bool:
        return True

    def ping_latency_ms(self) -> float | None:
        return 4.0


class _DealFillMt5Client(_ImmediateFillMt5Client):
    def submit_order(self, request: Mt5OrderRequest) -> Mt5OrderState:
        self.requests.append(request)
        deal = Mt5DealState(
            broker_deal_id="5001",
            broker_order_id="mt5-order-deal-1",
            broker_symbol=request.broker_symbol,
            timestamp=request.submitted_at,
            side=request.side,
            volume_lots=request.volume_lots,
            price=1.1002,
            commission=-2.0,
            fee=-0.5,
            swap=0.25,
            position_ticket="7001",
        )
        state = Mt5OrderState(
            broker_order_id=deal.broker_order_id,
            broker_symbol=request.broker_symbol,
            status=ExecutionOrderStatus.FILLED,
            submitted_at=request.submitted_at,
            updated_at=request.submitted_at,
            requested_volume_lots=request.volume_lots,
            filled_volume_lots=request.volume_lots,
            remaining_volume_lots=0.0,
            average_fill_price=deal.price,
            deals=(deal,),
        )
        self._orders[state.broker_order_id] = state
        self._positions[request.broker_symbol] = Mt5PositionState(
            broker_symbol=request.broker_symbol,
            timestamp=request.submitted_at,
            net_volume_lots=request.volume_lots,
            average_entry_price=deal.price,
        )
        return state


class _BrokerPositionMt5Client:
    def __init__(self, *positions: Mt5PositionState) -> None:
        self.requests: list[Mt5OrderRequest] = []
        self._orders: dict[str, Mt5OrderState] = {}
        self._positions = {position.position_ticket or "net": position for position in positions}

    def submit_order(self, request: Mt5OrderRequest) -> Mt5OrderState:
        self.requests.append(request)
        state = Mt5OrderState(
            broker_order_id=f"mt5-order-{len(self.requests)}",
            broker_symbol=request.broker_symbol,
            status=ExecutionOrderStatus.FILLED,
            submitted_at=request.submitted_at,
            updated_at=request.submitted_at,
            requested_volume_lots=request.volume_lots,
            filled_volume_lots=request.volume_lots,
            remaining_volume_lots=0.0,
            average_fill_price=1.1000,
        )
        self._orders[state.broker_order_id] = state
        return state

    def cancel_order(self, broker_order_id: str, *, timestamp: datetime) -> Mt5OrderState:
        raise NotImplementedError

    def get_order(self, broker_order_id: str) -> Mt5OrderState | None:
        return self._orders.get(broker_order_id)

    def list_orders(self) -> tuple[Mt5OrderState, ...]:
        return tuple(self._orders.values())

    def get_position(self, broker_symbol: str) -> Mt5PositionState | None:
        positions = tuple(
            position
            for position in self._positions.values()
            if position.broker_symbol == broker_symbol
        )
        if not positions:
            return None
        return aggregate_mt5_positions(positions, broker_symbol=broker_symbol)

    def list_positions(self) -> tuple[Mt5PositionState, ...]:
        return tuple(self._positions.values())

    def is_connected(self) -> bool:
        return True

    def ping_latency_ms(self) -> float | None:
        return 4.0


class _PollingFillMt5Client:
    def __init__(self) -> None:
        self._orders: dict[str, Mt5OrderState] = {}
        self._positions: dict[str, Mt5PositionState] = {}
        self._poll_count = 0

    def submit_order(self, request: Mt5OrderRequest) -> Mt5OrderState:
        state = Mt5OrderState(
            broker_order_id="mt5-order-poll-1",
            broker_symbol=request.broker_symbol,
            status=ExecutionOrderStatus.ACCEPTED,
            submitted_at=request.submitted_at,
            updated_at=request.submitted_at,
            requested_volume_lots=request.volume_lots,
            filled_volume_lots=0.0,
            remaining_volume_lots=request.volume_lots,
        )
        self._orders[state.broker_order_id] = state
        return state

    def cancel_order(self, broker_order_id: str, *, timestamp: datetime) -> Mt5OrderState:
        state = self._orders[broker_order_id]
        canceled = Mt5OrderState(
            broker_order_id=state.broker_order_id,
            broker_symbol=state.broker_symbol,
            status=ExecutionOrderStatus.CANCELED,
            submitted_at=state.submitted_at,
            updated_at=timestamp,
            requested_volume_lots=state.requested_volume_lots,
            filled_volume_lots=state.filled_volume_lots,
            remaining_volume_lots=state.remaining_volume_lots,
            average_fill_price=state.average_fill_price,
            cancel_reason="user_requested",
        )
        self._orders[broker_order_id] = canceled
        return canceled

    def get_order(self, broker_order_id: str) -> Mt5OrderState | None:
        state = self._orders.get(broker_order_id)
        if state is None:
            return None
        self._poll_count += 1
        if self._poll_count == 1:
            filled_at = state.updated_at + timedelta(seconds=1)
            state = Mt5OrderState(
                broker_order_id=state.broker_order_id,
                broker_symbol=state.broker_symbol,
                status=ExecutionOrderStatus.FILLED,
                submitted_at=state.submitted_at,
                updated_at=filled_at,
                requested_volume_lots=state.requested_volume_lots,
                filled_volume_lots=state.requested_volume_lots,
                remaining_volume_lots=0.0,
                average_fill_price=1.0999,
            )
            self._orders[broker_order_id] = state
            self._positions[state.broker_symbol] = Mt5PositionState(
                broker_symbol=state.broker_symbol,
                timestamp=filled_at,
                net_volume_lots=state.filled_volume_lots,
                average_entry_price=1.0999,
            )
        return self._orders[broker_order_id]

    def list_orders(self) -> tuple[Mt5OrderState, ...]:
        return tuple(self._orders.values())

    def get_position(self, broker_symbol: str) -> Mt5PositionState | None:
        return self._positions.get(broker_symbol)

    def list_positions(self) -> tuple[Mt5PositionState, ...]:
        return tuple(self._positions.values())

    def is_connected(self) -> bool:
        return True

    def ping_latency_ms(self) -> float | None:
        return 6.0
