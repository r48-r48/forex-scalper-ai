"""Deterministic offline trading environment for policy training."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any

import numpy as np
import pandas as pd

from scalper_ai.rl.config import TradingEnvironmentConfig


class TradingAction(IntEnum):
    """Discrete target-position actions."""

    SHORT = -1
    FLAT = 0
    LONG = 1


@dataclass(frozen=True)
class TradingObservation:
    """Observation emitted by the trading environment."""

    values: np.ndarray
    timestamp: pd.Timestamp
    price: float
    position: float


@dataclass(frozen=True)
class TradingStepResult:
    """One environment transition result."""

    observation: TradingObservation
    reward: float
    done: bool
    info: dict[str, Any]


class TradingEnvironment:
    """Step-based trading environment with explicit cost accounting."""

    def __init__(self, market_frame: pd.DataFrame, *, config: TradingEnvironmentConfig | None = None) -> None:
        self._config = config or TradingEnvironmentConfig()
        self._market_frame = _prepare_market_frame(market_frame, config=self._config)
        self._feature_columns = _resolve_feature_columns(self._market_frame, config=self._config)
        self._current_index = 0
        self._position = 0.0
        self._cumulative_reward = 0.0
        self._cumulative_pnl = 0.0
        self._done = False

    @property
    def feature_columns(self) -> tuple[str, ...]:
        """Return the observation feature columns."""

        return self._feature_columns

    @property
    def action_count(self) -> int:
        """Return the number of discrete actions."""

        return len(TradingAction)

    @property
    def current_position(self) -> float:
        """Return the current position in environment units."""

        return self._position

    @property
    def cumulative_reward(self) -> float:
        """Return cumulative scaled reward."""

        return self._cumulative_reward

    @property
    def cumulative_pnl(self) -> float:
        """Return cumulative raw pnl net of costs."""

        return self._cumulative_pnl

    def reset(self) -> TradingObservation:
        """Reset the episode to the first available state."""

        self._current_index = 0
        self._position = 0.0
        self._cumulative_reward = 0.0
        self._cumulative_pnl = 0.0
        self._done = False
        return self._build_observation(self._current_index)

    def step(self, action: TradingAction | int) -> TradingStepResult:
        """Advance the environment one step using the selected action."""

        if self._done:
            raise RuntimeError("Cannot step a finished environment. Call reset() first.")

        current_row = self._market_frame.iloc[self._current_index]
        next_index = self._current_index + 1
        next_row = self._market_frame.iloc[next_index]

        target_position = float(self._resolve_action(action)) * self._config.position_size
        traded_units = target_position - self._position

        current_price = float(current_row[self._config.price_column])
        next_price = float(next_row[self._config.price_column])

        spread_cost = abs(traded_units) * current_price * (self._config.spread_bps / 10_000.0)
        slippage_cost = abs(traded_units) * current_price * (self._config.slippage_bps / 10_000.0)
        holding_cost = abs(target_position) * current_price * (self._config.holding_cost_bps / 10_000.0)
        price_pnl = target_position * (next_price - current_price)
        net_pnl = price_pnl - spread_cost - slippage_cost - holding_cost
        reward = net_pnl * self._config.reward_scale

        self._position = target_position
        self._current_index = next_index
        self._cumulative_pnl += net_pnl
        self._cumulative_reward += reward
        self._done = self._current_index >= len(self._market_frame) - 1

        info = {
            "timestamp": pd.Timestamp(current_row[self._config.timestamp_column]),
            "next_timestamp": pd.Timestamp(next_row[self._config.timestamp_column]),
            "action": int(self._resolve_action(action)),
            "target_position": target_position,
            "traded_units": traded_units,
            "price_pnl": price_pnl,
            "spread_cost": spread_cost,
            "slippage_cost": slippage_cost,
            "holding_cost": holding_cost,
            "net_pnl": net_pnl,
            "cumulative_pnl": self._cumulative_pnl,
            "cumulative_reward": self._cumulative_reward,
        }
        observation = self._build_observation(self._current_index)
        return TradingStepResult(observation=observation, reward=reward, done=self._done, info=info)

    def _build_observation(self, index: int) -> TradingObservation:
        row = self._market_frame.iloc[index]
        feature_values = row.loc[list(self._feature_columns)].to_numpy(dtype=np.float32, copy=True)
        if self._config.include_position_in_observation:
            values = np.concatenate([feature_values, np.asarray([self._position], dtype=np.float32)])
        else:
            values = feature_values
        return TradingObservation(
            values=values,
            timestamp=pd.Timestamp(row[self._config.timestamp_column]),
            price=float(row[self._config.price_column]),
            position=self._position,
        )

    @staticmethod
    def _resolve_action(action: TradingAction | int) -> TradingAction:
        if isinstance(action, TradingAction):
            return action
        return TradingAction(int(action))


def _prepare_market_frame(
    market_frame: pd.DataFrame,
    *,
    config: TradingEnvironmentConfig,
) -> pd.DataFrame:
    required_columns = {config.price_column, config.timestamp_column}
    missing = required_columns.difference(market_frame.columns)
    if missing:
        raise ValueError(f"Market frame is missing required columns: {', '.join(sorted(missing))}")
    if len(market_frame) < 2:
        raise ValueError("TradingEnvironment requires at least two rows of market data.")

    frame = market_frame.copy()
    frame.sort_values(by=[config.timestamp_column], inplace=True, kind="stable")
    frame.reset_index(drop=True, inplace=True)
    timestamps = pd.to_datetime(frame[config.timestamp_column], utc=True)
    if timestamps.isna().any():
        raise ValueError("timestamp_column contains invalid timestamps.")
    frame[config.timestamp_column] = timestamps
    frame[config.price_column] = frame[config.price_column].astype(float)
    return frame


def _resolve_feature_columns(
    market_frame: pd.DataFrame,
    *,
    config: TradingEnvironmentConfig,
) -> tuple[str, ...]:
    if config.feature_columns is not None:
        missing = [column for column in config.feature_columns if column not in market_frame.columns]
        if missing:
            raise ValueError(f"Market frame is missing configured feature columns: {', '.join(missing)}")
        return tuple(config.feature_columns)

    excluded = {config.timestamp_column}
    columns = [str(column) for column in market_frame.columns if column not in excluded]
    if config.price_column in columns:
        columns.remove(config.price_column)
    columns.insert(0, config.price_column)
    return tuple(columns)
