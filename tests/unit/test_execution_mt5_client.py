"""Tests for the real MT5 terminal client wrapper."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from scalper_ai.domain import OrderSide, OrderType, PositionMode
from scalper_ai.execution import ExecutionOrderStatus
from scalper_ai.execution.mt5_client import (
    Mt5TerminalClient,
    Mt5TerminalClientConfig,
    discover_mt5_terminal_path,
)
from scalper_ai.execution.mt5_live import (
    Mt5OrderRequest,
    Mt5PendingOrderModifyRequest,
    Mt5ProtectionUpdateRequest,
)


def test_mt5_terminal_client_initializes_and_normalizes_market_order_submission() -> None:
    module = _FakeMetaTrader5Module()
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(
            login=123456,
            password="secret",
            server="MetaQuotes-Demo",
            order_comment_prefix="scalper_ai",
        ),
        module=module,
    )
    submitted_at = datetime(2026, 3, 28, 14, 0, tzinfo=UTC)

    state = client.submit_order(
        Mt5OrderRequest(
            client_order_id="intent-1",
            broker_symbol="EURUSD.a",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            submitted_at=submitted_at,
            volume_lots=1.0,
            stop_loss_price=1.0950,
            take_profit_price=1.1050,
        )
    )

    assert module.initialize_kwargs["login"] == 123456
    assert module.initialize_kwargs["password"] == "secret"
    assert module.initialize_kwargs["server"] == "MetaQuotes-Demo"
    assert module.last_order_check_payload["symbol"] == "EURUSD.a"
    assert module.last_order_send_payload["action"] == module.TRADE_ACTION_DEAL
    assert module.last_order_send_payload["symbol"] == "EURUSD.a"
    assert module.last_order_send_payload["price"] == 1.1002
    assert module.last_order_send_payload["sl"] == 1.0950
    assert module.last_order_send_payload["tp"] == 1.1050
    assert state.broker_order_id == "9001"
    assert state.status is ExecutionOrderStatus.FILLED
    assert state.filled_volume_lots == 1.0
    assert state.average_fill_price == 1.1002
    assert state.stop_loss_price == 1.0950
    assert state.take_profit_price == 1.1050
    assert len(state.deals) == 1
    assert state.deals[0].broker_deal_id == "9901"
    assert state.deals[0].broker_order_id == "9001"
    assert state.deals[0].side is OrderSide.BUY
    assert state.deals[0].commission == -2.0
    assert state.deals[0].fee == -0.5
    assert state.deals[0].swap == 0.25
    assert state.deals[0].execution_cost == pytest.approx(2.5)
    position = client.get_position("EURUSD.a")
    assert position is not None
    assert position.stop_loss_price == pytest.approx(1.0950)
    assert position.take_profit_price == pytest.approx(1.1050)


def test_mt5_terminal_client_exposes_normalized_order_check_result() -> None:
    module = _FakeMetaTrader5Module()
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(),
        module=module,
    )

    check = client.check_order(
        Mt5OrderRequest(
            client_order_id="intent:check/with spaces",
            broker_symbol="EURUSD.a",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            submitted_at=datetime(2026, 3, 28, 14, 0, tzinfo=UTC),
            volume_lots=1.0,
        )
    )

    assert check.accepted is True
    assert check.retcode == 0
    assert check.margin == 100.0
    assert check.margin_free == 249900.0
    assert module.last_order_check_payload["comment"] == "scalper_ai_intent_check_with_"


def test_mt5_terminal_client_rejects_order_when_order_check_rejects_without_sending() -> None:
    module = _FakeMetaTrader5Module()
    module.order_check_result = SimpleNamespace(
        retcode=10013,
        comment="invalid volume",
        time=1_774_670_400,
    )
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(),
        module=module,
    )

    state = client.submit_order(
        Mt5OrderRequest(
            client_order_id="intent-rejected",
            broker_symbol="EURUSD.a",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            submitted_at=datetime(2026, 3, 28, 14, 0, tzinfo=UTC),
            volume_lots=1.0,
        )
    )

    assert state.status is ExecutionOrderStatus.REJECTED
    assert state.broker_order_id == "mt5-check-rejected-intent-rejected"
    assert state.rejection_reason == "invalid volume"
    assert module.order_send_call_count == 0


def test_mt5_terminal_client_rejects_order_when_order_check_returns_none() -> None:
    module = _FakeMetaTrader5Module()
    module.order_check_result = None
    module.last_error_payload = (10030, "check unavailable")
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(),
        module=module,
    )

    state = client.submit_order(
        Mt5OrderRequest(
            client_order_id="intent-check-none",
            broker_symbol="EURUSD.a",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            submitted_at=datetime(2026, 3, 28, 14, 0, tzinfo=UTC),
            volume_lots=1.0,
        )
    )

    assert state.status is ExecutionOrderStatus.REJECTED
    assert state.rejection_reason == "10030:check unavailable"
    assert module.order_send_call_count == 0


def test_mt5_terminal_client_keeps_send_failure_after_successful_check_as_rejection() -> None:
    module = _FakeMetaTrader5Module()
    module.use_default_order_send = False
    module.order_send_result = None
    module.last_error_payload = (10031, "send failed")
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(),
        module=module,
    )

    state = client.submit_order(
        Mt5OrderRequest(
            client_order_id="intent-send-none",
            broker_symbol="EURUSD.a",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            submitted_at=datetime(2026, 3, 28, 14, 0, tzinfo=UTC),
            volume_lots=1.0,
        )
    )

    assert state.status is ExecutionOrderStatus.REJECTED
    assert state.rejection_reason == "10031:send failed"
    assert module.order_send_call_count == 1


def test_mt5_terminal_client_preserves_partial_done_fallback_volume() -> None:
    module = _FakeMetaTrader5Module()
    module.use_default_order_send = False
    module.order_send_result = SimpleNamespace(
        retcode=module.TRADE_RETCODE_DONE_PARTIAL,
        order=9101,
        price=1.1002,
        volume=0.4,
        comment="partial",
        time=1_774_670_400,
    )
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(),
        module=module,
    )

    state = client.submit_order(
        Mt5OrderRequest(
            client_order_id="intent-partial-fallback",
            broker_symbol="EURUSD.a",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            submitted_at=datetime(2026, 3, 28, 14, 0, tzinfo=UTC),
            volume_lots=1.0,
        )
    )

    assert state.status is ExecutionOrderStatus.PARTIALLY_FILLED
    assert state.broker_order_id == "9101"
    assert state.filled_volume_lots == pytest.approx(0.4)
    assert state.remaining_volume_lots == pytest.approx(0.6)
    assert state.average_fill_price == pytest.approx(1.1002)


def test_mt5_terminal_client_clamps_oversized_partial_fallback_volume() -> None:
    module = _FakeMetaTrader5Module()
    module.use_default_order_send = False
    module.order_send_result = SimpleNamespace(
        retcode=module.TRADE_RETCODE_DONE_PARTIAL,
        order=9102,
        price=1.1002,
        volume=2.0,
        comment="partial",
        time=1_774_670_400,
    )
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(),
        module=module,
    )

    state = client.submit_order(
        Mt5OrderRequest(
            client_order_id="intent-partial-oversized-fallback",
            broker_symbol="EURUSD.a",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            submitted_at=datetime(2026, 3, 28, 14, 0, tzinfo=UTC),
            volume_lots=1.0,
        )
    )

    assert state.status is ExecutionOrderStatus.FILLED
    assert state.filled_volume_lots == pytest.approx(1.0)
    assert state.remaining_volume_lots == pytest.approx(0.0)


def test_mt5_terminal_client_describes_account_positions_and_closes_cleanly() -> None:
    module = _FakeMetaTrader5Module()
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(),
        module=module,
    )

    account = client.describe_account()
    positions = client.list_positions()

    assert account.login == 123456
    assert account.balance == 250000.0
    assert len(positions) == 1
    assert positions[0].broker_symbol == "USDJPY"
    assert positions[0].net_volume_lots == -0.5
    assert client.is_connected() is True
    assert client.ping_latency_ms() is not None

    client.close()
    assert module.shutdown_called is True


def test_mt5_terminal_client_reconnects_when_existing_session_is_stale() -> None:
    module = _FakeMetaTrader5Module()
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(),
        module=module,
    )

    module.terminal_connected = False
    module.account_connected = False

    account = client.describe_account()
    connection = client.describe_connection()

    assert account.login == 123456
    assert module.initialize_call_count == 2
    assert module.shutdown_call_count == 1
    assert connection.connected is True
    assert connection.reconnect_attempt_count == 1
    assert connection.circuit_breaker_open is False


def test_mt5_terminal_client_opens_circuit_breaker_after_failed_reconnect() -> None:
    module = _FakeMetaTrader5Module()
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(reconnect_max_attempts=1),
        module=module,
    )

    module.recover_connection_on_initialize = False
    module.terminal_connected = False
    module.account_connected = False
    module.last_error_payload = (10031, "terminal offline")

    assert client.is_connected() is False
    connection = client.describe_connection()

    assert module.initialize_call_count == 2
    assert module.shutdown_call_count == 1
    assert connection.connected is False
    assert connection.reconnect_attempt_count == 1
    assert connection.circuit_breaker_open is True
    assert "account_info" in str(connection.last_error)
    with pytest.raises(RuntimeError, match="circuit breaker"):
        client.describe_account()


def test_mt5_terminal_client_blocks_reconnect_when_disabled() -> None:
    module = _FakeMetaTrader5Module()
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(reconnect_enabled=False),
        module=module,
    )

    module.terminal_connected = False

    with pytest.raises(RuntimeError, match="reconnect is disabled"):
        client.describe_account()

    connection = client.describe_connection()
    assert module.initialize_call_count == 1
    assert module.shutdown_call_count == 0
    assert connection.connected is False
    assert connection.circuit_breaker_open is True


def test_mt5_terminal_client_aggregates_hedging_positions_by_symbol() -> None:
    module = _FakeMetaTrader5Module()
    module._positions = {
        "EURUSD-111": SimpleNamespace(
            ticket=111,
            symbol="EURUSD",
            type=module.POSITION_TYPE_BUY,
            volume=0.03,
            price_open=1.1000,
            time=1_774_670_400,
        ),
        "EURUSD-222": SimpleNamespace(
            ticket=222,
            symbol="EURUSD",
            type=module.POSITION_TYPE_SELL,
            volume=0.01,
            price_open=1.1010,
            time=1_774_670_401,
        ),
    }
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(account_mode="hedging"),
        module=module,
    )

    position = client.get_position("EURUSD")
    raw_positions = client.list_positions()

    assert position is not None
    assert position.position_mode is PositionMode.HEDGING
    assert position.net_volume_lots == pytest.approx(0.02)
    assert position.gross_volume_lots == pytest.approx(0.04)
    assert position.source_position_tickets == ("111", "222")
    assert {raw_position.position_ticket for raw_position in raw_positions} == {"111", "222"}


def test_mt5_terminal_client_includes_position_ticket_for_hedging_close() -> None:
    module = _FakeMetaTrader5Module()
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(account_mode="hedging"),
        module=module,
    )

    check = client.check_order(
        Mt5OrderRequest(
            client_order_id="close-ticket",
            broker_symbol="EURUSD",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            submitted_at=datetime(2026, 3, 28, 14, 0, tzinfo=UTC),
            volume_lots=0.01,
            reduce_only=True,
            position_ticket="111",
        )
    )

    assert check.accepted is True
    assert module.last_order_check_payload["position"] == 111


def test_mt5_terminal_client_includes_protective_prices_in_order_check() -> None:
    module = _FakeMetaTrader5Module()
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(),
        module=module,
    )

    check = client.check_order(
        Mt5OrderRequest(
            client_order_id="protected",
            broker_symbol="EURUSD",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            submitted_at=datetime(2026, 3, 28, 14, 0, tzinfo=UTC),
            volume_lots=0.01,
            stop_loss_price=1.0950,
            take_profit_price=1.1050,
        )
    )

    assert check.accepted is True
    assert module.last_order_check_payload["sl"] == 1.0950
    assert module.last_order_check_payload["tp"] == 1.1050


def test_mt5_terminal_client_modifies_position_protection_with_precheck() -> None:
    module = _FakeMetaTrader5Module()
    module._positions["EURUSD"] = SimpleNamespace(
        ticket=8801,
        symbol="EURUSD",
        type=module.POSITION_TYPE_BUY,
        volume=0.01,
        price_open=1.1002,
        sl=0.0,
        tp=0.0,
        time=1_774_670_400,
    )
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(),
        module=module,
    )

    position = client.modify_position_protection(
        Mt5ProtectionUpdateRequest(
            broker_symbol="EURUSD",
            position_ticket="8801",
            submitted_at=datetime(2026, 3, 28, 14, 0, tzinfo=UTC),
            stop_loss_price=1.0950,
            take_profit_price=1.1050,
        )
    )

    assert module.last_order_check_payload["action"] == module.TRADE_ACTION_SLTP
    assert module.last_order_check_payload["position"] == 8801
    assert module.last_order_send_payload["action"] == module.TRADE_ACTION_SLTP
    assert module.last_order_send_payload["sl"] == 1.0950
    assert module.last_order_send_payload["tp"] == 1.1050
    assert position.position_ticket == "8801"
    assert position.stop_loss_price == pytest.approx(1.0950)
    assert position.take_profit_price == pytest.approx(1.1050)


def test_mt5_terminal_client_does_not_modify_when_protection_precheck_rejects() -> None:
    module = _FakeMetaTrader5Module()
    module.order_check_result = SimpleNamespace(
        retcode=10016,
        comment="invalid stops",
        time=1_774_670_400,
    )
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(),
        module=module,
    )

    with pytest.raises(RuntimeError, match="invalid stops"):
        client.modify_position_protection(
            Mt5ProtectionUpdateRequest(
                broker_symbol="EURUSD",
                position_ticket="8801",
                submitted_at=datetime(2026, 3, 28, 14, 0, tzinfo=UTC),
                stop_loss_price=1.0950,
            )
        )

    assert module.order_send_call_count == 0


def test_mt5_terminal_client_modifies_pending_order_with_precheck() -> None:
    module = _FakeMetaTrader5Module()
    module._open_orders[6601] = SimpleNamespace(
        ticket=6601,
        symbol="EURUSD",
        type=module.ORDER_TYPE_BUY_LIMIT,
        state=module.ORDER_STATE_PLACED,
        volume_initial=0.01,
        volume_current=0.01,
        price_open=1.0990,
        sl=0.0,
        tp=0.0,
        time_setup=1_774_670_400,
    )
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(),
        module=module,
    )

    state = client.modify_pending_order(
        Mt5PendingOrderModifyRequest(
            broker_order_id="6601",
            broker_symbol="EURUSD",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            submitted_at=datetime(2026, 3, 28, 14, 0, tzinfo=UTC),
            limit_price=1.0985,
            stop_loss_price=1.0950,
            take_profit_price=1.1050,
        )
    )

    assert module.last_order_check_payload["action"] == module.TRADE_ACTION_MODIFY
    assert module.last_order_check_payload["order"] == 6601
    assert module.last_order_send_payload["action"] == module.TRADE_ACTION_MODIFY
    assert module.last_order_send_payload["price"] == 1.0985
    assert module.last_order_send_payload["sl"] == 1.0950
    assert module.last_order_send_payload["tp"] == 1.1050
    assert state.broker_order_id == "6601"
    assert state.limit_price == pytest.approx(1.0985)
    assert state.stop_loss_price == pytest.approx(1.0950)
    assert state.take_profit_price == pytest.approx(1.1050)


def test_mt5_terminal_client_does_not_modify_pending_order_when_precheck_rejects() -> None:
    module = _FakeMetaTrader5Module()
    module._open_orders[6601] = SimpleNamespace(
        ticket=6601,
        symbol="EURUSD",
        type=module.ORDER_TYPE_BUY_LIMIT,
        state=module.ORDER_STATE_PLACED,
        volume_initial=0.01,
        volume_current=0.01,
        price_open=1.0990,
        time_setup=1_774_670_400,
    )
    module.order_check_result = SimpleNamespace(
        retcode=10016,
        comment="invalid price",
        time=1_774_670_400,
    )
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(),
        module=module,
    )

    with pytest.raises(RuntimeError, match="invalid price"):
        client.modify_pending_order(
            Mt5PendingOrderModifyRequest(
                broker_order_id="6601",
                broker_symbol="EURUSD",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                submitted_at=datetime(2026, 3, 28, 14, 0, tzinfo=UTC),
                limit_price=1.0985,
            )
        )

    assert module.order_send_call_count == 0


def test_mt5_terminal_client_filters_history_order_ticket_after_pending_cancel() -> None:
    module = _FakeMetaTrader5Module()
    module.ignore_history_order_ticket_filter = True
    module._open_orders[6601] = SimpleNamespace(
        ticket=6601,
        symbol="EURUSD",
        type=module.ORDER_TYPE_BUY_LIMIT,
        state=module.ORDER_STATE_PLACED,
        volume_initial=0.01,
        volume_current=0.01,
        price_open=1.0990,
        time_setup=1_774_670_400,
    )
    module._history_orders[9001] = SimpleNamespace(
        ticket=9001,
        symbol="EURUSD",
        state=module.ORDER_STATE_FILLED,
        volume_initial=0.01,
        volume_current=0.0,
        time_setup=1_774_670_300,
        time_done=1_774_670_300,
        comment="old fill",
    )
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(),
        module=module,
    )

    state = client.cancel_order(
        "6601",
        timestamp=datetime(2026, 3, 28, 14, 1, tzinfo=UTC),
    )

    assert state.broker_order_id == "6601"
    assert state.status is ExecutionOrderStatus.CANCELED
    assert state.cancel_reason == "user_requested"


def test_mt5_terminal_client_normalizes_symbol_spec() -> None:
    module = _FakeMetaTrader5Module()
    client = Mt5TerminalClient(
        config=Mt5TerminalClientConfig(),
        module=module,
    )

    spec = client.get_symbol_spec("EURUSD.a")

    assert spec.broker_symbol == "EURUSD.a"
    assert spec.base_units_per_lot == pytest.approx(100_000.0)
    assert spec.volume_min_lots == pytest.approx(0.01)
    assert spec.volume_step_lots == pytest.approx(0.01)
    assert spec.volume_max_lots == pytest.approx(100.0)
    assert spec.digits == 5
    assert spec.point == pytest.approx(0.00001)
    assert spec.stops_level_points == 10
    assert spec.freeze_level_points == 5
    assert spec.trade_mode == 4
    assert spec.filling_mode == 2
    assert spec.trade_execution_mode == 2


def test_discover_mt5_terminal_path_finds_macos_bundle_executable(tmp_path: Path) -> None:
    applications_root = tmp_path / "Applications"
    executable = (
        applications_root
        / "MetaTrader 5.app"
        / "Wrapper"
        / "MetaTrader5Terminal.app"
        / "MetaTrader5Terminal"
    )
    executable.parent.mkdir(parents=True)
    executable.write_text("binary", encoding="utf-8")

    resolved = discover_mt5_terminal_path(search_roots=(applications_root,))

    assert resolved == executable.resolve()


class _FakeMetaTrader5Module:
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    TRADE_ACTION_SLTP = 6
    TRADE_ACTION_MODIFY = 7
    TRADE_ACTION_REMOVE = 8
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE_PARTIAL = 10010
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_BUY_STOP = 4
    ORDER_TYPE_SELL_STOP = 5
    ORDER_TYPE_BUY_STOP_LIMIT = 6
    ORDER_TYPE_SELL_STOP_LIMIT = 7
    ORDER_TIME_GTC = 0
    ORDER_TIME_DAY = 1
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    ORDER_STATE_PLACED = 1
    ORDER_STATE_PARTIAL = 2
    ORDER_STATE_FILLED = 4
    ORDER_STATE_CANCELED = 5
    ORDER_STATE_REJECTED = 7
    DEAL_TYPE_BUY = 0
    DEAL_TYPE_SELL = 1
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1

    def __init__(self) -> None:
        self.initialize_kwargs: dict[str, object] = {}
        self.initialize_call_count = 0
        self.shutdown_call_count = 0
        self.last_order_check_payload: dict[str, object] = {}
        self.last_order_send_payload: dict[str, object] = {}
        self.order_send_call_count = 0
        self.shutdown_called = False
        self.last_error_payload: tuple[int, str] = (0, "ok")
        self.terminal_connected = True
        self.account_connected = True
        self.recover_connection_on_initialize = True
        self.order_check_result: SimpleNamespace | None = SimpleNamespace(
            retcode=0,
            comment="check passed",
            balance=250000.0,
            equity=250100.0,
            margin=100.0,
            margin_free=249900.0,
            margin_level=2500.0,
            time=1_774_670_400,
        )
        self.use_default_order_send = True
        self.order_send_result: SimpleNamespace | None = None
        self.ignore_history_order_ticket_filter = False
        self._open_orders: dict[int, SimpleNamespace] = {}
        self._history_orders: dict[int, SimpleNamespace] = {}
        self._deals: dict[int, list[SimpleNamespace]] = {}
        self._positions: dict[str, SimpleNamespace] = {
            "USDJPY": SimpleNamespace(
                ticket=7701,
                symbol="USDJPY",
                type=self.POSITION_TYPE_SELL,
                volume=0.5,
                price_open=150.25,
                sl=0.0,
                tp=0.0,
                time=1_774_670_400,
            )
        }

    def initialize(self, **kwargs: object) -> bool:
        self.initialize_call_count += 1
        self.initialize_kwargs = dict(kwargs)
        if self.recover_connection_on_initialize:
            self.terminal_connected = True
            self.account_connected = True
        return True

    def shutdown(self) -> None:
        self.shutdown_call_count += 1
        self.shutdown_called = True

    def last_error(self) -> tuple[int, str]:
        return self.last_error_payload

    def terminal_info(self) -> SimpleNamespace | None:
        if not self.terminal_connected:
            return None
        return SimpleNamespace(name="MT5")

    def account_info(self) -> SimpleNamespace | None:
        if not self.account_connected:
            return None
        return SimpleNamespace(
            login=123456,
            server="MetaQuotes-Demo",
            balance=250000.0,
            equity=250100.0,
            leverage=100,
            company="MetaQuotes",
            currency="USD",
        )

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return True

    def symbol_info_tick(self, symbol: str) -> SimpleNamespace:
        return SimpleNamespace(bid=1.1000, ask=1.1002)

    def symbol_info(self, symbol: str) -> SimpleNamespace:
        return SimpleNamespace(
            name=symbol,
            trade_contract_size=100_000.0,
            volume_min=0.01,
            volume_step=0.01,
            volume_max=100.0,
            digits=5,
            point=0.00001,
            trade_stops_level=10,
            trade_freeze_level=5,
            trade_mode=4,
            filling_mode=2,
            trade_exemode=2,
        )

    def order_check(self, request: dict[str, object]) -> SimpleNamespace | None:
        self.last_order_check_payload = dict(request)
        return self.order_check_result

    def order_send(self, request: dict[str, object]) -> SimpleNamespace | None:
        self.order_send_call_count += 1
        self.last_order_send_payload = dict(request)
        if not self.use_default_order_send:
            return self.order_send_result
        if request["action"] == self.TRADE_ACTION_MODIFY:
            order_id = int(request["order"])
            order = self._open_orders[order_id]
            order.price_open = request["price"]
            order.price_stoplimit = request.get("stoplimit", 0.0)
            order.sl = request.get("sl", 0.0)
            order.tp = request.get("tp", 0.0)
            return SimpleNamespace(
                retcode=self.TRADE_RETCODE_DONE,
                order=order_id,
                comment="done",
                time=1_774_670_400,
            )
        if request["action"] == self.TRADE_ACTION_REMOVE:
            order_id = int(request["order"])
            self._open_orders.pop(order_id, None)
            return SimpleNamespace(
                retcode=self.TRADE_RETCODE_DONE,
                order=order_id,
                comment="removed",
                time=1_774_670_400,
            )
        if request["action"] == self.TRADE_ACTION_SLTP:
            position_ticket = int(request["position"])
            for position in self._positions.values():
                if int(position.ticket) == position_ticket:
                    position.sl = request.get("sl", 0.0)
                    position.tp = request.get("tp", 0.0)
                    position.time = 1_774_670_400
                    return SimpleNamespace(
                        retcode=self.TRADE_RETCODE_DONE,
                        order=0,
                        comment="done",
                        time=1_774_670_400,
                    )
            return SimpleNamespace(
                retcode=10036,
                order=0,
                comment="position not found",
                time=1_774_670_400,
            )
        order_id = 9001
        self._history_orders[order_id] = SimpleNamespace(
            ticket=order_id,
            symbol=request["symbol"],
            state=self.ORDER_STATE_FILLED,
            volume_initial=request["volume"],
            volume_current=0.0,
            sl=request.get("sl", 0.0),
            tp=request.get("tp", 0.0),
            time_setup=1_774_670_400,
            time_done=1_774_670_400,
            comment="done",
        )
        self._deals[order_id] = [
            SimpleNamespace(
                ticket=9901,
                order=order_id,
                symbol=request["symbol"],
                type=self.DEAL_TYPE_BUY,
                volume=request["volume"],
                price=request["price"],
                commission=-2.0,
                fee=-0.5,
                swap=0.25,
                position_id=8801,
                time=1_774_670_400,
            )
        ]
        self._positions[str(request["symbol"])] = SimpleNamespace(
            ticket=8801,
            symbol=request["symbol"],
            type=self.POSITION_TYPE_BUY,
            volume=request["volume"],
            price_open=request["price"],
            sl=request.get("sl", 0.0),
            tp=request.get("tp", 0.0),
            time=1_774_670_400,
        )
        return SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE,
            order=order_id,
            price=request["price"],
            comment="done",
            time=1_774_670_400,
        )

    def orders_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        ticket = kwargs.get("ticket")
        if ticket is None:
            return tuple(self._open_orders.values())
        order = self._open_orders.get(int(ticket))
        return () if order is None else (order,)

    def positions_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        symbol = kwargs.get("symbol")
        if symbol is None:
            return tuple(self._positions.values())
        return tuple(position for position in self._positions.values() if position.symbol == symbol)

    def history_orders_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        ticket = kwargs.get("ticket")
        if ticket is None or self.ignore_history_order_ticket_filter:
            return tuple(self._history_orders.values())
        order = self._history_orders.get(int(ticket))
        return () if order is None else (order,)

    def history_deals_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        deposit_deal = SimpleNamespace(
            ticket=9900,
            order=0,
            symbol="",
            type=self.DEAL_TYPE_BUY,
            volume=0.0,
            price=0.0,
            commission=0.0,
            fee=0.0,
            swap=0.0,
            position_id=0,
            time=1_774_670_399,
        )
        ticket = kwargs.get("ticket")
        if ticket is None:
            return (deposit_deal,) + tuple(
                deal for deals in self._deals.values() for deal in deals
            )
        return (deposit_deal,) + tuple(self._deals.get(int(ticket), ()))
