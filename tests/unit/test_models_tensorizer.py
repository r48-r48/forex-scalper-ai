"""Unit tests for lagged feature tensorization."""

from __future__ import annotations

import pandas as pd
import pytest

from scalper_ai.models.tensorizer import LaggedFeatureTensorizer


def test_tensorizer_builds_oldest_to_newest_sequences() -> None:
    frame = pd.DataFrame(
        {
            "lag_000__spread": [0.3],
            "lag_000__mid_return": [3.0],
            "lag_001__spread": [0.2],
            "lag_001__mid_return": [2.0],
            "lag_002__spread": [0.1],
            "lag_002__mid_return": [1.0],
        }
    )
    tensorizer = LaggedFeatureTensorizer(frame.columns)

    array = tensorizer.transform_frame(frame)

    assert array.shape == (1, 3, 2)
    assert array[0, 0, :].tolist() == pytest.approx([0.1, 1.0])
    assert array[0, 2, :].tolist() == pytest.approx([0.3, 3.0])


def test_tensorizer_build_batch_shapes_targets_as_column_vector() -> None:
    frame = pd.DataFrame(
        {
            "lag_000__spread": [0.3, 0.4],
            "lag_001__spread": [0.2, 0.3],
        }
    )
    tensorizer = LaggedFeatureTensorizer(frame.columns)

    batch = tensorizer.build_batch(frame, targets=[1.0, -1.0])

    assert batch.inputs.shape == (2, 2, 1)
    assert batch.targets is not None
    assert batch.targets.shape == (2, 1)
