# Validation Gate

## Purpose

The validation gate is the go/no-go artifact for promoting a strategy from research into paper or shadow evaluation.
It joins the offline evidence that matters before any live-facing change:

- deterministic backtest metrics
- walk-forward robustness metrics
- execution-stress metrics
- latency and slippage metrics
- explicit risk flags
- market-regime breakdown

The implementation lives in `src/scalper_ai/validation/gate.py`.

## Report Contract

Use `build_validation_gate_report()` with any available artifacts:

- `backtest_result` from `run_backtest`
- `walk_forward_result` from `run_walk_forward_validation`
- `execution_result` from `run_execution_aware_backtest`
- `risk_flags` from risk, OMS, review, or manual checks
- `market_frame` with either `regime`, `realized_volatility`, or `spread_bps`

Missing major artifacts produce `warn` checks. Breached thresholds or risk flags produce `fail`.

## Default Thresholds

Defaults are intentionally conservative and explicit:

- total PnL must be non-negative
- max drawdown must stay at or below 10 percent
- at least one trade is required
- at least 50 percent of walk-forward folds must be profitable
- fill ratio must be at least 95 percent
- cancel ratio must stay at or below 25 percent
- reject ratio must stay at or below 5 percent
- average slippage must stay at or below 5 bps
- risk flags must be zero

Override thresholds per experiment rather than hiding assumptions in strategy code.

## Artifact Path

Persist reports under an ignored artifact directory, for example:

```python
from pathlib import Path

from scalper_ai.validation import build_validation_gate_report, write_validation_gate_report

report = build_validation_gate_report(
    strategy_name="candidate",
    backtest_result=backtest_result,
    walk_forward_result=walk_forward_result,
    execution_result=execution_result,
    market_frame=market_frame,
)
write_validation_gate_report(report, output_dir=Path("data/artifacts/validation"))
```

Do not commit generated reports unless a review explicitly asks for a frozen fixture.

## Promotion Rule

A strategy is eligible for the next paper/shadow step only when:

- `report.status == "pass"`
- no manual risk flags remain open
- costs, slippage, and latency assumptions are documented
- the report was generated from UTC-aware data
- the strategy was compared against the baseline suite
