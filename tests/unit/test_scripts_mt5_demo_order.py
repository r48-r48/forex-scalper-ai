"""Unit tests for the controlled MT5 demo-order script."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

from scalper_ai.config.models import AppConfig, BrokerConfig, Mt5BrokerConfig
from scalper_ai.domain import OrderSide, TimeInForce


def test_demo_order_blocks_when_terminal_trade_is_not_allowed() -> None:
    module = _FakeMetaTrader5Module()
    module.terminal_trade_allowed = False
    demo_order = _load_demo_order_module()

    payload = demo_order.collect_mt5_demo_order_payload(
        _mt5_config(),
        symbol="EURUSD",
        side=OrderSide.BUY,
        time_in_force=TimeInForce.IOC,
        operator_confirmation=True,
        mt5_module=module,
        generated_at=datetime(2026, 4, 28, 12, 0, tzinfo=UTC),
    )

    assert payload["sent"] is False
    assert payload["blocked_reason"] == "terminal_trade_not_allowed"
    assert module.order_send_call_count == 0


def test_demo_order_requires_operator_confirmation() -> None:
    module = _FakeMetaTrader5Module()
    demo_order = _load_demo_order_module()

    payload = demo_order.collect_mt5_demo_order_payload(
        _mt5_config(),
        symbol="EURUSD",
        side=OrderSide.BUY,
        time_in_force=TimeInForce.IOC,
        operator_confirmation=False,
        mt5_module=module,
        generated_at=datetime(2026, 4, 28, 12, 0, tzinfo=UTC),
    )

    assert payload["sent"] is False
    assert payload["blocked_reason"] == "operator_confirmation_missing"
    assert module.order_send_call_count == 0


def test_demo_order_sends_min_volume_and_auto_flattens_on_demo_account() -> None:
    module = _FakeMetaTrader5Module()
    demo_order = _load_demo_order_module()

    payload = demo_order.collect_mt5_demo_order_payload(
        _mt5_config(),
        symbol="EURUSD",
        side=OrderSide.BUY,
        time_in_force=TimeInForce.IOC,
        expected_login=610769553,
        expected_server="Dukascopy-demo-mt5-1",
        operator_confirmation=True,
        mt5_module=module,
        generated_at=datetime(2026, 4, 28, 12, 0, tzinfo=UTC),
    )

    assert payload["sent"] is True
    assert payload["order_send_attempted"] is True
    assert payload["flatten_order_send_attempted"] is True
    assert payload["submitted_order"]["status"] == "filled"
    assert payload["flatten_order"]["status"] == "filled"
    assert payload["raw_history"]["raw_order_count"] == 2
    assert payload["raw_history"]["raw_deal_count"] == 2
    assert module.order_send_call_count == 2
    assert module.sent_payloads[0]["type_filling"] == module.ORDER_FILLING_IOC
    assert module.sent_payloads[0]["volume"] == 0.01
    assert module.sent_payloads[1]["type"] == module.ORDER_TYPE_SELL


def _load_demo_order_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "mt5_demo_order.py"
    spec = importlib.util.spec_from_file_location("mt5_demo_order", script_path)
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
        self.terminal_trade_allowed = True
        self.order_send_call_count = 0
        self.sent_payloads: list[dict[str, object]] = []
        self._positions: dict[str, SimpleNamespace] = {}
        self._history_orders: dict[int, SimpleNamespace] = {}
        self._history_deals: dict[int, list[SimpleNamespace]] = {}
        self._next_order_id = 9001

    def initialize(self, **kwargs: object) -> bool:
        return True

    def shutdown(self) -> None:
        return None

    def last_error(self) -> tuple[int, str]:
        return (1, "Success")

    def terminal_info(self) -> SimpleNamespace:
        return SimpleNamespace(
            name="MetaTrader 5",
            company="MetaQuotes Ltd.",
            connected=True,
            trade_allowed=self.terminal_trade_allowed,
            tradeapi_disabled=False,
            path="C:\\Program Files\\MetaTrader 5",
            data_path="C:\\Users\\demo\\MetaQuotes",
            build=5836,
        )

    def account_info(self) -> SimpleNamespace:
        return SimpleNamespace(
            login=610769553,
            server="Dukascopy-demo-mt5-1",
            balance=100000.0,
            equity=100000.0,
            leverage=100,
            company="Dukascopy Bank SA",
            currency="TRY",
            trade_allowed=True,
            trade_expert=True,
            trade_mode=0,
        )

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return symbol == "EURUSD" and enable

    def symbol_info_tick(self, symbol: str) -> SimpleNamespace:
        return SimpleNamespace(bid=1.17140, ask=1.17146)

    def order_check(self, request: dict[str, object]) -> SimpleNamespace:
        return SimpleNamespace(
            retcode=0,
            comment="Done",
            balance=100000.0,
            equity=100000.0,
            margin=528.17,
            margin_free=99471.83,
            margin_level=18933.29,
            time=1_777_419_360,
        )

    def order_send(self, request: dict[str, object]) -> SimpleNamespace:
        self.order_send_call_count += 1
        self.sent_payloads.append(dict(request))
        order_id = self._next_order_id
        self._next_order_id += 1
        symbol = str(request["symbol"])
        volume = float(request["volume"])
        price = float(request["price"])
        order_type = int(request["type"])
        existing = self._positions.get(symbol)
        signed_volume = volume if order_type == self.ORDER_TYPE_BUY else -volume
        net_volume = signed_volume
        if existing is not None:
            current = (
                existing.volume if existing.type == self.POSITION_TYPE_BUY else -existing.volume
            )
            net_volume = current + signed_volume
        if abs(net_volume) <= 1e-12:
            self._positions.pop(symbol, None)
        else:
            self._positions[symbol] = SimpleNamespace(
                symbol=symbol,
                type=self.POSITION_TYPE_BUY if net_volume > 0 else self.POSITION_TYPE_SELL,
                volume=abs(net_volume),
                price_open=price,
                time=1_777_419_360,
            )
        self._history_orders[order_id] = SimpleNamespace(
            ticket=order_id,
            symbol=symbol,
            state=self.ORDER_STATE_FILLED,
            volume_initial=volume,
            volume_current=0.0,
            time_setup=1_777_419_360,
            time_done=1_777_419_360,
            comment="done",
        )
        self._history_deals[order_id] = [
            SimpleNamespace(
                ticket=order_id + 1000,
                order=order_id,
                symbol=symbol,
                volume=volume,
                price=price,
                time=1_777_419_360,
            )
        ]
        return SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE,
            order=order_id,
            price=price,
            comment="done",
            time=1_777_419_360,
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
            return tuple(deal for deals in self._history_deals.values() for deal in deals)
        return tuple(self._history_deals.get(int(ticket), ()))
