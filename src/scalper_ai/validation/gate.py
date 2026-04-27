"""Unified validation gate reports for strategy promotion decisions."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd

from scalper_ai.backtesting import BacktestResult, ExecutionBacktestResult
from scalper_ai.validation.walk_forward import WalkForwardValidationResult


class ValidationGateStatus(str, Enum):  # noqa: UP042 - keep local Python 3.9 compatibility.
    """Go/no-go severity for validation gate checks."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class ValidationGateThresholds:
    """Explicit thresholds used by the validation gate."""

    min_total_pnl: float = 0.0
    max_drawdown: float = 0.10
    min_trade_count: int = 1
    min_profitable_fold_ratio: float = 0.50
    min_fill_ratio: float = 0.95
    max_cancel_ratio: float = 0.25
    max_reject_ratio: float = 0.05
    max_average_slippage_bps: float = 5.0
    max_risk_flag_count: int = 0

    def __post_init__(self) -> None:
        if self.max_drawdown < 0:
            raise ValueError("max_drawdown must be non-negative.")
        if self.min_trade_count < 0:
            raise ValueError("min_trade_count must be non-negative.")
        if not 0 <= self.min_profitable_fold_ratio <= 1:
            raise ValueError("min_profitable_fold_ratio must be between zero and one.")
        if not 0 <= self.min_fill_ratio <= 1:
            raise ValueError("min_fill_ratio must be between zero and one.")
        if not 0 <= self.max_cancel_ratio <= 1:
            raise ValueError("max_cancel_ratio must be between zero and one.")
        if not 0 <= self.max_reject_ratio <= 1:
            raise ValueError("max_reject_ratio must be between zero and one.")
        if self.max_average_slippage_bps < 0:
            raise ValueError("max_average_slippage_bps must be non-negative.")
        if self.max_risk_flag_count < 0:
            raise ValueError("max_risk_flag_count must be non-negative.")


@dataclass(frozen=True)
class ValidationGateCheck:
    """One validation check emitted by the unified report."""

    name: str
    status: ValidationGateStatus
    observed_value: float | int | str | None
    threshold: float | int | str | None
    message: str


@dataclass(frozen=True)
class ValidationGateReport:
    """Materialized go/no-go validation report."""

    strategy_name: str
    generated_at: datetime
    status: ValidationGateStatus
    checks: tuple[ValidationGateCheck, ...]
    backtest: Mapping[str, Any]
    walk_forward: Mapping[str, Any]
    execution_stress: Mapping[str, Any]
    latency_slippage: Mapping[str, Any]
    risk_flags: tuple[str, ...]
    regime_breakdown: tuple[Mapping[str, Any], ...]

    @property
    def passed(self) -> bool:
        """Return whether the report is eligible for promotion."""

        return self.status is ValidationGateStatus.PASS

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the validation report."""

        payload = asdict(self)
        payload["generated_at"] = self.generated_at.isoformat()
        payload["status"] = self.status.value
        payload["checks"] = [
            {
                **asdict(check),
                "status": check.status.value,
            }
            for check in self.checks
        ]
        return payload


def build_validation_gate_report(
    *,
    strategy_name: str,
    backtest_result: BacktestResult | None = None,
    walk_forward_result: WalkForwardValidationResult | None = None,
    execution_result: ExecutionBacktestResult | None = None,
    risk_flags: Sequence[str] = (),
    market_frame: pd.DataFrame | None = None,
    thresholds: ValidationGateThresholds | None = None,
    generated_at: datetime | None = None,
) -> ValidationGateReport:
    """Build one unified validation gate report from offline validation artifacts."""

    if not strategy_name.strip():
        raise ValueError("strategy_name must be non-empty.")

    resolved_thresholds = thresholds or ValidationGateThresholds()
    generated_timestamp = generated_at or datetime.now(timezone.utc)  # noqa: UP017
    if generated_timestamp.tzinfo is None or generated_timestamp.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware.")

    checks: list[ValidationGateCheck] = []
    backtest_summary = _backtest_summary(backtest_result)
    walk_forward_summary = _walk_forward_summary(walk_forward_result)
    execution_summary = _execution_summary(execution_result)
    latency_slippage = _latency_slippage_summary(execution_result)
    normalized_risk_flags = tuple(str(flag).strip() for flag in risk_flags if str(flag).strip())

    if backtest_result is None:
        checks.append(
            _check(
                "backtest_present",
                ValidationGateStatus.WARN,
                None,
                "required",
                "No single-run backtest result was attached.",
            )
        )
    else:
        checks.extend(_backtest_checks(backtest_result, thresholds=resolved_thresholds))

    if walk_forward_result is None:
        checks.append(
            _check(
                "walk_forward_present",
                ValidationGateStatus.WARN,
                None,
                "required",
                "No walk-forward validation result was attached.",
            )
        )
    else:
        checks.extend(_walk_forward_checks(walk_forward_result, thresholds=resolved_thresholds))

    if execution_result is None:
        checks.append(
            _check(
                "execution_stress_present",
                ValidationGateStatus.WARN,
                None,
                "required",
                "No execution-stress result was attached.",
            )
        )
    else:
        checks.extend(_execution_checks(execution_result, thresholds=resolved_thresholds))

    checks.append(
        _check(
            "risk_flags",
            ValidationGateStatus.FAIL
            if len(normalized_risk_flags) > resolved_thresholds.max_risk_flag_count
            else ValidationGateStatus.PASS,
            len(normalized_risk_flags),
            resolved_thresholds.max_risk_flag_count,
            "Risk flags must stay within the configured validation limit.",
        )
    )

    return ValidationGateReport(
        strategy_name=strategy_name.strip(),
        generated_at=generated_timestamp.astimezone(timezone.utc),  # noqa: UP017
        status=_aggregate_status(checks),
        checks=tuple(checks),
        backtest=backtest_summary,
        walk_forward=walk_forward_summary,
        execution_stress=execution_summary,
        latency_slippage=latency_slippage,
        risk_flags=normalized_risk_flags,
        regime_breakdown=_build_regime_breakdown(market_frame),
    )


def write_validation_gate_report(
    report: ValidationGateReport,
    *,
    output_dir: Path,
    filename: str | None = None,
) -> Path:
    """Persist a validation gate report as JSON under an artifact directory."""

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_strategy = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in report.strategy_name.lower()
    ).strip("_")
    resolved_filename = filename or f"{safe_strategy or 'strategy'}_validation_gate.json"
    path = output_dir / resolved_filename
    path.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _backtest_checks(
    result: BacktestResult,
    *,
    thresholds: ValidationGateThresholds,
) -> tuple[ValidationGateCheck, ...]:
    metrics = result.metrics
    return (
        _threshold_check(
            "backtest_total_pnl",
            observed=metrics.total_pnl,
            threshold=thresholds.min_total_pnl,
            passed=metrics.total_pnl >= thresholds.min_total_pnl,
            message="Backtest PnL must meet the configured minimum.",
        ),
        _threshold_check(
            "backtest_max_drawdown",
            observed=metrics.max_drawdown,
            threshold=thresholds.max_drawdown,
            passed=metrics.max_drawdown <= thresholds.max_drawdown,
            message="Backtest drawdown must stay within the configured maximum.",
        ),
        _threshold_check(
            "backtest_trade_count",
            observed=metrics.trade_count,
            threshold=thresholds.min_trade_count,
            passed=metrics.trade_count >= thresholds.min_trade_count,
            message="Backtest must produce enough activity to evaluate.",
        ),
    )


def _walk_forward_checks(
    result: WalkForwardValidationResult,
    *,
    thresholds: ValidationGateThresholds,
) -> tuple[ValidationGateCheck, ...]:
    summary = result.summary
    return (
        _threshold_check(
            "walk_forward_profitable_fold_ratio",
            observed=summary.profitable_fold_ratio,
            threshold=thresholds.min_profitable_fold_ratio,
            passed=summary.profitable_fold_ratio >= thresholds.min_profitable_fold_ratio,
            message="Walk-forward profitable-fold ratio must meet the configured minimum.",
        ),
        _threshold_check(
            "walk_forward_worst_drawdown",
            observed=summary.worst_max_drawdown,
            threshold=thresholds.max_drawdown,
            passed=summary.worst_max_drawdown <= thresholds.max_drawdown,
            message="Walk-forward worst drawdown must stay within the configured maximum.",
        ),
    )


def _execution_checks(
    result: ExecutionBacktestResult,
    *,
    thresholds: ValidationGateThresholds,
) -> tuple[ValidationGateCheck, ...]:
    metrics = result.metrics
    return (
        _threshold_check(
            "execution_fill_ratio",
            observed=metrics.fill_ratio,
            threshold=thresholds.min_fill_ratio,
            passed=metrics.fill_ratio >= thresholds.min_fill_ratio,
            message="Execution-stress fill ratio must meet the configured minimum.",
        ),
        _threshold_check(
            "execution_cancel_ratio",
            observed=metrics.cancel_ratio,
            threshold=thresholds.max_cancel_ratio,
            passed=metrics.cancel_ratio <= thresholds.max_cancel_ratio,
            message="Execution-stress cancel ratio must stay within the configured maximum.",
        ),
        _threshold_check(
            "execution_reject_ratio",
            observed=metrics.reject_ratio,
            threshold=thresholds.max_reject_ratio,
            passed=metrics.reject_ratio <= thresholds.max_reject_ratio,
            message="Execution-stress reject ratio must stay within the configured maximum.",
        ),
        _threshold_check(
            "execution_average_slippage_bps",
            observed=metrics.average_slippage_bps,
            threshold=thresholds.max_average_slippage_bps,
            passed=metrics.average_slippage_bps <= thresholds.max_average_slippage_bps,
            message="Average slippage must stay within the configured maximum.",
        ),
    )


def _backtest_summary(result: BacktestResult | None) -> Mapping[str, Any]:
    if result is None:
        return {}
    return asdict(result.metrics)


def _walk_forward_summary(result: WalkForwardValidationResult | None) -> Mapping[str, Any]:
    if result is None:
        return {}
    return asdict(result.summary)


def _execution_summary(result: ExecutionBacktestResult | None) -> Mapping[str, Any]:
    if result is None:
        return {}
    return asdict(result.metrics)


def _latency_slippage_summary(result: ExecutionBacktestResult | None) -> Mapping[str, Any]:
    if result is None:
        return {}
    metrics = result.metrics
    return {
        "average_latency_steps": metrics.average_latency_steps,
        "average_slippage_bps": metrics.average_slippage_bps,
        "spread_cost": metrics.spread_cost,
        "slippage_cost": metrics.slippage_cost,
        "commission": metrics.commission,
    }


def _build_regime_breakdown(frame: pd.DataFrame | None) -> tuple[Mapping[str, Any], ...]:
    if frame is None or frame.empty:
        return ()

    prepared = frame.copy()
    if "regime" not in prepared.columns:
        prepared["regime"] = _infer_regimes(prepared)

    rows: list[Mapping[str, Any]] = []
    for regime, group in prepared.groupby("regime", sort=True):
        row: dict[str, Any] = {
            "regime": str(regime),
            "row_count": int(len(group)),
        }
        if "mid_return" in group.columns:
            row["mean_mid_return"] = float(pd.to_numeric(group["mid_return"]).mean())
        if "spread_bps" in group.columns:
            row["mean_spread_bps"] = float(pd.to_numeric(group["spread_bps"]).mean())
        if "realized_volatility" in group.columns:
            row["mean_realized_volatility"] = float(
                pd.to_numeric(group["realized_volatility"]).mean()
            )
        rows.append(row)
    return tuple(rows)


def _infer_regimes(frame: pd.DataFrame) -> pd.Series:
    if "realized_volatility" in frame.columns:
        volatility = pd.to_numeric(frame["realized_volatility"], errors="coerce")
        median_volatility = float(volatility.median(skipna=True))
        return pd.Series(
            [
                "high_volatility" if value > median_volatility else "low_volatility"
                for value in volatility
            ],
            index=frame.index,
        )
    if "spread_bps" in frame.columns:
        spread = pd.to_numeric(frame["spread_bps"], errors="coerce")
        median_spread = float(spread.median(skipna=True))
        return pd.Series(
            ["wide_spread" if value > median_spread else "normal_spread" for value in spread],
            index=frame.index,
        )
    return pd.Series(["all"] * len(frame), index=frame.index)


def _threshold_check(
    name: str,
    *,
    observed: float | int,
    threshold: float | int,
    passed: bool,
    message: str,
) -> ValidationGateCheck:
    return _check(
        name,
        ValidationGateStatus.PASS if passed else ValidationGateStatus.FAIL,
        observed,
        threshold,
        message,
    )


def _check(
    name: str,
    status: ValidationGateStatus,
    observed: float | int | str | None,
    threshold: float | int | str | None,
    message: str,
) -> ValidationGateCheck:
    return ValidationGateCheck(
        name=name,
        status=status,
        observed_value=observed,
        threshold=threshold,
        message=message,
    )


def _aggregate_status(checks: Sequence[ValidationGateCheck]) -> ValidationGateStatus:
    if any(check.status is ValidationGateStatus.FAIL for check in checks):
        return ValidationGateStatus.FAIL
    if any(check.status is ValidationGateStatus.WARN for check in checks):
        return ValidationGateStatus.WARN
    return ValidationGateStatus.PASS
