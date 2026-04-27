"""Integration checks for deployment runtime bootstrap in safe paper mode."""

from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from scalper_ai.deployment import HealthStatus, bootstrap_runtime
from scalper_ai.domain import OrderIntent, OrderSide, OrderType
from scalper_ai.execution import ExecutionOrderStatus, ExecutionQuote
from scalper_ai.utils import resolve_repo_root


def test_bootstrap_runtime_in_paper_mode_routes_orders_safely() -> None:
    runtime = bootstrap_runtime(
        config_name="paper",
        config_dir=resolve_repo_root() / "configs",
    )

    try:
        timestamp = datetime(2025, 1, 1, 9, 30, tzinfo=timezone.utc)
        quote = ExecutionQuote(
            symbol="EURUSD",
            event_timestamp=timestamp,
            received_timestamp=timestamp,
            bid=1.0999,
            ask=1.1001,
            venue="paper",
        )
        intent = OrderIntent(
            intent_id="intent-1",
            strategy_id="paper-strategy",
            symbol="EURUSD",
            created_at=timestamp,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=2.0,
            paper=True,
        )

        summary = runtime.summary()
        assert summary.requested_mode == "paper"
        assert summary.effective_mode == "paper"
        assert summary.execution_enabled is True

        update = runtime.submit_order(intent, quote)
        assert update.order.status is ExecutionOrderStatus.FILLED
        assert update.position.net_quantity == 2.0

        snapshot = runtime.health_snapshot()
        assert snapshot.overall_status is HealthStatus.PASS

        metrics_text = runtime.metrics_text()
        assert "scalper_ai_runtime_start_total" in metrics_text
        assert "scalper_ai_execution_orders_submitted_total" in metrics_text
        assert 'requested_mode="paper"' in metrics_text
    finally:
        runtime.stop()


def test_bootstrap_runtime_can_auto_build_mt5_live_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    from scalper_ai.execution import mt5_client as mt5_client_module

    fake_module = _BootstrapFakeMetaTrader5Module()
    monkeypatch.setattr(mt5_client_module, "load_metatrader5_module", lambda: fake_module)

    runtime = bootstrap_runtime(
        config_name="mt5",
        config_dir=resolve_repo_root() / "configs",
        live_confirmation_token="ENABLE_LIVE_TRADING",
    )

    try:
        summary = runtime.summary()
        assert summary.requested_mode == "live"
        assert summary.effective_mode == "live"
        assert summary.execution_enabled is True

        timestamp = datetime(2026, 3, 28, 15, 0, tzinfo=timezone.utc)
        update = runtime.submit_order(
            OrderIntent(
                intent_id="intent-mt5-1",
                strategy_id="mt5-bootstrap-test",
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
        snapshot = runtime.health_snapshot()
        assert snapshot.overall_status is HealthStatus.PASS
    finally:
        runtime.stop()

    assert fake_module.shutdown_called is True


class _BootstrapFakeMetaTrader5Module:
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
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
    ORDER_STATE_FILLED = 4
    POSITION_TYPE_BUY = 0

    def __init__(self) -> None:
        self.shutdown_called = False
        self._history_orders: dict[int, SimpleNamespace] = {}
        self._deals: dict[int, list[SimpleNamespace]] = {}
        self._positions: dict[str, SimpleNamespace] = {}

    def initialize(self, **kwargs: object) -> bool:
        return True

    def shutdown(self) -> None:
        self.shutdown_called = True

    def last_error(self) -> tuple[int, str]:
        return (0, "ok")

    def terminal_info(self) -> SimpleNamespace:
        return SimpleNamespace(name="MT5")

    def account_info(self) -> SimpleNamespace:
        return SimpleNamespace(
            login=123456,
            server="MetaQuotes-Demo",
            balance=250000.0,
            equity=250000.0,
            leverage=100,
            company="MetaQuotes",
            currency="USD",
        )

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return True

    def symbol_info_tick(self, symbol: str) -> SimpleNamespace:
        return SimpleNamespace(bid=1.1000, ask=1.1002)

    def order_check(self, request: dict[str, object]) -> SimpleNamespace:
        return SimpleNamespace(retcode=0, comment="check passed", time=int(datetime.now(timezone.utc).timestamp()))

    def order_send(self, request: dict[str, object]) -> SimpleNamespace:
        order_id = 9101
        current_epoch = int(datetime.now(timezone.utc).timestamp())
        self._history_orders[order_id] = SimpleNamespace(
            ticket=order_id,
            symbol=request["symbol"],
            state=self.ORDER_STATE_FILLED,
            volume_initial=request["volume"],
            volume_current=0.0,
            time_setup=current_epoch,
            time_done=current_epoch,
            comment="done",
        )
        self._deals[order_id] = [
            SimpleNamespace(order=order_id, volume=request["volume"], price=request["price"])
        ]
        self._positions[str(request["symbol"])] = SimpleNamespace(
            symbol=request["symbol"],
            type=self.POSITION_TYPE_BUY,
            volume=request["volume"],
            price_open=request["price"],
            time=current_epoch,
        )
        return SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE,
            order=order_id,
            price=request["price"],
            comment="done",
            time=current_epoch,
        )

    def orders_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        return ()

    def positions_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        symbol = kwargs.get("symbol")
        if symbol is None:
            return tuple(self._positions.values())
        position = self._positions.get(str(symbol))
        return () if position is None else (position,)

    def history_orders_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        ticket = kwargs.get("ticket")
        if ticket is None:
            return tuple(self._history_orders.values())
        order = self._history_orders.get(int(ticket))
        return () if order is None else (order,)

    def history_deals_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        ticket = kwargs.get("ticket")
        if ticket is None:
            return tuple(deal for deals in self._deals.values() for deal in deals)
        return tuple(self._deals.get(int(ticket), ()))
