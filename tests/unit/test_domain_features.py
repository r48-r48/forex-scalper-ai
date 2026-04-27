"""Unit tests for feature snapshot contracts."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from scalper_ai.domain import FeatureSnapshot


def test_feature_snapshot_rejects_leaking_availability_timestamp() -> None:
    with pytest.raises(ValidationError, match="must not precede"):
        FeatureSnapshot(
            symbol="EURUSD",
            event_timestamp=datetime(2026, 3, 26, 9, 0, 1, tzinfo=timezone.utc),
            available_timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=timezone.utc),
            feature_set="microstructure",
            feature_version="v1",
            values={"spread": 0.0001},
        )


def test_feature_snapshot_rejects_non_finite_feature_value() -> None:
    with pytest.raises(ValidationError):
        FeatureSnapshot(
            symbol="EURUSD",
            event_timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=timezone.utc),
            available_timestamp=datetime(2026, 3, 26, 9, 0, tzinfo=timezone.utc),
            feature_set="microstructure",
            feature_version="v1",
            values={"spread": float("inf")},
        )
