"""Bar builders for time, tick, volatility, and imbalance aggregation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import log
from typing import Any, Optional

from scalper_ai.data.preprocessing import mid_price, trade_proxy_price, volume_proxy
from scalper_ai.domain import BarEvent, BarType, TickEvent


@dataclass
class _BarAccumulator:
    """Internal mutable aggregation state used by bar builders."""

    symbol: str
    venue: str
    start_timestamp: datetime
    end_timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    tick_count: int
    notional: float
    buy_volume: float
    sell_volume: float

    @classmethod
    def from_tick(cls, tick: TickEvent, signed_volume: float = 0.0) -> "_BarAccumulator":
        price = trade_proxy_price(tick)
        volume = volume_proxy(tick)
        buy_volume = volume if signed_volume > 0 else 0.0
        sell_volume = volume if signed_volume < 0 else 0.0
        return cls(
            symbol=tick.symbol,
            venue=tick.venue,
            start_timestamp=tick.event_timestamp,
            end_timestamp=tick.event_timestamp,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=volume,
            tick_count=1,
            notional=price * volume,
            buy_volume=buy_volume,
            sell_volume=sell_volume,
        )

    def update(self, tick: TickEvent, signed_volume: float = 0.0) -> None:
        price = trade_proxy_price(tick)
        volume = volume_proxy(tick)
        self.end_timestamp = tick.event_timestamp
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += volume
        self.tick_count += 1
        self.notional += price * volume
        if signed_volume > 0:
            self.buy_volume += volume
        elif signed_volume < 0:
            self.sell_volume += volume

    def to_bar(
        self,
        bar_type: BarType,
        *,
        imbalance: float | None = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> BarEvent:
        vwap = self.notional / self.volume if self.volume > 0 else None
        return BarEvent(
            symbol=self.symbol,
            venue=self.venue,
            bar_type=bar_type,
            start_timestamp=self.start_timestamp,
            end_timestamp=self.end_timestamp,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            tick_count=self.tick_count,
            notional=self.notional,
            vwap=vwap,
            buy_volume=self.buy_volume,
            sell_volume=self.sell_volume,
            imbalance=imbalance,
            metadata=metadata,
        )


class BaseBarBuilder(ABC):
    """Shared interface for streaming tick-to-bar aggregation."""

    def __init__(self, bar_type: BarType) -> None:
        self._bar_type = bar_type

    @abstractmethod
    def update(self, tick: TickEvent) -> list[BarEvent]:
        """Consume one tick and return any completed bars."""

    @abstractmethod
    def flush(self) -> list[BarEvent]:
        """Finalize and emit any partial bar still in progress."""


class TimeBarBuilder(BaseBarBuilder):
    """Aggregate ticks into fixed time buckets without emitting empty bars."""

    def __init__(self, interval: timedelta) -> None:
        super().__init__(bar_type=BarType.TIME)
        if interval.total_seconds() <= 0:
            raise ValueError("interval must be positive.")
        self._interval = interval
        self._bucket_start: datetime | None = None
        self._state: _BarAccumulator | None = None

    def update(self, tick: TickEvent) -> list[BarEvent]:
        bucket_start = floor_timestamp(tick.event_timestamp, self._interval)
        if self._state is None:
            self._bucket_start = bucket_start
            self._state = _BarAccumulator.from_tick(tick)
            self._state.start_timestamp = bucket_start
            return []

        assert self._bucket_start is not None
        if bucket_start != self._bucket_start:
            completed = self._state.to_bar(self._bar_type, metadata={"interval": str(self._interval)})
            self._bucket_start = bucket_start
            self._state = _BarAccumulator.from_tick(tick)
            self._state.start_timestamp = bucket_start
            return [completed]

        self._state.update(tick)
        return []

    def flush(self) -> list[BarEvent]:
        if self._state is None:
            return []
        completed = self._state.to_bar(self._bar_type, metadata={"interval": str(self._interval)})
        self._state = None
        self._bucket_start = None
        return [completed]


class TickBarBuilder(BaseBarBuilder):
    """Aggregate ticks into fixed-count bars."""

    def __init__(self, ticks_per_bar: int) -> None:
        super().__init__(bar_type=BarType.TICK)
        if ticks_per_bar <= 0:
            raise ValueError("ticks_per_bar must be greater than zero.")
        self._ticks_per_bar = ticks_per_bar
        self._state: _BarAccumulator | None = None

    def update(self, tick: TickEvent) -> list[BarEvent]:
        if self._state is None:
            self._state = _BarAccumulator.from_tick(tick)
        else:
            self._state.update(tick)

        if self._state.tick_count < self._ticks_per_bar:
            return []

        completed = self._state.to_bar(self._bar_type, metadata={"ticks_per_bar": self._ticks_per_bar})
        self._state = None
        return [completed]

    def flush(self) -> list[BarEvent]:
        if self._state is None:
            return []
        completed = self._state.to_bar(self._bar_type, metadata={"ticks_per_bar": self._ticks_per_bar})
        self._state = None
        return [completed]


class VolatilityBarBuilder(BaseBarBuilder):
    """Aggregate ticks until cumulative absolute mid-price move exceeds a threshold."""

    def __init__(self, threshold: float) -> None:
        super().__init__(bar_type=BarType.VOLATILITY)
        if threshold <= 0:
            raise ValueError("threshold must be greater than zero.")
        self._threshold = threshold
        self._state: _BarAccumulator | None = None
        self._previous_mid: float | None = None
        self._cumulative_move = 0.0

    def update(self, tick: TickEvent) -> list[BarEvent]:
        current_mid = mid_price(tick)
        if self._state is None:
            self._state = _BarAccumulator.from_tick(tick)
            self._previous_mid = current_mid
            return []

        assert self._previous_mid is not None
        self._state.update(tick)
        self._cumulative_move += abs(log(current_mid / self._previous_mid))
        self._previous_mid = current_mid

        if self._state.tick_count < 2 or self._cumulative_move < self._threshold:
            return []

        completed = self._state.to_bar(
            self._bar_type,
            metadata={"volatility_threshold": self._threshold},
        )
        self._state = None
        self._cumulative_move = 0.0
        return [completed]

    def flush(self) -> list[BarEvent]:
        if self._state is None:
            return []
        completed = self._state.to_bar(
            self._bar_type,
            metadata={"volatility_threshold": self._threshold},
        )
        self._state = None
        self._cumulative_move = 0.0
        return [completed]


class ImbalanceBarBuilder(BaseBarBuilder):
    """Aggregate ticks until signed order-flow imbalance exceeds a threshold."""

    def __init__(self, imbalance_threshold: float, volume_weighted: bool = False) -> None:
        super().__init__(bar_type=BarType.IMBALANCE)
        if imbalance_threshold <= 0:
            raise ValueError("imbalance_threshold must be greater than zero.")
        self._imbalance_threshold = imbalance_threshold
        self._volume_weighted = volume_weighted
        self._state: _BarAccumulator | None = None
        self._previous_mid: float | None = None
        self._last_direction = 1
        self._cumulative_imbalance = 0.0

    def update(self, tick: TickEvent) -> list[BarEvent]:
        current_mid = mid_price(tick)
        direction = self._resolve_direction(current_mid)
        weight = volume_proxy(tick) if self._volume_weighted else 1.0
        self._cumulative_imbalance += direction * weight

        if self._state is None:
            self._state = _BarAccumulator.from_tick(tick, signed_volume=float(direction))
        else:
            self._state.update(tick, signed_volume=float(direction))

        self._previous_mid = current_mid
        self._last_direction = direction

        if self._state.tick_count < 2 or abs(self._cumulative_imbalance) < self._imbalance_threshold:
            return []

        completed = self._state.to_bar(
            self._bar_type,
            imbalance=self._cumulative_imbalance,
            metadata={"imbalance_threshold": self._imbalance_threshold, "volume_weighted": self._volume_weighted},
        )
        self._state = None
        self._cumulative_imbalance = 0.0
        return [completed]

    def flush(self) -> list[BarEvent]:
        if self._state is None:
            return []
        completed = self._state.to_bar(
            self._bar_type,
            imbalance=self._cumulative_imbalance,
            metadata={"imbalance_threshold": self._imbalance_threshold, "volume_weighted": self._volume_weighted},
        )
        self._state = None
        self._cumulative_imbalance = 0.0
        return [completed]

    def _resolve_direction(self, current_mid: float) -> int:
        if self._previous_mid is None:
            return self._last_direction
        if current_mid > self._previous_mid:
            return 1
        if current_mid < self._previous_mid:
            return -1
        return self._last_direction


def build_bars(builder: BaseBarBuilder, ticks: list[TickEvent], *, flush: bool = True) -> list[BarEvent]:
    """Process a sequence of ticks through a bar builder."""

    bars: list[BarEvent] = []
    for tick in ticks:
        bars.extend(builder.update(tick))
    if flush:
        bars.extend(builder.flush())
    return bars


def floor_timestamp(timestamp: datetime, interval: timedelta) -> datetime:
    """Floor a UTC-aware timestamp to the start of its interval bucket."""

    total_seconds = interval.total_seconds()
    floored_seconds = int(timestamp.timestamp() // total_seconds) * total_seconds
    return datetime.fromtimestamp(floored_seconds, tz=timezone.utc)
