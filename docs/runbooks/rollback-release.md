# Runbook: Rollback Release

## Goal

Return to the previous known-good version without creating unmanaged exposure.

## Preconditions

- Previous known-good commit or artifact is identified.
- Current broker state is known.
- Paper fallback is available.

## Steps

1. Stop new risk-increasing decisions.
2. Capture health, metrics, journal tail, and broker state.
3. Reconcile open orders and positions.
4. Decide whether exposure should remain, reduce, or flatten.
5. Stop the current runtime.
6. Deploy previous known-good version.
7. Start in paper mode.
8. Run health check and reconciliation.
9. Re-enable shadow or live-safe mode only after explicit approval.

## Stop Criteria

- Unknown broker exposure.
- Missing previous known-good version.
- Rollback changes config without review.
- Health check fails after rollback.
