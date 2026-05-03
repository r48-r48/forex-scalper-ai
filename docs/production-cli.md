# Production CLI Entrypoints

This project keeps trading and validation logic in library modules. The scripts in this
document are thin reproducible entrypoints for local research, paper promotion checks,
and release evidence. They do not submit broker orders.

## Bootstrap Historical Data

```bash
.venv/bin/python scripts/bootstrap_history.py \
  --input-path data/vendor/eurusd-m1-export.csv \
  --output-path data/raw/history/eurusd-m1.parquet \
  --dataset-id eurusd-m1-demo-20260504 \
  --summary-output-path data/artifacts/eurusd-m1-history-summary.json \
  --quality-report-path data/artifacts/eurusd-m1-history-quality.json \
  --symbol EURUSD \
  --venue MT5 \
  --timeframe M1 \
  --max-event-gap-seconds 120
```

The command normalizes broker/vendor CSV or Parquet exports into the tick-like raw
schema consumed by `scripts/build_features.py`. It writes a data-quality report next
to the output evidence and refuses to write the raw artifact when error-level QA
issues are found, unless `--allow-quality-errors` is explicitly provided.

Input rows must provide timezone-aware UTC timestamps and either real `bid` / `ask`
columns or an explicit `--mid-price-column` plus `--synthetic-spread-bps`. No spread
is synthesized by default. If `received_timestamp` is absent, it is filled from
`event_timestamp` and this assumption is recorded in the summary.

## Download Dukascopy Tick History

```bash
.venv/bin/python scripts/download_dukascopy_ticks.py \
  --symbol EURUSD \
  --start-date 2016-01-01 \
  --end-date 2026-05-04 \
  --vendor-output-dir data/raw/vendor/dukascopy \
  --parsed-output-dir data/raw/vendor/dukascopy/parsed \
  --bootstrap-output-dir data/raw/history/dukascopy \
  --bars-output-dir data/processed/bars/dukascopy \
  --summary-output-path data/artifacts/dukascopy/EURUSD/download-summary-2016-01-01-to-2026-05-04.json \
  --day-workers 8 \
  --hour-workers 4
```

This downloader uses Dukascopy's public historical datafeed as the vendor source.
It saves the original hourly `.bi5` archives, decodes bid/ask ticks, runs the same
QA-gated `bootstrap_history.py` normalization, and derives local bar files for:
`TICK`, `M1`, `M2`, `M3`, `M4`, `M5`, `M6`, `M10`, `M12`, `M15`, `M20`, `M30`,
`H1`, `H2`, `H3`, `H4`, `H6`, `H8`, `H12`, and `D1`.

The command is resumable by default. Completed daily tick and bar outputs are skipped
on rerun, raw hourly archives are reused when present, and days without public
archives are recorded as `no_data_available` instead of stopping a multi-year range.
`--day-workers` can be used for moderate parallelism across independent UTC days,
and `--hour-workers` downloads independent hourly archives inside each day in
parallel. All timestamps are decoded as UTC; no broker orders are submitted.

## Build Offline Features

```bash
.venv/bin/python scripts/build_features.py \
  --input-path data/raw/eurusd-ticks.csv \
  --output-path data/processed/eurusd-features.parquet \
  --summary-output-path data/artifacts/eurusd-features-summary.json \
  --symbol EURUSD \
  --venue MT5
```

Input rows must contain `event_timestamp`, `received_timestamp`, `bid`, and `ask`.
They may also include `symbol`, `venue`, sizes, last trade proxies, and sequence.
Run one symbol/venue stream at a time so feature state cannot mix markets.

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

For bid/ask-aware replay, pass `--bid-price-column` and `--ask-price-column`. BUY
fills then use ask and SELL fills use bid, while `--price-column` remains the
mark-to-market column.

For session or regime-specific costs, pass `--spread-bps-column`,
`--slippage-bps-column`, or `--commission-bps-column`; each row must be non-negative
and overrides the constant cost for that event. FX symbol realism metrics can be
enabled with `--fx-pip-size`, plus optional contract size, margin rate, rollover hour,
and long/short swap-per-lot assumptions. These fields add pip value, margin
utilization, margin-level, effective-leverage, and rollover swap cost metrics without
changing defaults. To model broker-style forced liquidation, pass
`--margin-call-level` as an `equity / margin_required` threshold, for example `1.0`
for a 100% margin level stop-out.

For broker-exported or curated symbol assumptions, prefer a strict JSON file and pass
`--fx-symbol-spec-path data/config/eurusd-symbol.json` instead of manual FX flags:

```json
{
  "fx_symbol": {
    "base_currency": "EUR",
    "quote_currency": "USD",
    "account_currency": "USD",
    "pip_size": 0.0001,
    "contract_size": 100000,
    "quote_to_account_rate": 1.0,
    "margin_rate": 0.02,
    "swap_long_per_lot": -4.1,
    "swap_short_per_lot": 1.2,
    "rollover_hour_utc": 21
  }
}
```

The loader rejects unknown fields and missing required values so broker assumptions
stay explicit in the report.

For completed-bar stop-loss / take-profit path simulation, pass
`--high-price-column`, `--low-price-column`, and one or both of
`--stop-loss-price-column` / `--take-profit-price-column`. Protective prices become
active after the current row is available, so a row never uses its own high/low range
to trigger protection it just created. If both SL and TP are touched inside the same
bar, `--protective-exit-priority` chooses the fill assumption and defaults to the
conservative `stop_loss`.

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

## Run Supervised Filter Validation

```bash
.venv/bin/python scripts/run_supervised_filter.py \
  --input-path data/processed/supervised-dataset.parquet \
  --output-path data/artifacts/supervised-filter.json \
  --fold-metrics-output-path data/artifacts/supervised-filter-folds.csv \
  --train-size 2048 \
  --validation-size 512 \
  --test-size 512 \
  --embargo-size 16 \
  --target-threshold 0.0001 \
  --spread-bps 0.5 \
  --slippage-bps 0.2
```

This command fits the transparent supervised baseline filter on train folds only and
evaluates directional predictions on out-of-sample test folds. Cost settings are
recorded in the report as explicit evaluation context; this directional filter does
not convert predictions into broker fills.

## Train And Export A Supervised Filter Bundle

```bash
.venv/bin/python scripts/train_supervised_filter.py \
  --input-path data/processed/supervised-dataset.parquet \
  --output-dir data/artifacts/models/eurusd-filter-20260503 \
  --model-id eurusd-filter-20260503 \
  --training-end 2026-05-03T12:00:00Z \
  --dataset-id eurusd-m1-demo \
  --target-horizon 1m \
  --target-threshold 0.0001
```

The command writes `model.json`, `scaler.json`, `metadata.json`,
`feature_importance.csv`, and `training-report.json`. To avoid target leakage, use an
explicit UTC `--training-end`; rows whose target end timestamp crosses that cutoff are
excluded. Only pass `--input-is-train-only` when the input file was already curated as
a training-only slice outside this command.

The resulting bundle can be loaded by `load_baseline_filter_inference_package()` for
runtime scoring. The loader verifies artifact SHA-256 values, model type, and ordered
feature columns before returning predictions.

## Train And Export A Transformer Bundle

```bash
.venv/bin/python scripts/train_transformer.py \
  --input-path data/processed/supervised-dataset.parquet \
  --output-dir data/artifacts/models/eurusd-transformer-20260503 \
  --model-id eurusd-transformer-20260503 \
  --training-end 2026-05-03T12:00:00Z \
  --dataset-id eurusd-m1-demo \
  --target-horizon 1m \
  --validation-fraction 0.2 \
  --epochs 20 \
  --batch-size 128
```

The command writes `model.pt`, `scaler.json`, `metadata.json`, and
`training-report.json`. It uses the same cutoff posture as the supervised filter:
provide a UTC `--training-end` unless the input is already a curated train-only slice.
The validation split is a tail split inside the selected training window, so later
rows are not used to fit earlier rows. The resulting bundle can be loaded by
`load_transformer_inference_package()` for runtime scoring without touching broker or
live execution code.
