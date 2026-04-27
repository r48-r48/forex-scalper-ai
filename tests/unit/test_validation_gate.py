"""Unit tests for unified validation gate reports."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from scalper_ai.backtesting import (
    BacktestConfig,
    ExecutionSimulatorConfig,
    run_backtest,
    run_execution_aware_backtest,
)
from scalper_ai.data.datasets import DatasetConfig, build_supervised_dataset
from scalper_ai.data.splits import WalkForwardConfig
from scalper_ai.validation import (
    ValidationGateStatus,
    ValidationGateThresholds,
    build_validation_gate_report,
    run_walk_forward_validation,
    supervised_partition_to_backtest_frame,
    write_validation_gate_report,
)


def test_validation_gate_passes_complete_offline_artifact_bundle(tmp_path) -> None:
    frame = _market_frame(row_count=18)
    backtest_result = run_backtest(
        frame,
        _signal_strategy,
        config=BacktestConfig(initial_cash=100_000.0),
    )
    execution_result = run_execution_aware_backtest(
        frame,
        _signal_strategy,
        config=ExecutionSimulatorConfig(base=BacktestConfig(initial_cash=100_000.0)),
    )
    dataset = build_supervised_dataset(
        feature_frame=frame,
        config=DatasetConfig(
            history_length=2,
            horizon=1,
            target_column="mid_return",
        ),
    )
    walk_forward_result = run_walk_forward_validation(
        dataset,
        _walk_forward_strategy_factory,
        walk_forward_config=WalkForwardConfig(
            train_size=4,
            validation_size=2,
            test_size=2,
            embargo_size=1,
            step_size=2,
        ),
        backtest_config=BacktestConfig(initial_cash=100_000.0),
        frame_builder=supervised_partition_to_backtest_frame,
    )

    report = build_validation_gate_report(
        strategy_name="signal-baseline",
        backtest_result=backtest_result,
        walk_forward_result=walk_forward_result,
        execution_result=execution_result,
        market_frame=frame,
        thresholds=ValidationGateThresholds(
            min_total_pnl=-10.0,
            max_drawdown=0.50,
            min_profitable_fold_ratio=0.0,
            min_trade_count=1,
            min_fill_ratio=0.95,
            max_cancel_ratio=0.05,
            max_reject_ratio=0.05,
            max_average_slippage_bps=1.0,
        ),
        generated_at=datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc),  # noqa: UP017
    )

    assert report.status is ValidationGateStatus.PASS
    assert report.passed is True
    assert report.backtest["trade_count"] >= 1
    assert report.walk_forward["fold_count"] == 4
    assert report.execution_stress["fill_ratio"] == pytest.approx(1.0)
    assert report.latency_slippage["average_slippage_bps"] == pytest.approx(0.0)
    assert {row["regime"] for row in report.regime_breakdown} == {
        "high_volatility",
        "low_volatility",
    }

    path = write_validation_gate_report(report, output_dir=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["strategy_name"] == "signal-baseline"
    assert payload["status"] == "pass"
    assert payload["generated_at"] == "2026-04-28T12:00:00+00:00"


def test_validation_gate_fails_when_risk_flags_exceed_threshold() -> None:
    report = build_validation_gate_report(
        strategy_name="candidate",
        risk_flags=("max_drawdown_breach",),
        thresholds=ValidationGateThresholds(max_risk_flag_count=0),
        generated_at=datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc),  # noqa: UP017
    )

    assert report.status is ValidationGateStatus.FAIL
    assert report.risk_flags == ("max_drawdown_breach",)
    assert report.passed is False
    assert any(
        check.name == "risk_flags" and check.status is ValidationGateStatus.FAIL
        for check in report.checks
    )


def test_validation_gate_requires_timezone_aware_generation_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_validation_gate_report(
            strategy_name="candidate",
            generated_at=datetime(2026, 4, 28, 12, 0),  # noqa: DTZ001
        )


def _market_frame(*, row_count: int) -> pd.DataFrame:
    base_time = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)  # noqa: UP017
    records: list[dict[str, object]] = []
    for index in range(row_count):
        timestamp = base_time + timedelta(minutes=index)
        direction = 1.0 if index % 4 < 2 else -1.0
        records.append(
            {
                "symbol": "EURUSD",
                "event_timestamp": timestamp,
                "available_timestamp": timestamp,
                "feature_set": "microstructure",
                "feature_version": "v1",
                "mid_price": 100.0 + (index * 0.2),
                "mid_return": direction * 0.0015,
                "signal": direction,
                "spread_bps": 0.5,
                "ofi": direction * 2.0,
                "mlofi_total": direction * 2.0,
                "realized_volatility": 0.0003 + (0.0001 * (index % 3)),
            }
        )
    return pd.DataFrame.from_records(records)


def _signal_strategy(event, state) -> float:
    del state
    return float(event.row_payload["signal"])


def _walk_forward_strategy_factory(*, train, validation, test, split):
    del train, validation, test, split
    return _signal_strategy
