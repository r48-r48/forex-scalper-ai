"""Provider contracts for richer runtime dependency health checks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


def _ensure_aware(timestamp: datetime, *, field_name: str) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")


def _ensure_positive(value: float | None, *, field_name: str) -> None:
    if value is not None and value <= 0:
        raise ValueError(f"{field_name} must be greater than zero when provided.")


@dataclass(frozen=True)
class DataFreshnessSnapshot:
    """Latest online market-data and feature freshness state."""

    checked_at: datetime
    latest_market_data_at: datetime | None = None
    latest_features_at: datetime | None = None
    market_data_stale_after_seconds: float | None = None
    features_stale_after_seconds: float | None = None
    source: str = "runtime"
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _ensure_aware(self.checked_at, field_name="checked_at")
        if self.latest_market_data_at is not None:
            _ensure_aware(self.latest_market_data_at, field_name="latest_market_data_at")
        if self.latest_features_at is not None:
            _ensure_aware(self.latest_features_at, field_name="latest_features_at")
        _ensure_positive(
            self.market_data_stale_after_seconds,
            field_name="market_data_stale_after_seconds",
        )
        _ensure_positive(
            self.features_stale_after_seconds,
            field_name="features_stale_after_seconds",
        )
        if not self.source.strip():
            raise ValueError("source must be non-empty.")

    def market_data_age_seconds(self) -> float | None:
        """Return market-data age relative to checked_at."""

        return _age_seconds(self.checked_at, self.latest_market_data_at)

    def features_age_seconds(self) -> float | None:
        """Return feature age relative to checked_at."""

        return _age_seconds(self.checked_at, self.latest_features_at)

    def market_data_fresh(self) -> bool:
        """Return whether market data is present and inside the stale threshold."""

        return _is_fresh(
            self.market_data_age_seconds(),
            stale_after_seconds=self.market_data_stale_after_seconds,
        )

    def features_fresh(self) -> bool:
        """Return whether features are present and inside the stale threshold."""

        return _is_fresh(
            self.features_age_seconds(),
            stale_after_seconds=self.features_stale_after_seconds,
        )


@dataclass(frozen=True)
class ModelHealthSnapshot:
    """Latest forecasting or policy model readiness state."""

    checked_at: datetime
    ready: bool
    model_id: str | None = None
    last_loaded_at: datetime | None = None
    last_prediction_at: datetime | None = None
    last_prediction_stale_after_seconds: float | None = None
    reason: str | None = None
    source: str = "runtime"
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _ensure_aware(self.checked_at, field_name="checked_at")
        if self.last_loaded_at is not None:
            _ensure_aware(self.last_loaded_at, field_name="last_loaded_at")
        if self.last_prediction_at is not None:
            _ensure_aware(self.last_prediction_at, field_name="last_prediction_at")
        _ensure_positive(
            self.last_prediction_stale_after_seconds,
            field_name="last_prediction_stale_after_seconds",
        )
        if self.model_id is not None and not self.model_id.strip():
            raise ValueError("model_id must be non-empty when provided.")
        if self.reason is not None and not self.reason.strip():
            raise ValueError("reason must be non-empty when provided.")
        if not self.source.strip():
            raise ValueError("source must be non-empty.")

    def prediction_age_seconds(self) -> float | None:
        """Return the latest prediction age relative to checked_at."""

        return _age_seconds(self.checked_at, self.last_prediction_at)

    def prediction_fresh(self) -> bool:
        """Return whether the latest prediction is inside the configured threshold."""

        if (
            self.last_prediction_stale_after_seconds is None
            and self.last_prediction_at is None
        ):
            return True
        return _is_fresh(
            self.prediction_age_seconds(),
            stale_after_seconds=self.last_prediction_stale_after_seconds,
        )

    def healthy_for_trading(self) -> bool:
        """Return whether the model can be trusted by the pre-trade risk gate."""

        return self.ready and self.prediction_fresh()


@dataclass(frozen=True)
class GuardStateSnapshot:
    """Latest online dependency guard state used by risk filters."""

    checked_at: datetime
    volatility_guard_active: bool = False
    news_guard_active: bool = False
    volatility_reason: str | None = None
    news_reason: str | None = None
    source: str = "runtime"
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _ensure_aware(self.checked_at, field_name="checked_at")
        if self.volatility_reason is not None and not self.volatility_reason.strip():
            raise ValueError("volatility_reason must be non-empty when provided.")
        if self.news_reason is not None and not self.news_reason.strip():
            raise ValueError("news_reason must be non-empty when provided.")
        if not self.source.strip():
            raise ValueError("source must be non-empty.")


class DataFreshnessProvider(Protocol):
    """Provider that exposes latest market-data and feature freshness."""

    def describe_data_freshness(self) -> DataFreshnessSnapshot:
        """Return one freshness snapshot."""


class ModelHealthProvider(Protocol):
    """Provider that exposes online model readiness."""

    def describe_model_health(self) -> ModelHealthSnapshot:
        """Return one model health snapshot."""


class GuardStateProvider(Protocol):
    """Provider that exposes volatility/news guard state."""

    def describe_guard_state(self) -> GuardStateSnapshot:
        """Return one dependency guard snapshot."""


def _age_seconds(checked_at: datetime, timestamp: datetime | None) -> float | None:
    if timestamp is None:
        return None
    return max(0.0, (checked_at - timestamp).total_seconds())


def _is_fresh(
    age_seconds: float | None,
    *,
    stale_after_seconds: float | None,
) -> bool:
    if age_seconds is None:
        return False
    if stale_after_seconds is None:
        return True
    return age_seconds <= stale_after_seconds
