"""Broker account snapshots used by pre-trade risk checks."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class BrokerAccountSnapshot:
    """Normalized broker account state relevant to runtime risk budgets."""

    venue: str
    checked_at: datetime
    balance: float | None = None
    equity: float | None = None
    margin: float | None = None
    margin_free: float | None = None
    margin_level_percent: float | None = None
    effective_leverage: float | None = None
    leverage: float | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        if not self.venue.strip():
            raise ValueError("venue must be non-empty.")
        if self.checked_at.tzinfo is None or self.checked_at.utcoffset() is None:
            raise ValueError("checked_at must be timezone-aware.")
        for field_name, value in {
            "balance": self.balance,
            "equity": self.equity,
            "margin": self.margin,
            "margin_free": self.margin_free,
            "margin_level_percent": self.margin_level_percent,
            "effective_leverage": self.effective_leverage,
            "leverage": self.leverage,
        }.items():
            if value is None:
                continue
            if not math.isfinite(value):
                raise ValueError(f"{field_name} must be finite when provided.")
        for field_name, value in {
            "margin": self.margin,
            "margin_level_percent": self.margin_level_percent,
            "effective_leverage": self.effective_leverage,
            "leverage": self.leverage,
        }.items():
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be non-negative when provided.")
        if self.currency is not None and not self.currency.strip():
            raise ValueError("currency must be non-empty when provided.")


class BrokerAccountProvider(Protocol):
    """Broker-facing contract that exposes normalized account risk state."""

    def describe_broker_account(self) -> BrokerAccountSnapshot:
        """Return the latest broker account snapshot."""
