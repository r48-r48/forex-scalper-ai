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

## Baseline Filter Bundle Layout

`scripts/train_supervised_filter.py` exports the first runtime-loadable bundle shape:

```text
data/artifacts/models/eurusd-filter-20260503/
  metadata.json
  model.json
  scaler.json
  feature_importance.csv
  training-report.json
```

The training command requires either an explicit UTC `--training-end` or a declared
`--input-is-train-only` source. When `--training-end` is provided, rows are filtered by
`available_timestamp` and by the target end timestamp so labels cannot cross the
training cutoff.

## Transformer Bundle Layout

`scripts/train_transformer.py` exports the runtime-loadable transformer bundle shape:

```text
data/artifacts/models/eurusd-transformer-20260503/
  metadata.json
  model.pt
  scaler.json
  training-report.json
```

The command trains on the selected training window only, fits feature mean/scale
preprocessing on the fit rows, and uses a tail validation split inside the selected
window. `model.pt` stores the transformer config plus `state_dict`; `scaler.json`
stores ordered feature means and scales. The runtime loader verifies both artifacts by
SHA-256 when hashes are present, then reconstructs `TransformerSignalModel`,
`LaggedFeatureTensorizer`, and preprocessing without reaching into broker/live code.

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

## Runtime Loading

```python
from pathlib import Path

import pandas as pd

from scalper_ai.models import (
    load_baseline_filter_inference_package,
    load_transformer_inference_package,
)

package = load_baseline_filter_inference_package(
    Path("data/artifacts/models/eurusd-filter-20260503"),
)
feature_frame = pd.DataFrame(...)
predictions = package.predict_frame(feature_frame)
latest_signal = package.predict_latest(feature_frame, symbol="EURUSD")

transformer_package = load_transformer_inference_package(
    Path("data/artifacts/models/eurusd-transformer-20260503"),
)
scores = transformer_package.score_frame(feature_frame)
```

The runtime loader verifies referenced artifact existence, SHA-256 digests when
present, `model_type`, ordered feature columns, transformer dimensions, and scaler
contract before scoring. It does not submit orders and does not reach into live
adapters; order routing remains the deployment runtime's responsibility.
