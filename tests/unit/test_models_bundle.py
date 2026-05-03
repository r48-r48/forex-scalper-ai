"""Unit tests for production model bundle metadata contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from scalper_ai.models import (
    ModelBundleArtifact,
    ModelBundleMetadata,
    ModelTargetSpec,
    TrainingDataWindow,
    compute_feature_contract_hash,
    load_model_bundle_metadata,
    save_model_bundle_metadata,
)


def test_model_bundle_metadata_serializes_and_loads_json(tmp_path: Path) -> None:
    metadata = _bundle_metadata(
        trained_at=datetime(2026, 5, 3, 15, 30, tzinfo=timezone(timedelta(hours=3)))
    )
    path = tmp_path / "bundles" / "eurusd-transformer" / "metadata.json"

    saved_path = save_model_bundle_metadata(metadata, path)
    restored = load_model_bundle_metadata(saved_path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert saved_path == path
    assert restored == metadata
    assert restored.trained_at == datetime(2026, 5, 3, 12, 30, tzinfo=UTC)
    assert payload["trained_at"] == "2026-05-03T12:30:00Z"
    assert payload["feature_columns"] == [
        "lag_000__spread_bps",
        "lag_000__mid_return",
        "lag_001__mid_return",
    ]
    assert payload["schema_hash"] == metadata.schema_hash


def test_model_bundle_metadata_validates_required_contract_fields() -> None:
    with pytest.raises(ValueError, match="trained_at must be timezone-aware"):
        _bundle_metadata(trained_at=datetime(2026, 5, 3, 12, 30))

    with pytest.raises(ValueError, match="feature_columns must not be empty"):
        _bundle_metadata(feature_columns=())

    with pytest.raises(ValueError, match="schema_hash must match"):
        _bundle_metadata(schema_hash="0" * 64)


def test_feature_contract_hash_is_stable_and_order_sensitive() -> None:
    first_target = ModelTargetSpec(
        name="future_mid_return",
        target_type="regression",
        horizon="5m",
        parameters={"threshold": 0.0, "labels": ["short", "flat", "long"]},
    )
    equivalent_target = ModelTargetSpec(
        name="future_mid_return",
        target_type="regression",
        horizon="5m",
        parameters={"labels": ["short", "flat", "long"], "threshold": 0.0},
    )
    feature_columns = ("lag_000__spread_bps", "lag_000__mid_return")

    first_hash = compute_feature_contract_hash(feature_columns, target_spec=first_target)
    second_hash = compute_feature_contract_hash(feature_columns, target_spec=equivalent_target)
    reordered_hash = compute_feature_contract_hash(
        tuple(reversed(feature_columns)),
        target_spec=first_target,
    )

    assert first_hash == second_hash
    assert first_hash != reordered_hash
    assert len(first_hash) == 64


def _bundle_metadata(
    *,
    trained_at: datetime | None = None,
    feature_columns: tuple[str, ...] = (
        "lag_000__spread_bps",
        "lag_000__mid_return",
        "lag_001__mid_return",
    ),
    schema_hash: str | None = None,
) -> ModelBundleMetadata:
    target_spec = ModelTargetSpec(
        name="future_mid_return",
        target_type="regression",
        horizon="5m",
        parameters={"label_source": "mid_price", "lookahead_steps": 5},
    )
    resolved_hash = schema_hash or compute_feature_contract_hash(
        feature_columns,
        target_spec=target_spec,
    )
    return ModelBundleMetadata(
        model_id="eurusd-transformer-20260503",
        model_type="transformer_signal",
        trained_at=trained_at or datetime(2026, 5, 3, 12, 30, tzinfo=UTC),
        feature_columns=feature_columns,
        target_spec=target_spec,
        scaler_artifact=ModelBundleArtifact(
            name="standard-scaler",
            path="artifacts/eurusd-transformer/scaler.json",
            sha256="a" * 64,
        ),
        model_artifact=ModelBundleArtifact(
            name="signal-model",
            path="artifacts/eurusd-transformer/model.pt",
            sha256="b" * 64,
        ),
        schema_hash=resolved_hash,
        metrics={"validation_mae": 0.00012, "walk_forward_accuracy": 0.54},
        training_data=TrainingDataWindow(
            dataset_id="eurusd-m1-2026q1",
            symbols=("EURUSD",),
            window_start=datetime(2026, 1, 1, tzinfo=UTC),
            window_end=datetime(2026, 3, 31, 23, 59, tzinfo=UTC),
            row_count=12345,
            metadata={"bar_type": "m1", "broker": "dukascopy-demo"},
        ),
        metadata={"git_commit": "abc123", "paper_mode_default": True},
    )
