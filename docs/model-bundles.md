# Model Bundles

## Purpose

Model bundle metadata is the production sidecar contract for promoted supervised
models. It records which model artifact, scaler artifact, ordered feature columns,
target definition, training window, and validation metrics belong together.

The implementation lives in `src/scalper_ai/models/bundle.py` and intentionally uses
only the standard library.

## Contract

A bundle metadata JSON file contains:

- `bundle_format_version`: current metadata contract version, currently `1`
- `model_id`: stable identifier for the trained model
- `model_type`: implementation family, for example `transformer_signal`
- `trained_at`: timezone-aware UTC timestamp serialized with `Z`
- `feature_columns`: non-empty ordered feature list used by inference
- `target_spec`: target name, type, horizon, and JSON-safe parameters
- `scaler_artifact`: scaler `name`, relative or absolute `path`, and optional SHA-256
- `model_artifact`: model `name`, relative or absolute `path`, and optional SHA-256
- `schema_hash`: deterministic SHA-256 feature contract hash
- `hash_algorithm`: currently `sha256`
- `metrics`: finite numeric training or validation metrics
- `training_data`: dataset id, symbols, UTC window, row count, and JSON-safe metadata
- `metadata`: optional JSON-safe provenance such as git commit, config name, or run id

The `schema_hash` is computed from the ordered `feature_columns` plus `target_spec`.
Feature order is part of the contract because tensorized model inputs depend on it.
Equivalent JSON key ordering inside `target_spec.parameters` produces the same hash.

## Validation Rules

The contract rejects:

- naive timestamps
- empty model ids, artifact names, paths, targets, symbols, or feature columns
- duplicate feature columns or symbols
- non-finite metrics or numeric metadata values
- mismatched `schema_hash`
- non-JSON-safe metadata payloads

Timezone-aware non-UTC timestamps are normalized to UTC before serialization.

## Usage

```python
from datetime import UTC, datetime
from pathlib import Path

from scalper_ai.models import (
    ModelBundleArtifact,
    ModelBundleMetadata,
    ModelTargetSpec,
    TrainingDataWindow,
    compute_feature_contract_hash,
    load_model_bundle_metadata,
    save_model_bundle_metadata,
)

target_spec = ModelTargetSpec(
    name="future_mid_return",
    target_type="regression",
    horizon="5m",
    parameters={"lookahead_steps": 5},
)
feature_columns = ("lag_000__spread_bps", "lag_000__mid_return")

metadata = ModelBundleMetadata(
    model_id="eurusd-transformer-20260503",
    model_type="transformer_signal",
    trained_at=datetime(2026, 5, 3, 12, 30, tzinfo=UTC),
    feature_columns=feature_columns,
    target_spec=target_spec,
    scaler_artifact=ModelBundleArtifact(
        name="standard-scaler",
        path="data/artifacts/models/eurusd-transformer/scaler.json",
    ),
    model_artifact=ModelBundleArtifact(
        name="signal-model",
        path="data/artifacts/models/eurusd-transformer/model.pt",
    ),
    schema_hash=compute_feature_contract_hash(feature_columns, target_spec=target_spec),
    metrics={"walk_forward_accuracy": 0.54, "validation_mae": 0.00012},
    training_data=TrainingDataWindow(
        dataset_id="eurusd-m1-2026q1",
        symbols=("EURUSD",),
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2026, 3, 31, 23, 59, tzinfo=UTC),
        row_count=12345,
        metadata={"bar_type": "m1"},
    ),
)

path = save_model_bundle_metadata(
    metadata,
    Path("data/artifacts/models/eurusd-transformer/metadata.json"),
)
restored = load_model_bundle_metadata(path)
```

`save_model_bundle_metadata()` writes a temporary file in the same directory, fsyncs
it, and replaces the destination path. This is sufficient for local repository
artifact work; a later registry or object-store layer can wrap the same contract.
