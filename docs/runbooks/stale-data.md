# Runbook: Stale Data

## Goal

Block decisions based on stale market data while preserving the ability to reduce risk.

## Immediate Actions

1. Mark the stale-data risk condition active.
2. Block new risk-increasing orders.
3. Record latest event and available timestamps.
4. Check data adapter health and broker connectivity separately.
5. Keep paper/shadow decision logging active only if clearly labeled stale.

## Recovery

1. Confirm fresh UTC-aware market data has resumed.
2. Verify no gaps or duplicate timestamp windows affect the strategy.
3. Re-run health snapshot.
4. Reconcile positions before releasing the stale-data block.

## Escalate When

- Staleness persists beyond the session threshold.
- Data timestamps are naive or out of order.
- Strategy decisions changed materially during stale period.
