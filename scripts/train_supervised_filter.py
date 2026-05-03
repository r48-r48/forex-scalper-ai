"""Train and export a promoted supervised baseline filter bundle."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
SRC_ROOT = PROJECT_ROOT / "src"
for import_path in (SCRIPT_ROOT, SRC_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from cli_utils import dataframe_records, load_frame, write_frame, write_json
from run_walk_forward import supervised_dataset_from_frame

from scalper_ai.data.datasets import SupervisedDataset
from scalper_ai.data.labels import (
    TARGET_COLUMN,
    TARGET_END_EVENT_TIMESTAMP_COLUMN,
    TARGET_END_TIMESTAMP_COLUMN,
)
from scalper_ai.models import (
    BASELINE_FILTER_MODEL_TYPE,
    ModelBundleArtifact,
    ModelBundleMetadata,
    ModelTargetSpec,
    SupervisedBaselineFilterConfig,
    SupervisedBaselineFilterModel,
    TrainingDataWindow,
    compute_feature_contract_hash,
    fit_supervised_baseline_filter,
    hash_file_sha256,
    save_model_bundle_metadata,
    save_supervised_baseline_filter_model,
    target_directions,
)


def train_supervised_filter_cli(
    *,
    input_path: Path,
    output_dir: Path,
    model_id: str,
    training_start: str | datetime | pd.Timestamp | None = None,
    training_end: str | datetime | pd.Timestamp | None = None,
    input_is_train_only: bool = False,
    dataset_id: str | None = None,
    target_horizon: str = "unknown",
    target_threshold: float = 0.0,
    score_threshold: float = 0.0,
    min_scale: float = 1e-12,
    top_features_limit: int = 20,
    report_output_path: Path | None = None,
    feature_importance_output_path: Path | None = None,
    compression: str = "zstd",
) -> dict[str, object]:
    """Fit on an explicit training window and export a runtime-loadable bundle."""

    if not model_id.strip():
        raise ValueError("model_id must be non-empty.")
    if top_features_limit <= 0:
        raise ValueError("top_features_limit must be greater than zero.")

    dataset_frame = load_frame(input_path)
    training_frame = _select_training_frame(
        dataset_frame,
        training_start=training_start,
        training_end=training_end,
        input_is_train_only=input_is_train_only,
    )
    dataset = supervised_dataset_from_frame(training_frame)
    model_config = SupervisedBaselineFilterConfig(
        target_threshold=target_threshold,
        score_threshold=score_threshold,
        min_scale=min_scale,
    )
    model = fit_supervised_baseline_filter(dataset, config=model_config)
    metrics = _training_metrics(dataset=dataset, model=model, config=model_config)

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model.json"
    scaler_path = output_dir / "scaler.json"
    metadata_path = output_dir / "metadata.json"
    resolved_feature_importance_path = (
        feature_importance_output_path or output_dir / "feature_importance.csv"
    )
    resolved_report_path = report_output_path or output_dir / "training-report.json"

    save_supervised_baseline_filter_model(model, model_path)
    write_json(_scaler_payload(model), scaler_path)
    feature_importance = model.feature_importance()
    write_frame(
        feature_importance,
        resolved_feature_importance_path,
        compression=compression,
    )

    target_spec = ModelTargetSpec(
        name=TARGET_COLUMN,
        target_type="directional_regression_filter",
        horizon=target_horizon,
        parameters={
            "target_threshold": target_threshold,
            "score_threshold": score_threshold,
            "min_scale": min_scale,
        },
    )
    metadata = _build_bundle_metadata(
        model_id=model_id.strip(),
        dataset=dataset,
        input_path=input_path,
        input_rows=len(dataset_frame),
        input_is_train_only=input_is_train_only,
        training_start=training_start,
        training_end=training_end,
        dataset_id=dataset_id or input_path.stem,
        target_spec=target_spec,
        model_artifact=ModelBundleArtifact(
            name="supervised-baseline-filter-model",
            path=model_path.name,
            sha256=hash_file_sha256(model_path),
        ),
        scaler_artifact=ModelBundleArtifact(
            name="supervised-baseline-filter-scaler",
            path=scaler_path.name,
            sha256=hash_file_sha256(scaler_path),
        ),
        metrics=metrics,
    )
    save_model_bundle_metadata(metadata, metadata_path)

    payload: dict[str, object] = {
        "input_path": str(input_path),
        "input_rows": len(dataset_frame),
        "training_rows": len(dataset),
        "excluded_rows": len(dataset_frame) - len(training_frame),
        "model_id": metadata.model_id,
        "model_type": metadata.model_type,
        "output_dir": str(output_dir),
        "metadata_path": str(metadata_path),
        "model_path": str(model_path),
        "scaler_path": str(scaler_path),
        "feature_importance_path": str(resolved_feature_importance_path),
        "feature_count": len(dataset.feature_columns),
        "feature_columns": list(dataset.feature_columns),
        "target_spec": target_spec.to_dict(),
        "model_config": asdict(model_config),
        "metrics": metrics,
        "training_data": metadata.training_data.to_dict(),
        "schema_hash": metadata.schema_hash,
        "top_features": dataframe_records(feature_importance.head(top_features_limit)),
        "leakage_controls": {
            "input_is_train_only": input_is_train_only,
            "training_start": _optional_timestamp_text(training_start),
            "training_end": _optional_timestamp_text(training_end),
            "target_end_cutoff_enforced": (
                training_end is not None and _target_end_cutoff_column(dataset_frame) is not None
            ),
        },
    }
    write_json(payload, resolved_report_path)
    return payload


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Train and export a runtime-loadable supervised filter bundle.",
    )
    parser.add_argument("--input-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--training-start", default=None)
    parser.add_argument("--training-end", default=None)
    parser.add_argument(
        "--input-is-train-only",
        action="store_true",
        help="Treat the entire input as curated training-only data.",
    )
    parser.add_argument("--dataset-id", default=None)
    parser.add_argument("--target-horizon", default="unknown")
    parser.add_argument("--target-threshold", type=float, default=0.0)
    parser.add_argument("--score-threshold", type=float, default=0.0)
    parser.add_argument("--min-scale", type=float, default=1e-12)
    parser.add_argument("--top-features-limit", type=int, default=20)
    parser.add_argument("--report-output-path", type=Path, default=None)
    parser.add_argument("--feature-importance-output-path", type=Path, default=None)
    parser.add_argument(
        "--compression",
        default="zstd",
        help="Parquet compression codec, or 'none'. Ignored for CSV output.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""

    args = parse_args()
    payload = train_supervised_filter_cli(
        input_path=args.input_path,
        output_dir=args.output_dir,
        model_id=args.model_id,
        training_start=args.training_start,
        training_end=args.training_end,
        input_is_train_only=args.input_is_train_only,
        dataset_id=args.dataset_id,
        target_horizon=args.target_horizon,
        target_threshold=args.target_threshold,
        score_threshold=args.score_threshold,
        min_scale=args.min_scale,
        top_features_limit=args.top_features_limit,
        report_output_path=args.report_output_path,
        feature_importance_output_path=args.feature_importance_output_path,
        compression=args.compression,
    )
    print(write_json(payload, None))


def _select_training_frame(
    frame: pd.DataFrame,
    *,
    training_start: str | datetime | pd.Timestamp | None,
    training_end: str | datetime | pd.Timestamp | None,
    input_is_train_only: bool,
) -> pd.DataFrame:
    if training_end is None and not input_is_train_only:
        raise ValueError(
            "Provide training_end or pass input_is_train_only for an already curated "
            "training-only dataset."
        )

    start_timestamp = _parse_optional_utc_timestamp(training_start, "training_start")
    end_timestamp = _parse_optional_utc_timestamp(training_end, "training_end")
    if (
        start_timestamp is not None
        and end_timestamp is not None
        and start_timestamp > end_timestamp
    ):
        raise ValueError("training_start must not be after training_end.")

    anchor_column = (
        "available_timestamp" if "available_timestamp" in frame.columns else "event_timestamp"
    )
    mask = pd.Series(True, index=frame.index)
    if start_timestamp is not None:
        mask &= pd.to_datetime(frame[anchor_column], utc=True) >= start_timestamp
    if end_timestamp is not None:
        mask &= pd.to_datetime(frame[anchor_column], utc=True) <= end_timestamp
        target_end_column = _target_end_cutoff_column(frame)
        if target_end_column is None:
            raise ValueError(
                "training_end requires target_end_timestamp or "
                "target_end_event_timestamp so labels cannot cross the cutoff."
            )
        mask &= pd.to_datetime(frame[target_end_column], utc=True) <= end_timestamp

    training_frame = frame.loc[mask].reset_index(drop=True)
    if training_frame.empty:
        raise ValueError("Training selection produced no rows.")
    return training_frame


def _training_metrics(
    *,
    dataset: SupervisedDataset,
    model: SupervisedBaselineFilterModel,
    config: SupervisedBaselineFilterConfig,
) -> dict[str, float]:
    predictions = model.predict_frame(dataset.features)
    scores = model.score_frame(dataset.features)
    labels = target_directions(dataset.targets, threshold=config.target_threshold)
    prediction_values = predictions.to_numpy(dtype=int, copy=False)
    evaluated_mask = labels != 0
    evaluated_count = int(evaluated_mask.sum())
    accuracy = (
        float((prediction_values[evaluated_mask] == labels[evaluated_mask]).mean())
        if evaluated_count
        else 0.0
    )
    total_predictions = max(1, len(prediction_values))
    active_predictions = int((prediction_values != 0).sum())
    return {
        "training_directional_accuracy": accuracy,
        "training_coverage": float(active_predictions / total_predictions),
        "training_long_ratio": float((prediction_values == 1).sum() / total_predictions),
        "training_short_ratio": float((prediction_values == -1).sum() / total_predictions),
        "training_neutral_ratio": float((prediction_values == 0).sum() / total_predictions),
        "training_mean_abs_score": float(scores.abs().mean()),
    }


def _build_bundle_metadata(
    *,
    model_id: str,
    dataset: SupervisedDataset,
    input_path: Path,
    input_rows: int,
    input_is_train_only: bool,
    training_start: str | datetime | pd.Timestamp | None,
    training_end: str | datetime | pd.Timestamp | None,
    dataset_id: str,
    target_spec: ModelTargetSpec,
    model_artifact: ModelBundleArtifact,
    scaler_artifact: ModelBundleArtifact,
    metrics: dict[str, float],
) -> ModelBundleMetadata:
    training_window = _training_data_window(
        dataset=dataset,
        dataset_id=dataset_id,
        input_path=input_path,
        input_rows=input_rows,
        input_is_train_only=input_is_train_only,
        training_start=training_start,
        training_end=training_end,
    )
    return ModelBundleMetadata(
        model_id=model_id,
        model_type=BASELINE_FILTER_MODEL_TYPE,
        trained_at=datetime.now(tz=UTC),
        feature_columns=dataset.feature_columns,
        target_spec=target_spec,
        scaler_artifact=scaler_artifact,
        model_artifact=model_artifact,
        schema_hash=compute_feature_contract_hash(
            dataset.feature_columns,
            target_spec=target_spec,
        ),
        metrics=metrics,
        training_data=training_window,
        metadata={
            "source_script": "scripts/train_supervised_filter.py",
            "input_path": str(input_path),
            "input_rows": input_rows,
            "input_is_train_only": input_is_train_only,
            "training_start": _optional_timestamp_text(training_start),
            "training_end": _optional_timestamp_text(training_end),
            "paper_mode_default": True,
        },
    )


def _training_data_window(
    *,
    dataset: SupervisedDataset,
    dataset_id: str,
    input_path: Path,
    input_rows: int,
    input_is_train_only: bool,
    training_start: str | datetime | pd.Timestamp | None,
    training_end: str | datetime | pd.Timestamp | None,
) -> TrainingDataWindow:
    metadata = dataset.metadata
    anchor_timestamps = pd.to_datetime(metadata["available_timestamp"], utc=True)
    target_end_column = _target_end_cutoff_column(metadata)
    end_timestamps = (
        pd.to_datetime(metadata[target_end_column], utc=True)
        if target_end_column is not None
        else anchor_timestamps
    )
    symbols = tuple(sorted(str(symbol) for symbol in metadata["symbol"].unique()))
    return TrainingDataWindow(
        dataset_id=dataset_id,
        symbols=symbols,
        window_start=anchor_timestamps.min().to_pydatetime().astimezone(UTC),
        window_end=end_timestamps.max().to_pydatetime().astimezone(UTC),
        row_count=len(dataset),
        metadata={
            "input_path": str(input_path),
            "input_rows": input_rows,
            "input_is_train_only": input_is_train_only,
            "training_start": _optional_timestamp_text(training_start),
            "training_end": _optional_timestamp_text(training_end),
            "anchor_timestamp_column": "available_timestamp",
            "target_end_timestamp_column": target_end_column,
        },
    )


def _scaler_payload(model: SupervisedBaselineFilterModel) -> dict[str, object]:
    return {
        "scaler_format_version": 1,
        "scaler_type": "standard_mean_scale",
        "feature_columns": list(model.feature_columns),
        "feature_means": list(model.feature_means),
        "feature_scales": list(model.feature_scales),
    }


def _target_end_cutoff_column(frame: pd.DataFrame) -> str | None:
    if TARGET_END_TIMESTAMP_COLUMN in frame.columns:
        return TARGET_END_TIMESTAMP_COLUMN
    if TARGET_END_EVENT_TIMESTAMP_COLUMN in frame.columns:
        return TARGET_END_EVENT_TIMESTAMP_COLUMN
    return None


def _parse_optional_utc_timestamp(
    value: str | datetime | pd.Timestamp | None,
    field_name: str,
) -> pd.Timestamp | None:
    if value is None:
        return None
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError(f"{field_name} must be a valid timestamp.")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
    return timestamp.tz_convert("UTC")


def _optional_timestamp_text(value: str | datetime | pd.Timestamp | None) -> str | None:
    timestamp = _parse_optional_utc_timestamp(value, "timestamp")
    if timestamp is None:
        return None
    return str(timestamp.isoformat()).replace("+00:00", "Z")


if __name__ == "__main__":
    main()
