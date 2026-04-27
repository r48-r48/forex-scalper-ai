"""Incremental online feature calculator for microstructure signals."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Optional

from scalper_ai.domain import BookSnapshot, FeatureSnapshot, TickEvent
from scalper_ai.features.macro import MacroContextProvider, NullMacroContextProvider
from scalper_ai.features.order_flow import empty_mlofi, multi_level_ofi, rolling_ofi, top_of_book_ofi
from scalper_ai.features.primitives import (
    TopOfBookEvent,
    log_mid_return,
    mid_price,
    quote_intensity,
    realized_volatility,
    spread,
    spread_bps,
)
from scalper_ai.features.schema import (
    FeatureConfig,
    MID_RETURN_FEATURE,
    OFI_FEATURE,
    QUOTE_INTENSITY_FEATURE,
    REALIZED_VOLATILITY_FEATURE,
    SPREAD_BPS_FEATURE,
    SPREAD_FEATURE,
    TOXICITY_VPIN_FEATURE,
    empty_feature_values,
    feature_names,
    feature_snapshot_from_values,
    normalize_feature_values,
)
from scalper_ai.features.toxicity import signed_trade_volume, toxicity_vpin_proxy


class OnlineFeatureCalculator:
    """Stateful calculator that emits leakage-safe feature snapshots incrementally."""

    def __init__(
        self,
        *,
        config: Optional[FeatureConfig] = None,
        macro_provider: Optional[MacroContextProvider] = None,
    ) -> None:
        self._config = config or FeatureConfig()
        self._macro_provider = macro_provider or NullMacroContextProvider()
        self._quote_timestamps: deque[datetime] = deque()
        self._log_returns: deque[float] = deque(maxlen=self._config.volatility_window)
        self._ofi_increments: deque[float] = deque(maxlen=self._config.ofi_window)
        self._absolute_volumes: deque[float] = deque(maxlen=self._config.toxicity_window)
        self._signed_volumes: deque[float] = deque(maxlen=self._config.toxicity_window)
        self._previous_top_of_book: TopOfBookEvent | None = None
        self._previous_mid: float | None = None
        self._previous_trade_price: float | None = None
        self._previous_trade_sign: int = 1
        self._previous_book: BookSnapshot | None = None
        self._latest_mlofi = empty_mlofi(depth=self._config.mlofi_depth)
        self._latest_toxicity = 0.0

    @property
    def config(self) -> FeatureConfig:
        """Return the immutable calculator configuration."""

        return self._config

    @property
    def output_feature_names(self) -> tuple[str, ...]:
        """Return the stable ordered output feature names."""

        return feature_names(mlofi_depth=self._config.mlofi_depth)

    def update(self, event: TopOfBookEvent) -> FeatureSnapshot:
        """Update the calculator from a canonical tick or book event."""

        if isinstance(event, TickEvent):
            return self.update_tick(event)
        return self.update_book(event)

    def update_tick(self, tick: TickEvent) -> FeatureSnapshot:
        """Update the calculator from a top-of-book tick."""

        return self._update_common(event=tick, is_trade_event=True)

    def update_book(self, book: BookSnapshot) -> FeatureSnapshot:
        """Update the calculator from a multi-level order book snapshot."""

        if self._previous_book is not None:
            self._latest_mlofi = multi_level_ofi(
                self._previous_book,
                book,
                depth=self._config.mlofi_depth,
            )
        self._previous_book = book
        return self._update_common(event=book, is_trade_event=False)

    def _update_common(self, *, event: TopOfBookEvent, is_trade_event: bool) -> FeatureSnapshot:
        received_timestamp = _received_timestamp(event)
        self._append_quote_timestamp(received_timestamp)

        current_mid = mid_price(event)
        mid_return = 0.0
        if self._previous_mid is not None:
            mid_return = log_mid_return(self._previous_mid, current_mid)
            self._log_returns.append(mid_return)
        self._previous_mid = current_mid

        if self._previous_top_of_book is not None:
            ofi_increment = top_of_book_ofi(self._previous_top_of_book, event)
            self._ofi_increments.append(ofi_increment)
        self._previous_top_of_book = event

        if is_trade_event:
            volume, signed_volume, sign = signed_trade_volume(
                event,
                previous_trade_price=self._previous_trade_price,
                previous_sign=self._previous_trade_sign,
            )
            self._absolute_volumes.append(volume)
            self._signed_volumes.append(signed_volume)
            self._previous_trade_price = float(event.last_price or current_mid)
            self._previous_trade_sign = sign
            self._latest_toxicity = toxicity_vpin_proxy(self._signed_volumes, self._absolute_volumes)

        values = empty_feature_values(self._config)
        values[SPREAD_FEATURE] = spread(event)
        values[SPREAD_BPS_FEATURE] = spread_bps(event)
        values[MID_RETURN_FEATURE] = mid_return
        values[REALIZED_VOLATILITY_FEATURE] = realized_volatility(self._log_returns)
        values[QUOTE_INTENSITY_FEATURE] = quote_intensity(
            self._quote_timestamps,
            now=received_timestamp,
            window_seconds=self._config.quote_intensity_window_seconds,
        )
        values[OFI_FEATURE] = rolling_ofi(self._ofi_increments)
        values[TOXICITY_VPIN_FEATURE] = self._latest_toxicity
        values.update(self._latest_mlofi)
        values.update(
            normalize_feature_values(
                self._macro_provider.get_features(
                    symbol=event.symbol,
                    event_timestamp=event.event_timestamp,
                )
            )
        )

        return feature_snapshot_from_values(
            symbol=event.symbol,
            event_timestamp=event.event_timestamp,
            available_timestamp=received_timestamp,
            values=values,
            source=event.source if isinstance(event, TickEvent) else None,
            tags={"trigger": "tick" if is_trade_event else "book"},
        )

    def _append_quote_timestamp(self, timestamp: datetime) -> None:
        self._quote_timestamps.append(timestamp)
        window = timedelta(seconds=self._config.quote_intensity_window_seconds)
        while self._quote_timestamps and timestamp - self._quote_timestamps[0] > window:
            self._quote_timestamps.popleft()


def _received_timestamp(event: TopOfBookEvent) -> datetime:
    return event.received_timestamp
