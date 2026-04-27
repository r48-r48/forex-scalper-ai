# Paper And Shadow Mode

## Purpose

Shadow mode compares a champion strategy against one or more challengers without submitting orders.
It is for decision drift, not execution simulation.

The implementation lives in `src/scalper_ai/validation/shadow.py`.

## What It Records

`run_shadow_decision_session()` replays a market frame through:

- one champion strategy
- one or more challenger strategies
- the same `BacktestEvent` and `BacktestState` contract used by replay backtests

For every event it records:

- strategy name and role
- event and availability timestamps
- mark price
- current champion shadow position
- resolved target position
- raw target position, where `None` means unchanged

It also emits challenger diff rows:

- champion target position
- challenger target position
- absolute target delta
- direction-change flag

## Safety Boundary

Shadow mode does not create `OrderIntent` objects and does not touch broker adapters.
The current position shown to challengers follows the champion's prior decision so challengers are compared against the same paper context.

## Artifact Path

Persist reports under an ignored artifact directory, for example:

```python
from pathlib import Path

from scalper_ai.validation import (
    ShadowStrategySpec,
    run_shadow_decision_session,
    write_shadow_decision_report,
)

report = run_shadow_decision_session(
    market_frame,
    champion=ShadowStrategySpec(name="champion", strategy=champion_strategy),
    challengers=(ShadowStrategySpec(name="candidate", strategy=candidate_strategy),),
)
write_shadow_decision_report(report, output_dir=Path("data/artifacts/shadow"))
```

## Review Rule

A challenger should not move toward paper execution until:

- daily disagreement ratios are understood
- direction-change deltas have been reviewed by regime
- validation gate status is `pass`
- risk/OMS behavior remains unchanged except for explicit config
