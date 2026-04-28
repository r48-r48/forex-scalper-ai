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

Current result:

```text
Found 511 errors.
331 fixable with --fix.
```

Statistics:

```text
156 UP045 non-pep604-annotation-optional
148 E501  line-too-long
107 UP017 datetime-timezone-utc
 27 I001  unsorted-imports
 18 UP035 deprecated-import
 16 UP042 replace-str-enum
 14 UP037 quoted-annotation
  6 B009  get-attr-with-constant
  6 B905  zip-without-explicit-strict
  5 E402  module-import-not-at-top-of-file
  3 F401  unused-import
  2 B008  function-call-in-default-argument
  2 UP007 non-pep604-annotation-union
  1 B007  unused-loop-control-variable
```

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
