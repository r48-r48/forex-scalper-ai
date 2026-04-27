# Runbook: Market Open

## Goal

Start the trading session in paper-first posture with broker, data, risk, and journal checks complete.

## Preconditions

- Target commit is approved.
- Production checklist is complete for the selected mode.
- MT5 preflight has passed if broker validation is in scope.
- Paper mode is available as fallback.

## Steps

1. Confirm runtime mode, symbols, max position, and max daily loss.
2. Run compile and tests for the release candidate.
3. Run health check in paper mode.
4. Run MT5 preflight if live-safe mode is being validated.
5. Verify journal output path is writable.
6. Verify artifact output path is writable.
7. Verify latest market data timestamps are UTC-aware and fresh.
8. Verify broker connectivity and account snapshot.
9. Verify internal positions match broker positions.
10. Confirm all kill switches are in the expected state.
11. Start in paper or shadow mode first.
12. Review first decision report before enabling any live-safe submit path.

## Stop Criteria

- Broker disconnect.
- Stale market data.
- Reconciliation drift.
- Unexpected risk flag.
- Missing journal output.
- Operator cannot identify runtime mode.
