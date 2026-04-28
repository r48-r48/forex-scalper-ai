# MT5 Windows Validation

Snapshot date: 2026-04-28

## Environment

- Host: Windows notebook accessed through SSH on the local network.
- Repository path: `C:\Users\PC\projects\forex-scalper-ai`
- Commit: `f5c5894 Add offline validation hardening artifacts`
- Python: `3.12.10`
- MT5 Python package: installed and importable.
- Terminal: MetaTrader 5, build `5834`.
- Broker session: MetaQuotes demo session already authorized in the terminal.

## Checks Run

From the project `.venv`:

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe scripts\mt5_smoke.py --config-name mt5 --preflight-only
.\.venv\Scripts\python.exe scripts\mt5_smoke.py --config-name mt5
```

Additional direct broker-side probe:

- `mt5.initialize()`
- `mt5.terminal_info()`
- `mt5.account_info()`
- `mt5.symbols_get()`
- `mt5.symbol_info("EURUSD")`
- `mt5.symbol_info_tick("EURUSD")`
- `mt5.order_check()` for a minimum-volume EURUSD buy request
- `mt5.orders_total()`
- `mt5.positions_total()`

No `order_send()` call was made.

## Results

- Connection succeeded.
- Account snapshot was available.
- Terminal snapshot was available.
- Symbol universe contained `6053` symbols.
- `EURUSD` was visible and selected.
- EURUSD tick data was available.
- Open orders: `0`.
- Open positions: `0`.
- EURUSD minimum volume: `0.01`.
- EURUSD spread at probe time: `2` points.
- FOK `order_check` returned broker retcode `0` with comment `Done`.
- IOC and RETURN filling modes returned broker retcode `10030` with comment `Unsupported filling mode`.
- `SCALPER_AI_LIVE_CONFIRMATION` was not set, so true live runtime remains blocked by design.

## Follow-Up

- Keep using FOK for this demo broker/symbol unless symbol metadata changes.
- Configure explicit terminal path and broker env vars only when moving beyond saved terminal-session smoke checks.
- Enable and document live confirmation only for an intentionally approved live-safe run.
- Validate history/deal normalization after a controlled demo fill or imported broker history is available.
- Keep `order_send()` disabled until the operator explicitly approves a demo-order test.
