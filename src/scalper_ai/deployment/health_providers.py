"""Provider contracts and concrete trackers for runtime dependency health."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from scalper_ai.domain import BookSnapshot, FeatureSnapshot, TickEvent
from scalper_ai.features.schema import REALIZED_VOLATILITY_FEATURE

Clock = Callable[[], datetime]


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


class RuntimeDataFreshnessProvider:
    """Mutable live tracker for latest market-data and feature timestamps."""

    def __init__(
        self,
        *,
        market_data_stale_after_seconds: float | None,
        features_stale_after_seconds: float | None,
        source: str = "runtime_data_freshness",
        clock: Clock | None = None,
    ) -> None:
        _ensure_positive(
            market_data_stale_after_seconds,
            field_name="market_data_stale_after_seconds",
        )
        _ensure_positive(
            features_stale_after_seconds,
            field_name="features_stale_after_seconds",
        )
        if not source.strip():
            raise ValueError("source must be non-empty.")
        self._market_data_stale_after_seconds = market_data_stale_after_seconds
        self._features_stale_after_seconds = features_stale_after_seconds
        self._source = source
        self._clock = clock or _utc_now
        self._latest_market_data_at: datetime | None = None
        self._latest_features_at: datetime | None = None
        self._latest_symbol: str | None = None
        self._market_data_details: dict[str, object] = {}
        self._feature_details: dict[str, object] = {}

    def record_market_data_event(self, event: TickEvent | BookSnapshot) -> None:
        """Record freshness from a canonical market-data event."""

        self.record_market_data_timestamp(
            event.received_timestamp,
            symbol=event.symbol,
            details={
                "market_data_event_timestamp": event.event_timestamp.isoformat(),
                "market_data_venue": event.venue,
            },
        )

    def record_market_data_timestamp(
        self,
        timestamp: datetime,
        *,
        symbol: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        """Record a latest market-data availability timestamp."""

        _ensure_aware(timestamp, field_name="timestamp")
        previous_timestamp = self._latest_market_data_at
        self._latest_market_data_at = _latest_timestamp(previous_timestamp, timestamp)
        if previous_timestamp is not None and timestamp < previous_timestamp:
            return
        if symbol is not None:
            normalized_symbol = symbol.strip()
            if not normalized_symbol:
                raise ValueError("symbol must be non-empty when provided.")
            self._latest_symbol = normalized_symbol
        self._market_data_details = dict(details or {})

    def record_feature_snapshot(self, snapshot: FeatureSnapshot) -> None:
        """Record freshness from an online feature snapshot."""

        previous_timestamp = self._latest_features_at
        self._latest_features_at = _latest_timestamp(
            previous_timestamp,
            snapshot.available_timestamp,
        )
        if (
            previous_timestamp is not None
            and snapshot.available_timestamp < previous_timestamp
        ):
            return
        self._latest_symbol = snapshot.symbol
        self._feature_details = {
            "feature_event_timestamp": snapshot.event_timestamp.isoformat(),
            "feature_set": snapshot.feature_set,
            "feature_version": snapshot.feature_version,
        }
        if snapshot.source is not None:
            self._feature_details["feature_source"] = snapshot.source.value

    def describe_data_freshness(self) -> DataFreshnessSnapshot:
        """Return one data freshness snapshot."""

        details: dict[str, object] = {}
        if self._latest_symbol is not None:
            details["symbol"] = self._latest_symbol
        details.update(self._market_data_details)
        details.update(self._feature_details)
        return DataFreshnessSnapshot(
            checked_at=_checked_at(self._clock),
            latest_market_data_at=self._latest_market_data_at,
            latest_features_at=self._latest_features_at,
            market_data_stale_after_seconds=self._market_data_stale_after_seconds,
            features_stale_after_seconds=self._features_stale_after_seconds,
            source=self._source,
            details=details,
        )


class RuntimeModelHealthProvider:
    """Mutable live tracker for model load and prediction readiness."""

    def __init__(
        self,
        *,
        model_id: str | None = None,
        prediction_stale_after_seconds: float | None = None,
        source: str = "runtime_model_health",
        clock: Clock | None = None,
    ) -> None:
        _ensure_positive(
            prediction_stale_after_seconds,
            field_name="prediction_stale_after_seconds",
        )
        if model_id is not None and not model_id.strip():
            raise ValueError("model_id must be non-empty when provided.")
        if not source.strip():
            raise ValueError("source must be non-empty.")
        self._model_id = None if model_id is None else model_id.strip()
        self._prediction_stale_after_seconds = prediction_stale_after_seconds
        self._source = source
        self._clock = clock or _utc_now
        self._ready = False
        self._last_loaded_at: datetime | None = None
        self._last_prediction_at: datetime | None = None
        self._reason = "model_not_loaded"
        self._details: dict[str, object] = {}

    def mark_loaded(
        self,
        *,
        model_id: str | None = None,
        timestamp: datetime | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        """Mark the model artifact as loaded and usable."""

        resolved_timestamp = _optional_or_clock(timestamp, self._clock)
        if model_id is not None:
            normalized_model_id = model_id.strip()
            if not normalized_model_id:
                raise ValueError("model_id must be non-empty when provided.")
            self._model_id = normalized_model_id
        self._ready = True
        self._last_loaded_at = resolved_timestamp
        self._reason = None
        self._details = dict(details or {})

    def record_prediction(
        self,
        *,
        timestamp: datetime | None = None,
        model_id: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        """Record a successful online prediction."""

        resolved_timestamp = _optional_or_clock(timestamp, self._clock)
        if model_id is not None:
            normalized_model_id = model_id.strip()
            if not normalized_model_id:
                raise ValueError("model_id must be non-empty when provided.")
            self._model_id = normalized_model_id
        self._ready = True
        self._last_prediction_at = resolved_timestamp
        self._reason = None
        self._details = dict(details or {})

    def mark_unavailable(
        self,
        reason: str,
        *,
        timestamp: datetime | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        """Mark the model as unavailable for trading decisions."""

        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("reason must be non-empty.")
        self._ready = False
        self._reason = normalized_reason
        self._details = dict(details or {})
        self._details["unavailable_at"] = _optional_or_clock(
            timestamp,
            self._clock,
        ).isoformat()

    def describe_model_health(self) -> ModelHealthSnapshot:
        """Return one model readiness snapshot."""

        return ModelHealthSnapshot(
            checked_at=_checked_at(self._clock),
            ready=self._ready,
            model_id=self._model_id,
            last_loaded_at=self._last_loaded_at,
            last_prediction_at=self._last_prediction_at,
            last_prediction_stale_after_seconds=self._prediction_stale_after_seconds,
            reason=self._reason,
            source=self._source,
            details=dict(self._details),
        )


class RuntimeGuardStateProvider:
    """Mutable live tracker for volatility/news guard state."""

    def __init__(
        self,
        *,
        volatility_threshold: float | None = None,
        volatility_feature_name: str = REALIZED_VOLATILITY_FEATURE,
        source: str = "runtime_guard_state",
        clock: Clock | None = None,
    ) -> None:
        if volatility_threshold is not None and volatility_threshold < 0:
            raise ValueError("volatility_threshold must be non-negative when provided.")
        normalized_feature_name = volatility_feature_name.strip()
        if not normalized_feature_name:
            raise ValueError("volatility_feature_name must be non-empty.")
        if not source.strip():
            raise ValueError("source must be non-empty.")
        self._volatility_threshold = volatility_threshold
        self._volatility_feature_name = normalized_feature_name
        self._source = source
        self._clock = clock or _utc_now
        self._volatility_guard_active = False
        self._news_guard_active = False
        self._volatility_reason: str | None = None
        self._news_reason: str | None = None
        self._details: dict[str, object] = {}

    def record_feature_snapshot(self, snapshot: FeatureSnapshot) -> None:
        """Optionally derive the volatility guard from an online feature snapshot."""

        if self._volatility_threshold is None:
            return
        value = snapshot.values.get(self._volatility_feature_name)
        if value is None:
            return
        active = float(value) > self._volatility_threshold
        self.set_volatility_guard(
            active,
            reason="volatility_threshold_exceeded" if active else None,
            details={
                "symbol": snapshot.symbol,
                "volatility_feature_name": self._volatility_feature_name,
                "volatility_feature_value": float(value),
                "volatility_threshold": self._volatility_threshold,
                "feature_available_timestamp": snapshot.available_timestamp.isoformat(),
            },
        )

    def set_volatility_guard(
        self,
        active: bool,
        *,
        reason: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        """Set the volatility guard state explicitly."""

        self._volatility_guard_active = bool(active)
        self._volatility_reason = _optional_reason(reason)
        self._details.update(dict(details or {}))
        self._details["volatility_guard_updated_at"] = _checked_at(self._clock).isoformat()

    def set_news_guard(
        self,
        active: bool,
        *,
        reason: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        """Set the news guard state explicitly."""

        self._news_guard_active = bool(active)
        self._news_reason = _optional_reason(reason)
        self._details.update(dict(details or {}))
        self._details["news_guard_updated_at"] = _checked_at(self._clock).isoformat()

    def describe_guard_state(self) -> GuardStateSnapshot:
        """Return one guard state snapshot."""

        return GuardStateSnapshot(
            checked_at=_checked_at(self._clock),
            volatility_guard_active=self._volatility_guard_active,
            news_guard_active=self._news_guard_active,
            volatility_reason=self._volatility_reason,
            news_reason=self._news_reason,
            source=self._source,
            details=dict(self._details),
        )


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


def _latest_timestamp(current: datetime | None, candidate: datetime) -> datetime:
    if current is None or candidate > current:
        return candidate
    return current


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _checked_at(clock: Clock) -> datetime:
    timestamp = clock()
    _ensure_aware(timestamp, field_name="clock")
    return timestamp


def _optional_or_clock(timestamp: datetime | None, clock: Clock) -> datetime:
    if timestamp is None:
        return _checked_at(clock)
    _ensure_aware(timestamp, field_name="timestamp")
    return timestamp


def _optional_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    normalized = reason.strip()
    if not normalized:
        raise ValueError("reason must be non-empty when provided.")
    return normalized
