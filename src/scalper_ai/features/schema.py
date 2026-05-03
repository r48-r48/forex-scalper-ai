"""Shared contracts and naming for feature engineering pipelines."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from scalper_ai.domain import EventSource, FeatureSnapshot

FEATURE_SET = "microstructure"
FEATURE_VERSION = "v1"

SPREAD_FEATURE = "spread"
SPREAD_BPS_FEATURE = "spread_bps"
MID_RETURN_FEATURE = "mid_return"
REALIZED_VOLATILITY_FEATURE = "realized_volatility"
QUOTE_INTENSITY_FEATURE = "quote_intensity"
OFI_FEATURE = "ofi"
MLOFI_TOTAL_FEATURE = "mlofi_total"
TOXICITY_VPIN_FEATURE = "toxicity_vpin"
MACRO_UTC_HOUR_FEATURE = "macro_utc_hour"
MACRO_WEEKDAY_FEATURE = "macro_weekday"
MACRO_EVENT_RISK_FEATURE = "macro_event_risk"


def mlofi_feature_name(level: int) -> str:
    """Return the stable feature name for a given MLOFI depth level."""

    if level <= 0:
        raise ValueError("MLOFI levels must be positive integers.")
    return f"mlofi_l{level}"


@dataclass(frozen=True)
class FeatureConfig:
    """Configuration shared by offline and online feature calculators."""

    volatility_window: int = 20
    quote_intensity_window_seconds: float = 60.0
    ofi_window: int = 10
    toxicity_window: int = 20
    mlofi_depth: int = 5

    def __post_init__(self) -> None:
        if self.volatility_window <= 0:
            raise ValueError("volatility_window must be greater than zero.")
        if self.quote_intensity_window_seconds <= 0:
            raise ValueError("quote_intensity_window_seconds must be greater than zero.")
        if self.ofi_window <= 0:
            raise ValueError("ofi_window must be greater than zero.")
        if self.toxicity_window <= 0:
            raise ValueError("toxicity_window must be greater than zero.")
        if self.mlofi_depth <= 0:
            raise ValueError("mlofi_depth must be greater than zero.")


def feature_names(*, mlofi_depth: int) -> tuple[str, ...]:
    """Return the stable ordered feature vector for a configuration."""

    names = [
        SPREAD_FEATURE,
        SPREAD_BPS_FEATURE,
        MID_RETURN_FEATURE,
        REALIZED_VOLATILITY_FEATURE,
        QUOTE_INTENSITY_FEATURE,
        OFI_FEATURE,
        MLOFI_TOTAL_FEATURE,
        TOXICITY_VPIN_FEATURE,
        MACRO_UTC_HOUR_FEATURE,
        MACRO_WEEKDAY_FEATURE,
        MACRO_EVENT_RISK_FEATURE,
    ]
    names.extend(mlofi_feature_name(level) for level in range(1, mlofi_depth + 1))
    return tuple(names)


def empty_feature_values(config: FeatureConfig) -> dict[str, float]:
    """Return a zero-initialized feature vector for the given configuration."""

    return {name: 0.0 for name in feature_names(mlofi_depth=config.mlofi_depth)}


def normalize_feature_values(values: Mapping[str, float]) -> dict[str, float]:
    """Normalize arbitrary numeric mappings into finite Python floats."""

    normalized: dict[str, float] = {}
    for name, value in values.items():
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            raise ValueError(f"Feature '{name}' must be finite.")
        normalized[name] = numeric_value
    return normalized


def feature_snapshot_from_values(
    *,
    symbol: str,
    event_timestamp: datetime,
    available_timestamp: datetime,
    values: Mapping[str, float],
    source: EventSource | None = None,
    tags: dict[str, str] | None = None,
) -> FeatureSnapshot:
    """Build a canonical feature snapshot from a flat numeric mapping."""

    return FeatureSnapshot(
        symbol=symbol,
        event_timestamp=event_timestamp,
        available_timestamp=available_timestamp,
        feature_set=FEATURE_SET,
        feature_version=FEATURE_VERSION,
        values=normalize_feature_values(values),
        source=source,
        tags=tags,
    )
