"""Execution adapters, routing helpers, and paper trading workflow models."""

from scalper_ai.execution.connectivity import BrokerConnectivitySnapshot
from scalper_ai.execution.interfaces import (
    BrokerConnectivityProvider,
    BrokerSnapshotProvider,
    ExecutionAdapter,
)
from scalper_ai.execution.live_stub import LiveExecutionStubAdapter, LiveExecutionStubConfig
from scalper_ai.execution.mt5_client import Mt5AccountSnapshot, Mt5TerminalClient, Mt5TerminalClientConfig
from scalper_ai.execution.mt5_live import (
    Mt5ExecutionAdapter,
    Mt5ExecutionClientProtocol,
    Mt5ExecutionConfig,
    Mt5OrderRequest,
    Mt5OrderState,
    Mt5PositionState,
)
from scalper_ai.execution.models import (
    ExecutionOrder,
    ExecutionOrderStatus,
    ExecutionQuote,
    ExecutionUpdate,
)
from scalper_ai.execution.paper import PaperExecutionAdapter, PaperExecutionConfig
from scalper_ai.execution.reconciliation import (
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
    ReconciliationIssue,
    ReconciliationReport,
    ReconciliationSeverity,
    build_reconciliation_report,
    build_reconciliation_report_for_positions,
    reconcile_order,
    reconcile_position,
)
from scalper_ai.execution.snapshots import ExecutionStateTracker, build_snapshot_reconciliation_report
from scalper_ai.execution.router import ExecutionRouter

__all__ = [
    "BrokerOrderSnapshot",
    "BrokerPositionSnapshot",
    "BrokerConnectivityProvider",
    "BrokerConnectivitySnapshot",
    "BrokerSnapshotProvider",
    "ExecutionAdapter",
    "ExecutionOrder",
    "ExecutionOrderStatus",
    "ExecutionQuote",
    "ExecutionRouter",
    "ExecutionStateTracker",
    "ExecutionUpdate",
    "LiveExecutionStubAdapter",
    "LiveExecutionStubConfig",
    "Mt5ExecutionAdapter",
    "Mt5AccountSnapshot",
    "Mt5ExecutionClientProtocol",
    "Mt5ExecutionConfig",
    "Mt5OrderRequest",
    "Mt5OrderState",
    "Mt5PositionState",
    "Mt5TerminalClient",
    "Mt5TerminalClientConfig",
    "PaperExecutionAdapter",
    "PaperExecutionConfig",
    "ReconciliationIssue",
    "ReconciliationReport",
    "ReconciliationSeverity",
    "build_reconciliation_report",
    "build_reconciliation_report_for_positions",
    "build_snapshot_reconciliation_report",
    "reconcile_order",
    "reconcile_position",
]
