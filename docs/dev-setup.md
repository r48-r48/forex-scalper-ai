# Development Setup

## Runtime Target

- Project target: Python 3.11+
- Current local desktop host observed on 2026-04-27: Python 3.9.6
- CI target: Python 3.11

The local Python 3.9.6 environment can run the current tests, but release validation must happen on Python 3.11+ because `pyproject.toml` declares `requires-python = ">=3.11"`.

## Install

Create and activate a Python 3.11+ virtual environment, then install:

```bash
pip install -e ".[dev,ml]"
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
```

`PYTHONPYCACHEPREFIX` keeps Python bytecode writes inside `/tmp` when sandbox or host permissions prevent writes to the default user cache.

## Configuration

Config overlays live in `configs/`:

- `base.yaml`
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

## MT5 Preflight

```bash
make mt5-preflight
```

This is read-only and does not attempt a terminal connection when preflight is not ready. In an environment without the `MetaTrader5` package or credentials, it should print structured diagnostics rather than a traceback.

Real MT5 validation remains pending until:

- `MetaTrader5` Python package is installed
- terminal path is configured or auto-discovered
- `SCALPER_AI_BROKER_MT5_*` credentials are available or terminal-side saved credentials are intentionally used
- `SCALPER_AI_LIVE_CONFIRMATION` is provided for live-safe runtime startup

## Docker

The repository currently ships `docker-compose.yml` with Redis for development infrastructure. Docker/Kubernetes production packaging is intentionally deferred until the POST-PHASE hardening work has completed the MT5 safe submit chain, event journal, and OMS/RiskEngine state machine.
