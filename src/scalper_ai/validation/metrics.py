"""Fold-level metrics and robustness summaries for walk-forward validation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FoldValidationMetrics:
    """Fold-level metrics emitted by walk-forward validation."""

    split_index: int
    train_size: int
    validation_size: int
    test_size: int
    train_end_timestamp: object
    validation_end_timestamp: object
    test_end_timestamp: object
    total_pnl: float
    final_equity: float
    max_drawdown: float
    trade_count: int
    turnover_quote: float


@dataclass(frozen=True)
class RobustnessSummary:
    """Aggregate robustness statistics across validation folds."""

    fold_count: int
    total_pnl: float
    mean_pnl: float
    median_pnl: float
    pnl_std: float
    best_fold_pnl: float
    worst_fold_pnl: float
    profitable_fold_ratio: float
    mean_final_equity: float
    mean_max_drawdown: float
    worst_max_drawdown: float
    total_trade_count: int
    mean_trade_count: float
    total_turnover_quote: float
    mean_turnover_quote: float


def summarize_fold_metrics(metrics: Sequence[FoldValidationMetrics]) -> RobustnessSummary:
    """Aggregate explicit pnl, drawdown, and activity metrics across folds."""

    if not metrics:
        raise ValueError("summarize_fold_metrics requires at least one fold metric.")

    pnl_values = np.asarray([float(metric.total_pnl) for metric in metrics], dtype=float)
    final_equities = np.asarray([float(metric.final_equity) for metric in metrics], dtype=float)
    drawdowns = np.asarray([float(metric.max_drawdown) for metric in metrics], dtype=float)
    trade_counts = np.asarray([int(metric.trade_count) for metric in metrics], dtype=float)
    turnovers = np.asarray([float(metric.turnover_quote) for metric in metrics], dtype=float)

    return RobustnessSummary(
        fold_count=len(metrics),
        total_pnl=float(pnl_values.sum()),
        mean_pnl=float(pnl_values.mean()),
        median_pnl=float(np.median(pnl_values)),
        pnl_std=float(pnl_values.std(ddof=0)),
        best_fold_pnl=float(pnl_values.max()),
        worst_fold_pnl=float(pnl_values.min()),
        profitable_fold_ratio=float((pnl_values > 0).mean()),
        mean_final_equity=float(final_equities.mean()),
        mean_max_drawdown=float(drawdowns.mean()),
        worst_max_drawdown=float(drawdowns.max()),
        total_trade_count=int(trade_counts.sum()),
        mean_trade_count=float(trade_counts.mean()),
        total_turnover_quote=float(turnovers.sum()),
        mean_turnover_quote=float(turnovers.mean()),
    )


def fold_metrics_to_frame(metrics: Sequence[FoldValidationMetrics]) -> pd.DataFrame:
    """Return a reporting-friendly dataframe from fold metrics."""

    return pd.DataFrame.from_records(asdict(metric) for metric in metrics)
