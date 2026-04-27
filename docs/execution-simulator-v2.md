# Execution-Aware Simulator V2

## Purpose

The execution-aware simulator extends the original immediate-fill backtest without replacing it.
V1 remains useful for fast strategy iteration. V2 adds explicit execution scenarios so strategies can
be stress-tested against more realistic order behavior before paper or live promotion.

## Implemented Scenarios

- latency by replay steps
- partial-fill ratios
- available-liquidity caps
- queue-ahead quantity proxy
- forced rejection
- forced cancellation
- cancel/replace race fill before cancellation
- closed market rejection
- stale-market-data rejection

## Inputs

V2 uses the same required frame columns as `run_backtest()`:

- `symbol`
- `event_timestamp`
- `available_timestamp`
- `mid_price`

Optional scenario columns:

- `market_status`
- `partial_fill_ratio`
- `latency_steps`
- `available_liquidity`
- `queue_ahead_quantity`
- `force_reject`
- `force_cancel`
- `cancel_replace_race`

All timestamps remain UTC-aware, and all spread/slippage/commission assumptions continue to come
from `BacktestConfig`.

## Metrics

`ExecutionQualityMetrics` reports:

- PnL, final equity, and max drawdown
- submitted, filled, partial, cancelled, and rejected order counts
- requested and filled quantity
- fill ratio
- cancel ratio
- reject ratio
- turnover
- spread cost
- slippage cost
- commission
- average slippage in bps
- average latency in replay steps

## Boundary

This simulator is still offline-only. It does not call broker adapters, does not weaken paper-first
safety, and does not replace MT5 real-terminal validation.
