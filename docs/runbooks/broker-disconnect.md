# Runbook: Broker Disconnect

## Goal

Prevent unmanaged exposure and recover broker visibility safely.

## Immediate Actions

1. Pause new risk-increasing order submissions.
2. Keep paper/shadow logging active if market data remains available.
3. Record a risk event with UTC timestamp.
4. Capture the latest internal orders and positions.
5. Run MT5 preflight or broker connectivity diagnostics.

## Recovery

1. Restore broker terminal/session connectivity.
2. Fetch broker orders, deals, and positions.
3. Reconcile broker state against internal state.
4. If positions differ, decide whether emergency flatten is required.
5. Clear kill switch only after state is reconciled and documented.

## Escalate When

- Broker state cannot be fetched.
- Internal and broker positions differ.
- Unknown open orders exist.
- Emergency flatten cannot be confirmed.
