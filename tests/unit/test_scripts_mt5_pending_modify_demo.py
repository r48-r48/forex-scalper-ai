"""Unit tests for the controlled MT5 pending-order modify script."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

from scalper_ai.config.models import AppConfig, BrokerConfig, Mt5BrokerConfig
from scalper_ai.domain import OrderSide


def test_pending_modify_requires_operator_confirmation() -> None:
    module = _FakeMetaTrader5Module()
    pending_modify = _load_pending_modify_module()

    payload = pending_modify.collect_mt5_pending_modify_payload(
        _mt5_config(),
        symbol="EURUSD",
        side=OrderSide.BUY,
        operator_confirmation=False,
        mt5_module=module,
        generated_at=datetime(2026, 5, 1, 1, 0, tzinfo=UTC),
    )

    assert payload["blocked_reason"] == "operator_confirmation_missing"
    assert payload["pending_order_send_attempted"] is False
    assert module.order_send_call_count == 0


def test_pending_modify_places_modifies_and_cancels_demo_pending_order() -> None:
    module = _FakeMetaTrader5Module()
    pending_modify = _load_pending_modify_module()

    payload = pending_modify.collect_mt5_pending_modify_payload(
        _mt5_config(),
        symbol="EURUSD",
        side=OrderSide.BUY,
        expected_login=610769553,
        expected_server="Dukascopy-demo-mt5-1",
        operator_confirmation=True,
        mt5_module=module,
        generated_at=datetime(2026, 5, 1, 1, 0, tzinfo=UTC),
    )

    assert payload.get("blocked_reason") is None
    assert payload.get("error") is None
    assert payload["pending_order_send_attempted"] is True
    assert payload["modify_attempted"] is True
    assert payload["cancel_order_send_attempted"] is True
    assert payload["placed_order"]["status"] == "accepted"
    assert payload["modified_order"]["limit_price"] == payload["pending_prices"][
        "modified_limit_price"
    ]
    assert payload["remaining_open_orders"] == []
    assert payload["remaining_positions"] == []
    assert module.order_send_call_count == 3
    assert [request["action"] for request in module.sent_payloads] == [
        module.TRADE_ACTION_PENDING,
        module.TRADE_ACTION_MODIFY,
        module.TRADE_ACTION_REMOVE,
    ]
    assert module.sent_payloads[1]["price"] < module.sent_payloads[0]["price"]


def test_pending_modify_blocks_when_current_open_orders_exist() -> None:
    module = _FakeMetaTrader5Module()
    module._open_orders[8001] = module.open_order(ticket=8001, price_open=1.15000)
    pending_modify = _load_pending_modify_module()

    payload = pending_modify.collect_mt5_pending_modify_payload(
        _mt5_config(),
        symbol="EURUSD",
        side=OrderSide.BUY,
        operator_confirmation=True,
        mt5_module=module,
        generated_at=datetime(2026, 5, 1, 1, 0, tzinfo=UTC),
    )

    assert payload["blocked_reason"] == "open_orders_present"
    assert payload["pending_order_send_attempted"] is False
    assert module.order_send_call_count == 0


def _load_pending_modify_module() -> ModuleType:
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "mt5_pending_modify_demo.py"
    )
    spec = importlib.util.spec_from_file_location("mt5_pending_modify_demo", script_path)
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
    TRADE_ACTION_PENDING = 5
    TRADE_ACTION_MODIFY = 7
    TRADE_ACTION_REMOVE = 8
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TIME_GTC = 0
    ORDER_FILLING_RETURN = 2
    ORDER_STATE_PLACED = 1
    ORDER_STATE_CANCELED = 7

    def __init__(self) -> None:
        self.order_send_call_count = 0
        self.sent_payloads: list[dict[str, object]] = []
        self._open_orders: dict[int, SimpleNamespace] = {}
        self._next_order_id = 8001

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
            trade_allowed=True,
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

    def symbol_info(self, symbol: str) -> SimpleNamespace:
        return SimpleNamespace(
            name=symbol,
            trade_contract_size=100000.0,
            volume_min=0.01,
            volume_step=0.01,
            volume_max=100.0,
            digits=5,
            point=0.00001,
            trade_stops_level=100,
            trade_freeze_level=50,
            trade_mode=4,
            filling_mode=2,
            trade_exemode=2,
        )

    def symbol_info_tick(self, symbol: str) -> SimpleNamespace:
        return SimpleNamespace(bid=1.17140, ask=1.17146)

    def order_check(self, request: dict[str, object]) -> SimpleNamespace:
        return SimpleNamespace(
            retcode=0,
            comment="Done",
            balance=100000.0,
            equity=100000.0,
            margin=12.0,
            margin_free=99988.0,
            margin_level=1000.0,
            time=1_777_503_600,
        )

    def order_send(self, request: dict[str, object]) -> SimpleNamespace:
        self.order_send_call_count += 1
        self.sent_payloads.append(dict(request))
        action = int(request["action"])
        if action == self.TRADE_ACTION_PENDING:
            order_id = self._next_order_id
            self._next_order_id += 1
            self._open_orders[order_id] = self.open_order(
                ticket=order_id,
                price_open=float(request["price"]),
                volume=float(request["volume"]),
                order_type=int(request["type"]),
            )
            return SimpleNamespace(
                retcode=self.TRADE_RETCODE_PLACED,
                order=order_id,
                price=float(request["price"]),
                comment="placed",
                time=1_777_503_600,
            )
        if action == self.TRADE_ACTION_MODIFY:
            order_id = int(request["order"])
            order = self._open_orders[order_id]
            order.price_open = float(request["price"])
            order.sl = float(request.get("sl", 0.0))
            order.tp = float(request.get("tp", 0.0))
            return SimpleNamespace(
                retcode=self.TRADE_RETCODE_DONE,
                order=order_id,
                price=float(request["price"]),
                comment="modified",
                time=1_777_503_601,
            )
        if action == self.TRADE_ACTION_REMOVE:
            order_id = int(request["order"])
            self._open_orders.pop(order_id, None)
            return SimpleNamespace(
                retcode=self.TRADE_RETCODE_DONE,
                order=order_id,
                comment="removed",
                time=1_777_503_602,
            )
        raise AssertionError(f"unexpected action: {action}")

    def orders_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        ticket = kwargs.get("ticket")
        if ticket is not None:
            order = self._open_orders.get(int(ticket))
            return () if order is None else (order,)
        symbol = kwargs.get("symbol")
        if symbol is not None:
            return tuple(
                order for order in self._open_orders.values() if order.symbol == symbol
            )
        return tuple(self._open_orders.values())

    def positions_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        return ()

    def history_orders_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        return ()

    def history_deals_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        return ()

    def open_order(
        self,
        *,
        ticket: int,
        price_open: float,
        volume: float = 0.01,
        order_type: int | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            ticket=ticket,
            symbol="EURUSD",
            type=self.ORDER_TYPE_BUY_LIMIT if order_type is None else order_type,
            state=self.ORDER_STATE_PLACED,
            volume_initial=volume,
            volume_current=volume,
            price_open=price_open,
            sl=0.0,
            tp=0.0,
            time_setup=1_777_503_600,
        )
