"""Macro context placeholders and provider interfaces."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from scalper_ai.features.schema import (
    MACRO_EVENT_RISK_FEATURE,
    MACRO_UTC_HOUR_FEATURE,
    MACRO_WEEKDAY_FEATURE,
)


class MacroContextProvider(Protocol):
    """Protocol for injecting external macro context into feature pipelines."""

    def get_features(self, *, symbol: str, event_timestamp: datetime) -> dict[str, float]:
        """Return flat macro feature values available at the given timestamp."""


class NullMacroContextProvider:
    """Default macro provider with stable placeholder features only."""

    def get_features(self, *, symbol: str, event_timestamp: datetime) -> dict[str, float]:
        del symbol
        utc_hour = (
            event_timestamp.hour
            + (event_timestamp.minute / 60.0)
            + (event_timestamp.second / 3600.0)
            + (event_timestamp.microsecond / 3_600_000_000.0)
        )
        weekday = float(event_timestamp.weekday())
        return {
            MACRO_UTC_HOUR_FEATURE: utc_hour / 24.0,
            MACRO_WEEKDAY_FEATURE: weekday / 6.0,
            MACRO_EVENT_RISK_FEATURE: 0.0,
        }
