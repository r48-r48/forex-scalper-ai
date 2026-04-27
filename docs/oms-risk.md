# OMS And Risk Engine

## Purpose

P0.4 adds a deterministic execution-control layer above broker adapters. The OMS owns order
lifecycle state. The RiskEngine owns pre-trade approval and rejection. Broker adapters still only
translate approved intents to paper or live broker actions.

## OMS Lifecycle

Canonical states:

- `NEW`
- `CHECKED`
- `SENT`
- `ACK`
- `PARTIAL`
- `FILLED`
- `REJECTED`
- `CANCELLED`
- `RECONCILED`

Allowed transitions:

- `NEW` -> `CHECKED`, `REJECTED`, `CANCELLED`
- `CHECKED` -> `SENT`, `REJECTED`, `CANCELLED`
- `SENT` -> `ACK`, `REJECTED`, `CANCELLED`
- `ACK` -> `PARTIAL`, `FILLED`, `REJECTED`, `CANCELLED`
- `PARTIAL` -> `PARTIAL`, `FILLED`, `CANCELLED`
- `FILLED` -> `RECONCILED`
- `REJECTED` -> `RECONCILED`
- `CANCELLED` -> `RECONCILED`

Invalid transitions raise immediately. Rejected and cancelled transitions require explicit reasons.

## Risk Controls

The first RiskEngine contract evaluates one `OrderIntent` against a deterministic `RiskContext`.

Implemented controls:

- session kill switch
- symbol kill switch
- duplicate intent detection
- duplicate broker order detection when a candidate broker order id is supplied
- reject-burst kill switch
- stale-market-data kill switch
- max order rate per minute
- max daily loss
- max daily drawdown
- max projected position size
- reduce-only exposure-increase rejection

Risk decisions can be written as `risk_event` journal records through
`RiskDecision.to_journal_event()`.

## Emergency Flatten

`build_emergency_flatten_intent()` creates a reduce-only market `OrderIntent` for the opposite side
of the current net position. It returns `None` when the position is already flat.

This helper creates the intent only. It does not bypass risk, paper/live routing, journal recording,
or broker adapter safety checks.
