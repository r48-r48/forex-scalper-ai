# AGENTS.md

## Project Goal

Build a production-grade AI agent for Forex scalping on tick/M1 data with optional Level 2 / DOM support, feature engineering, Transformer forecasting, DRL policy training, realistic execution, validation, and deployment workflows.

## Current Phase

- Active implementation target: POST-PHASE — Hardening, live integration refinement, and operational stabilization
- Completed phases: PHASE 1, PHASE 2, PHASE 3, PHASE 4, PHASE 5, PHASE 6, PHASE 7, PHASE 8, PHASE 9, PHASE 10, PHASE 11, PHASE 12

Always read before making changes:
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/AGENT_HANDOFF.md`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/docs/current-state.md`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/docs/todo-next.md`
- `/Users/dzhabrailtalkanov/Desktop/forex-scalper-ai/SESSION_CHECKPOINT.md`

## Non-Negotiable Constraints

- Do not introduce look-ahead bias.
- Do not introduce target leakage.
- All timestamps must remain UTC-aware.
- Model costs explicitly; do not hide spread/slippage assumptions.
- Keep paper mode as the default live-safety posture.
- Keep adapters separated from domain logic.
- Keep offline and online pipelines separated.
- Prefer pure functions for feature engineering and preprocessing.
- Do not introduce hidden global state.
- Do not use Russian identifiers in code.

## Architecture

- `src/scalper_ai/config` = settings, config loading, logging bootstrap integration
- `src/scalper_ai/domain` = canonical immutable schemas and enums
- `src/scalper_ai/data` = ingestion, replay, batching, raw persistence, bar builders, preprocessing
- `src/scalper_ai/features` = feature engineering layer
- `src/scalper_ai/models` = supervised forecasting models
- `src/scalper_ai/rl` = environment and policy training
- `src/scalper_ai/backtesting` = event-driven simulator
- `src/scalper_ai/execution` = broker/paper execution adapters
- `src/scalper_ai/deployment` = runtime bootstrap, health, metrics, and safe startup orchestration
- `src/scalper_ai/validation` = walk-forward and robustness validation
- `scripts` = runnable entrypoints and maintenance utilities

## Editing Rules

- Make minimal diffs consistent with the current phase.
- Do not redo already completed phases unless a bug blocks the current phase.
- If you extend schemas or contracts, keep backwards compatibility unless the task explicitly changes the contract.
- When adding new modules, keep them typed, testable, and modular.
- When real integrations are unavailable, provide clean interfaces plus replay/mock fallback.
- Update project memory files when a phase or major milestone is completed.
- During long sessions, refresh `SESSION_CHECKPOINT.md` after substantial milestones, broad test sweeps, or when switching to a new workstream.

## Commands

- Install: `pip install -e ".[dev,ml]"`
- Tests: `pytest`
- Syntax-only fallback: `python3 -m compileall src tests scripts`
- Show handoff status: `python3 scripts/handoff.py status`
- Show resume prompt: `python3 scripts/handoff.py prompt`

## Files To Treat Carefully

- `src/scalper_ai/domain/*` = canonical contracts; do not change lightly
- `configs/base.yaml` = baseline runtime assumptions
- `AGENT_HANDOFF.md` = current session handoff
- `SESSION_CHECKPOINT.md` = freshest compact session snapshot for same-window continuation
- `docs/architecture-decisions.md` = persisted architecture decisions

## Definition Of Done

- Code compiles or runs in the target environment.
- Relevant tests are added and pass when dependencies are available.
- Constraints above remain satisfied.
- Project memory files are updated if the phase boundary or project state changed.
