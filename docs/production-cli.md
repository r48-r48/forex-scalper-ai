# Production CLI Entrypoints

This project keeps trading and validation logic in library modules. The scripts in this
document are thin reproducible entrypoints for local research, paper promotion checks,
and release evidence. They do not submit broker orders.

## Build A Supervised Dataset

```bash
.venv/bin/python scripts/build_dataset.py \
  --input-path data/processed/features.csv \
  --output-path data/processed/supervised-dataset.parquet \
  --summary-output-path data/artifacts/supervised-dataset-summary.json \
  --history-length 32 \
  --horizon 1 \
  --target-column mid_return
```

Input rows must contain `symbol`, `event_timestamp`, `available_timestamp`, and
numeric feature columns. Timestamps must be timezone-aware and are normalized to UTC.
Targets are built from future rows only; current features are lagged as
`lag_000__feature_name`, `lag_001__feature_name`, and so on.

## Run Baseline Backtests

```bash
.venv/bin/python scripts/run_backtest.py \
  --input-path data/processed/features.csv \
  --output-path data/artifacts/baseline-backtest.json \
  --strategy all \
  --spread-bps 0.5 \
  --slippage-bps 0.2 \
  --commission-bps 0.0 \
  --max-abs-position 1.0
```

The report includes explicit cost assumptions, strategy names, risk limits, and the
baseline metrics frame. Supported strategies are `spread_mean_reversion`,
`ofi_imbalance`, `volatility_breakout`, or `all`.

## Run Walk-Forward Validation

```bash
.venv/bin/python scripts/run_walk_forward.py \
  --input-path data/processed/supervised-dataset.parquet \
  --output-path data/artifacts/baseline-walk-forward.json \
  --fold-metrics-output-path data/artifacts/baseline-fold-metrics.csv \
  --strategy all \
  --train-size 2048 \
  --validation-size 512 \
  --test-size 512 \
  --embargo-size 16 \
  --spread-bps 0.5 \
  --slippage-bps 0.2
```

The script reconstructs the `SupervisedDataset` contract from a flat dataset frame,
then runs the existing leakage-safe walk-forward engine over out-of-sample test folds.
Only current-state `lag_000__` features are converted back into replay rows for the
baseline backtester.
