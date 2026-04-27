# Alert Rules

## Purpose

These rules describe the operational meaning of existing and planned health/metrics surfaces.
They are written as operator guidance first; wiring to Prometheus, Alertmanager, or another system can happen once deployment topology is fixed.

## Broker Disconnect

- Signal: broker connectivity status is not connected, or broker heartbeat age exceeds the configured threshold.
- Severity: critical in live-safe mode, warning in paper mode.
- Initial threshold: 2 missed heartbeat intervals or 30 seconds, whichever is longer.
- Action: stop new order submission, keep paper fallback available, run MT5 preflight, inspect terminal/session status, and reconcile open orders before resuming.

## Stale Data

- Signal: latest market `available_timestamp` is older than the stale-data threshold.
- Severity: critical for live-safe order submission, warning for research replay.
- Initial threshold: 5 seconds for tick-driven live paths, strategy-specific for M1 paths.
- Action: block new risk-increasing orders, allow reduce-only/emergency flatten when broker connectivity is healthy, and record a risk event.

## Reconciliation Drift

- Signal: internal order or position state differs from the broker snapshot beyond tolerance.
- Severity: critical when exposure differs, warning for metadata-only order mismatch.
- Initial threshold: any unknown live order, any missing open order, or position quantity delta above configured tolerance.
- Action: pause new order submission, fetch broker orders/deals/positions, reconcile state, and escalate if exposure cannot be explained.

## Reject Burst

- Signal: order rejects exceed the configured count inside the configured lookback window.
- Severity: critical.
- Initial threshold: reuse RiskEngine reject-burst settings.
- Action: activate session or symbol kill switch, keep paper logging active, inspect broker retcodes, spread/liquidity, order_check output, and strategy target churn.

## Kill Switch Activation

- Signal: session kill switch, symbol kill switch, stale-data kill switch, daily-loss block, or emergency flatten path is active.
- Severity: critical for live-safe mode, warning for paper-only mode.
- Initial threshold: any activation.
- Action: record incident, verify no new risk-increasing orders can pass, reconcile exposure, and require explicit operator release.

## Alert Hygiene

- Alerts must include UTC timestamps.
- Alerts must state whether the runtime is research, paper, or live-safe.
- Alerts must separate broker state from internal state.
- Alerts must include the current paper/live posture.
