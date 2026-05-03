"""Unit tests for production-facing research CLI scripts."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pandas as pd


def test_build_dataset_cli_writes_flat_dataset_and_summary(tmp_path: Path) -> None:
    script = _load_script_module("build_dataset")
    input_path = tmp_path / "features.csv"
    output_path = tmp_path / "dataset.csv"
    summary_path = tmp_path / "dataset-summary.json"
    _feature_frame(row_count=6).to_csv(input_path, index=False)

    payload = script.build_dataset_cli(
        input_path=input_path,
        output_path=output_path,
        summary_output_path=summary_path,
        history_length=2,
        horizon=1,
        target_column="mid_return",
    )

    dataset_frame = pd.read_csv(output_path)
    assert output_path.exists()
    assert summary_path.exists()
    assert payload["dataset_rows"] == 4
    assert payload["feature_count"] == 10
    assert "target" in dataset_frame.columns
    assert "lag_000__mid_price" in dataset_frame.columns
    assert "lag_001__mid_return" in dataset_frame.columns


def test_run_backtest_cli_writes_explicit_cost_baseline_report(tmp_path: Path) -> None:
    script = _load_script_module("run_backtest")
    input_path = tmp_path / "replay.csv"
    output_path = tmp_path / "backtest.json"
    fx_spec_path = tmp_path / "eurusd-symbol.json"
    _feature_frame(row_count=5, include_protective_columns=True).to_csv(
        input_path,
        index=False,
    )
    fx_spec_path.write_text(
        json.dumps(
            {
                "fx_symbol": {
                    "base_currency": "EUR",
                    "quote_currency": "USD",
                    "account_currency": "USD",
                    "pip_size": 0.0001,
                    "margin_rate": 0.02,
                }
            }
        ),
        encoding="utf-8",
    )

    payload = script.run_backtest_cli(
        input_path=input_path,
        output_path=output_path,
        strategy="spread_mean_reversion",
        spread_bps=0.4,
        slippage_bps=0.1,
        commission_bps=0.05,
        high_price_column="high_price",
        low_price_column="low_price",
        fx_symbol_spec_path=fx_spec_path,
        margin_call_level=1.0,
        stop_loss_price_column="stop_loss_price",
        take_profit_price_column="take_profit_price",
        protective_exit_priority="take_profit",
        max_abs_position=0.5,
    )

    assert output_path.exists()
    assert payload["strategies"] == ["spread_mean_reversion"]
    assert payload["backtest_config"]["margin_call_level"] == 1.0
    assert payload["backtest_config"]["fx_symbol"]["pip_size"] == 0.0001
    assert payload["backtest_config"]["fx_symbol"]["margin_rate"] == 0.02
    assert payload["backtest_config"]["protective_exit_priority"] == "take_profit"
    summary = payload["summary"]
    assert len(summary) == 1
    assert summary[0]["strategy_name"] == "spread_mean_reversion"
    assert summary[0]["spread_bps"] == 0.4
    assert summary[0]["trade_count"] >= 1


def test_run_walk_forward_cli_writes_summary_and_fold_metrics(tmp_path: Path) -> None:
    script = _load_script_module("run_walk_forward")
    input_path = tmp_path / "dataset.csv"
    output_path = tmp_path / "walk-forward.json"
    fold_metrics_path = tmp_path / "fold-metrics.csv"
    _supervised_dataset_frame(row_count=6).to_csv(input_path, index=False)

    payload = script.run_walk_forward_cli(
        input_path=input_path,
        output_path=output_path,
        fold_metrics_output_path=fold_metrics_path,
        strategy="spread_mean_reversion",
        train_size=2,
        validation_size=1,
        test_size=2,
        spread_bps=0.4,
        slippage_bps=0.1,
        commission_bps=0.05,
    )

    fold_metrics = pd.read_csv(fold_metrics_path)
    assert output_path.exists()
    assert fold_metrics_path.exists()
    assert payload["strategies"] == ["spread_mean_reversion"]
    assert payload["feature_count"] == 4
    assert len(payload["fold_metrics"]) == 1
    assert len(payload["summary"]) == 1
    assert fold_metrics.loc[0, "strategy_name"] == "spread_mean_reversion"


def _load_script_module(name: str) -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _feature_frame(
    *,
    row_count: int,
    include_protective_columns: bool = False,
) -> pd.DataFrame:
    start = datetime(2026, 5, 3, 10, 0, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    returns = (-0.0004, 0.00045, -0.00035, 0.0005, -0.0003, 0.00042)
    for index in range(row_count):
        timestamp = start + timedelta(minutes=index)
        mid_price = 1.1000 + index * 0.0001
        row: dict[str, object] = {
            "symbol": "EURUSD",
            "event_timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            "available_timestamp": (
                timestamp + timedelta(milliseconds=50)
            ).isoformat().replace("+00:00", "Z"),
            "feature_set": "unit",
            "feature_version": "1",
            "mid_price": mid_price,
            "mid_return": returns[index % len(returns)],
            "spread_bps": 0.5,
            "ofi": 1.5 if index % 2 else -1.5,
            "realized_volatility": 0.00008,
        }
        if include_protective_columns:
            row.update(
                {
                    "high_price": mid_price + 0.001,
                    "low_price": mid_price - 0.001,
                    "stop_loss_price": mid_price - 0.005,
                    "take_profit_price": mid_price + 0.005,
                }
            )
        rows.append(row)
    return pd.DataFrame.from_records(rows)


def _supervised_dataset_frame(*, row_count: int) -> pd.DataFrame:
    start = datetime(2026, 5, 3, 10, 0, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    returns = (-0.0004, 0.00045, -0.00035, 0.0005, -0.0003, 0.00042)
    for index in range(row_count):
        timestamp = start + timedelta(minutes=index)
        target_end = timestamp + timedelta(minutes=1)
        rows.append(
            {
                "symbol": "EURUSD",
                "event_timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "available_timestamp": (
                    timestamp + timedelta(milliseconds=50)
                ).isoformat().replace("+00:00", "Z"),
                "feature_set": "unit",
                "feature_version": "1",
                "target_end_timestamp": (
                    target_end + timedelta(milliseconds=50)
                ).isoformat().replace("+00:00", "Z"),
                "target_end_event_timestamp": target_end.isoformat().replace(
                    "+00:00",
                    "Z",
                ),
                "lag_000__mid_price": 1.1000 + index * 0.0001,
                "lag_000__mid_return": returns[index % len(returns)],
                "lag_000__spread_bps": 0.5,
                "lag_000__ofi": 1.5 if index % 2 else -1.5,
                "target": returns[(index + 1) % len(returns)],
            }
        )
    return pd.DataFrame.from_records(rows)
