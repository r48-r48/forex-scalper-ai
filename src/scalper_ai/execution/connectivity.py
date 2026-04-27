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

    def snapshot_age_seconds(self) -> float | None:
        """Return the age of the latest broker snapshot in seconds if available."""

        if self.last_snapshot_at is None:
            return None
        age_seconds = (self.checked_at - self.last_snapshot_at).total_seconds()
        return max(0.0, age_seconds)
