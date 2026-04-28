# MT5 Windows Validation

Snapshot date: 2026-04-28

## Environment

- Host: Windows notebook accessed through SSH on the local network.
- Repository path: `C:\Users\PC\projects\forex-scalper-ai`
- Validated code: current repository state on 2026-04-28, including the safe broker-probe/comment-limit changes.
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
.\.venv\Scripts\python.exe scripts\mt5_broker_probe.py --config-name mt5 --symbol EURUSD --time-in-force fok --include-raw-samples --output-path data\artifacts\mt5_broker_probe_windows.json
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
- `scripts\mt5_broker_probe.py` through the normalized `Mt5TerminalClient`

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
- Safe broker probe through `Mt5TerminalClient.check_order()` returned `accepted=true`, `retcode=0`, margin `11.71`, free margin `99988.29`, and broker comment `Done`.
- Safe broker probe normalized raw history and live state successfully: `0` open orders, `0` open positions, `0` raw historical orders, and `0` raw historical deals in the 24-hour window.
- The probe confirmed the artifact path `data\artifacts\mt5_broker_probe_windows.json` is writable on the Windows repo.
- A real terminal check found the MetaTrader5 Python bridge rejects order comments at `30+` characters; the client now sanitizes comments to ASCII alphanumeric/underscore and clamps them to `29` characters.
- `SCALPER_AI_LIVE_CONFIRMATION` was not set, so true live runtime remains blocked by design.

## Follow-Up

- Keep using FOK for this demo broker/symbol unless symbol metadata changes.
- Keep MT5 order comments at `29` characters or shorter.
- Configure explicit terminal path and broker env vars only when moving beyond saved terminal-session smoke checks.
- Enable and document live confirmation only for an intentionally approved live-safe run.
- Validate non-empty history/deal normalization after a controlled demo fill or imported broker history is available.
- Keep `order_send()` disabled until the operator explicitly approves a demo-order test.
