"""Unit tests for the online feature calculator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scalper_ai.domain import TickEvent
from scalper_ai.features.online import OnlineFeatureCalculator
from scalper_ai.features.schema import FeatureConfig, feature_names


def test_online_calculator_emits_stable_feature_vector_without_l2() -> None:
    calculator = OnlineFeatureCalculator(
        config=FeatureConfig(
            volatility_window=3,
            quote_intensity_window_seconds=10.0,
            ofi_window=3,
            toxicity_window=3,
            mlofi_depth=2,
        )
    )

    snapshot = calculator.update_tick(
        TickEvent(
            symbol="EURUSD",
            venue="TEST",
            event_timestamp=datetime(2026, 3, 26, 9, 0, 0, tzinfo=UTC),
            received_timestamp=datetime(2026, 3, 26, 9, 0, 0, tzinfo=UTC),
            bid=1.1000,
            ask=1.1002,
            bid_size=2.0,
            ask_size=3.0,
            last_price=1.1001,
            last_size=1.0,
        )
    )

    assert tuple(snapshot.values) == feature_names(mlofi_depth=2)
    assert snapshot.values["mlofi_l1"] == 0.0
    assert snapshot.values["mlofi_l2"] == 0.0
    assert snapshot.values["mlofi_total"] == 0.0


def test_online_calculator_rolls_forward_ofi_and_volatility() -> None:
    calculator = OnlineFeatureCalculator(
        config=FeatureConfig(
            volatility_window=3,
            quote_intensity_window_seconds=10.0,
            ofi_window=3,
            toxicity_window=3,
            mlofi_depth=1,
        )
    )
    base_timestamp = datetime(2026, 3, 26, 9, 0, 0, tzinfo=UTC)

    calculator.update_tick(
        TickEvent(
            symbol="EURUSD",
            venue="TEST",
            event_timestamp=base_timestamp,
            received_timestamp=base_timestamp,
            bid=1.1000,
            ask=1.1002,
            bid_size=2.0,
            ask_size=3.0,
            last_price=1.1001,
            last_size=1.0,
        )
    )
    snapshot = calculator.update_tick(
        TickEvent(
            symbol="EURUSD",
            venue="TEST",
            event_timestamp=base_timestamp + timedelta(seconds=1),
            received_timestamp=base_timestamp + timedelta(seconds=1),
            bid=1.1001,
            ask=1.1002,
            bid_size=4.0,
            ask_size=2.0,
            last_price=1.1002,
            last_size=1.5,
        )
    )

    assert snapshot.values["ofi"] == 5.0
    assert snapshot.values["realized_volatility"] > 0.0
