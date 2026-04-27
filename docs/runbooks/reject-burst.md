# Runbook: Reject Burst

## Goal

Stop repeated broker rejects before they become uncontrolled churn.

## Immediate Actions

1. Activate session or symbol kill switch according to RiskEngine policy.
2. Stop new risk-increasing submissions.
3. Capture rejected order intents, broker retcodes, comments, and `order_check` results.
4. Record a risk event.
5. Reconcile open orders and current positions.

## Investigation

1. Check market status and session hours.
2. Check spread, liquidity, and stale-data status.
3. Check order size, symbol constraints, margin, and filling mode.
4. Check whether strategy target churn created cancel/replace pressure.

## Recovery

1. Fix the root cause.
2. Run paper/shadow mode first.
3. Require explicit operator release before live-safe submit path resumes.
