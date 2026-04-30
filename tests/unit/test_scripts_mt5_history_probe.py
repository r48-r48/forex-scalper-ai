"""Unit tests for the read-only MT5 history probe script."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

from scalper_ai.config.models import AppConfig, BrokerConfig, Mt5BrokerConfig


def test_mt5_history_probe_collects_multiple_history_call_shapes_without_order_send() -> None:
    module = _FakeHistoryMetaTrader5Module()
    probe = _load_history_probe_module()
    config = AppConfig(
        environment="mt5",
        broker=BrokerConfig(
            live_adapter="mt5",
            mt5=Mt5BrokerConfig(history_lookback_hours=24),
        ),
    )

    payload = probe.collect_mt5_history_probe_payload(
        config,
        symbol="EURUSD",
        order_ticket=44,
        position_ticket=8801,
        lookback_hours=48,
        include_raw_samples=True,
        mt5_module=module,
        generated_at=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),  # noqa: UP017
    )

    assert payload["connected"] is True
    assert payload["order_send_called"] is False
    assert module.order_send_call_count == 0
    assert payload["window"]["lookback_hours"] == 48
    assert payload["current_orders"]["count"] == 1
    assert payload["current_positions"]["count"] == 1
    assert payload["history_calls"]["orders_window"]["count"] == 1
    assert payload["history_calls"]["deals_window"]["count"] == 1
    assert payload["history_calls"]["orders_window_group"]["count"] == 1
    assert payload["history_calls"]["deals_window_group"]["count"] == 1
    assert payload["history_calls"]["orders_ticket"]["count"] == 1
    assert payload["history_calls"]["deals_ticket"]["count"] == 1
    assert payload["history_calls"]["deals_position"]["count"] == 1
    assert payload["history_calls"]["deals_position"]["first"]["position_id"] == 8801


def _load_history_probe_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "mt5_history_probe.py"
    spec = importlib.util.spec_from_file_location("mt5_history_probe", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeHistoryMetaTrader5Module:
    def __init__(self) -> None:
        self.order_send_call_count = 0
        self.shutdown_called = False
        self._order = SimpleNamespace(
            ticket=44,
            symbol="EURUSD",
            state=4,
            volume_initial=0.01,
            volume_current=0.0,
            time_setup=1_777_404_445,
            time_done=1_777_404_445,
            comment="done",
        )
        self._deal = SimpleNamespace(
            ticket=55,
            order=44,
            position_id=8801,
            symbol="EURUSD",
            volume=0.01,
            price=1.1002,
            time=1_777_404_445,
        )
        self._position = SimpleNamespace(
            ticket=8801,
            symbol="EURUSD",
            volume=0.01,
            price_open=1.1002,
            time=1_777_404_445,
        )

    def initialize(self, **kwargs: object) -> bool:
        return True

    def shutdown(self) -> None:
        self.shutdown_called = True

    def last_error(self) -> tuple[int, str]:
        return (1, "Success")

    def terminal_info(self) -> SimpleNamespace:
        return SimpleNamespace(name="MetaTrader 5", connected=True, trade_allowed=True)

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

    def order_send(self, request: dict[str, object]) -> None:
        self.order_send_call_count += 1
        raise AssertionError("mt5_history_probe must not call order_send")

    def orders_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        return (self._order,)

    def positions_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        return (self._position,)

    def history_orders_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        ticket = kwargs.get("ticket")
        group = kwargs.get("group")
        if ticket is not None and int(ticket) != int(self._order.ticket):
            return ()
        if group is not None and "EURUSD" not in str(group):
            return ()
        return (self._order,)

    def history_deals_get(self, *args: object, **kwargs: object) -> tuple[SimpleNamespace, ...]:
        ticket = kwargs.get("ticket")
        position = kwargs.get("position")
        group = kwargs.get("group")
        if ticket is not None and int(ticket) != int(self._order.ticket):
            return ()
        if position is not None and int(position) != int(self._deal.position_id):
            return ()
        if group is not None and "EURUSD" not in str(group):
            return ()
        return (self._deal,)
