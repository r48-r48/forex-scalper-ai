"""Durable execution runtime state storage."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, StrEnum
from pathlib import Path
from typing import Protocol

import orjson

from scalper_ai.domain import DomainModel, FillEvent, OrderIntent, PositionState
from scalper_ai.domain.validators import ensure_utc_datetime, serialize_utc_datetime
from scalper_ai.execution.models import (
    ExecutionOrder,
    ExecutionOrderStatus,
    ExecutionQuote,
    ExecutionUpdate,
)
from scalper_ai.risk import RiskDecision, RiskDecisionStatus, RiskRejectCode
from scalper_ai.services import OmsOrderRecord, OmsOrderStatus

SCHEMA_VERSION = "1"
SESSION_KILL_SWITCH_SYMBOL = "__session__"


class KillSwitchScope(StrEnum):
    """Durable kill-switch scope."""

    SESSION = "session"
    SYMBOL = "symbol"


@dataclass(frozen=True)
class KillSwitchState:
    """Persisted kill-switch state for startup recovery."""

    scope: KillSwitchScope
    enabled: bool
    updated_at: datetime
    symbol: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        ensure_utc_datetime(self.updated_at)
        if self.scope is KillSwitchScope.SYMBOL and not _has_text(self.symbol):
            raise ValueError("symbol is required for symbol kill switches.")
        if self.scope is KillSwitchScope.SESSION and self.symbol is not None:
            raise ValueError("session kill switches must not include symbol.")


class ExecutionStateStore(Protocol):
    """Durable runtime state store used by deployment startup recovery."""

    def save_order_intent(self, intent: OrderIntent) -> None:
        """Persist or update one order intent."""

    def save_risk_decision(self, decision: RiskDecision) -> None:
        """Persist one pre-trade risk decision."""

    def save_oms_record(self, record: OmsOrderRecord) -> None:
        """Persist one OMS transition record."""

    def save_execution_update(self, update: ExecutionUpdate) -> None:
        """Persist one execution update plus its fills and latest position."""

    def save_kill_switch_state(self, state: KillSwitchState) -> None:
        """Persist one kill-switch state."""

    def list_order_intents(self) -> tuple[OrderIntent, ...]:
        """Return persisted order intents."""

    def list_risk_decisions(self) -> tuple[RiskDecision, ...]:
        """Return persisted risk decisions in insertion order."""

    def list_oms_records(self) -> tuple[OmsOrderRecord, ...]:
        """Return the latest persisted OMS record per intent id."""

    def list_execution_updates(self) -> tuple[ExecutionUpdate, ...]:
        """Return persisted execution updates in insertion order."""

    def list_fill_events(self) -> tuple[FillEvent, ...]:
        """Return persisted fills in fill-id order."""

    def list_position_states(self) -> tuple[PositionState, ...]:
        """Return the latest persisted position state per route and symbol."""

    def list_kill_switch_states(self) -> tuple[KillSwitchState, ...]:
        """Return persisted kill-switch states."""


class SqliteExecutionStateStore:
    """SQLite-backed durable state store for runtime recovery."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        """Create the runtime state schema if it does not already exist."""

        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS order_intents (
                    intent_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    paper INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS risk_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    intent_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    reject_code TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oms_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    intent_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    broker_order_id TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_updates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    broker_order_id TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    order_status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fill_events (
                    fill_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL,
                    broker_order_id TEXT,
                    symbol TEXT NOT NULL,
                    event_timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS position_states (
                    route_paper INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (route_paper, symbol)
                );
                CREATE TABLE IF NOT EXISTS kill_switch_states (
                    scope TEXT NOT NULL,
                    symbol_key TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    reason TEXT,
                    PRIMARY KEY (scope, symbol_key)
                );
                """
            )
            connection.execute(
                """
                INSERT INTO schema_metadata (key, value)
                VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (SCHEMA_VERSION,),
            )

    def save_order_intent(self, intent: OrderIntent) -> None:
        """Persist or update one order intent."""

        payload = intent.to_record(exclude_none=False)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO order_intents (
                    intent_id,
                    created_at,
                    symbol,
                    paper,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(intent_id) DO UPDATE SET
                    created_at=excluded.created_at,
                    symbol=excluded.symbol,
                    paper=excluded.paper,
                    payload_json=excluded.payload_json
                """,
                (
                    intent.intent_id,
                    serialize_utc_datetime(intent.created_at),
                    intent.symbol,
                    int(intent.paper),
                    _json_dumps(payload),
                ),
            )

    def save_risk_decision(self, decision: RiskDecision) -> None:
        """Persist one pre-trade risk decision."""

        payload = _risk_decision_to_payload(decision)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO risk_decisions (
                    intent_id,
                    symbol,
                    status,
                    checked_at,
                    reject_code,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.intent_id,
                    decision.symbol,
                    decision.status.value,
                    serialize_utc_datetime(decision.checked_at),
                    None if decision.code is None else decision.code.value,
                    _json_dumps(payload),
                ),
            )

    def save_oms_record(self, record: OmsOrderRecord) -> None:
        """Persist one OMS transition record."""

        self.save_order_intent(record.intent)
        payload = _oms_record_to_payload(record)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO oms_transitions (
                    intent_id,
                    status,
                    updated_at,
                    broker_order_id,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.intent.intent_id,
                    record.status.value,
                    serialize_utc_datetime(record.updated_at),
                    record.broker_order_id,
                    _json_dumps(payload),
                ),
            )

    def save_execution_update(self, update: ExecutionUpdate) -> None:
        """Persist one execution update plus its fills and latest position."""

        self.save_order_intent(update.order.intent)
        payload = _execution_update_to_payload(update)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO execution_updates (
                    broker_order_id,
                    intent_id,
                    order_status,
                    updated_at,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    update.order.broker_order_id,
                    update.order.intent.intent_id,
                    update.order.status.value,
                    serialize_utc_datetime(update.order.updated_at),
                    _json_dumps(payload),
                ),
            )
            for fill in update.fills:
                connection.execute(
                    """
                    INSERT INTO fill_events (
                        fill_id,
                        intent_id,
                        broker_order_id,
                        symbol,
                        event_timestamp,
                        payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(fill_id) DO UPDATE SET
                        intent_id=excluded.intent_id,
                        broker_order_id=excluded.broker_order_id,
                        symbol=excluded.symbol,
                        event_timestamp=excluded.event_timestamp,
                        payload_json=excluded.payload_json
                    """,
                    (
                        fill.fill_id,
                        fill.intent_id,
                        fill.broker_order_id,
                        fill.symbol,
                        serialize_utc_datetime(fill.event_timestamp),
                        _json_dumps(fill.to_record(exclude_none=False)),
                    ),
                )
            connection.execute(
                """
                INSERT INTO position_states (
                    route_paper,
                    symbol,
                    updated_at,
                    payload_json
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(route_paper, symbol) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    int(update.order.intent.paper),
                    update.position.symbol,
                    serialize_utc_datetime(update.position.timestamp),
                    _json_dumps(update.position.to_record(exclude_none=False)),
                ),
            )

    def save_kill_switch_state(self, state: KillSwitchState) -> None:
        """Persist one kill-switch state."""

        symbol_key = _kill_switch_symbol_key(state)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO kill_switch_states (
                    scope,
                    symbol_key,
                    enabled,
                    updated_at,
                    reason
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scope, symbol_key) DO UPDATE SET
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at,
                    reason=excluded.reason
                """,
                (
                    state.scope.value,
                    symbol_key,
                    int(state.enabled),
                    serialize_utc_datetime(state.updated_at),
                    state.reason,
                ),
            )

    def list_order_intents(self) -> tuple[OrderIntent, ...]:
        """Return persisted order intents."""

        rows = self._fetch_payloads(
            "SELECT payload_json FROM order_intents ORDER BY created_at, intent_id"
        )
        return tuple(OrderIntent.from_record(_json_loads(row)) for row in rows)

    def list_risk_decisions(self) -> tuple[RiskDecision, ...]:
        """Return persisted risk decisions in insertion order."""

        rows = self._fetch_payloads("SELECT payload_json FROM risk_decisions ORDER BY id")
        return tuple(_risk_decision_from_payload(_json_loads(row)) for row in rows)

    def list_oms_records(self) -> tuple[OmsOrderRecord, ...]:
        """Return the latest persisted OMS record per intent id."""

        rows = self._fetch_payloads("SELECT payload_json FROM oms_transitions ORDER BY id")
        latest_by_intent_id: dict[str, OmsOrderRecord] = {}
        for row in rows:
            record = _oms_record_from_payload(_json_loads(row))
            latest_by_intent_id[record.intent.intent_id] = record
        return tuple(
            sorted(latest_by_intent_id.values(), key=lambda record: record.intent.intent_id)
        )

    def list_execution_updates(self) -> tuple[ExecutionUpdate, ...]:
        """Return persisted execution updates in insertion order."""

        rows = self._fetch_payloads("SELECT payload_json FROM execution_updates ORDER BY id")
        return tuple(_execution_update_from_payload(_json_loads(row)) for row in rows)

    def list_fill_events(self) -> tuple[FillEvent, ...]:
        """Return persisted fills in fill-id order."""

        rows = self._fetch_payloads("SELECT payload_json FROM fill_events ORDER BY fill_id")
        return tuple(FillEvent.from_record(_json_loads(row)) for row in rows)

    def list_position_states(self) -> tuple[PositionState, ...]:
        """Return the latest persisted position state per route and symbol."""

        rows = self._fetch_payloads(
            """
            SELECT payload_json
            FROM position_states
            ORDER BY route_paper, symbol
            """
        )
        return tuple(PositionState.from_record(_json_loads(row)) for row in rows)

    def list_kill_switch_states(self) -> tuple[KillSwitchState, ...]:
        """Return persisted kill-switch states."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT scope, symbol_key, enabled, updated_at, reason
                FROM kill_switch_states
                ORDER BY scope, symbol_key
                """
            ).fetchall()
        states: list[KillSwitchState] = []
        for row in rows:
            scope = KillSwitchScope(str(row["scope"]))
            symbol_key = str(row["symbol_key"])
            symbol = None if symbol_key == SESSION_KILL_SWITCH_SYMBOL else symbol_key
            states.append(
                KillSwitchState(
                    scope=scope,
                    enabled=bool(row["enabled"]),
                    updated_at=_parse_datetime(str(row["updated_at"])),
                    symbol=symbol,
                    reason=row["reason"],
                )
            )
        return tuple(states)

    def count_rows(self, table_name: str) -> int:
        """Return row count for a known state table."""

        if table_name not in _COUNTABLE_TABLES:
            raise ValueError(f"Unsupported state table: {table_name}")
        with self._connect() as connection:
            row = connection.execute(f"SELECT COUNT(*) AS row_count FROM {table_name}").fetchone()
        return int(row["row_count"])

    def _fetch_payloads(self, query: str) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        return tuple(str(row["payload_json"]) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


def _risk_decision_to_payload(decision: RiskDecision) -> dict[str, object]:
    return {
        "status": decision.status.value,
        "checked_at": decision.checked_at,
        "intent_id": decision.intent_id,
        "symbol": decision.symbol,
        "code": None if decision.code is None else decision.code.value,
        "reason": decision.reason,
        "projected_position": decision.projected_position,
    }


def _risk_decision_from_payload(payload: Mapping[str, object]) -> RiskDecision:
    raw_code = payload.get("code")
    code = None if raw_code is None else RiskRejectCode(str(raw_code))
    return RiskDecision(
        status=RiskDecisionStatus(str(payload["status"])),
        checked_at=_parse_datetime(str(payload["checked_at"])),
        intent_id=str(payload["intent_id"]),
        symbol=str(payload["symbol"]),
        code=code,
        reason=None if payload.get("reason") is None else str(payload["reason"]),
        projected_position=_optional_float(payload, "projected_position"),
    )


def _oms_record_to_payload(record: OmsOrderRecord) -> dict[str, object]:
    return {
        "intent": record.intent,
        "status": record.status.value,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "broker_order_id": record.broker_order_id,
        "filled_quantity": record.filled_quantity,
        "rejection_reason": record.rejection_reason,
        "cancel_reason": record.cancel_reason,
    }


def _oms_record_from_payload(payload: Mapping[str, object]) -> OmsOrderRecord:
    return OmsOrderRecord(
        intent=OrderIntent.from_record(_mapping(payload["intent"])),
        status=OmsOrderStatus(str(payload["status"])),
        created_at=_parse_datetime(str(payload["created_at"])),
        updated_at=_parse_datetime(str(payload["updated_at"])),
        broker_order_id=(
            None if payload.get("broker_order_id") is None else str(payload["broker_order_id"])
        ),
        filled_quantity=float(payload.get("filled_quantity") or 0.0),
        rejection_reason=(
            None if payload.get("rejection_reason") is None else str(payload["rejection_reason"])
        ),
        cancel_reason=_optional_str(payload, "cancel_reason"),
    )


def _execution_update_to_payload(update: ExecutionUpdate) -> dict[str, object]:
    return {
        "order": _execution_order_to_payload(update.order),
        "fills": list(update.fills),
        "position": update.position,
        "cash_balance": update.cash_balance,
        "equity": update.equity,
        "quote": _quote_to_payload(update.quote),
    }


def _execution_update_from_payload(payload: Mapping[str, object]) -> ExecutionUpdate:
    return ExecutionUpdate(
        order=_execution_order_from_payload(_mapping(payload["order"])),
        fills=tuple(FillEvent.from_record(_mapping(fill)) for fill in _sequence(payload["fills"])),
        position=PositionState.from_record(_mapping(payload["position"])),
        cash_balance=float(payload["cash_balance"]),
        equity=float(payload["equity"]),
        quote=_quote_from_payload(_mapping(payload["quote"])),
    )


def _execution_order_to_payload(order: ExecutionOrder) -> dict[str, object]:
    return {
        "intent": order.intent,
        "broker_order_id": order.broker_order_id,
        "status": order.status.value,
        "submitted_at": order.submitted_at,
        "updated_at": order.updated_at,
        "requested_quantity": order.requested_quantity,
        "filled_quantity": order.filled_quantity,
        "remaining_quantity": order.remaining_quantity,
        "fills": list(order.fills),
        "triggered_at": order.triggered_at,
        "rejection_reason": order.rejection_reason,
        "cancel_reason": order.cancel_reason,
    }


def _execution_order_from_payload(payload: Mapping[str, object]) -> ExecutionOrder:
    triggered_at = payload.get("triggered_at")
    return ExecutionOrder(
        intent=OrderIntent.from_record(_mapping(payload["intent"])),
        broker_order_id=str(payload["broker_order_id"]),
        status=ExecutionOrderStatus(str(payload["status"])),
        submitted_at=_parse_datetime(str(payload["submitted_at"])),
        updated_at=_parse_datetime(str(payload["updated_at"])),
        requested_quantity=float(payload["requested_quantity"]),
        filled_quantity=float(payload.get("filled_quantity") or 0.0),
        remaining_quantity=float(payload.get("remaining_quantity") or 0.0),
        fills=tuple(FillEvent.from_record(_mapping(fill)) for fill in _sequence(payload["fills"])),
        triggered_at=None if triggered_at is None else _parse_datetime(str(triggered_at)),
        rejection_reason=(
            None if payload.get("rejection_reason") is None else str(payload["rejection_reason"])
        ),
        cancel_reason=_optional_str(payload, "cancel_reason"),
    )


def _quote_to_payload(quote: ExecutionQuote) -> dict[str, object]:
    return {
        "symbol": quote.symbol,
        "event_timestamp": quote.event_timestamp,
        "received_timestamp": quote.received_timestamp,
        "bid": quote.bid,
        "ask": quote.ask,
        "venue": quote.venue,
    }


def _quote_from_payload(payload: Mapping[str, object]) -> ExecutionQuote:
    return ExecutionQuote(
        symbol=str(payload["symbol"]),
        event_timestamp=_parse_datetime(str(payload["event_timestamp"])),
        received_timestamp=_parse_datetime(str(payload["received_timestamp"])),
        bid=float(payload["bid"]),
        ask=float(payload["ask"]),
        venue=str(payload["venue"]),
    )


def _json_dumps(payload: Mapping[str, object]) -> str:
    return orjson.dumps(_json_ready(payload), option=orjson.OPT_SORT_KEYS).decode("utf-8")


def _json_loads(payload: str) -> dict[str, object]:
    return dict(orjson.loads(payload))


def _json_ready(value: object) -> object:
    if isinstance(value, DomainModel):
        return value.to_record(exclude_none=False)
    if isinstance(value, datetime):
        return serialize_utc_datetime(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_ready(item) for item in value]
    return value


def _parse_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return ensure_utc_datetime(datetime.fromisoformat(normalized))


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("expected mapping payload")
    return value


def _sequence(value: object) -> tuple[object, ...]:
    if not isinstance(value, list | tuple):
        raise TypeError("expected sequence payload")
    return tuple(value)


def _optional_float(payload: Mapping[str, object], key: str) -> float | None:
    value = payload.get(key)
    return None if value is None else float(value)


def _optional_str(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return None if value is None else str(value)


def _kill_switch_symbol_key(state: KillSwitchState) -> str:
    if state.scope is KillSwitchScope.SESSION:
        return SESSION_KILL_SWITCH_SYMBOL
    if state.symbol is None:
        raise ValueError("symbol is required for symbol kill switches.")
    return state.symbol


def _has_text(value: str | None) -> bool:
    return value is not None and bool(value.strip())


_COUNTABLE_TABLES = frozenset(
    {
        "order_intents",
        "risk_decisions",
        "oms_transitions",
        "execution_updates",
        "fill_events",
        "position_states",
        "kill_switch_states",
    }
)
