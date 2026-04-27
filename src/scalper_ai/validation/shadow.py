"""Paper/shadow decision reporting for target-position strategies."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from scalper_ai.backtesting.accounting import mark_position
from scalper_ai.backtesting.config import BacktestConfig
from scalper_ai.backtesting.engine import (
    BacktestState,
    TargetPositionStrategy,
    _build_backtest_event,
    _coerce_target_position,
    _prepare_backtest_frame,
)
from scalper_ai.domain import PositionState

_ZERO_TOLERANCE = 1e-12
_SUMMARY_COLUMNS = (
    "challenger_name",
    "event_count",
    "different_target_count",
    "direction_changed_count",
    "mean_absolute_delta",
    "max_absolute_delta",
    "disagreement_ratio",
    "direction_change_ratio",
)


@dataclass(frozen=True)
class ShadowStrategySpec:
    """Named strategy participating in a shadow decision session."""

    name: str
    strategy: TargetPositionStrategy
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Shadow strategy name must be non-empty.")


@dataclass(frozen=True)
class ShadowDecision:
    """One strategy decision observed during a shadow session."""

    strategy_name: str
    role: str
    symbol: str
    event_timestamp: pd.Timestamp
    available_timestamp: pd.Timestamp
    mark_price: float
    current_position: float
    target_position: float
    raw_target_position: float | None


@dataclass(frozen=True)
class ShadowDecisionDiff:
    """Decision disagreement between the champion and one challenger."""

    challenger_name: str
    symbol: str
    event_timestamp: pd.Timestamp
    available_timestamp: pd.Timestamp
    champion_target_position: float
    challenger_target_position: float
    absolute_delta: float
    direction_changed: bool


@dataclass(frozen=True)
class ShadowDecisionReport:
    """Materialized decision-only report for paper/shadow validation."""

    champion_name: str
    challenger_names: tuple[str, ...]
    generated_at: datetime
    decisions: tuple[ShadowDecision, ...]
    diffs: tuple[ShadowDecisionDiff, ...]
    summary: pd.DataFrame

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the shadow report."""

        return {
            "champion_name": self.champion_name,
            "challenger_names": list(self.challenger_names),
            "generated_at": self.generated_at.isoformat(),
            "decisions": [_decision_to_dict(decision) for decision in self.decisions],
            "diffs": [_diff_to_dict(diff) for diff in self.diffs],
            "summary": self.summary.to_dict(orient="records"),
        }


def run_shadow_decision_session(
    frame: pd.DataFrame,
    *,
    champion: ShadowStrategySpec,
    challengers: tuple[ShadowStrategySpec, ...],
    config: BacktestConfig | None = None,
    generated_at: datetime | None = None,
) -> ShadowDecisionReport:
    """Replay market events through strategies and report decision drift only."""

    if not challengers:
        raise ValueError("At least one challenger strategy is required.")

    challenger_names = tuple(challenger.name.strip() for challenger in challengers)
    if len(set(challenger_names)) != len(challenger_names):
        raise ValueError("Challenger strategy names must be unique.")
    if champion.name.strip() in challenger_names:
        raise ValueError("Champion and challenger strategy names must be distinct.")

    resolved_config = config or BacktestConfig()
    market_frame = _prepare_backtest_frame(frame, config=resolved_config)
    symbol = str(market_frame.iloc[0][resolved_config.symbol_column])
    generated_timestamp = generated_at or datetime.now(timezone.utc)  # noqa: UP017
    if generated_timestamp.tzinfo is None or generated_timestamp.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware.")

    decisions: list[ShadowDecision] = []
    diffs: list[ShadowDecisionDiff] = []
    shadow_position: PositionState | None = None

    for _, row in market_frame.iterrows():
        event = _build_backtest_event(row, config=resolved_config)
        current_position = mark_position(
            shadow_position,
            symbol=symbol,
            timestamp=event.available_timestamp.to_pydatetime(),
            mark_price=event.mark_price,
        )
        state = BacktestState(
            current_position=current_position,
            cash_balance=float(resolved_config.initial_cash),
            equity=float(resolved_config.initial_cash),
            peak_equity=float(resolved_config.initial_cash),
            drawdown=0.0,
            trade_count=0,
            turnover_quote=0.0,
        )

        champion_raw = champion.strategy(event, state)
        champion_target = _resolve_target(champion_raw, current_position=current_position)
        decisions.append(
            _build_decision(
                strategy_name=champion.name,
                role="champion",
                event=event,
                current_position=current_position,
                target_position=champion_target,
                raw_target_position=champion_raw,
            )
        )

        for challenger in challengers:
            challenger_raw = challenger.strategy(event, state)
            challenger_target = _resolve_target(challenger_raw, current_position=current_position)
            decisions.append(
                _build_decision(
                    strategy_name=challenger.name,
                    role="challenger",
                    event=event,
                    current_position=current_position,
                    target_position=challenger_target,
                    raw_target_position=challenger_raw,
                )
            )
            diffs.append(
                ShadowDecisionDiff(
                    challenger_name=challenger.name,
                    symbol=event.symbol,
                    event_timestamp=event.event_timestamp,
                    available_timestamp=event.available_timestamp,
                    champion_target_position=champion_target,
                    challenger_target_position=challenger_target,
                    absolute_delta=abs(champion_target - challenger_target),
                    direction_changed=_position_direction(champion_target)
                    != _position_direction(challenger_target),
                )
            )

        shadow_position = _set_shadow_position(
            current_position,
            target_position=champion_target,
            mark_price=event.mark_price,
        )

    return ShadowDecisionReport(
        champion_name=champion.name,
        challenger_names=challenger_names,
        generated_at=generated_timestamp.astimezone(timezone.utc),  # noqa: UP017
        decisions=tuple(decisions),
        diffs=tuple(diffs),
        summary=_build_summary_frame(diffs),
    )


def write_shadow_decision_report(
    report: ShadowDecisionReport,
    *,
    output_dir: Path,
    filename: str | None = None,
) -> Path:
    """Persist a shadow decision report as JSON."""

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_champion = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in report.champion_name.lower()
    ).strip("_")
    resolved_filename = filename or f"{safe_champion or 'champion'}_shadow_decisions.json"
    path = output_dir / resolved_filename
    path.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _resolve_target(raw_target: float | None, *, current_position: PositionState) -> float:
    if raw_target is None:
        return float(current_position.net_quantity)
    return _coerce_target_position(raw_target)


def _build_decision(
    *,
    strategy_name: str,
    role: str,
    event,
    current_position: PositionState,
    target_position: float,
    raw_target_position: float | None,
) -> ShadowDecision:
    return ShadowDecision(
        strategy_name=strategy_name,
        role=role,
        symbol=event.symbol,
        event_timestamp=event.event_timestamp,
        available_timestamp=event.available_timestamp,
        mark_price=event.mark_price,
        current_position=float(current_position.net_quantity),
        target_position=target_position,
        raw_target_position=None if raw_target_position is None else float(raw_target_position),
    )


def _set_shadow_position(
    current_position: PositionState,
    *,
    target_position: float,
    mark_price: float,
) -> PositionState:
    if math.isclose(target_position, 0.0, abs_tol=_ZERO_TOLERANCE):
        return current_position.model_copy(
            update={
                "net_quantity": 0.0,
                "average_entry_price": 0.0,
                "unrealized_pnl": 0.0,
                "exposure_quote": 0.0,
            }
        )
    return current_position.model_copy(
        update={
            "net_quantity": target_position,
            "average_entry_price": mark_price,
            "unrealized_pnl": 0.0,
            "exposure_quote": target_position * mark_price,
        }
    )


def _build_summary_frame(diffs: list[ShadowDecisionDiff]) -> pd.DataFrame:
    if not diffs:
        return pd.DataFrame(columns=list(_SUMMARY_COLUMNS))

    frame = pd.DataFrame.from_records(asdict(diff) for diff in diffs)
    grouped = frame.groupby("challenger_name", sort=True)
    summary = grouped.agg(
        event_count=("absolute_delta", "size"),
        different_target_count=(
            "absolute_delta",
            lambda values: int((values > _ZERO_TOLERANCE).sum()),
        ),
        direction_changed_count=("direction_changed", "sum"),
        mean_absolute_delta=("absolute_delta", "mean"),
        max_absolute_delta=("absolute_delta", "max"),
    ).reset_index()
    summary["direction_changed_count"] = summary["direction_changed_count"].astype(int)
    summary["disagreement_ratio"] = (
        summary["different_target_count"] / summary["event_count"]
    )
    summary["direction_change_ratio"] = (
        summary["direction_changed_count"] / summary["event_count"]
    )
    return summary.loc[:, list(_SUMMARY_COLUMNS)]


def _position_direction(quantity: float) -> int:
    if math.isclose(quantity, 0.0, abs_tol=_ZERO_TOLERANCE):
        return 0
    return 1 if quantity > 0 else -1


def _decision_to_dict(decision: ShadowDecision) -> Mapping[str, Any]:
    payload = asdict(decision)
    payload["event_timestamp"] = decision.event_timestamp.isoformat()
    payload["available_timestamp"] = decision.available_timestamp.isoformat()
    return payload


def _diff_to_dict(diff: ShadowDecisionDiff) -> Mapping[str, Any]:
    payload = asdict(diff)
    payload["event_timestamp"] = diff.event_timestamp.isoformat()
    payload["available_timestamp"] = diff.available_timestamp.isoformat()
    return payload
