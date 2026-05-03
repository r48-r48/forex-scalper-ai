"""Canonical enums shared across domain events."""

from __future__ import annotations

from enum import StrEnum


class BookSide(StrEnum):
    """Side of the order book."""

    BID = "bid"
    ASK = "ask"


class BarType(StrEnum):
    """Canonical aggregated bar families."""

    TIME = "time"
    TICK = "tick"
    VOLATILITY = "volatility"
    IMBALANCE = "imbalance"


class OrderSide(StrEnum):
    """Trading direction."""

    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    """Execution intent category."""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(StrEnum):
    """Broker-facing lifetime instructions."""

    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"
    DAY = "day"


class LiquidityFlag(StrEnum):
    """Fill-side liquidity information."""

    MAKER = "maker"
    TAKER = "taker"
    UNKNOWN = "unknown"


class EventSource(StrEnum):
    """Origin of an event payload."""

    LIVE = "live"
    REPLAY = "replay"
    BACKTEST = "backtest"
    PAPER = "paper"
    EXTERNAL = "external"


class PositionMode(StrEnum):
    """Broker/account position accounting mode."""

    NETTING = "netting"
    HEDGING = "hedging"
