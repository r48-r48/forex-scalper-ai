"""Unit tests for the safe MT5 broker probe script."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

from scalper_ai.config.models import AppConfig, BrokerConfig, Mt5BrokerConfig
from scalper_ai.domain import OrderSide, TimeInForce


def test_mt5_broker_probe_collects_history_and_order_check_without_order_send() -> None:
    module = _FakeMetaTrader5Module()
    probe = _load_probe_module()
    config = AppConfig(
        environment="mt5",
        broker=BrokerConfig(
            live_adapter="mt5",
            mt5=Mt5BrokerConfig(history_lookback_hours=2),
        ),
    )

    payload = probe.collect_mt5_broker_probe_payload(
        config,
        symbol="EURUSD",
        side=OrderSide.BUY,
        time_in_force=TimeInForce.FOK,
        volume_lots=0.01,
        include_order_check=True,
        include_raw_samples=True,
        mt5_module=module,
        generated_at=datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc),  # noqa: UP017
    )

    assert payload["connected"] is True
    assert payload["order_send_called"] is False
    assert module.order_send_call_count == 0
    assert payload["symbol_info"]["name"] == "EURUSD"
    assert payload["raw_history"]["raw_order_count"] == 1
    assert payload["raw_history"]["raw_deal_count"] == 1
    assert payload["normalized_order_count"] == 1
    assert payload["normalized_orders"][0]["status"] == "filled"
    assert payload["normalized_orders"][0]["average_fill_price"] == 1.1002
    assert payload["normalized_position_count"] == 1
    assert payload["order_check"]["accepted"] is True
    assert payload["order_check"]["retcode"] == 0
    assert module.last_order_check_payload["type_filling"] == module.ORDER_FILLING_FOK


def _load_probe_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "mt5_broker_probe.py"
    spec = importlib.util.spec_from_file_location("mt5_broker_probe", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeMetaTrader5Module:
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE_PARTIAL = 10010
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    ORDER_STATE_FILLED = 4
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1

    def __init__(self) -> None:
        self.last_order_check_payload: dict[str, object] = {}
        self.order_send_call_count = 0
        self.shutdown_called = False

    def initialize(self, **kwargs: object) -> bool:
        return True

    def shutdown(self) -> None:
        self.shutdown_called = True

    def last_error(self) -> tuple[int, str]:
        return (1, "Success")

    def terminal_info(self) -> SimpleNamespace:
        return SimpleNamespace(
            name="MetaTrader 5",
            company="MetaQuotes Ltd.",
            connected=True,
            trade_allowed=False,
            tradeapi_disabled=False,
            path="C:\\Program Files\\MetaTrader 5",
            data_path="C:\\Users\\PC\\AppData\\Roaming\\MetaQuotes\\Terminal\\demo",
            build=5834,
        )

    def account_info(self) -> SimpleNamespace:
        return SimpleNamespace(
            login=123456,
            server="MetaQuotes-Demo",
            balance=100000.0,
            equity=100000.0,
            leverage=100,
            company="MetaQuotes Ltd.",
            currency="USD",
        )

    def symbols_get(self) -> tuple[SimpleNamespace, ...]:
        return (SimpleNamespace(name="EURUSD"),)

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return symbol == "EURUSD" and enable

    def symbol_info(self, symbol: str) -> SimpleNamespace | None:
        if symbol != "EURUSD":
            return None
        return SimpleNamespace(
            name="EURUSD",
            description="Euro vs US Dollar",
            visible=True,
            trade_mode=4,
            filling_mode=1,
            spread=2,
            spread_float=True,
            digits=5,
            volume_min=0.01,
            volume_max=500.0,
            volume_step=0.01,
            trade_contract_size=100000.0,
            currency_base="EUR",
            currency_profit="USD",
            currency_margin="EUR",
        )

    def symbol_info_tick(self, symbol: str) -> SimpleNamespace:
        return SimpleNamespace(
            time=1_777_404_445,
            time_msc=1_777_404_445_559,
            bid=1.1000,
            ask=1.1002,
            last=0.0,
            volume=0,
            flags=1030,
        )

    def order_check(self, request: dict[str, object]) -> SimpleNamespace:
        self.last_order_check_payload = dict(request)
        return SimpleNamespace(
            retcode=0,
            comment="Done",
            balance=100000.0,
            equity=100000.0,
            margin=11.71,
            margin_free=99988.29,
            margin_level=853970.96,
            time=1_777_404_445,
        )

    def order_send(self, request: dict[str, object]) -> None:
        self.order_send_call_count += 1
        raise AssertionError("order_send must not be called by mt5_broker_probe")

    def orders_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        return ()

    def positions_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        return (
            SimpleNamespace(
                symbol="EURUSD",
                type=self.POSITION_TYPE_BUY,
                volume=0.01,
                price_open=1.1002,
                time=1_777_404_445,
            ),
        )

    def history_orders_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        return (
            SimpleNamespace(
                ticket=44,
                symbol="EURUSD",
                state=self.ORDER_STATE_FILLED,
                volume_initial=0.01,
                volume_current=0.0,
                time_setup=1_777_404_445,
                time_done=1_777_404_445,
                comment="done",
            ),
        )

    def history_deals_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        return (
            SimpleNamespace(
                ticket=55,
                order=44,
                symbol="EURUSD",
                volume=0.01,
                price=1.1002,
                time=1_777_404_445,
            ),
        )
