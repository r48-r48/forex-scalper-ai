# Development Setup

## Runtime Target

- Project target: Python 3.11+
- Current local desktop host observed on 2026-04-27: Python 3.9.6
- CI target: Python 3.11

The system Python 3.9.6 environment can run the current tests as a compatibility signal, but the
project target is Python 3.11+. A local `.venv` was created from bundled Python 3.12.13 for target
validation.

## Install

Create and activate a Python 3.11+ virtual environment, then install:

```bash
pip install -e ".[dev,ml]"
```

Current local target-validation environment:

```bash
.venv/bin/python --version
make PYTHON=.venv/bin/python compile
make PYTHON=.venv/bin/python test
make PYTHON=.venv/bin/python mt5-preflight
```

The `ml` extra is needed for the current Torch-based model and RL tests.

## Common Commands

Use the Makefile as the first-class local interface:

```bash
make install
make compile
make test
make lint
make typecheck
make run-paper
make health-paper
make mt5-preflight
```

Equivalent raw commands:

```bash
PYTHONPYCACHEPREFIX=/tmp/scalper_ai_pycache python3 -m compileall src tests scripts
python3 -m pytest
python3 -m ruff check src tests scripts
python3 -m mypy src
python3 scripts/run_runtime.py describe --config-name paper
python3 scripts/run_runtime.py health --config-name paper
python3 scripts/mt5_smoke.py --config-name mt5 --preflight-only
python3 scripts/mt5_broker_probe.py --config-name mt5 --symbol EURUSD --time-in-force fok --skip-order-check
```

`PYTHONPYCACHEPREFIX` keeps Python bytecode writes inside `/tmp` when sandbox or host permissions prevent writes to the default user cache.

## Configuration

Config overlays live in `configs/`:

- `base.yaml`
- `baselines.yaml`
- `research.yaml`
- `paper.yaml`
- `live.yaml`
- `mt5.yaml`

Environment overrides use the `SCALPER_AI_` prefix. Start from `.env.example`, but do not commit real `.env` files or broker credentials.

Important safety posture:

- paper mode remains the default safe operational path
- live mode requires explicit confirmation
- MT5 credentials are optional for preflight diagnostics but required for real terminal validation

## Paper Runtime Smoke

```bash
make run-paper
make health-paper
```

Expected behavior:

- runtime starts in `paper`
- health snapshot returns overall `pass`
- runtime stops after printing the requested surface

## MT5 Preflight And Broker Probe

```bash
make mt5-preflight
```

This is read-only and does not attempt a terminal connection when preflight is not ready. In an environment without the `MetaTrader5` package or credentials, it should print structured diagnostics rather than a traceback.

When an authorized terminal session is available, run the safe broker probe:

```bash
python3 scripts/mt5_broker_probe.py --config-name mt5 --symbol EURUSD --time-in-force fok --include-raw-samples --output-path data/artifacts/mt5_broker_probe.json
```

The broker probe reads terminal/account/symbol/tick/history state and can run `order_check`, but it never calls `order_send`.

Further MT5 validation still requires:

- `MetaTrader5` Python package is installed
- terminal path is configured or auto-discovered
- `SCALPER_AI_BROKER_MT5_*` credentials are available or terminal-side saved credentials are intentionally used
- `SCALPER_AI_LIVE_CONFIRMATION` is provided for live-safe runtime startup
- explicit operator approval before any controlled demo-order `order_send` test

## Docker

The repository currently ships `docker-compose.yml` with Redis for development infrastructure.
Docker/Kubernetes production packaging remains deferred until real MT5 terminal validation is
complete.
