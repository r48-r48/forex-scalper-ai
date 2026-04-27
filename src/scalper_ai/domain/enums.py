"""Canonical enums shared across domain events."""

from __future__ import annotations

from enum import Enum


class BookSide(str, Enum):
    """Side of the order book."""

    BID = "bid"
    ASK = "ask"


class BarType(str, Enum):
    """Canonical aggregated bar families."""

    TIME = "time"
    TICK = "tick"
    VOLATILITY = "volatility"
    IMBALANCE = "imbalance"


class OrderSide(str, Enum):
    """Trading direction."""

    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    """Execution intent category."""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(str, Enum):
    """Broker-facing lifetime instructions."""

    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"
    DAY = "day"


class LiquidityFlag(str, Enum):
    """Fill-side liquidity information."""

    MAKER = "maker"
    TAKER = "taker"
    UNKNOWN = "unknown"


class EventSource(str, Enum):
    """Origin of an event payload."""

    LIVE = "live"
    REPLAY = "replay"
    BACKTEST = "backtest"
    PAPER = "paper"
    EXTERNAL = "external"


class PositionMode(str, Enum):
    """Broker/account position accounting mode."""

    NETTING = "netting"
    HEDGING = "hedging"
