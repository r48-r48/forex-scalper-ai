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

Current result after the scripts and config-layer cleanup batches:

```text
Found 434 errors.
275 fixable with --fix.
```

Statistics:

```text
134 E501  line-too-long
107 UP017 datetime-timezone-utc
107 UP045 non-pep604-annotation-optional
 24 I001  unsorted-imports
 18 UP035 deprecated-import
 16 UP042 replace-str-enum
 11 UP037 quoted-annotation
  6 B009  get-attr-with-constant
  6 B905  zip-without-explicit-strict
  2 F401  unused-import
  2 UP007 non-pep604-annotation-union
  1 B007  unused-loop-control-variable
```

Completed cleanup batches:

- 2026-04-28: scripts entrypoints cleanup removed script-level `B008`, `E402`, several `E501`, one `I001`, one `F401`, and two `UP045` issues. `collect_ticks.py --help` now works without editable install by bootstrapping `src/` like the other scripts.
- 2026-04-28: config-layer cleanup converted optional annotations in `src/scalper_ai/config`, sorted imports, and wrapped remaining long expressions. Targeted Ruff is green for `src/scalper_ai/config` and `tests/unit/test_config_loader.py`.

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

- Full `make lint` and `make typecheck` are known non-green baseline checks.
- Targeted Ruff must pass for newly added or materially touched code.
- Full `.venv/bin/python -m compileall src tests scripts` and `.venv/bin/pytest` remain required regression checks.
