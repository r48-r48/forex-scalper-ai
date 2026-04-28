# MT5 Windows Validation

Snapshot date: 2026-04-28

## Windows Notebook Environment

- Host: Windows notebook accessed through SSH on the local network.
- Repository path: `C:\Users\PC\projects\forex-scalper-ai`
- Validated code: current repository state on 2026-04-28, including the safe broker-probe/comment-limit changes.
- Python: `3.12.10`
- MT5 Python package: installed and importable.
- Terminal: MetaTrader 5, build `5834`.
- Broker session: MetaQuotes demo session already authorized in the terminal.

## Parallels Windows 11 Environment

- Host: same-Mac Parallels VM accessed through `prlctl exec "Windows 11" --current-user`.
- SSH is not required for this VM.
- Repository path: `C:\Users\dzhabrailtalkanov\projects\forex-scalper-ai`
- MT5 terminal: `C:\Program Files\MetaTrader 5\terminal64.exe`
- Python: `3.12.10`
- MT5 Python package: `5.0.5735`
- Terminal: MetaTrader 5, build `5836`.
- Broker session: Dukascopy Bank SA demo account `610769553` on `Dukascopy-demo-mt5-1`.

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
- `scripts\mt5_demo_order.py` with explicit operator confirmation in Parallels; it blocked before `order_send` because terminal-side trading permission was disabled

No `order_send()` call was made.

## Windows Notebook Results

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

## Parallels Results

Safe smoke:

- `scripts\mt5_smoke.py --config-name mt5` connected to Dukascopy demo account `610769553`.
- Balance and equity were both `100000.0`.
- Account currency was `TRY`.
- Leverage was `100`.
- Open orders: `0`.
- Open positions: `0`.
- No `order_send()` call was made.

Safe broker probe:

- `scripts\mt5_broker_probe.py --config-name mt5 --symbol EURUSD --time-in-force ioc --history-lookback-hours 8760 --include-raw-samples`
- EURUSD IOC `order_check` returned `accepted=true`, retcode `0`, and comment `Done`.
- EURUSD FOK `order_check` returned retcode `10030` and comment `Unsupported filling mode`.
- EURUSD symbol metadata reported `filling_mode=2`, minimum volume `0.01`, volume step `0.01`, and floating spread.
- Raw history remained empty even with a one-year lookback: `0` historical orders and `0` historical deals.
- `terminal_info().tradeapi_disabled` was `false`.
- `account_info().trade_allowed` and `account_info().trade_expert` were `true`.
- `terminal_info().trade_allowed` was `false`, so terminal-side trading/AutoTrading is still disabled for API order submission.
- `scripts\mt5_demo_order.py` was run with explicit operator confirmation and correctly returned `blocked_reason=terminal_trade_not_allowed`; `order_send_attempted=false`.
- No `order_send()` call was made.

## Follow-Up

- Treat MT5 filling mode as broker and symbol specific. The Windows notebook MetaQuotes demo accepted FOK for EURUSD, while the Parallels Dukascopy demo accepted IOC and rejected FOK.
- Use IOC for Dukascopy EURUSD unless symbol metadata changes.
- Use FOK for the previous Windows notebook MetaQuotes demo only if a fresh broker probe still accepts it.
- Keep MT5 order comments at `29` characters or shorter.
- Configure explicit terminal path and broker env vars only when moving beyond saved terminal-session smoke checks.
- Enable and document live confirmation only for an intentionally approved live-safe run.
- Validate non-empty history/deal normalization after a controlled demo fill or imported broker history is available.
- Keep `order_send()` disabled until the operator explicitly approves a demo-order test and a fresh terminal permission check shows terminal-side trading is enabled.
