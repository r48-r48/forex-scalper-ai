# Test Matrix

Snapshot date: 2026-04-27

## Current Status

Latest local validation in the available desktop environment:

```bash
python3 -m pytest
```

Result:

```text
119 passed
```

The local desktop Python is 3.9.6. The declared project runtime is Python 3.11+, so Python 3.11+ CI and target-environment validation remain required.

## Test Groups

| Group | Files | Coverage focus | External dependencies | Current status |
|---|---:|---|---|---|
| Config and logging | `tests/unit/test_config_loader.py`, `tests/unit/test_logging.py` | YAML overlays, env overrides, UTC logging config | none | passed locally |
| Domain contracts | `tests/unit/test_domain_*.py`, `tests/integration/test_domain_roundtrip.py` | immutable schemas, UTC timestamps, trading invariants, serialization | none | passed locally |
| Data ingestion and preprocessing | `tests/unit/test_data_*.py`, `tests/integration/test_raw_parquet_ingestion.py`, `tests/integration/test_walk_forward_splits.py` | replay readers, MT5 ingestion normalization stubs, bar builders, preprocessing, labels, datasets, splits, Parquet writer | local filesystem, pandas/pyarrow | passed locally |
| Feature engineering | `tests/unit/test_features_*.py`, `tests/integration/test_feature_offline_online_parity.py` | spread/returns/volatility, OFI/MLOFI, VPIN proxy, online/offline parity | none | passed locally |
| Models | `tests/unit/test_models_*.py`, `tests/integration/test_model_dataset_bridge.py` | tensorizer, causal mask, Transformer predictor bridge | Torch | passed locally |
| RL | `tests/unit/test_rl_*.py`, `tests/integration/test_rl_episode_rollout.py` | deterministic trading environment, policy helpers, rollout/training smoke | Torch | passed locally |
| Backtesting | `tests/unit/test_backtesting_accounting.py`, `tests/integration/test_backtesting_replay.py` | costs-aware market fills, netting accounting, replay engine V1 | pandas | passed locally |
| Validation | `tests/unit/test_validation_metrics.py`, `tests/integration/test_validation_walk_forward.py` | fold metrics, walk-forward orchestration, backtest-frame conversion | pandas | passed locally |
| Execution | `tests/unit/test_execution_*.py`, `tests/integration/test_execution_workflow.py` | paper adapter, router, live stub, MT5 adapter/client fakes, reconciliation | no real broker; fake MT5 modules | passed locally |
| Journal | `tests/unit/test_journal_events.py`, `tests/integration/test_journal_jsonl.py` | audit event envelope, event categories, JSONL write/read, flat record export | local filesystem | passed locally |
| Deployment | `tests/unit/test_deployment_*.py`, `tests/integration/test_deployment_bootstrap.py` | runtime safety, health, metrics, MT5 preflight, live factory fakes | no real broker; fake MT5 modules | passed locally |

## Environment Classification

| Check | Status | Notes |
|---|---|---|
| `python3 -m pytest` on local Python 3.9.6 | passing | Useful compatibility signal, but not the declared target runtime |
| `PYTHONPYCACHEPREFIX=/tmp/scalper_ai_pycache python3 -m compileall src tests scripts` | passing | Needed locally because default Python cache path can be sandbox-blocked |
| Python 3.11+ full suite | pending | Required by `pyproject.toml` |
| Real MT5 terminal smoke | pending | Requires package, terminal, account/session credentials, and explicit live confirmation |
| GitHub Actions Python 3.11 | added | Safe CI, no live credentials or live order submission; compile/test/preflight only until lint/typecheck are validated in a dev environment |

## Test Risk Notes

- MT5 tests currently use fake modules and do not prove broker behavior against a real terminal.
- Backtesting V1 models immediate market fills with explicit costs, but latency, queue position, partial fills, and cancel/replace races are future P1 work.
- Current test suite includes the unified event journal contract, but OMS/RiskEngine state machine and baseline strategy suite remain future POST-PHASE roadmap items.
- Linting is exposed through `make lint`, but ruff is not installed in the current local Python 3.9.6 environment.
- Full type checking is exposed through `make typecheck`, but mypy is not yet part of the GitHub Actions gate until the codebase has been validated under Python 3.11+ with all dev dependencies.
