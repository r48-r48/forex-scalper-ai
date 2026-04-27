# Baseline Strategy Suite

## Purpose

P1.2 adds deterministic baseline strategies that future ML and RL systems must beat before
promotion. These baselines are intentionally simple, replayable, and auditable.

They use the existing `TargetPositionStrategy` protocol from the backtesting layer, so no parallel
strategy contract was introduced.

## Strategies

- `spread_mean_reversion`: trades against the current `mid_return` when `spread_bps` is within the
  configured limit.
- `ofi_imbalance`: follows the current `ofi` value and falls back to `mlofi_total` when top-level
  OFI is unavailable. If neither signal exists, it stays flat.
- `volatility_breakout`: follows current return breakouts above a realized-volatility-adjusted
  threshold.

All strategies return target net position in base units. Position size is capped by
`max_abs_position`, and wide-spread conditions force a flat target when `max_spread_bps` is set.

## Leakage Controls

- Strategies only read the current replay event payload.
- Walk-forward reporting evaluates only out-of-sample test partitions.
- The supervised dataset target column is not read by the baseline strategies.
- Missing optional L2/OFI fields degrade to flat behavior instead of synthetic future information.

## Reporting Helpers

The public helpers are:

- `build_default_baseline_specs()`
- `run_baseline_suite()`
- `run_default_baseline_sensitivity()`
- `run_baseline_walk_forward_suite()`

`run_baseline_suite()` emits one row per strategy with explicit PnL, drawdown, turnover, and cost
assumptions. `run_default_baseline_sensitivity()` repeats the default suite across explicit
spread/slippage/commission and position-limit scenarios. `run_baseline_walk_forward_suite()`
produces fold-level and aggregate out-of-sample reports.

## Configuration Reference

`configs/baselines.yaml` documents the default baseline suite, walk-forward sizes, and sensitivity
scenarios. It is a reporting reference for now; runtime config loading remains separate from this
research validation layer.

## Example

```python
from scalper_ai.backtesting import BacktestConfig
from scalper_ai.validation import run_baseline_suite

result = run_baseline_suite(
    replay_frame,
    backtest_config=BacktestConfig(
        initial_cash=100_000.0,
        spread_bps=0.5,
        slippage_bps=0.2,
        commission_bps=0.0,
    ),
)
print(result.summary)
```
