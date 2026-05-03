"""Broker connectivity snapshots used by runtime health checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class BrokerConnectivitySnapshot:
    """One normalized broker connectivity snapshot."""

    venue: str
    checked_at: datetime
    connected: bool
    last_snapshot_at: datetime | None = None
    latency_ms: float | None = None
    reconnect_enabled: bool | None = None
    reconnect_attempt_count: int | None = None
    circuit_breaker_open: bool | None = None
    last_reconnect_at: datetime | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        if self.checked_at.tzinfo is None or self.checked_at.utcoffset() is None:
            raise ValueError("checked_at must be timezone-aware.")
        if (
            self.last_snapshot_at is not None
            and (
                self.last_snapshot_at.tzinfo is None
                or self.last_snapshot_at.utcoffset() is None
            )
        ):
            raise ValueError("last_snapshot_at must be timezone-aware when provided.")
        if (
            self.last_reconnect_at is not None
            and (
                self.last_reconnect_at.tzinfo is None
                or self.last_reconnect_at.utcoffset() is None
            )
        ):
            raise ValueError("last_reconnect_at must be timezone-aware when provided.")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative when provided.")
        if self.reconnect_attempt_count is not None and self.reconnect_attempt_count < 0:
            raise ValueError("reconnect_attempt_count must be non-negative when provided.")
        if self.last_error is not None and not self.last_error.strip():
            raise ValueError("last_error must be non-empty when provided.")

    def snapshot_age_seconds(self) -> float | None:
        """Return the age of the latest broker snapshot in seconds if available."""

        if self.last_snapshot_at is None:
            return None
        age_seconds = (self.checked_at - self.last_snapshot_at).total_seconds()
        return max(0.0, age_seconds)
