"""Utilities for turning lagged dataset rows into sequence tensors."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch

LAGGED_FEATURE_PATTERN = re.compile(r"^lag_(\d+)__(.+)$")


@dataclass(frozen=True)
class SignalModelBatch:
    """Tensor batch contract for supervised signal models."""

    inputs: torch.Tensor
    targets: torch.Tensor | None = None
    feature_names: tuple[str, ...] = ()
    context_length: int = 0


class LaggedFeatureTensorizer:
    """Convert flat lagged feature columns into [batch, time, feature] tensors."""

    def __init__(self, feature_columns: Sequence[str]) -> None:
        if len(feature_columns) == 0:
            raise ValueError("feature_columns must not be empty.")
        self._column_map = _parse_feature_columns(feature_columns)
        self._max_lag = max(lag for lag, _ in self._column_map)
        self._feature_names = _ordered_feature_names(feature_columns, column_map=self._column_map)

    @property
    def context_length(self) -> int:
        """Return the inferred sequence length."""

        return self._max_lag + 1

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Return the stable ordered base feature names."""

        return self._feature_names

    @property
    def input_size(self) -> int:
        """Return the number of features per time step."""

        return len(self._feature_names)

    def transform_frame(self, feature_frame: pd.DataFrame) -> np.ndarray:
        """Return a float32 array shaped [batch, time, feature]."""

        missing = [
            column_name
            for column_name in self._column_map.values()
            if column_name not in feature_frame.columns
        ]
        if missing:
            raise ValueError(
                f"Feature frame is missing required lagged columns: {', '.join(missing)}"
            )

        batch_size = int(len(feature_frame))
        array = np.empty(
            (batch_size, self.context_length, self.input_size),
            dtype=np.float32,
        )
        for lag in range(self._max_lag, -1, -1):
            time_index = self._max_lag - lag
            column_names = [
                self._column_map[(lag, feature_name)] for feature_name in self._feature_names
            ]
            array[:, time_index, :] = feature_frame.loc[:, column_names].to_numpy(
                dtype=np.float32,
                copy=True,
            )
        return array

    def transform_targets(self, targets: Sequence[float] | pd.Series) -> np.ndarray:
        """Return a float32 target array shaped [batch, 1]."""

        target_array = np.asarray(list(targets), dtype=np.float32).reshape(-1, 1)
        return target_array

    def build_batch(
        self,
        feature_frame: pd.DataFrame,
        *,
        targets: Sequence[float] | pd.Series | None = None,
        device: torch.device | str | None = None,
        dtype: torch.dtype = torch.float32,
    ) -> SignalModelBatch:
        """Build a tensor batch from a lagged feature frame."""

        input_array = self.transform_frame(feature_frame)
        inputs = torch.as_tensor(input_array, dtype=dtype, device=device)

        target_tensor: torch.Tensor | None = None
        if targets is not None:
            target_array = self.transform_targets(targets)
            if target_array.shape[0] != input_array.shape[0]:
                raise ValueError("targets length must match feature_frame length.")
            target_tensor = torch.as_tensor(target_array, dtype=dtype, device=device)

        return SignalModelBatch(
            inputs=inputs,
            targets=target_tensor,
            feature_names=self.feature_names,
            context_length=self.context_length,
        )


def _parse_feature_columns(feature_columns: Sequence[str]) -> dict[tuple[int, str], str]:
    column_map: dict[tuple[int, str], str] = {}
    for column_name in feature_columns:
        match = LAGGED_FEATURE_PATTERN.match(column_name)
        if match is None:
            raise ValueError(
                "Lagged feature columns must match the pattern 'lag_<index>__<feature_name>'."
            )
        lag = int(match.group(1))
        feature_name = match.group(2)
        column_map[(lag, feature_name)] = column_name

    feature_names = {feature_name for _, feature_name in column_map}
    max_lag = max(lag for lag, _ in column_map)
    for lag in range(0, max_lag + 1):
        for feature_name in feature_names:
            if (lag, feature_name) not in column_map:
                raise ValueError("Each lag must provide the same base feature set.")
    return column_map


def _ordered_feature_names(
    feature_columns: Sequence[str],
    *,
    column_map: dict[tuple[int, str], str],
) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for column_name in feature_columns:
        match = LAGGED_FEATURE_PATTERN.match(column_name)
        assert match is not None
        feature_name = match.group(2)
        if feature_name not in seen and (0, feature_name) in column_map:
            ordered.append(feature_name)
            seen.add(feature_name)
    return tuple(ordered)
