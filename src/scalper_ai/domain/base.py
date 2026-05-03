"""Base model and serialization helpers for canonical domain events."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar, cast

import orjson
from pydantic import BaseModel, ConfigDict

from scalper_ai.domain.validators import serialize_utc_datetime

DomainModelT = TypeVar("DomainModelT", bound="DomainModel")


class DomainModel(BaseModel):
    """Immutable, serialization-ready base for domain contracts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=False,
        ser_json_inf_nan="null",
    )

    @staticmethod
    def _to_json_compatible(value: Any) -> Any:
        """Recursively normalize values for stable JSON serialization."""

        if isinstance(value, datetime):
            return serialize_utc_datetime(value)
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, Mapping):
            return {key: DomainModel._to_json_compatible(item) for key, item in value.items()}
        if isinstance(value, list):
            return [DomainModel._to_json_compatible(item) for item in value]
        if isinstance(value, tuple):
            return [DomainModel._to_json_compatible(item) for item in value]
        return value

    def to_record(self, *, exclude_none: bool = False) -> dict[str, Any]:
        """Return a JSON-ready dictionary for persistence or transport."""

        payload = self.model_dump(mode="python", exclude_none=exclude_none)
        normalized = self._to_json_compatible(payload)
        return cast(dict[str, Any], normalized)

    def to_json_bytes(self, *, exclude_none: bool = False) -> bytes:
        """Return stable JSON bytes for replay logs or message transport."""

        return orjson.dumps(self.to_record(exclude_none=exclude_none), option=orjson.OPT_SORT_KEYS)

    def to_json_str(self, *, exclude_none: bool = False) -> str:
        """Return stable JSON text for diagnostics or snapshots."""

        return self.to_json_bytes(exclude_none=exclude_none).decode("utf-8")

    @classmethod
    def from_record(cls: type[DomainModelT], payload: Mapping[str, Any]) -> DomainModelT:
        """Materialize a domain event from a mapping payload."""

        return cls.model_validate(dict(payload))

    @classmethod
    def from_json_bytes(cls: type[DomainModelT], payload: bytes) -> DomainModelT:
        """Materialize a domain event from serialized JSON bytes."""

        return cls.model_validate(orjson.loads(payload))
