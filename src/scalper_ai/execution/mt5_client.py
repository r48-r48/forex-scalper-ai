"""Real MetaTrader 5 terminal client backing the MT5 execution adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

from scalper_ai.domain import OrderSide, OrderType, TimeInForce
from scalper_ai.execution.models import ExecutionOrderStatus
from scalper_ai.execution.mt5_live import (
    Mt5ExecutionClientProtocol,
    Mt5OrderRequest,
    Mt5OrderState,
    Mt5PositionState,
)


@dataclass(frozen=True)
class Mt5TerminalClientConfig:
    """Terminal and account settings required by the real MT5 client."""

    terminal_path: Path | None = None
    login: int | None = None
    password: str | None = None
    server: str | None = None
    timeout_milliseconds: int = 10_000
    magic_number: int = 4_242_001
    deviation_points: int = 20
    history_lookback_hours: int = 24
    account_mode: str = "netting"
    order_comment_prefix: str = "scalper_ai"

    def __post_init__(self) -> None:
        if self.timeout_milliseconds <= 0:
            raise ValueError("timeout_milliseconds must be greater than zero.")
        if self.magic_number < 0:
            raise ValueError("magic_number must be non-negative.")
        if self.deviation_points < 0:
            raise ValueError("deviation_points must be non-negative.")
        if self.history_lookback_hours <= 0:
            raise ValueError("history_lookback_hours must be greater than zero.")
        if self.account_mode != "netting":
            raise ValueError("Mt5TerminalClient currently supports only account_mode='netting'.")
        if not self.order_comment_prefix.strip():
            raise ValueError("order_comment_prefix must be non-empty.")


@dataclass(frozen=True)
class Mt5AccountSnapshot:
    """Small read-only account summary used by smoke checks and operations."""

    login: int | None
    server: str | None
    balance: float | None
    equity: float | None
    leverage: int | None
    company: str | None
    currency: str | None


class MetaTrader5ModuleProtocol(Protocol):
    """Minimal dynamic surface expected from the MetaTrader5 Python package."""

    def initialize(self, **kwargs: Any) -> bool:
        """Initialize the terminal connection."""

    def shutdown(self) -> Any:
        """Shut down the terminal connection."""

    def last_error(self) -> Any:
        """Return the last terminal error payload."""

    def terminal_info(self) -> Any:
        """Return terminal metadata if connected."""

    def account_info(self) -> Any:
        """Return account metadata if connected."""

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        """Enable or disable one symbol in MarketWatch."""

    def symbol_info_tick(self, symbol: str) -> Any:
        """Return the latest top-of-book snapshot for one symbol."""

    def order_send(self, request: Mapping[str, Any]) -> Any:
        """Submit one trading request."""

    def orders_get(self, *args: Any, **kwargs: Any) -> Any:
        """Return current broker orders."""

    def positions_get(self, *args: Any, **kwargs: Any) -> Any:
        """Return current broker positions."""

    def history_orders_get(self, *args: Any, **kwargs: Any) -> Any:
        """Return historic broker orders."""

    def history_deals_get(self, *args: Any, **kwargs: Any) -> Any:
        """Return historic broker deals."""


DEFAULT_MT5_SEARCH_ROOTS: tuple[Path, ...] = (
    Path("/Applications"),
    Path("~/Applications"),
)

DEFAULT_MT5_APP_BUNDLE_NAMES: tuple[str, ...] = (
    "MetaTrader 5.app",
    "MetaTrader5.app",
)

DEFAULT_MT5_BUNDLE_EXECUTABLE_SUFFIXES: tuple[Path, ...] = (
    Path("Wrapper/MetaTrader5Terminal.app/MetaTrader5Terminal"),
    Path("Contents/MacOS/MetaTrader 5"),
    Path("Contents/MacOS/MetaTrader5"),
)

DEFAULT_MT5_EXECUTABLE_NAMES: tuple[str, ...] = (
    "MetaTrader5Terminal",
    "MetaTrader 5",
    "MetaTrader5",
    "terminal64.exe",
    "terminal.exe",
)


def load_metatrader5_module() -> MetaTrader5ModuleProtocol:
    """Import the MetaTrader5 package lazily with a clear operational error."""

    try:
        return importlib.import_module("MetaTrader5")
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on host installation
        raise RuntimeError(
            "MetaTrader5 package is not installed. Install it in the target live environment first."
        ) from exc


def is_metatrader5_package_available(
    *,
    module_loader: Any = None,
) -> bool:
    """Return whether the MetaTrader5 Python package can be imported in this environment."""

    loader = load_metatrader5_module if module_loader is None else module_loader
    try:
        loader()
    except Exception:
        return False
    return True


def discover_mt5_terminal_path(
    configured_path: Path | None = None,
    *,
    search_roots: Sequence[Path] | None = None,
) -> Path | None:
    """Resolve one usable MT5 terminal executable path from config or common install locations."""

    seen: set[Path] = set()
    candidates = (
        _expand_mt5_path_candidate(configured_path)
        if configured_path is not None
        else _iter_mt5_terminal_candidates(search_roots=DEFAULT_MT5_SEARCH_ROOTS if search_roots is None else search_roots)
    )
    for candidate in candidates:
        expanded = candidate.expanduser()
        if expanded in seen:
            continue
        seen.add(expanded)
        if expanded.is_file():
            return expanded.resolve()
    return None


def _iter_mt5_terminal_candidates(
    *,
    search_roots: Sequence[Path],
) -> tuple[Path, ...]:
    candidates: list[Path] = []
    for root in search_roots:
        expanded_root = Path(root).expanduser()
        candidates.extend(_expand_mt5_path_candidate(expanded_root))
        for bundle_name in DEFAULT_MT5_APP_BUNDLE_NAMES:
            candidates.extend(_expand_mt5_path_candidate(expanded_root / bundle_name))
    return tuple(candidates)


def _expand_mt5_path_candidate(path: Path) -> tuple[Path, ...]:
    expanded = path.expanduser()
    candidates: list[Path] = [expanded]
    is_bundle_like = expanded.suffix.lower() == ".app" or expanded.name in DEFAULT_MT5_APP_BUNDLE_NAMES
    if is_bundle_like or expanded.is_dir():
        candidates.extend(expanded / suffix for suffix in DEFAULT_MT5_BUNDLE_EXECUTABLE_SUFFIXES)
        candidates.extend(expanded / executable_name for executable_name in DEFAULT_MT5_EXECUTABLE_NAMES)
    return tuple(candidates)


class Mt5TerminalClient(Mt5ExecutionClientProtocol):
    """Real terminal-backed client that normalizes MT5 state for the execution adapter."""

    def __init__(
        self,
        *,
        config: Mt5TerminalClientConfig,
        module: MetaTrader5ModuleProtocol | None = None,
    ) -> None:
        self._config = config
        self._module = load_metatrader5_module() if module is None else module
        self._initialized = False
        self._last_ping_latency_ms: float | None = None
        self._ensure_initialized()

    def close(self) -> None:
        """Shut down the terminal session if it was opened by this client."""

        if self._initialized:
            self._module.shutdown()
            self._initialized = False

    def describe_account(self) -> Mt5AccountSnapshot:
        """Return one normalized read-only MT5 account summary."""

        self._ensure_initialized()
        account = self._coerce_mapping(self._module.account_info())
        return Mt5AccountSnapshot(
            login=self._coerce_int(account.get("login")),
            server=self._coerce_optional_str(account.get("server")),
            balance=self._coerce_float(account.get("balance")),
            equity=self._coerce_float(account.get("equity")),
            leverage=self._coerce_int(account.get("leverage")),
            company=self._coerce_optional_str(account.get("company")),
            currency=self._coerce_optional_str(account.get("currency")),
        )

    def submit_order(self, request: Mt5OrderRequest) -> Mt5OrderState:
        """Submit one MT5 order request and return the normalized broker order state."""

        self._ensure_initialized()
        self._ensure_symbol_selected(request.broker_symbol)
        sent_at = request.submitted_at
        result = self._module.order_send(self._build_order_payload(request))
        if result is None:
            return self._build_rejected_state(
                request,
                broker_order_id=f"mt5-rejected-{request.client_order_id}",
                updated_at=sent_at,
                reason=self._last_error_message(),
            )

        retcode = getattr(result, "retcode", None)
        broker_order_id = self._resolve_result_order_id(result, request)
        if not self._retcode_is_success(retcode):
            return self._build_rejected_state(
                request,
                broker_order_id=broker_order_id,
                updated_at=self._result_timestamp(result, fallback=sent_at),
                reason=self._result_comment(result) or self._last_error_message(),
            )

        live_state = self.get_order(broker_order_id)
        if live_state is not None:
            return live_state

        return self._build_success_fallback_state(
            request,
            broker_order_id=broker_order_id,
            result=result,
            updated_at=self._result_timestamp(result, fallback=sent_at),
        )

    def cancel_order(self, broker_order_id: str, *, timestamp: datetime) -> Mt5OrderState:
        """Cancel one open MT5 order and return the normalized broker order state."""

        self._ensure_initialized()
        current_state = self.get_order(broker_order_id)
        if current_state is None:
            raise KeyError(f"Unknown broker_order_id: {broker_order_id}")

        result = self._module.order_send(
            {
                "action": getattr(self._module, "TRADE_ACTION_REMOVE", 8),
                "order": int(broker_order_id),
                "symbol": current_state.broker_symbol,
                "magic": self._config.magic_number,
                "comment": self._comment_for("cancel"),
            }
        )
        if result is None or not self._retcode_is_success(getattr(result, "retcode", None)):
            raise RuntimeError(self._result_comment(result) or self._last_error_message())

        refreshed_state = self.get_order(broker_order_id)
        if refreshed_state is not None:
            return refreshed_state

        return Mt5OrderState(
            broker_order_id=broker_order_id,
            broker_symbol=current_state.broker_symbol,
            status=ExecutionOrderStatus.CANCELED,
            submitted_at=current_state.submitted_at,
            updated_at=timestamp,
            requested_volume_lots=current_state.requested_volume_lots,
            filled_volume_lots=current_state.filled_volume_lots,
            remaining_volume_lots=current_state.remaining_volume_lots,
            average_fill_price=current_state.average_fill_price,
            cancel_reason="user_requested",
        )

    def get_order(self, broker_order_id: str) -> Mt5OrderState | None:
        """Return one current or recent MT5 order state if available."""

        self._ensure_initialized()
        ticket = int(broker_order_id)

        open_order = self._first_or_none(self._orders_get(ticket=ticket))
        if open_order is not None:
            return self._normalize_order_record(open_order, historical=False)

        historic_order = self._first_or_none(self._history_orders_get(ticket=ticket))
        if historic_order is not None:
            return self._normalize_order_record(historic_order, historical=True)
        return None

    def list_orders(self) -> tuple[Mt5OrderState, ...]:
        """Return current open orders plus recent historic orders for reconciliation."""

        self._ensure_initialized()
        states: dict[str, Mt5OrderState] = {}
        for raw_order in self._history_orders_get():
            state = self._normalize_order_record(raw_order, historical=True)
            states[state.broker_order_id] = state
        for raw_order in self._orders_get():
            state = self._normalize_order_record(raw_order, historical=False)
            states[state.broker_order_id] = state
        return tuple(sorted(states.values(), key=lambda state: state.broker_order_id))

    def get_position(self, broker_symbol: str) -> Mt5PositionState | None:
        """Return one normalized broker position if present."""

        self._ensure_initialized()
        positions = self._positions_get(symbol=broker_symbol)
        if not positions:
            return None
        if len(positions) > 1:
            raise RuntimeError("MT5 client expects one netting position per symbol, but multiple positions were found.")
        return self._normalize_position_record(positions[0])

    def list_positions(self) -> tuple[Mt5PositionState, ...]:
        """Return normalized broker positions for reconciliation."""

        self._ensure_initialized()
        positions = [self._normalize_position_record(raw_position) for raw_position in self._positions_get()]
        return tuple(sorted(positions, key=lambda position: position.broker_symbol))

    def is_connected(self) -> bool:
        """Return whether the terminal and account dependencies are reachable."""

        try:
            self._ensure_initialized()
        except Exception:
            return False
        return self._module.terminal_info() is not None and self._module.account_info() is not None

    def ping_latency_ms(self) -> float | None:
        """Return one lightweight terminal round-trip latency estimate."""

        try:
            self._ensure_initialized()
        except Exception:
            return None

        started = perf_counter()
        terminal_info = self._module.terminal_info()
        if terminal_info is None:
            return None
        self._last_ping_latency_ms = (perf_counter() - started) * 1000.0
        return self._last_ping_latency_ms

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return

        initialize_kwargs: dict[str, Any] = {"timeout": self._config.timeout_milliseconds}
        if self._config.terminal_path is not None:
            initialize_kwargs["path"] = str(self._config.terminal_path)
        if self._config.login is not None:
            initialize_kwargs["login"] = self._config.login
        if self._config.password is not None:
            initialize_kwargs["password"] = self._config.password
        if self._config.server is not None:
            initialize_kwargs["server"] = self._config.server

        initialized = self._module.initialize(**initialize_kwargs)
        if not initialized:
            raise RuntimeError(f"MT5 initialize failed: {self._last_error_message()}")
        if self._module.account_info() is None:
            raise RuntimeError("MT5 initialize succeeded but account_info() returned no active account.")
        self._initialized = True

    def _build_order_payload(self, request: Mt5OrderRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "symbol": request.broker_symbol,
            "volume": request.volume_lots,
            "magic": self._config.magic_number,
            "deviation": self._config.deviation_points,
            "comment": self._comment_for(request.client_order_id),
            "type": self._order_type_code(request.side, request.order_type),
            "type_time": self._time_policy_code(request.time_in_force),
            "type_filling": self._filling_policy_code(request.time_in_force),
        }

        if request.order_type is OrderType.MARKET:
            payload["action"] = getattr(self._module, "TRADE_ACTION_DEAL", 1)
            payload["price"] = self._market_price(request.broker_symbol, side=request.side)
        else:
            payload["action"] = getattr(self._module, "TRADE_ACTION_PENDING", 5)
            payload["price"] = self._pending_order_price(request)
            if request.order_type is OrderType.STOP_LIMIT:
                payload["stoplimit"] = request.limit_price
        return payload

    def _market_price(self, broker_symbol: str, *, side: OrderSide) -> float:
        tick = self._coerce_mapping(self._module.symbol_info_tick(broker_symbol))
        ask = self._coerce_float(tick.get("ask"))
        bid = self._coerce_float(tick.get("bid"))
        if ask is None or bid is None:
            raise RuntimeError(f"MT5 symbol_info_tick returned no usable bid/ask for {broker_symbol}.")
        return ask if side is OrderSide.BUY else bid

    @staticmethod
    def _pending_order_price(request: Mt5OrderRequest) -> float:
        if request.order_type is OrderType.LIMIT:
            if request.limit_price is None:
                raise ValueError("Limit orders require limit_price.")
            return request.limit_price
        if request.order_type is OrderType.STOP:
            if request.stop_price is None:
                raise ValueError("Stop orders require stop_price.")
            return request.stop_price
        if request.order_type is OrderType.STOP_LIMIT:
            if request.stop_price is None or request.limit_price is None:
                raise ValueError("Stop-limit orders require stop_price and limit_price.")
            return request.stop_price
        raise ValueError(f"Unsupported pending order type: {request.order_type}")

    def _normalize_order_record(self, raw_order: Any, *, historical: bool) -> Mt5OrderState:
        payload = self._coerce_mapping(raw_order)
        broker_order_id = str(payload["ticket"])
        broker_symbol = str(payload["symbol"])
        requested_volume_lots = self._coerce_float(
            payload.get("volume_initial", payload.get("volume", payload.get("volume_current")))
        )
        if requested_volume_lots is None:
            raise ValueError("MT5 order payload is missing volume information.")

        remaining_volume_lots = self._coerce_float(payload.get("volume_current"))
        fill_quantity_lots, average_fill_price = self._deal_fill_summary(int(broker_order_id))

        if remaining_volume_lots is None:
            remaining_volume_lots = max(0.0, requested_volume_lots - fill_quantity_lots)

        if historical and fill_quantity_lots <= 0 and self._historical_order_is_filled(payload):
            fill_quantity_lots = requested_volume_lots
            remaining_volume_lots = 0.0

        status = self._map_order_status(
            state_code=payload.get("state"),
            filled_volume_lots=fill_quantity_lots,
            remaining_volume_lots=remaining_volume_lots,
            historical=historical,
        )
        submitted_at = self._payload_timestamp(payload, primary="time_setup_msc", fallback="time_setup")
        updated_at = self._payload_timestamp(
            payload,
            primary="time_done_msc",
            fallback="time_done",
            default=submitted_at,
        )
        rejection_reason = self._coerce_optional_str(payload.get("comment")) if status is ExecutionOrderStatus.REJECTED else None
        cancel_reason = self._coerce_optional_str(payload.get("comment")) if status is ExecutionOrderStatus.CANCELED else None
        return Mt5OrderState(
            broker_order_id=broker_order_id,
            broker_symbol=broker_symbol,
            status=status,
            submitted_at=submitted_at,
            updated_at=updated_at,
            requested_volume_lots=requested_volume_lots,
            filled_volume_lots=fill_quantity_lots,
            remaining_volume_lots=remaining_volume_lots,
            average_fill_price=average_fill_price,
            rejection_reason=rejection_reason,
            cancel_reason=cancel_reason,
        )

    def _normalize_position_record(self, raw_position: Any) -> Mt5PositionState:
        payload = self._coerce_mapping(raw_position)
        raw_type = payload.get("type")
        direction = 1.0 if raw_type == getattr(self._module, "POSITION_TYPE_BUY", 0) else -1.0
        volume_lots = self._coerce_float(payload.get("volume"))
        if volume_lots is None:
            raise ValueError("MT5 position payload is missing volume.")
        return Mt5PositionState(
            broker_symbol=str(payload["symbol"]),
            timestamp=self._payload_timestamp(payload, primary="time_msc", fallback="time"),
            net_volume_lots=direction * volume_lots,
            average_entry_price=float(payload.get("price_open", 0.0)),
        )

    def _deal_fill_summary(self, ticket: int) -> tuple[float, float | None]:
        deals = self._history_deals_get(ticket=ticket)
        if not deals:
            return 0.0, None
        total_volume = 0.0
        weighted_notional = 0.0
        for deal in deals:
            payload = self._coerce_mapping(deal)
            volume = self._coerce_float(payload.get("volume")) or 0.0
            price = self._coerce_float(payload.get("price")) or 0.0
            total_volume += abs(volume)
            weighted_notional += abs(volume) * price
        if total_volume <= 0:
            return 0.0, None
        return total_volume, weighted_notional / total_volume

    def _orders_get(self, **kwargs: Any) -> tuple[Any, ...]:
        try:
            return self._safe_sequence(self._module.orders_get(**kwargs))
        except TypeError:
            orders = self._safe_sequence(self._module.orders_get())
            ticket = kwargs.get("ticket")
            if ticket is None:
                return orders
            return tuple(order for order in orders if self._coerce_mapping(order).get("ticket") == ticket)

    def _positions_get(self, **kwargs: Any) -> tuple[Any, ...]:
        try:
            return self._safe_sequence(self._module.positions_get(**kwargs))
        except TypeError:
            positions = self._safe_sequence(self._module.positions_get())
            symbol = kwargs.get("symbol")
            if symbol is None:
                return positions
            return tuple(position for position in positions if self._coerce_mapping(position).get("symbol") == symbol)

    def _history_orders_get(self, **kwargs: Any) -> tuple[Any, ...]:
        start_time, end_time = self._history_window()
        try:
            return self._safe_sequence(self._module.history_orders_get(start_time, end_time, **kwargs))
        except TypeError:
            orders = self._safe_sequence(self._module.history_orders_get(start_time, end_time))
            ticket = kwargs.get("ticket")
            if ticket is None:
                return orders
            return tuple(order for order in orders if self._coerce_mapping(order).get("ticket") == ticket)

    def _history_deals_get(self, **kwargs: Any) -> tuple[Any, ...]:
        start_time, end_time = self._history_window()
        try:
            return self._safe_sequence(self._module.history_deals_get(start_time, end_time, **kwargs))
        except TypeError:
            deals = self._safe_sequence(self._module.history_deals_get(start_time, end_time))
            ticket = kwargs.get("ticket")
            if ticket is None:
                return deals
            return tuple(
                deal
                for deal in deals
                if self._coerce_mapping(deal).get("order") == ticket or self._coerce_mapping(deal).get("ticket") == ticket
            )

    def _history_window(self) -> tuple[datetime, datetime]:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=self._config.history_lookback_hours)
        return start_time, end_time

    def _ensure_symbol_selected(self, symbol: str) -> None:
        selected = self._module.symbol_select(symbol, True)
        if not selected:
            raise RuntimeError(f"MT5 symbol_select failed for {symbol}: {self._last_error_message()}")

    def _build_rejected_state(
        self,
        request: Mt5OrderRequest,
        *,
        broker_order_id: str,
        updated_at: datetime,
        reason: str,
    ) -> Mt5OrderState:
        return Mt5OrderState(
            broker_order_id=broker_order_id,
            broker_symbol=request.broker_symbol,
            status=ExecutionOrderStatus.REJECTED,
            submitted_at=request.submitted_at,
            updated_at=updated_at,
            requested_volume_lots=request.volume_lots,
            filled_volume_lots=0.0,
            remaining_volume_lots=request.volume_lots,
            rejection_reason=reason,
        )

    def _build_success_fallback_state(
        self,
        request: Mt5OrderRequest,
        *,
        broker_order_id: str,
        result: Any,
        updated_at: datetime,
    ) -> Mt5OrderState:
        status = self._retcode_to_status(getattr(result, "retcode", None))
        filled_volume_lots = request.volume_lots if status is ExecutionOrderStatus.FILLED else 0.0
        remaining_volume_lots = max(0.0, request.volume_lots - filled_volume_lots)
        average_fill_price = self._coerce_float(getattr(result, "price", None))
        return Mt5OrderState(
            broker_order_id=broker_order_id,
            broker_symbol=request.broker_symbol,
            status=status,
            submitted_at=request.submitted_at,
            updated_at=updated_at,
            requested_volume_lots=request.volume_lots,
            filled_volume_lots=filled_volume_lots,
            remaining_volume_lots=remaining_volume_lots,
            average_fill_price=average_fill_price,
        )

    def _resolve_result_order_id(self, result: Any, request: Mt5OrderRequest) -> str:
        order_id = self._coerce_int(getattr(result, "order", None))
        if order_id is not None and order_id > 0:
            return str(order_id)
        request_id = self._coerce_int(getattr(result, "request_id", None))
        if request_id is not None and request_id > 0:
            return str(request_id)
        return f"mt5-{request.client_order_id}"

    def _result_timestamp(self, result: Any, *, fallback: datetime) -> datetime:
        payload = self._coerce_mapping(result)
        return self._payload_timestamp(payload, primary="time_msc", fallback="time", default=fallback)

    @staticmethod
    def _result_comment(result: Any) -> str | None:
        if result is None:
            return None
        comment = getattr(result, "comment", None)
        if comment is None:
            return None
        normalized = str(comment).strip()
        return normalized or None

    def _retcode_is_success(self, retcode: Any) -> bool:
        success_codes = {
            getattr(self._module, "TRADE_RETCODE_DONE", 10009),
            getattr(self._module, "TRADE_RETCODE_DONE_PARTIAL", 10010),
            getattr(self._module, "TRADE_RETCODE_PLACED", 10008),
        }
        return retcode in success_codes

    def _retcode_to_status(self, retcode: Any) -> ExecutionOrderStatus:
        if retcode == getattr(self._module, "TRADE_RETCODE_DONE", 10009):
            return ExecutionOrderStatus.FILLED
        if retcode == getattr(self._module, "TRADE_RETCODE_DONE_PARTIAL", 10010):
            return ExecutionOrderStatus.PARTIALLY_FILLED
        if retcode == getattr(self._module, "TRADE_RETCODE_PLACED", 10008):
            return ExecutionOrderStatus.ACCEPTED
        return ExecutionOrderStatus.REJECTED

    def _map_order_status(
        self,
        *,
        state_code: Any,
        filled_volume_lots: float,
        remaining_volume_lots: float,
        historical: bool,
    ) -> ExecutionOrderStatus:
        if state_code == getattr(self._module, "ORDER_STATE_PARTIAL", 2):
            return ExecutionOrderStatus.PARTIALLY_FILLED
        if state_code == getattr(self._module, "ORDER_STATE_FILLED", 4):
            return ExecutionOrderStatus.FILLED
        if state_code in {
            getattr(self._module, "ORDER_STATE_CANCELED", 5),
            getattr(self._module, "ORDER_STATE_EXPIRED", 6),
            getattr(self._module, "ORDER_STATE_REQUEST_CANCEL", 8),
        }:
            return ExecutionOrderStatus.CANCELED
        if state_code == getattr(self._module, "ORDER_STATE_REJECTED", 7):
            return ExecutionOrderStatus.REJECTED
        if filled_volume_lots > 0 and remaining_volume_lots > 0:
            return ExecutionOrderStatus.PARTIALLY_FILLED
        if historical and remaining_volume_lots <= 0 and filled_volume_lots > 0:
            return ExecutionOrderStatus.FILLED
        return ExecutionOrderStatus.ACCEPTED

    def _historical_order_is_filled(self, payload: Mapping[str, Any]) -> bool:
        return payload.get("state") == getattr(self._module, "ORDER_STATE_FILLED", 4)

    def _order_type_code(self, side: OrderSide, order_type: OrderType) -> int:
        side_prefix = "BUY" if side is OrderSide.BUY else "SELL"
        if order_type is OrderType.MARKET:
            return getattr(self._module, f"ORDER_TYPE_{side_prefix}", 0 if side is OrderSide.BUY else 1)
        if order_type is OrderType.LIMIT:
            return getattr(self._module, f"ORDER_TYPE_{side_prefix}_LIMIT", 2 if side is OrderSide.BUY else 3)
        if order_type is OrderType.STOP:
            return getattr(self._module, f"ORDER_TYPE_{side_prefix}_STOP", 4 if side is OrderSide.BUY else 5)
        if order_type is OrderType.STOP_LIMIT:
            return getattr(
                self._module,
                f"ORDER_TYPE_{side_prefix}_STOP_LIMIT",
                6 if side is OrderSide.BUY else 7,
            )
        raise ValueError(f"Unsupported MT5 order type: {order_type}")

    def _time_policy_code(self, time_in_force: TimeInForce | None) -> int:
        if time_in_force is TimeInForce.DAY:
            return getattr(self._module, "ORDER_TIME_DAY", 1)
        return getattr(self._module, "ORDER_TIME_GTC", 0)

    def _filling_policy_code(self, time_in_force: TimeInForce | None) -> int:
        if time_in_force is TimeInForce.IOC:
            return getattr(self._module, "ORDER_FILLING_IOC", 1)
        if time_in_force is TimeInForce.FOK:
            return getattr(self._module, "ORDER_FILLING_FOK", 0)
        return getattr(self._module, "ORDER_FILLING_RETURN", 2)

    def _comment_for(self, suffix: str) -> str:
        return f"{self._config.order_comment_prefix}:{suffix}"[:31]

    def _last_error_message(self) -> str:
        error = self._module.last_error()
        if isinstance(error, tuple) and len(error) >= 2:
            return f"{error[0]}:{error[1]}"
        return str(error)

    @staticmethod
    def _payload_timestamp(
        payload: Mapping[str, Any],
        *,
        primary: str,
        fallback: str,
        default: datetime | None = None,
    ) -> datetime:
        primary_value = payload.get(primary)
        if primary_value is not None:
            return datetime.fromtimestamp(float(primary_value) / 1000.0, tz=timezone.utc)
        fallback_value = payload.get(fallback)
        if fallback_value is not None:
            return datetime.fromtimestamp(float(fallback_value), tz=timezone.utc)
        if default is not None:
            return default
        raise ValueError("MT5 payload did not contain a usable timestamp.")

    @staticmethod
    def _coerce_mapping(raw_payload: Any) -> Mapping[str, Any]:
        if raw_payload is None:
            return {}
        if isinstance(raw_payload, Mapping):
            return raw_payload
        if hasattr(raw_payload, "_asdict"):
            payload = raw_payload._asdict()
            if isinstance(payload, Mapping):
                return payload
        return {
            key: getattr(raw_payload, key)
            for key in dir(raw_payload)
            if not key.startswith("_") and not callable(getattr(raw_payload, key))
        }

    @staticmethod
    def _safe_sequence(raw_payload: Any) -> tuple[Any, ...]:
        if raw_payload is None:
            return ()
        if isinstance(raw_payload, tuple):
            return raw_payload
        if isinstance(raw_payload, Sequence):
            return tuple(raw_payload)
        return tuple(raw_payload)

    @staticmethod
    def _first_or_none(items: Sequence[Any]) -> Any | None:
        if not items:
            return None
        return items[0]

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        if value is None:
            return None
        return float(value)

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if value is None:
            return None
        return int(value)

    @staticmethod
    def _coerce_optional_str(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None
