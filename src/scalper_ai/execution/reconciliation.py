"""Pure reconciliation helpers for comparing internal and broker execution state."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, Optional, Sequence

from scalper_ai.domain import PositionMode, PositionState
from scalper_ai.execution.models import ExecutionOrder, ExecutionOrderStatus

_ZERO_TOLERANCE = 1e-9


class ReconciliationSeverity(str, Enum):
    """Severity levels for reconciliation issues."""

    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass(frozen=True)
class BrokerOrderSnapshot:
    """Normalized broker-side order state used for reconciliation."""

    broker_order_id: str
    symbol: str
    status: ExecutionOrderStatus
    updated_at: datetime
    requested_quantity: float
    filled_quantity: float
    remaining_quantity: float

    def __post_init__(self) -> None:
        if not self.broker_order_id.strip():
            raise ValueError("broker_order_id must be non-empty.")
        if not self.symbol.strip():
            raise ValueError("symbol must be non-empty.")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware.")
        if self.requested_quantity <= 0:
            raise ValueError("requested_quantity must be greater than zero.")
        if self.filled_quantity < 0:
            raise ValueError("filled_quantity must be non-negative.")
        if self.remaining_quantity < 0:
            raise ValueError("remaining_quantity must be non-negative.")
        quantity_gap = (self.filled_quantity + self.remaining_quantity) - self.requested_quantity
        if not math.isclose(quantity_gap, 0.0, abs_tol=_ZERO_TOLERANCE):
            raise ValueError("filled_quantity and remaining_quantity must reconcile to requested_quantity.")

    @property
    def is_open(self) -> bool:
        """Return whether the broker snapshot still represents an open order."""

        return self.status in {
            ExecutionOrderStatus.ACCEPTED,
            ExecutionOrderStatus.TRIGGERED,
            ExecutionOrderStatus.PARTIALLY_FILLED,
        }


@dataclass(frozen=True)
class BrokerPositionSnapshot:
    """Normalized broker-side net position used for reconciliation."""

    symbol: str
    timestamp: datetime
    net_quantity: float
    average_entry_price: float = 0.0
    position_mode: PositionMode = PositionMode.NETTING

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol must be non-empty.")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware.")
        if math.isclose(self.net_quantity, 0.0, abs_tol=_ZERO_TOLERANCE) and not math.isclose(
            self.average_entry_price,
            0.0,
            abs_tol=_ZERO_TOLERANCE,
        ):
            raise ValueError("Flat positions must not carry a non-zero average_entry_price.")
        if not math.isclose(self.net_quantity, 0.0, abs_tol=_ZERO_TOLERANCE) and self.average_entry_price <= 0:
            raise ValueError("Non-flat positions require a positive average_entry_price.")


@dataclass(frozen=True)
class ReconciliationIssue:
    """One reconciliation discrepancy."""

    scope: str
    reference_id: str
    severity: ReconciliationSeverity
    code: str
    message: str
    details: Optional[dict[str, object]] = None

    def __post_init__(self) -> None:
        if not self.scope.strip():
            raise ValueError("scope must be non-empty.")
        if not self.reference_id.strip():
            raise ValueError("reference_id must be non-empty.")
        if not self.code.strip():
            raise ValueError("code must be non-empty.")
        if not self.message.strip():
            raise ValueError("message must be non-empty.")


@dataclass(frozen=True)
class ReconciliationReport:
    """Aggregated reconciliation output."""

    checked_at: datetime
    issues: tuple[ReconciliationIssue, ...]

    def __post_init__(self) -> None:
        if self.checked_at.tzinfo is None or self.checked_at.utcoffset() is None:
            raise ValueError("checked_at must be timezone-aware.")

    @property
    def error_count(self) -> int:
        """Return the number of error-level issues."""

        return sum(issue.severity is ReconciliationSeverity.ERROR for issue in self.issues)

    @property
    def warning_count(self) -> int:
        """Return the number of warning-level issues."""

        return sum(issue.severity is ReconciliationSeverity.WARN for issue in self.issues)

    @property
    def has_errors(self) -> bool:
        """Return whether the report contains at least one error."""

        return self.error_count > 0


def reconcile_order(
    internal_order: ExecutionOrder,
    broker_order: Optional[BrokerOrderSnapshot],
    *,
    quantity_tolerance: float = _ZERO_TOLERANCE,
    allow_missing_terminal_order: bool = True,
) -> tuple[ReconciliationIssue, ...]:
    """Compare one internal order to one broker-side snapshot."""

    issues: list[ReconciliationIssue] = []

    if broker_order is None:
        if internal_order.is_open:
            issues.append(
                ReconciliationIssue(
                    scope="order",
                    reference_id=internal_order.broker_order_id,
                    severity=ReconciliationSeverity.ERROR,
                    code="missing_broker_order",
                    message="Open internal order is missing from broker reconciliation data.",
                    details={"status": internal_order.status.value},
                )
            )
        elif not allow_missing_terminal_order:
            issues.append(
                ReconciliationIssue(
                    scope="order",
                    reference_id=internal_order.broker_order_id,
                    severity=ReconciliationSeverity.WARN,
                    code="missing_terminal_broker_order",
                    message="Terminal internal order is missing from broker reconciliation data.",
                    details={"status": internal_order.status.value},
                )
            )
        return tuple(issues)

    if internal_order.intent.symbol != broker_order.symbol:
        issues.append(
            ReconciliationIssue(
                scope="order",
                reference_id=internal_order.broker_order_id,
                severity=ReconciliationSeverity.ERROR,
                code="symbol_mismatch",
                message="Internal and broker order symbols do not match.",
                details={
                    "internal_symbol": internal_order.intent.symbol,
                    "broker_symbol": broker_order.symbol,
                },
            )
        )

    if internal_order.status is not broker_order.status:
        severity = (
            ReconciliationSeverity.ERROR
            if internal_order.is_open != broker_order.is_open
            else ReconciliationSeverity.WARN
        )
        issues.append(
            ReconciliationIssue(
                scope="order",
                reference_id=internal_order.broker_order_id,
                severity=severity,
                code="status_mismatch",
                message="Internal and broker order statuses do not match.",
                details={
                    "internal_status": internal_order.status.value,
                    "broker_status": broker_order.status.value,
                },
            )
        )

    _compare_quantity(
        issues,
        reference_id=internal_order.broker_order_id,
        code="requested_quantity_mismatch",
        message="Internal and broker requested quantities do not match.",
        internal_value=internal_order.requested_quantity,
        broker_value=broker_order.requested_quantity,
        tolerance=quantity_tolerance,
    )
    _compare_quantity(
        issues,
        reference_id=internal_order.broker_order_id,
        code="filled_quantity_mismatch",
        message="Internal and broker filled quantities do not match.",
        internal_value=internal_order.filled_quantity,
        broker_value=broker_order.filled_quantity,
        tolerance=quantity_tolerance,
    )
    _compare_quantity(
        issues,
        reference_id=internal_order.broker_order_id,
        code="remaining_quantity_mismatch",
        message="Internal and broker remaining quantities do not match.",
        internal_value=internal_order.remaining_quantity,
        broker_value=broker_order.remaining_quantity,
        tolerance=quantity_tolerance,
    )

    return tuple(issues)


def reconcile_position(
    internal_position: Optional[PositionState],
    broker_position: Optional[BrokerPositionSnapshot],
    *,
    quantity_tolerance: float = _ZERO_TOLERANCE,
    price_tolerance: float = _ZERO_TOLERANCE,
) -> tuple[ReconciliationIssue, ...]:
    """Compare one internal net position to one broker-side position snapshot."""

    issues: list[ReconciliationIssue] = []
    internal_quantity = 0.0 if internal_position is None else float(internal_position.net_quantity)
    internal_symbol = None if internal_position is None else internal_position.symbol
    internal_entry = 0.0 if internal_position is None else float(internal_position.average_entry_price)

    broker_quantity = 0.0 if broker_position is None else float(broker_position.net_quantity)
    broker_symbol = None if broker_position is None else broker_position.symbol
    broker_entry = 0.0 if broker_position is None else float(broker_position.average_entry_price)

    reference_id = broker_symbol or internal_symbol or "position"

    if internal_symbol is not None and broker_symbol is not None and internal_symbol != broker_symbol:
        issues.append(
            ReconciliationIssue(
                scope="position",
                reference_id=reference_id,
                severity=ReconciliationSeverity.ERROR,
                code="position_symbol_mismatch",
                message="Internal and broker positions refer to different symbols.",
                details={
                    "internal_symbol": internal_symbol,
                    "broker_symbol": broker_symbol,
                },
            )
        )

    if not math.isclose(internal_quantity, broker_quantity, abs_tol=quantity_tolerance):
        issues.append(
            ReconciliationIssue(
                scope="position",
                reference_id=reference_id,
                severity=ReconciliationSeverity.ERROR,
                code="position_quantity_mismatch",
                message="Internal and broker net quantities do not match.",
                details={
                    "internal_quantity": internal_quantity,
                    "broker_quantity": broker_quantity,
                },
            )
        )

    if (
        not math.isclose(internal_quantity, 0.0, abs_tol=quantity_tolerance)
        and not math.isclose(broker_quantity, 0.0, abs_tol=quantity_tolerance)
        and not math.isclose(internal_entry, broker_entry, abs_tol=price_tolerance)
    ):
        issues.append(
            ReconciliationIssue(
                scope="position",
                reference_id=reference_id,
                severity=ReconciliationSeverity.WARN,
                code="average_entry_mismatch",
                message="Internal and broker average entry prices do not match.",
                details={
                    "internal_average_entry_price": internal_entry,
                    "broker_average_entry_price": broker_entry,
                },
            )
        )

    if internal_position is not None and broker_position is not None:
        internal_mode = internal_position.position_mode or PositionMode.NETTING
        if internal_mode is not broker_position.position_mode:
            issues.append(
                ReconciliationIssue(
                    scope="position",
                    reference_id=reference_id,
                    severity=ReconciliationSeverity.WARN,
                    code="position_mode_mismatch",
                    message="Internal and broker position modes do not match.",
                    details={
                        "internal_mode": internal_mode.value,
                        "broker_mode": broker_position.position_mode.value,
                    },
                )
            )

    return tuple(issues)


def build_reconciliation_report(
    *,
    internal_orders: Sequence[ExecutionOrder],
    broker_orders: Mapping[str, BrokerOrderSnapshot],
    internal_position: Optional[PositionState],
    broker_position: Optional[BrokerPositionSnapshot],
    checked_at: Optional[datetime] = None,
    allow_missing_terminal_orders: bool = True,
) -> ReconciliationReport:
    """Build one aggregated reconciliation report."""

    internal_positions: dict[str, PositionState] = {}
    if internal_position is not None:
        internal_positions[internal_position.symbol] = internal_position

    broker_positions: dict[str, BrokerPositionSnapshot] = {}
    if broker_position is not None:
        broker_positions[broker_position.symbol] = broker_position

    return build_reconciliation_report_for_positions(
        internal_orders=internal_orders,
        broker_orders=broker_orders,
        internal_positions=internal_positions,
        broker_positions=broker_positions,
        checked_at=checked_at,
        allow_missing_terminal_orders=allow_missing_terminal_orders,
    )


def build_reconciliation_report_for_positions(
    *,
    internal_orders: Sequence[ExecutionOrder],
    broker_orders: Mapping[str, BrokerOrderSnapshot],
    internal_positions: Mapping[str, PositionState],
    broker_positions: Mapping[str, BrokerPositionSnapshot],
    checked_at: Optional[datetime] = None,
    allow_missing_terminal_orders: bool = True,
) -> ReconciliationReport:
    """Build one aggregated reconciliation report across many symbols."""

    issues: list[ReconciliationIssue] = []
    seen_order_ids = {order.broker_order_id for order in internal_orders}

    for internal_order in internal_orders:
        issues.extend(
            reconcile_order(
                internal_order,
                broker_orders.get(internal_order.broker_order_id),
                allow_missing_terminal_order=allow_missing_terminal_orders,
            )
        )

    for broker_order_id, broker_order in broker_orders.items():
        if broker_order_id not in seen_order_ids:
            issues.append(
                ReconciliationIssue(
                    scope="order",
                    reference_id=broker_order_id,
                    severity=ReconciliationSeverity.WARN,
                    code="unknown_broker_order",
                    message="Broker reconciliation data contains an order unknown to internal state.",
                    details={"symbol": broker_order.symbol, "status": broker_order.status.value},
                )
            )

    for symbol in sorted(set(internal_positions) | set(broker_positions)):
        issues.extend(reconcile_position(internal_positions.get(symbol), broker_positions.get(symbol)))

    return ReconciliationReport(
        checked_at=checked_at or datetime.now(timezone.utc),
        issues=tuple(issues),
    )


def _compare_quantity(
    issues: list[ReconciliationIssue],
    *,
    reference_id: str,
    code: str,
    message: str,
    internal_value: float,
    broker_value: float,
    tolerance: float,
) -> None:
    if not math.isclose(internal_value, broker_value, abs_tol=tolerance):
        issues.append(
            ReconciliationIssue(
                scope="order",
                reference_id=reference_id,
                severity=ReconciliationSeverity.ERROR,
                code=code,
                message=message,
                details={
                    "internal_value": internal_value,
                    "broker_value": broker_value,
                },
            )
        )
