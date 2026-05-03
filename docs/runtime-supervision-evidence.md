# Runtime Supervision Evidence

Snapshot date: 2026-05-03

## Purpose

This note records bounded paper-runtime supervision evidence beyond the initial
5-iteration Compose smoke. It is intentionally paper-safe: no live broker
credentials are required and no MT5 `order_send` path is exercised.

## Local Paper Supervisor

Command:

```bash
make PYTHON=.venv/bin/python \
  SUPERVISOR_ITERATIONS=30 \
  SUPERVISOR_HEALTH_INTERVAL_SECONDS=0.05 \
  SUPERVISOR_RECONCILIATION_INTERVAL_SECONDS=0.05 \
  SUPERVISOR_IDLE_SLEEP_SECONDS=0.05 \
  LOCAL_SUPERVISOR_ALERT_JSONL_PATH=data/artifacts/paper-supervisor-30-make-alerts.jsonl \
  supervise-paper
```

Result:

- iterations: `30`
- overall status: `30 pass`
- health due on every iteration: `true`
- reconciliation due on every iteration: `true`
- metrics rendered on every iteration: `true`
- runtime errors: `0`
- alert transport errors: `0`
- alert events: `0`
- first check: `2026-05-03T14:06:14.935469+00:00`
- last check: `2026-05-03T14:06:16.533603+00:00`

Raw local evidence was written under ignored `data/artifacts/` paths. The alert
JSONL file was not created because no alert events were emitted.

## Parallels MT5 Read-Only Smoke

The same-Mac Parallels VM was reachable as `Windows 11`.

Read-only smoke command shape:

```cmd
cd /d C:\Users\dzhabrailtalkanov\projects\forex-scalper-ai
set BROKER_MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
.venv\Scripts\python.exe scripts\mt5_smoke.py --config-name mt5
```

Result:

- connected: `true`
- account: `610769553`
- server: `Dukascopy-demo-mt5-1`
- company: `Dukascopy Bank SA`
- currency: `TRY`
- balance/equity: `99941.09`
- ping latency: about `17 ms`
- open orders: `0`
- open positions: `0`
- `order_send`: not called by this smoke path

The VM project folder is currently a source copy, not a Git checkout. Future
real-terminal validation against the latest repository code should sync or clone
the current `main` branch into the VM before running code-sensitive MT5 checks.

## Operational Reading

- Paper supervisor scheduling, health, reconciliation, metrics rendering, and
  alert routing stayed stable across a longer bounded loop.
- The Parallels terminal session remains available for future explicit read-only
  MT5 probes or controlled demo-only scenarios.
- The next operational hardening step is to extend this into longer wall-clock
  paper/shadow supervision with persisted artifacts and any required alert sink
  topology.
