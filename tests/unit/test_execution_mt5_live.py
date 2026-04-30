"""Tests for the MT5 live execution adapter skeleton."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scalper_ai.domain import OrderIntent, OrderSide, OrderType, PositionMode, TimeInForce
from scalper_ai.execution import (
    ExecutionOrderStatus,
    ExecutionQuote,
    Mt5DealState,
    Mt5ExecutionAdapter,
    Mt5ExecutionConfig,
    Mt5OrderRequest,
    Mt5OrderState,
    Mt5PositionState,
    Mt5ProtectionUpdateRequest,
    Mt5SymbolSpec,
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
            stop_loss_price=1.0950,
            take_profit_price=1.1050,
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
        stop_loss_price=1.0950,
        take_profit_price=1.1050,
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
    assert broker_orders[0].stop_loss_price == pytest.approx(1.0950)
    assert broker_orders[0].take_profit_price == pytest.approx(1.1050)
    assert broker_positions[0].net_quantity == pytest.approx(100_000.0)
    assert broker_positions[0].stop_loss_price == pytest.approx(1.0950)
    assert broker_positions[0].take_profit_price == pytest.approx(1.1050)
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
    assert update.fills[0].broker_deal_id == "5001"
    assert update.fills[0].broker_position_id == "7001"
    assert update.fills[0].broker_commission == pytest.approx(-2.0)
    assert update.fills[0].broker_fee == pytest.approx(-0.5)
    assert update.fills[0].broker_swap == pytest.approx(0.25)
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


def test_mt5_execution_adapter_uses_symbol_spec_for_conservative_lot_quantization() -> None:
    client = _SymbolSpecMt5Client(
        Mt5SymbolSpec(
            broker_symbol="EURUSD",
            base_units_per_lot=10_000.0,
            volume_min_lots=0.1,
            volume_step_lots=0.1,
            volume_max_lots=2.0,
        )
    )
    adapter = Mt5ExecutionAdapter(
        client,
        config=Mt5ExecutionConfig(base_units_per_lot=100_000.0),
    )
    timestamp = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)

    update = adapter.submit_order(
        OrderIntent(
            intent_id="mt5-quantized",
            strategy_id="mt5-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=12_345.0,
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

    assert client.requests[0].volume_lots == pytest.approx(1.2)
    assert update.order.requested_quantity == pytest.approx(12_000.0)
    assert update.order.status is ExecutionOrderStatus.FILLED


def test_mt5_execution_adapter_rejects_symbol_spec_volume_above_maximum() -> None:
    client = _SymbolSpecMt5Client(
        Mt5SymbolSpec(
            broker_symbol="EURUSD",
            base_units_per_lot=100_000.0,
            volume_min_lots=0.01,
            volume_step_lots=0.01,
            volume_max_lots=0.5,
        )
    )
    adapter = Mt5ExecutionAdapter(
        client,
        config=Mt5ExecutionConfig(base_units_per_lot=100_000.0),
    )
    timestamp = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)

    update = adapter.submit_order(
        OrderIntent(
            intent_id="mt5-too-large",
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
            bid=1.0998,
            ask=1.1000,
            venue="broker-feed",
        ),
    )

    assert client.requests == []
    assert update.order.status is ExecutionOrderStatus.REJECTED
    assert update.order.rejection_reason == (
        "Requested base-unit quantity exceeds the broker maximum lot size."
    )


def test_mt5_execution_adapter_quantizes_prices_to_symbol_precision() -> None:
    client = _SymbolSpecMt5Client(
        Mt5SymbolSpec(
            broker_symbol="EURUSD",
            digits=3,
            point=0.001,
        )
    )
    adapter = Mt5ExecutionAdapter(client)
    timestamp = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)

    update = adapter.submit_order(
        OrderIntent(
            intent_id="mt5-price-precision",
            strategy_id="mt5-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100_000.0,
            stop_loss_price=1.0954,
            take_profit_price=1.1066,
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
    assert client.requests[0].stop_loss_price == pytest.approx(1.095)
    assert client.requests[0].take_profit_price == pytest.approx(1.107)


def test_mt5_execution_adapter_rejects_protection_inside_stops_level() -> None:
    client = _SymbolSpecMt5Client(
        Mt5SymbolSpec(
            broker_symbol="EURUSD",
            point=0.0001,
            stops_level_points=10,
        )
    )
    adapter = Mt5ExecutionAdapter(client)
    timestamp = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)

    update = adapter.submit_order(
        OrderIntent(
            intent_id="mt5-stops-too-close",
            strategy_id="mt5-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100_000.0,
            stop_loss_price=1.0995,
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

    assert client.requests == []
    assert update.order.status is ExecutionOrderStatus.REJECTED
    assert update.order.rejection_reason is not None
    assert "stop_loss_price is inside broker stops level" in update.order.rejection_reason


def test_mt5_execution_adapter_rejects_unsupported_trade_mode_exposure() -> None:
    client = _SymbolSpecMt5Client(
        Mt5SymbolSpec(
            broker_symbol="EURUSD",
            trade_mode=1,
        )
    )
    adapter = Mt5ExecutionAdapter(client)
    timestamp = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)

    update = adapter.submit_order(
        OrderIntent(
            intent_id="mt5-long-only-sell",
            strategy_id="mt5-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.SELL,
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

    assert client.requests == []
    assert update.order.status is ExecutionOrderStatus.REJECTED
    assert update.order.rejection_reason == (
        "MT5 symbol trade_mode only allows long exposure increases."
    )


def test_mt5_execution_adapter_uses_ioc_when_symbol_only_allows_ioc() -> None:
    client = _SymbolSpecMt5Client(
        Mt5SymbolSpec(
            broker_symbol="EURUSD",
            filling_mode=2,
            trade_execution_mode=2,
        )
    )
    adapter = Mt5ExecutionAdapter(client)
    timestamp = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)

    update = adapter.submit_order(
        OrderIntent(
            intent_id="mt5-ioc-default",
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
    assert client.requests[0].time_in_force is TimeInForce.IOC


def test_mt5_execution_adapter_rejects_unsupported_requested_filling_mode() -> None:
    client = _SymbolSpecMt5Client(
        Mt5SymbolSpec(
            broker_symbol="EURUSD",
            filling_mode=2,
        )
    )
    adapter = Mt5ExecutionAdapter(client)
    timestamp = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)

    update = adapter.submit_order(
        OrderIntent(
            intent_id="mt5-fok-unsupported",
            strategy_id="mt5-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=100_000.0,
            time_in_force=TimeInForce.FOK,
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

    assert client.requests == []
    assert update.order.status is ExecutionOrderStatus.REJECTED
    assert update.order.rejection_reason == "MT5 symbol filling_mode does not support fok."


def test_mt5_execution_adapter_rejects_fok_for_pending_order() -> None:
    client = _SymbolSpecMt5Client(Mt5SymbolSpec(broker_symbol="EURUSD"))
    adapter = Mt5ExecutionAdapter(client)
    timestamp = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)

    update = adapter.submit_order(
        OrderIntent(
            intent_id="mt5-pending-fok",
            strategy_id="mt5-test",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=100_000.0,
            limit_price=1.0990,
            time_in_force=TimeInForce.FOK,
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

    assert client.requests == []
    assert update.order.status is ExecutionOrderStatus.REJECTED
    assert update.order.rejection_reason == (
        "MT5 pending orders require return filling; use GTC, DAY, or unset time_in_force."
    )


def test_mt5_execution_adapter_repairs_position_protection_preserving_existing_target() -> None:
    timestamp = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)
    client = _RepairableProtectionMt5Client(
        Mt5SymbolSpec(
            broker_symbol="EURUSD",
            point=0.0001,
            stops_level_points=10,
            freeze_level_points=5,
        ),
        Mt5PositionState(
            broker_symbol="EURUSD",
            timestamp=timestamp,
            net_volume_lots=0.01,
            average_entry_price=1.1002,
            position_ticket="111",
            source_position_tickets=("111",),
            take_profit_price=1.1060,
        ),
    )
    adapter = Mt5ExecutionAdapter(client)
    quote = ExecutionQuote(
        symbol="EURUSD",
        event_timestamp=timestamp,
        received_timestamp=timestamp,
        bid=1.1000,
        ask=1.1002,
        venue="broker-feed",
    )

    snapshot = adapter.repair_position_protection(
        "EURUSD",
        position_id="111",
        stop_loss_price=1.09504,
        take_profit_price=None,
        quote=quote,
        timestamp=timestamp,
    )

    assert len(client.protection_requests) == 1
    assert client.protection_requests[0].position_ticket == "111"
    assert client.protection_requests[0].stop_loss_price == pytest.approx(1.0950)
    assert client.protection_requests[0].take_profit_price == pytest.approx(1.1060)
    assert snapshot.position_id == "111"
    assert snapshot.stop_loss_price == pytest.approx(1.0950)
    assert snapshot.take_profit_price == pytest.approx(1.1060)


def test_mt5_execution_adapter_rejects_protection_repair_inside_freeze_level() -> None:
    timestamp = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)
    client = _RepairableProtectionMt5Client(
        Mt5SymbolSpec(
            broker_symbol="EURUSD",
            point=0.0001,
            freeze_level_points=5,
        ),
        Mt5PositionState(
            broker_symbol="EURUSD",
            timestamp=timestamp,
            net_volume_lots=0.01,
            average_entry_price=1.1002,
            position_ticket="111",
            source_position_tickets=("111",),
        ),
    )
    adapter = Mt5ExecutionAdapter(client)
    quote = ExecutionQuote(
        symbol="EURUSD",
        event_timestamp=timestamp,
        received_timestamp=timestamp,
        bid=1.1000,
        ask=1.1002,
        venue="broker-feed",
    )

    with pytest.raises(ValueError, match="broker freeze level"):
        adapter.repair_position_protection(
            "EURUSD",
            position_id="111",
            stop_loss_price=1.0997,
            take_profit_price=None,
            quote=quote,
            timestamp=timestamp,
        )

    assert client.protection_requests == []


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


def test_mt5_execution_adapter_blocks_required_missing_protection() -> None:
    client = _ImmediateFillMt5Client()
    adapter = Mt5ExecutionAdapter(
        client,
        config=Mt5ExecutionConfig(
            base_units_per_lot=100_000.0,
            require_stop_loss=True,
            require_take_profit=True,
        ),
    )
    timestamp = datetime(2026, 3, 28, 13, 0, tzinfo=UTC)

    update = adapter.submit_order(
        OrderIntent(
            intent_id="mt5-unprotected",
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

    assert client.requests == []
    assert update.order.status is ExecutionOrderStatus.REJECTED
    assert update.order.rejection_reason == (
        "required protective prices are missing for exposure-increasing MT5 order: "
        "stop_loss_price, take_profit_price."
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
            stop_loss_price=request.stop_loss_price,
            take_profit_price=request.take_profit_price,
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
            stop_loss_price=request.stop_loss_price,
            take_profit_price=request.take_profit_price,
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


class _SymbolSpecMt5Client(_ImmediateFillMt5Client):
    def __init__(self, spec: Mt5SymbolSpec) -> None:
        super().__init__()
        self._spec = spec

    def get_symbol_spec(self, broker_symbol: str) -> Mt5SymbolSpec:
        assert broker_symbol == self._spec.broker_symbol
        return self._spec


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


class _RepairableProtectionMt5Client(_ImmediateFillMt5Client):
    def __init__(self, spec: Mt5SymbolSpec, position: Mt5PositionState) -> None:
        super().__init__()
        self._spec = spec
        self._positions[position.position_ticket or "net"] = position
        self.protection_requests: list[Mt5ProtectionUpdateRequest] = []

    def get_symbol_spec(self, broker_symbol: str) -> Mt5SymbolSpec:
        assert broker_symbol == self._spec.broker_symbol
        return self._spec

    def modify_position_protection(
        self,
        request: Mt5ProtectionUpdateRequest,
    ) -> Mt5PositionState:
        self.protection_requests.append(request)
        position = self._positions[request.position_ticket]
        updated = Mt5PositionState(
            broker_symbol=position.broker_symbol,
            timestamp=request.submitted_at,
            net_volume_lots=position.net_volume_lots,
            average_entry_price=position.average_entry_price,
            position_ticket=position.position_ticket,
            position_mode=position.position_mode,
            gross_volume_lots=position.gross_lots,
            source_position_tickets=position.source_position_tickets,
            stop_loss_price=request.stop_loss_price,
            take_profit_price=request.take_profit_price,
        )
        self._positions[request.position_ticket] = updated
        return updated


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
