# Runbook: Market Close

## Goal

End the session with reconciled state, durable logs, and no unknown exposure.

## Steps

1. Stop new risk-increasing order decisions.
2. Allow reduce-only or flatten actions only when required.
3. Capture final health snapshot.
4. Capture broker orders, deals, and positions.
5. Reconcile internal order state against broker state.
6. Reconcile internal position state against broker state.
7. Confirm journal files are flushed.
8. Persist validation, shadow, or paper session artifacts under `data/artifacts`.
9. Record open risk flags.
10. Update incident or session notes if anything unusual happened.

## Stop Criteria

- Unknown broker position.
- Unknown open order.
- Journal write failure.
- Reconciliation drift without documented explanation.
