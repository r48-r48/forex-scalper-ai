"""Walk-forward validation helpers built on dataset splits and replay backtests."""

from scalper_ai.validation.baseline_suite import (
    BaselineBacktestRun,
    BaselineSensitivityScenario,
    BaselineSuiteResult,
    BaselineWalkForwardRun,
    BaselineWalkForwardSuiteResult,
    default_baseline_sensitivity_scenarios,
    run_baseline_suite,
    run_baseline_walk_forward_suite,
    run_default_baseline_sensitivity,
)
from scalper_ai.validation.gate import (
    ValidationGateCheck,
    ValidationGateReport,
    ValidationGateStatus,
    ValidationGateThresholds,
    build_validation_gate_report,
    write_validation_gate_report,
)
from scalper_ai.validation.metrics import (
    FoldValidationMetrics,
    RobustnessSummary,
    fold_metrics_to_frame,
    summarize_fold_metrics,
)
from scalper_ai.validation.shadow import (
    ShadowDecision,
    ShadowDecisionDiff,
    ShadowDecisionReport,
    ShadowStrategySpec,
    run_shadow_decision_session,
    write_shadow_decision_report,
)
from scalper_ai.validation.supervised_filter import (
    SupervisedFilterFoldMetrics,
    SupervisedFilterFoldResult,
    SupervisedFilterSummary,
    SupervisedFilterWalkForwardResult,
    run_supervised_filter_walk_forward,
)
from scalper_ai.validation.walk_forward import (
    PartitionFrameBuilder,
    WalkForwardStrategyFactory,
    WalkForwardValidationFold,
    WalkForwardValidationResult,
    run_walk_forward_validation,
    supervised_partition_to_backtest_frame,
)

__all__ = [
    "BaselineBacktestRun",
    "BaselineSensitivityScenario",
    "BaselineSuiteResult",
    "BaselineWalkForwardRun",
    "BaselineWalkForwardSuiteResult",
    "FoldValidationMetrics",
    "PartitionFrameBuilder",
    "RobustnessSummary",
    "ShadowDecision",
    "ShadowDecisionDiff",
    "ShadowDecisionReport",
    "ShadowStrategySpec",
    "SupervisedFilterFoldMetrics",
    "SupervisedFilterFoldResult",
    "SupervisedFilterSummary",
    "SupervisedFilterWalkForwardResult",
    "ValidationGateCheck",
    "ValidationGateReport",
    "ValidationGateStatus",
    "ValidationGateThresholds",
    "WalkForwardStrategyFactory",
    "WalkForwardValidationFold",
    "WalkForwardValidationResult",
    "build_validation_gate_report",
    "default_baseline_sensitivity_scenarios",
    "fold_metrics_to_frame",
    "run_baseline_suite",
    "run_baseline_walk_forward_suite",
    "run_default_baseline_sensitivity",
    "run_shadow_decision_session",
    "run_supervised_filter_walk_forward",
    "run_walk_forward_validation",
    "summarize_fold_metrics",
    "supervised_partition_to_backtest_frame",
    "write_shadow_decision_report",
    "write_validation_gate_report",
]
