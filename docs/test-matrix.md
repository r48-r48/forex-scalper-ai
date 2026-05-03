# Test Matrix

Snapshot date: 2026-04-30

## Current Status

Latest local validation in the available desktop environment:

```bash
.venv/bin/pytest
```

Result:

```text
188 passed
```

The local desktop `/usr/bin/python3` is 3.9.6. A project `.venv` was created with bundled Python 3.12.13 for target-runtime validation.

## Test Groups

| Group | Files | Coverage focus | External dependencies | Current status |
|---|---:|---|---|---|
| Config and logging | `tests/unit/test_config_loader.py`, `tests/unit/test_logging.py` | YAML overlays, env overrides, UTC logging config | none | passed locally |
| Domain contracts | `tests/unit/test_domain_*.py`, `tests/integration/test_domain_roundtrip.py` | immutable schemas, UTC timestamps, trading invariants, serialization | none | passed locally |
| Data ingestion and preprocessing | `tests/unit/test_data_*.py`, `tests/integration/test_raw_parquet_ingestion.py`, `tests/integration/test_walk_forward_splits.py` | replay readers, MT5 ingestion normalization stubs, bar builders, preprocessing, labels, datasets, splits, Parquet writer | local filesystem, pandas/pyarrow | passed locally |
| Feature engineering | `tests/unit/test_features_*.py`, `tests/integration/test_feature_offline_online_parity.py` | spread/returns/volatility, OFI/MLOFI, VPIN proxy, online/offline parity | none | passed locally |
| Models | `tests/unit/test_models_*.py`, `tests/integration/test_model_dataset_bridge.py` | tensorizer, causal mask, Transformer predictor bridge, interpretable supervised baseline filter | Torch for Transformer tests | passed locally |
| RL | `tests/unit/test_rl_*.py`, `tests/integration/test_rl_episode_rollout.py` | deterministic trading environment, policy helpers, rollout/training smoke | Torch | passed locally |
| Backtesting | `tests/unit/test_backtesting_accounting.py`, `tests/unit/test_backtesting_baselines.py`, `tests/unit/test_backtesting_execution_simulator.py`, `tests/integration/test_backtesting_replay.py` | costs-aware market fills, netting accounting, replay engine V1, baseline strategies, execution-aware V2 scenarios | pandas | passed locally |
| Validation | `tests/unit/test_validation_metrics.py`, `tests/unit/test_validation_baseline_suite.py`, `tests/unit/test_validation_gate.py`, `tests/unit/test_validation_shadow.py`, `tests/integration/test_validation_walk_forward.py`, `tests/integration/test_baseline_walk_forward_suite.py`, `tests/integration/test_supervised_filter_walk_forward.py` | fold metrics, walk-forward orchestration, backtest-frame conversion, baseline suite and sensitivity reports, validation gate, shadow decisions, supervised filter walk-forward | pandas | passed locally |
| Execution | `tests/unit/test_execution_*.py`, `tests/unit/test_scripts_mt5_broker_probe.py`, `tests/unit/test_scripts_mt5_demo_order.py`, `tests/unit/test_scripts_mt5_flatten_positions.py`, `tests/integration/test_execution_workflow.py` | paper adapter, router, live stub, MT5 adapter/client fakes, reconciliation, durable state store, safe broker probe payloads, controlled demo-order safety gates, position-ticket flattening | no real broker; fake MT5 modules | passed locally |
| Journal | `tests/unit/test_journal_events.py`, `tests/integration/test_journal_jsonl.py` | audit event envelope, event categories, JSONL write/read, flat record export | local filesystem | passed locally |
| OMS/Risk | `tests/unit/test_services_oms.py`, `tests/unit/test_risk_engine.py` | OMS lifecycle transitions, emergency flatten intent, deterministic pre-trade risk blocks, journalable risk decisions | none | passed locally |
| Deployment | `tests/unit/test_deployment_*.py`, `tests/integration/test_deployment_bootstrap.py` | runtime safety, health, metrics, durable recovery gates, JSONL/webhook alerts, MT5 preflight, live factory fakes | no real broker; fake MT5 modules; webhook uses fake opener | passed locally |

## Environment Classification

| Check | Status | Notes |
|---|---|---|
| `python3 -m pytest` on local Python 3.9.6 | passing | Useful compatibility signal, but not the declared target runtime |
| `PYTHONPYCACHEPREFIX=/tmp/scalper_ai_pycache python3 -m compileall src tests scripts` | passing | Needed locally because default Python cache path can be sandbox-blocked |
| Python 3.11+ full suite | passing | Python 3.12.13 `.venv`, full suite currently `265 passed` |
| Real MT5 terminal smoke | partial pass | Windows notebook and same-Mac Parallels VM both connect to demo terminals without `order_send`; Windows/MetaQuotes accepted EURUSD FOK, while Parallels/Dukascopy accepted EURUSD IOC and rejected FOK |
| Docker/Compose paper runtime | passing on Docker Desktop | `docker build -t forex-scalper-ai:local .` passed on 2026-05-03; Compose `paper-runtime` describe/health/metrics passed with paper mode, health `overall_status=pass`, Prometheus metrics output, and a bounded 5-iteration supervisor run with zero alerts/errors; test Redis container/network was removed with `docker compose --profile paper down` |
| GitHub Actions Python 3.11 | added | Safe CI, no live credentials or live order submission; compile/test/preflight only until lint/typecheck are validated in a dev environment |

## Test Risk Notes

- MT5 unit tests use fake modules, while the Windows/Parallels broker probes now prove connection, symbol/tick polling, broker-specific filling-mode handling, controlled demo order submission, position-ticket flattening, and 29-character comment clamping against real authorized demo terminals.
- Webhook alert transport tests use a fake opener and do not send real network requests.
- Backtesting V1 models immediate market fills with explicit costs; execution-aware V2 now covers latency, queue position, partial fills, stale/closed markets, and cancel/replace races with forced-scenario tests.
- Baseline strategies are deterministic and report explicit costs, but they still rely on replayed feature-frame quality and do not prove live broker profitability.
- Linting is exposed through `make lint` and `make lint-baseline`. The Python 3.12.13 `.venv` baseline currently reports `374` historical Ruff issues after the scripts, config-layer, logging-utils, journal, OMS, validation, models, and risk cleanup batches; targeted Ruff is green for the 2026-04-30 P0.C/P0.D/P0.E MT5/reconciliation/deal-accounting/protective-order changes; newly touched code should keep targeted Ruff checks green.
- Full type checking is exposed through `make typecheck` and `make typecheck-baseline`. The Python 3.12.13 `.venv` baseline currently reports `51` mypy errors in `30` files, so mypy is not yet part of the GitHub Actions gate.
