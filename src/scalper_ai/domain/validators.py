"""Shared validation helpers and reusable constrained types for domain models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, Field, FiniteFloat, StringConstraints


def ensure_utc_datetime(value: datetime) -> datetime:
    """Require timezone-aware timestamps and normalize them to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware.")
    return value.astimezone(UTC)


def serialize_utc_datetime(value: datetime) -> str:
    """Serialize UTC datetimes using ISO-8601 with `Z` suffix."""

    normalized = ensure_utc_datetime(value)
    return normalized.isoformat().replace("+00:00", "Z")


UtcDatetime = Annotated[datetime, AfterValidator(ensure_utc_datetime)]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PositiveFiniteFloat = Annotated[FiniteFloat, Field(gt=0)]
NonNegativeFiniteFloat = Annotated[FiniteFloat, Field(ge=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]
