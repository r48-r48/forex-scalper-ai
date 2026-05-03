"""Unit tests for preprocessing helpers including fractional differentiation."""

from __future__ import annotations

import math

import numpy as np

from scalper_ai.data.preprocessing import fractional_diff_weights, fractional_differentiate


def test_fractional_diff_weights_start_with_one_and_truncate() -> None:
    weights = fractional_diff_weights(order=1.0, max_terms=10, threshold=1e-8)

    assert np.allclose(weights, np.asarray([1.0, -1.0]))


def test_fractional_differentiate_returns_first_difference_for_order_one() -> None:
    series = [1.0, 2.0, 4.0, 7.0]

    result = fractional_differentiate(series, order=1.0, threshold=1e-8)

    assert math.isnan(result[0])
    assert np.allclose(result[1:], np.asarray([1.0, 2.0, 3.0]))


def test_fractional_differentiate_preserves_length() -> None:
    series = [1.0, 1.2, 1.1, 1.4, 1.5]

    result = fractional_differentiate(series, order=0.4, threshold=1e-3)

    assert result.shape == (5,)
