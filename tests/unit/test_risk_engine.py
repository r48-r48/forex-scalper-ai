"""Unit tests for deterministic pre-trade risk checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scalper_ai.domain import OrderIntent, OrderSide, OrderType, PositionMode, PositionState
from scalper_ai.journal import JournalEventType
from scalper_ai.risk import RiskContext, RiskDecisionStatus, RiskEngine, RiskLimits, RiskRejectCode

BASE_TS = datetime(2026, 4, 27, 11, 30, tzinfo=UTC)


def test_risk_engine_approves_order_and_emits_journalable_decision() -> None:
    engine = RiskEngine(_limits())
    context = _context(
        positions={"EURUSD": _position(10_000.0)},
    )

    decision = engine.evaluate_order(_intent(quantity=5_000.0), context)
    journal_event = decision.to_journal_event(event_id="risk-decision-1")

    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.accepted is True
    assert decision.projected_position == 15_000.0
    assert journal_event.event_type is JournalEventType.RISK
    assert journal_event.payload["status"] == "approved"
    assert journal_event.payload["intent_id"] == "intent-1"


@pytest.mark.parametrize(
    ("context_kwargs", "expected_code", "broker_order_id"),
    [
        ({"session_kill_switch": True}, RiskRejectCode.SESSION_KILL_SWITCH, None),
        ({"symbol_kill_switches": frozenset({"EURUSD"})}, RiskRejectCode.SYMBOL_KILL_SWITCH, None),
        ({"known_intent_ids": frozenset({"intent-1"})}, RiskRejectCode.DUPLICATE_INTENT, None),
        (
            {"known_broker_order_ids": frozenset({"broker-1"})},
            RiskRejectCode.DUPLICATE_BROKER_ORDER,
            "broker-1",
        ),
        (
            {
                "recent_rejection_timestamps": (
                    BASE_TS - timedelta(seconds=10),
                    BASE_TS - timedelta(seconds=20),
                )
            },
            RiskRejectCode.REJECT_BURST,
            None,
        ),
        (
            {"latest_market_data_at": BASE_TS - timedelta(seconds=3)},
            RiskRejectCode.STALE_MARKET_DATA,
            None,
        ),
        (
            {"order_timestamps": (BASE_TS - timedelta(seconds=1), BASE_TS - timedelta(seconds=2))},
            RiskRejectCode.MAX_ORDER_RATE,
            None,
        ),
        ({"realized_pnl_today": -600.0}, RiskRejectCode.MAX_DAILY_LOSS, None),
        (
            {"starting_equity": 100_000.0, "current_equity": 97_500.0},
            RiskRejectCode.MAX_DAILY_DRAWDOWN,
            None,
        ),
    ],
)
def test_risk_engine_blocks_required_risk_conditions(
    context_kwargs: dict[str, object],
    expected_code: RiskRejectCode,
    broker_order_id: str | None,
) -> None:
    engine = RiskEngine(_limits())

    decision = engine.evaluate_order(
        _intent(quantity=5_000.0),
        _context(**context_kwargs),
        broker_order_id=broker_order_id,
    )

    assert decision.status is RiskDecisionStatus.REJECTED
    assert decision.accepted is False
    assert decision.code is expected_code
    assert decision.reason == expected_code.value


def test_risk_engine_blocks_position_limit_and_reduce_only_exposure_increase() -> None:
    engine = RiskEngine(_limits(max_position_size=20_000.0))

    too_large = engine.evaluate_order(
        _intent(quantity=15_000.0),
        _context(positions={"EURUSD": _position(10_000.0)}),
    )
    reduce_only_growth = engine.evaluate_order(
        _intent(quantity=1_000.0, reduce_only=True),
        _context(positions={"EURUSD": _position(10_000.0)}),
    )

    assert too_large.code is RiskRejectCode.MAX_POSITION
    assert too_large.projected_position == 25_000.0
    assert reduce_only_growth.code is RiskRejectCode.REDUCE_ONLY_INCREASES_EXPOSURE
    assert reduce_only_growth.projected_position == 11_000.0


@pytest.mark.parametrize(
    ("limits", "context_kwargs", "expected_code"),
    [
        (
            {"max_spread_pips": 1.5},
            {"current_spread_pips": 2.0},
            RiskRejectCode.MAX_SPREAD,
        ),
        (
            {
                "post_loss_cooldown_seconds": 300.0,
                "loss_burst_threshold": 2,
            },
            {
                "recent_loss_timestamps": (
                    BASE_TS - timedelta(seconds=10),
                    BASE_TS - timedelta(seconds=20),
                )
            },
            RiskRejectCode.LOSS_COOLDOWN,
        ),
        ({}, {"volatility_guard_active": True}, RiskRejectCode.VOLATILITY_GUARD),
        ({}, {"news_guard_active": True}, RiskRejectCode.NEWS_GUARD),
        ({}, {"features_healthy": False}, RiskRejectCode.STALE_FEATURES),
        ({}, {"model_healthy": False}, RiskRejectCode.MODEL_UNHEALTHY),
    ],
)
def test_risk_engine_blocks_market_and_dependency_guards(
    limits: dict[str, object],
    context_kwargs: dict[str, object],
    expected_code: RiskRejectCode,
) -> None:
    engine = RiskEngine(_limits(**limits))

    decision = engine.evaluate_order(
        _intent(quantity=5_000.0),
        _context(**context_kwargs),
    )

    assert decision.status is RiskDecisionStatus.REJECTED
    assert decision.code is expected_code


def test_risk_engine_uses_target_position_for_projection() -> None:
    engine = RiskEngine(_limits(max_position_size=50_000.0))

    decision = engine.evaluate_order(
        _intent(quantity=None, target_position=-25_000.0),
        _context(positions={"EURUSD": _position(10_000.0)}),
    )

    assert decision.accepted is True
    assert decision.projected_position == -25_000.0


def _limits(
    *,
    max_position_size: float = 100_000.0,
    max_spread_pips: float | None = None,
    post_loss_cooldown_seconds: float = 0.0,
    loss_burst_threshold: int = 2,
) -> RiskLimits:
    return RiskLimits(
        max_position_size=max_position_size,
        max_daily_loss=500.0,
        max_daily_drawdown=0.02,
        max_spread_pips=max_spread_pips,
        max_order_rate_per_minute=2,
        stale_market_data_seconds=2.0,
        reject_burst_threshold=2,
        reject_burst_window_seconds=60.0,
        post_loss_cooldown_seconds=post_loss_cooldown_seconds,
        loss_burst_threshold=loss_burst_threshold,
    )


def _context(**overrides: object) -> RiskContext:
    values = {
        "checked_at": BASE_TS,
        "positions": {},
        "latest_market_data_at": BASE_TS - timedelta(seconds=1),
        "starting_equity": 100_000.0,
        "current_equity": 100_000.0,
    }
    values.update(overrides)
    return RiskContext(**values)


def _intent(
    *,
    quantity: float | None = 10_000.0,
    target_position: float | None = None,
    reduce_only: bool = False,
) -> OrderIntent:
    return OrderIntent(
        intent_id="intent-1",
        strategy_id="strategy-1",
        symbol="EURUSD",
        created_at=BASE_TS,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        target_position=target_position,
        reduce_only=reduce_only,
        paper=True,
    )


def _position(net_quantity: float) -> PositionState:
    return PositionState(
        symbol="EURUSD",
        timestamp=BASE_TS,
        net_quantity=net_quantity,
        average_entry_price=1.1 if net_quantity else 0.0,
        mark_price=1.1001,
        realized_pnl=0.0,
        unrealized_pnl=1.0,
        exposure_quote=net_quantity * 1.1001,
        position_mode=PositionMode.NETTING,
    )
