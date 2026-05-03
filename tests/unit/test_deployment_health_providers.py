"""Tests for concrete runtime dependency health providers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scalper_ai.deployment import (
    RuntimeDataFreshnessProvider,
    RuntimeGuardStateProvider,
    RuntimeModelHealthProvider,
)
from scalper_ai.domain import FeatureSnapshot, TickEvent
from scalper_ai.domain.enums import EventSource


def test_runtime_data_freshness_provider_tracks_market_and_feature_timestamps() -> None:
    now = datetime(2026, 5, 3, 10, 0, 5, tzinfo=UTC)
    provider = RuntimeDataFreshnessProvider(
        market_data_stale_after_seconds=10.0,
        features_stale_after_seconds=10.0,
        clock=lambda: now,
    )
    provider.record_market_data_event(
        TickEvent(
            symbol="EURUSD",
            venue="paper",
            event_timestamp=now - timedelta(seconds=2),
            received_timestamp=now - timedelta(seconds=1),
            bid=1.0999,
            ask=1.1001,
            source=EventSource.PAPER,
        )
    )
    provider.record_market_data_event(
        TickEvent(
            symbol="GBPUSD",
            venue="stale-source",
            event_timestamp=now - timedelta(seconds=9),
            received_timestamp=now - timedelta(seconds=8),
            bid=1.2499,
            ask=1.2501,
            source=EventSource.PAPER,
        )
    )
    provider.record_feature_snapshot(
        _feature_snapshot(
            event_timestamp=now - timedelta(seconds=2),
            available_timestamp=now - timedelta(seconds=1),
        )
    )
    provider.record_feature_snapshot(
        _feature_snapshot(
            event_timestamp=now - timedelta(seconds=9),
            available_timestamp=now - timedelta(seconds=8),
            feature_set="stale-features",
        )
    )

    snapshot = provider.describe_data_freshness()

    assert snapshot.market_data_fresh() is True
    assert snapshot.features_fresh() is True
    assert snapshot.market_data_age_seconds() == 1.0
    assert snapshot.features_age_seconds() == 1.0
    assert snapshot.details["symbol"] == "EURUSD"
    assert snapshot.details["market_data_venue"] == "paper"
    assert snapshot.details["feature_set"] == "unit-test-features"


def test_runtime_model_health_provider_tracks_prediction_freshness() -> None:
    current_time = datetime(2026, 5, 3, 10, 1, tzinfo=UTC)

    def clock() -> datetime:
        return current_time

    provider = RuntimeModelHealthProvider(
        model_id="eurusd-transformer",
        prediction_stale_after_seconds=5.0,
        clock=clock,
    )
    provider.mark_loaded(timestamp=current_time)

    loaded_snapshot = provider.describe_model_health()
    assert loaded_snapshot.ready is True
    assert loaded_snapshot.healthy_for_trading() is False

    provider.record_prediction(timestamp=current_time, details={"batch_size": 1})
    fresh_snapshot = provider.describe_model_health()
    assert fresh_snapshot.healthy_for_trading() is True
    assert fresh_snapshot.details["batch_size"] == 1

    current_time = current_time + timedelta(seconds=10)
    stale_snapshot = provider.describe_model_health()
    assert stale_snapshot.ready is True
    assert stale_snapshot.healthy_for_trading() is False


def test_runtime_guard_state_provider_derives_volatility_and_tracks_news_guard() -> None:
    now = datetime(2026, 5, 3, 10, 2, tzinfo=UTC)
    provider = RuntimeGuardStateProvider(
        volatility_threshold=0.002,
        clock=lambda: now,
    )

    provider.record_feature_snapshot(
        _feature_snapshot(
            event_timestamp=now,
            available_timestamp=now,
            realized_volatility=0.003,
        )
    )
    provider.set_news_guard(True, reason="central_bank_event", details={"calendar": "test"})

    snapshot = provider.describe_guard_state()

    assert snapshot.volatility_guard_active is True
    assert snapshot.volatility_reason == "volatility_threshold_exceeded"
    assert snapshot.news_guard_active is True
    assert snapshot.news_reason == "central_bank_event"
    assert snapshot.details["volatility_feature_value"] == 0.003
    assert snapshot.details["calendar"] == "test"


def _feature_snapshot(
    *,
    event_timestamp: datetime,
    available_timestamp: datetime,
    realized_volatility: float = 0.001,
    feature_set: str = "unit-test-features",
) -> FeatureSnapshot:
    return FeatureSnapshot(
        symbol="EURUSD",
        event_timestamp=event_timestamp,
        available_timestamp=available_timestamp,
        feature_set=feature_set,
        feature_version="1",
        values={"realized_volatility": realized_volatility, "spread": 0.0002},
        source=EventSource.PAPER,
    )
