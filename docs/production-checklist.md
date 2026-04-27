# Production Checklist

## Pre-Release

- Full test suite passes in Python 3.12.13 or target Python 3.11+ runtime.
- `python3 -m compileall src tests scripts` passes.
- MT5 preflight passes against the real terminal when live validation is in scope.
- Paper mode remains the default.
- Live confirmation is explicit and documented.
- Baseline strategy comparison is attached.
- Validation gate report status is `pass`.
- Risk flags are empty.
- Shadow decision report is reviewed for challenger changes.
- Spread, slippage, commission, latency, and fill assumptions are explicit.
- Rollback plan is ready.

## Runtime

- Config source is known.
- Artifact output path is writable.
- Journal output path is writable.
- Broker credentials are loaded from environment or approved secret storage.
- Health endpoint or health command is available.
- Metrics output is available.
- Operator knows current runtime mode: research, paper, or live-safe.

## Safety

- Session kill switch tested.
- Symbol kill switch tested.
- Reject-burst kill switch tested.
- Stale-data block tested.
- Emergency flatten path reviewed.
- Reconciliation tolerance reviewed.
- No risk-increasing order can bypass RiskEngine/OMS.

## Release Decision

- Approver:
- Date UTC:
- Target version/commit:
- Runtime mode:
- Symbols:
- Max position:
- Max daily loss:
- Notes:
