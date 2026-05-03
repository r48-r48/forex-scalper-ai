# Lint And Typecheck Baseline

Snapshot date: 2026-04-28

## Purpose

This document records the current full-repository Ruff and mypy baseline so cleanup can be done in small, reviewable batches without mixing style churn into trading-behavior changes.

New or touched production code should keep passing targeted Ruff checks even while the historical backlog is being retired.

## Commands

```bash
.venv/bin/ruff check src tests scripts --statistics
.venv/bin/mypy src
```

## Ruff Baseline

Current result after scripts, config, logging, journal, OMS, validation, models, risk, data-layer, backtesting, features, RL, deployment, execution, domain, and final UTC cleanup batches:

```text
All checks passed.
```

Completed cleanup batches:

- 2026-04-28: scripts entrypoints cleanup removed script-level `B008`, `E402`, several `E501`, one `I001`, one `F401`, and two `UP045` issues. `collect_ticks.py --help` now works without editable install by bootstrapping `src/` like the other scripts.
- 2026-04-28: config-layer cleanup converted optional annotations in `src/scalper_ai/config`, sorted imports, and wrapped remaining long expressions. Targeted Ruff is green for `src/scalper_ai/config` and `tests/unit/test_config_loader.py`.
- 2026-04-28: logging-utils cleanup converted the remaining optional annotation in `src/scalper_ai/utils/logging.py`. Targeted Ruff is green for `src/scalper_ai/utils` and `tests/unit/test_logging.py`.
- 2026-04-28: journal cleanup converted `JournalEventType` to `StrEnum`, modernized optional annotations, removed quoted annotations, and switched tests to `datetime.UTC`. Targeted Ruff is green for `src/scalper_ai/journal` and journal tests.
- 2026-04-28: OMS cleanup converted `OmsOrderStatus` to `StrEnum`, modernized optional annotations, removed quoted annotations, and switched tests to `datetime.UTC`. Targeted Ruff is green for `src/scalper_ai/services` and OMS tests.
- 2026-04-28: validation cleanup sorted imports, moved `Sequence` to `collections.abc`, wrapped long walk-forward expressions, and switched validation tests to `datetime.UTC`. Targeted Ruff is green for `src/scalper_ai/validation` and selected validation tests.
- 2026-04-28: models cleanup modernized tensorizer/transformer annotations, moved `Sequence` to `collections.abc`, and wrapped long tensorizer/transformer expressions. Targeted Ruff is green for `src/scalper_ai/models` and selected model tests.
- 2026-04-28: risk cleanup converted risk enums to `StrEnum`, added a narrow `RiskConfigLike` protocol for typed config-derived limits, modernized optional annotations, and switched tests to `datetime.UTC`. Targeted Ruff is green for `src/scalper_ai/risk` and risk tests.
- 2026-05-03: data-layer cleanup modernized optional annotations, switched UTC usage to `datetime.UTC`, sorted imports, removed a constant `getattr`, and wrapped remaining long expressions across `src/scalper_ai/data` and selected data tests. Targeted Ruff is green for the data package and selected data/integration tests.
- 2026-05-03: backtesting cleanup switched tests to `datetime.UTC`, sorted imports, added explicit `zip(..., strict=True)`, removed one unused loop index, and wrapped long accounting/engine expressions without changing fill or PnL math. Targeted Ruff is green for `src/scalper_ai/backtesting` and selected backtesting tests.
- 2026-05-03: features cleanup modernized optional and union annotations, moved imports to `collections.abc`, switched tests to `datetime.UTC`, added explicit `zip(..., strict=True)`, sorted imports, and wrapped long feature expressions without changing feature calculations. Targeted Ruff is green for `src/scalper_ai/features` and selected feature tests.
- 2026-05-03: RL cleanup modernized optional annotations, moved `Sequence` to `collections.abc`, removed an unused import, switched tests to `datetime.UTC`, sorted imports, and wrapped long environment/training expressions without changing reward or policy-training logic. Targeted Ruff is green for `src/scalper_ai/rl` and selected RL tests.
- 2026-05-03: deployment cleanup converted `HealthStatus` to `StrEnum`, modernized health annotations/imports, and wrapped Prometheus metric rendering/key expressions without changing output semantics. Targeted Ruff is green for `src/scalper_ai/deployment` and selected deployment tests.
- 2026-05-03: execution cleanup switched selected tests/live stub UTC usage to `datetime.UTC`, sorted imports, wrapped protocol/router signatures, and wrapped paper execution cost/order-trigger expressions without changing order lifecycle or fill math. Targeted Ruff is green for `src/scalper_ai/execution` and selected execution tests.
- 2026-05-03: domain and final UTC cleanup modernized canonical domain annotations/imports, converted domain enums to `StrEnum`, switched remaining tests to `datetime.UTC`, and closed the remaining full-repository Ruff backlog. Full Ruff is now green for `src`, `tests`, and `scripts`.

Recommended cleanup order:

1. Run targeted `ruff check --fix` batches on imports and pyupgrade-only changes.
2. Fix `E501` by wrapping expressions without changing behavior.
3. Review `B905`, `B009`, and `B007` manually because they can affect behavior/readability.
4. Decide whether Typer entrypoints should keep local `# noqa: B008` exceptions or move defaults to module-level constants.
5. Keep script `E402` exceptions only where `src/` path bootstrapping is required before local imports.

## Mypy Baseline

Current result:

```text
Found 51 errors in 30 files.
```

Main categories:

- missing third-party stubs for `pandas` and `pyarrow`
- Pydantic settings fallback typing in `config/loader.py`
- protocol variance in `data/interfaces.py`
- optional float narrowing in execution adapters
- union narrowing in online features and MT5 live adapter
- untyped Torch distribution calls in RL policy/training
- a few `Any` returns in model/simulator paths

Recommended cleanup order:

1. Add dependency-stub policy first, especially whether to add `pandas-stubs` and whether to ignore `pyarrow` imports centrally.
2. Fix small internal typing errors that do not touch behavior: logging formatter signature, protocol variance, optional narrowing, and literal narrowing.
3. Add explicit casts or helper functions around Torch distribution calls.
4. Re-run mypy after each batch and update this baseline until `make typecheck` can become a CI gate.

## Gate Policy Until Cleanup

- Full Ruff is green for `src`, `tests`, and `scripts`; mypy remains a known non-green baseline check.
- Targeted Ruff must pass for newly added or materially touched code.
- Full `.venv/bin/python -m compileall src tests scripts` and `.venv/bin/pytest` remain required regression checks.
