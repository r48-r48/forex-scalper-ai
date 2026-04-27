"""Unit tests for paper/shadow decision reporting."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from scalper_ai.validation import (
    ShadowStrategySpec,
    run_shadow_decision_session,
    write_shadow_decision_report,
)


def test_shadow_decision_session_reports_challenger_drift_without_orders(tmp_path) -> None:
    report = run_shadow_decision_session(
        _market_frame(),
        champion=ShadowStrategySpec(name="champion", strategy=_signal_strategy),
        challengers=(
            ShadowStrategySpec(name="flat_filter", strategy=_flat_strategy),
            ShadowStrategySpec(name="mirror", strategy=_mirror_strategy),
        ),
        generated_at=datetime(2026, 4, 28, 12, 30, tzinfo=timezone.utc),  # noqa: UP017
    )

    assert report.champion_name == "champion"
    assert report.challenger_names == ("flat_filter", "mirror")
    assert len(report.decisions) == 12
    assert len(report.diffs) == 8
    assert report.summary["challenger_name"].tolist() == ["flat_filter", "mirror"]
    assert report.summary["disagreement_ratio"].tolist() == pytest.approx([1.0, 1.0])
    assert report.summary["direction_change_ratio"].tolist() == pytest.approx([1.0, 1.0])

    champion_decisions = [decision for decision in report.decisions if decision.role == "champion"]
    assert champion_decisions[1].current_position == pytest.approx(
        champion_decisions[0].target_position
    )

    path = write_shadow_decision_report(report, output_dir=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["generated_at"] == "2026-04-28T12:30:00+00:00"
    assert len(payload["decisions"]) == 12
    assert payload["summary"][0]["challenger_name"] == "flat_filter"


def test_shadow_decision_session_validates_strategy_names_and_time() -> None:
    frame = _market_frame()

    with pytest.raises(ValueError, match="distinct"):
        run_shadow_decision_session(
            frame,
            champion=ShadowStrategySpec(name="same", strategy=_signal_strategy),
            challengers=(ShadowStrategySpec(name="same", strategy=_flat_strategy),),
        )

    with pytest.raises(ValueError, match="timezone-aware"):
        run_shadow_decision_session(
            frame,
            champion=ShadowStrategySpec(name="champion", strategy=_signal_strategy),
            challengers=(ShadowStrategySpec(name="flat", strategy=_flat_strategy),),
            generated_at=datetime(2026, 4, 28, 12, 30),  # noqa: DTZ001
        )


def _market_frame() -> pd.DataFrame:
    base_time = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)  # noqa: UP017
    records: list[dict[str, object]] = []
    signals = (1.0, -1.0, 1.0, -1.0)
    for index, signal in enumerate(signals):
        timestamp = base_time + timedelta(minutes=index)
        records.append(
            {
                "symbol": "EURUSD",
                "event_timestamp": timestamp,
                "available_timestamp": timestamp,
                "mid_price": 100.0 + index,
                "signal": signal,
            }
        )
    return pd.DataFrame.from_records(records)


def _signal_strategy(event, state) -> float:
    del state
    return float(event.row_payload["signal"])


def _flat_strategy(event, state) -> float:
    del event, state
    return 0.0


def _mirror_strategy(event, state) -> float:
    del state
    return -float(event.row_payload["signal"])
