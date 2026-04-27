"""Feature engineering utilities for offline and online microstructure pipelines."""

from scalper_ai.features.macro import MacroContextProvider, NullMacroContextProvider
from scalper_ai.features.offline import build_feature_frame, build_feature_snapshots, merge_feature_events
from scalper_ai.features.online import OnlineFeatureCalculator
from scalper_ai.features.order_flow import empty_mlofi, multi_level_ofi, rolling_ofi, top_of_book_ofi
from scalper_ai.features.primitives import (
    best_bid_ask,
    best_sizes,
    log_mid_return,
    mid_price,
    quote_intensity,
    realized_volatility,
    spread,
    spread_bps,
)
from scalper_ai.features.schema import (
    FEATURE_SET,
    FEATURE_VERSION,
    FeatureConfig,
    empty_feature_values,
    feature_names,
    feature_snapshot_from_values,
    mlofi_feature_name,
)
from scalper_ai.features.toxicity import signed_trade_volume, tick_rule_sign, toxicity_vpin_proxy

__all__ = [
    "FEATURE_SET",
    "FEATURE_VERSION",
    "FeatureConfig",
    "MacroContextProvider",
    "NullMacroContextProvider",
    "OnlineFeatureCalculator",
    "best_bid_ask",
    "best_sizes",
    "build_feature_frame",
    "build_feature_snapshots",
    "empty_feature_values",
    "empty_mlofi",
    "feature_names",
    "feature_snapshot_from_values",
    "log_mid_return",
    "merge_feature_events",
    "mid_price",
    "mlofi_feature_name",
    "multi_level_ofi",
    "quote_intensity",
    "realized_volatility",
    "rolling_ofi",
    "signed_trade_volume",
    "spread",
    "spread_bps",
    "tick_rule_sign",
    "top_of_book_ofi",
    "toxicity_vpin_proxy",
]
