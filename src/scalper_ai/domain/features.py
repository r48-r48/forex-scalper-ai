"""Canonical feature snapshot events."""

from __future__ import annotations

from typing import Optional

from pydantic import Field, FiniteFloat, model_validator

from scalper_ai.domain.base import DomainModel
from scalper_ai.domain.enums import EventSource
from scalper_ai.domain.validators import NonEmptyStr, UtcDatetime


class FeatureSnapshot(DomainModel):
    """Stable feature metadata plus extensible numeric feature values."""

    symbol: NonEmptyStr
    event_timestamp: UtcDatetime
    available_timestamp: UtcDatetime
    feature_set: NonEmptyStr
    feature_version: NonEmptyStr
    values: dict[NonEmptyStr, FiniteFloat] = Field(min_length=1)
    source: Optional[EventSource] = None
    tags: Optional[dict[NonEmptyStr, NonEmptyStr]] = None

    @model_validator(mode="after")
    def validate_feature_timestamps(self) -> "FeatureSnapshot":
        if self.available_timestamp < self.event_timestamp:
            raise ValueError("Feature availability must not precede the event timestamp.")
        return self
