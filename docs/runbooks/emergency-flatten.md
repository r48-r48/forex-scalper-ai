# Runbook: Emergency Flatten

## Goal

Reduce exposure to flat when risk requires immediate action.

## Preconditions

- Broker connectivity is available.
- Current broker position snapshot is known.
- Operator understands symbol, size, and expected side.

## Steps

1. Activate session or symbol kill switch.
2. Block all new risk-increasing orders.
3. Capture internal and broker position snapshots.
4. Generate reduce-only flatten intent from OMS.
5. Run broker pre-check where available.
6. Submit flatten order only through the approved adapter path.
7. Capture broker response and fills.
8. Reconcile broker position to zero or the intended residual state.
9. Keep kill switch active until post-incident review.

## Stop Criteria

- Broker position cannot be verified.
- Broker rejects flatten order.
- Internal state and broker state diverge.
- Operator cannot identify the active symbol or side.
