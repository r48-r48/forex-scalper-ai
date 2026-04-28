"""Unit tests for the controlled MT5 position flattener script."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

from scalper_ai.config.models import AppConfig, BrokerConfig, Mt5BrokerConfig


def test_flatten_positions_requires_operator_confirmation() -> None:
    module = _FakeMetaTrader5Module()
    flatten = _load_flatten_module()

    payload = flatten.flatten_mt5_demo_positions(
        _mt5_config(),
        symbol="EURUSD",
        expected_login=610769553,
        expected_server="Dukascopy-demo-mt5-1",
        time_in_force="ioc",
        operator_confirmation=False,
        mt5_module=module,
        generated_at=datetime(2026, 4, 28, 12, 0, tzinfo=UTC),
    )

    assert payload["blocked_reason"] == "operator_confirmation_missing"
    assert module.order_send_call_count == 0


def test_flatten_positions_closes_each_position_by_ticket() -> None:
    module = _FakeMetaTrader5Module()
    flatten = _load_flatten_module()

    payload = flatten.flatten_mt5_demo_positions(
        _mt5_config(),
        symbol="EURUSD",
        expected_login=610769553,
        expected_server="Dukascopy-demo-mt5-1",
        time_in_force="ioc",
        operator_confirmation=True,
        mt5_module=module,
        generated_at=datetime(2026, 4, 28, 12, 0, tzinfo=UTC),
    )

    assert payload["close_order_send_attempts"] == 2
    assert payload["remaining_positions"] == []
    assert module.order_send_call_count == 2
    assert module.sent_payloads[0]["position"] == 111
    assert module.sent_payloads[0]["type"] == module.ORDER_TYPE_SELL
    assert module.sent_payloads[1]["position"] == 222
    assert module.sent_payloads[1]["type"] == module.ORDER_TYPE_BUY


def _load_flatten_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "mt5_flatten_positions.py"
    spec = importlib.util.spec_from_file_location("mt5_flatten_positions", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mt5_config() -> AppConfig:
    return AppConfig(
        environment="mt5",
        broker=BrokerConfig(
            live_adapter="mt5",
            mt5=Mt5BrokerConfig(history_lookback_hours=24, min_volume_lots=0.01),
        ),
    )


class _FakeMetaTrader5Module:
    TRADE_ACTION_DEAL = 1
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE_PARTIAL = 10010
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1

    def __init__(self) -> None:
        self.order_send_call_count = 0
        self.sent_payloads: list[dict[str, object]] = []
        self.positions: dict[int, SimpleNamespace] = {
            111: SimpleNamespace(
                ticket=111,
                symbol="EURUSD",
                type=self.POSITION_TYPE_BUY,
                volume=0.01,
                price_open=1.1,
            ),
            222: SimpleNamespace(
                ticket=222,
                symbol="EURUSD",
                type=self.POSITION_TYPE_SELL,
                volume=0.01,
                price_open=1.2,
            ),
        }

    def initialize(self, **kwargs: object) -> bool:
        return True

    def shutdown(self) -> None:
        return None

    def last_error(self) -> tuple[int, str]:
        return (1, "Success")

    def account_info(self) -> SimpleNamespace:
        return SimpleNamespace(
            login=610769553,
            server="Dukascopy-demo-mt5-1",
            balance=100000.0,
            equity=100000.0,
            currency="TRY",
            trade_allowed=True,
            trade_expert=True,
        )

    def terminal_info(self) -> SimpleNamespace:
        return SimpleNamespace(
            name="MetaTrader 5",
            build=5836,
            connected=True,
            trade_allowed=True,
            tradeapi_disabled=False,
        )

    def positions_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        symbol = kwargs.get("symbol")
        if symbol is None:
            return tuple(self.positions.values())
        return tuple(position for position in self.positions.values() if position.symbol == symbol)

    def symbol_info_tick(self, symbol: str) -> SimpleNamespace:
        return SimpleNamespace(bid=1.17104, ask=1.17159)

    def order_check(self, request: dict[str, object]) -> SimpleNamespace:
        return SimpleNamespace(retcode=0, comment="Done")

    def order_send(self, request: dict[str, object]) -> SimpleNamespace:
        self.order_send_call_count += 1
        self.sent_payloads.append(dict(request))
        self.positions.pop(int(request["position"]), None)
        return SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE,
            order=333 + self.order_send_call_count,
            comment="Request executed",
        )
