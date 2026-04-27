"""Load application configuration from YAML and environment variables."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
import json
import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ModuleNotFoundError:  # pragma: no cover - exercised only in reduced local environments
    BaseSettings = None
    SettingsConfigDict = dict

from scalper_ai.config.models import AppConfig
from scalper_ai.utils.paths import resolve_repo_root


_ENV_FIELD_ALIASES = {
    "env": "ENV",
    "config_dir": "CONFIG_DIR",
    "log_level": "LOG_LEVEL",
    "log_json": "LOG_JSON",
    "runtime_mode": "RUNTIME_MODE",
    "paper_trading_default": "PAPER_TRADING_DEFAULT",
    "redis_host": "REDIS_HOST",
    "redis_port": "REDIS_PORT",
    "redis_db": "REDIS_DB",
    "max_position_size": "MAX_POSITION_SIZE",
    "max_daily_drawdown": "MAX_DAILY_DRAWDOWN",
    "kill_switch_enabled": "KILL_SWITCH_ENABLED",
    "data_raw_dir": "DATA_RAW_DIR",
    "data_processed_dir": "DATA_PROCESSED_DIR",
    "data_artifacts_dir": "DATA_ARTIFACTS_DIR",
    "broker_live_enabled": "BROKER_LIVE_ENABLED",
    "broker_live_adapter": "BROKER_LIVE_ADAPTER",
    "broker_allow_live_without_kill_switch": "BROKER_ALLOW_LIVE_WITHOUT_KILL_SWITCH",
    "broker_mt5_terminal_path": "BROKER_MT5_TERMINAL_PATH",
    "broker_mt5_login": "BROKER_MT5_LOGIN",
    "broker_mt5_password": "BROKER_MT5_PASSWORD",
    "broker_mt5_server": "BROKER_MT5_SERVER",
    "broker_mt5_timeout_milliseconds": "BROKER_MT5_TIMEOUT_MILLISECONDS",
    "broker_mt5_magic_number": "BROKER_MT5_MAGIC_NUMBER",
    "broker_mt5_deviation_points": "BROKER_MT5_DEVIATION_POINTS",
    "broker_mt5_base_units_per_lot": "BROKER_MT5_BASE_UNITS_PER_LOT",
    "broker_mt5_min_volume_lots": "BROKER_MT5_MIN_VOLUME_LOTS",
    "broker_mt5_volume_step_lots": "BROKER_MT5_VOLUME_STEP_LOTS",
    "broker_mt5_history_lookback_hours": "BROKER_MT5_HISTORY_LOOKBACK_HOURS",
    "broker_mt5_account_mode": "BROKER_MT5_ACCOUNT_MODE",
    "broker_mt5_order_comment_prefix": "BROKER_MT5_ORDER_COMMENT_PREFIX",
    "broker_mt5_symbol_map_json": "BROKER_MT5_SYMBOL_MAP_JSON",
    "monitoring_health_enabled": "MONITORING_HEALTH_ENABLED",
    "monitoring_metrics_enabled": "MONITORING_METRICS_ENABLED",
    "monitoring_service_name": "MONITORING_SERVICE_NAME",
    "deployment_create_directories_on_startup": "DEPLOYMENT_CREATE_DIRECTORIES_ON_STARTUP",
    "deployment_fallback_to_paper_on_live_failure": "DEPLOYMENT_FALLBACK_TO_PAPER_ON_LIVE_FAILURE",
    "deployment_require_live_confirmation": "DEPLOYMENT_REQUIRE_LIVE_CONFIRMATION",
    "deployment_live_confirmation_phrase": "DEPLOYMENT_LIVE_CONFIRMATION_PHRASE",
}


def _load_env_override_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for alias in _ENV_FIELD_ALIASES.values():
        env_key = f"SCALPER_AI_{alias}"
        if env_key in os.environ:
            payload[alias] = os.environ[env_key]
    return payload


class _EnvOverridesMixin:
    env: Optional[str] = Field(default=None, alias="ENV")
    config_dir: Optional[Path] = Field(default=None, alias="CONFIG_DIR")
    log_level: Optional[str] = Field(default=None, alias="LOG_LEVEL")
    log_json: Optional[bool] = Field(default=None, alias="LOG_JSON")
    runtime_mode: Optional[str] = Field(default=None, alias="RUNTIME_MODE")
    paper_trading_default: Optional[bool] = Field(default=None, alias="PAPER_TRADING_DEFAULT")
    redis_host: Optional[str] = Field(default=None, alias="REDIS_HOST")
    redis_port: Optional[int] = Field(default=None, alias="REDIS_PORT")
    redis_db: Optional[int] = Field(default=None, alias="REDIS_DB")
    max_position_size: Optional[float] = Field(default=None, alias="MAX_POSITION_SIZE")
    max_daily_drawdown: Optional[float] = Field(default=None, alias="MAX_DAILY_DRAWDOWN")
    kill_switch_enabled: Optional[bool] = Field(default=None, alias="KILL_SWITCH_ENABLED")
    data_raw_dir: Optional[Path] = Field(default=None, alias="DATA_RAW_DIR")
    data_processed_dir: Optional[Path] = Field(default=None, alias="DATA_PROCESSED_DIR")
    data_artifacts_dir: Optional[Path] = Field(default=None, alias="DATA_ARTIFACTS_DIR")
    broker_live_enabled: Optional[bool] = Field(default=None, alias="BROKER_LIVE_ENABLED")
    broker_live_adapter: Optional[str] = Field(default=None, alias="BROKER_LIVE_ADAPTER")
    broker_allow_live_without_kill_switch: Optional[bool] = Field(
        default=None,
        alias="BROKER_ALLOW_LIVE_WITHOUT_KILL_SWITCH",
    )
    broker_mt5_terminal_path: Optional[Path] = Field(default=None, alias="BROKER_MT5_TERMINAL_PATH")
    broker_mt5_login: Optional[int] = Field(default=None, alias="BROKER_MT5_LOGIN")
    broker_mt5_password: Optional[str] = Field(default=None, alias="BROKER_MT5_PASSWORD")
    broker_mt5_server: Optional[str] = Field(default=None, alias="BROKER_MT5_SERVER")
    broker_mt5_timeout_milliseconds: Optional[int] = Field(
        default=None,
        alias="BROKER_MT5_TIMEOUT_MILLISECONDS",
    )
    broker_mt5_magic_number: Optional[int] = Field(default=None, alias="BROKER_MT5_MAGIC_NUMBER")
    broker_mt5_deviation_points: Optional[int] = Field(default=None, alias="BROKER_MT5_DEVIATION_POINTS")
    broker_mt5_base_units_per_lot: Optional[float] = Field(
        default=None,
        alias="BROKER_MT5_BASE_UNITS_PER_LOT",
    )
    broker_mt5_min_volume_lots: Optional[float] = Field(default=None, alias="BROKER_MT5_MIN_VOLUME_LOTS")
    broker_mt5_volume_step_lots: Optional[float] = Field(
        default=None,
        alias="BROKER_MT5_VOLUME_STEP_LOTS",
    )
    broker_mt5_history_lookback_hours: Optional[int] = Field(
        default=None,
        alias="BROKER_MT5_HISTORY_LOOKBACK_HOURS",
    )
    broker_mt5_account_mode: Optional[str] = Field(default=None, alias="BROKER_MT5_ACCOUNT_MODE")
    broker_mt5_order_comment_prefix: Optional[str] = Field(
        default=None,
        alias="BROKER_MT5_ORDER_COMMENT_PREFIX",
    )
    broker_mt5_symbol_map_json: Optional[str] = Field(default=None, alias="BROKER_MT5_SYMBOL_MAP_JSON")
    monitoring_health_enabled: Optional[bool] = Field(default=None, alias="MONITORING_HEALTH_ENABLED")
    monitoring_metrics_enabled: Optional[bool] = Field(default=None, alias="MONITORING_METRICS_ENABLED")
    monitoring_service_name: Optional[str] = Field(default=None, alias="MONITORING_SERVICE_NAME")
    deployment_create_directories_on_startup: Optional[bool] = Field(
        default=None,
        alias="DEPLOYMENT_CREATE_DIRECTORIES_ON_STARTUP",
    )
    deployment_fallback_to_paper_on_live_failure: Optional[bool] = Field(
        default=None,
        alias="DEPLOYMENT_FALLBACK_TO_PAPER_ON_LIVE_FAILURE",
    )
    deployment_require_live_confirmation: Optional[bool] = Field(
        default=None,
        alias="DEPLOYMENT_REQUIRE_LIVE_CONFIRMATION",
    )
    deployment_live_confirmation_phrase: Optional[str] = Field(
        default=None,
        alias="DEPLOYMENT_LIVE_CONFIRMATION_PHRASE",
    )

    @classmethod
    def load(cls) -> "_EnvOverridesMixin":
        return cls()  # type: ignore[call-arg]

    def to_nested_dict(self) -> dict[str, Any]:
        """Convert flat environment fields into config tree patches."""

        overrides: dict[str, Any] = {}

        if self.log_level is not None:
            overrides.setdefault("logging", {})["level"] = self.log_level
        if self.log_json is not None:
            overrides.setdefault("logging", {})["json"] = self.log_json
        if self.runtime_mode is not None:
            overrides.setdefault("runtime", {})["mode"] = self.runtime_mode
        if self.paper_trading_default is not None:
            overrides.setdefault("runtime", {})["paper_trading_default"] = self.paper_trading_default
        if self.redis_host is not None:
            overrides.setdefault("redis", {})["host"] = self.redis_host
        if self.redis_port is not None:
            overrides.setdefault("redis", {})["port"] = self.redis_port
        if self.redis_db is not None:
            overrides.setdefault("redis", {})["db"] = self.redis_db
        if self.max_position_size is not None:
            overrides.setdefault("risk", {})["max_position_size"] = self.max_position_size
        if self.max_daily_drawdown is not None:
            overrides.setdefault("risk", {})["max_daily_drawdown"] = self.max_daily_drawdown
        if self.kill_switch_enabled is not None:
            overrides.setdefault("risk", {})["kill_switch_enabled"] = self.kill_switch_enabled
        if self.data_raw_dir is not None:
            overrides.setdefault("directories", {})["raw_dir"] = self.data_raw_dir
        if self.data_processed_dir is not None:
            overrides.setdefault("directories", {})["processed_dir"] = self.data_processed_dir
        if self.data_artifacts_dir is not None:
            overrides.setdefault("directories", {})["artifacts_dir"] = self.data_artifacts_dir
        if self.broker_live_enabled is not None:
            overrides.setdefault("broker", {})["live_enabled"] = self.broker_live_enabled
        if self.broker_live_adapter is not None:
            overrides.setdefault("broker", {})["live_adapter"] = self.broker_live_adapter
        if self.broker_allow_live_without_kill_switch is not None:
            overrides.setdefault("broker", {})[
                "allow_live_without_kill_switch"
            ] = self.broker_allow_live_without_kill_switch
        mt5_overrides = overrides.setdefault("broker", {}).setdefault("mt5", {})
        if self.broker_mt5_terminal_path is not None:
            mt5_overrides["terminal_path"] = self.broker_mt5_terminal_path
        if self.broker_mt5_login is not None:
            mt5_overrides["login"] = self.broker_mt5_login
        if self.broker_mt5_password is not None:
            mt5_overrides["password"] = self.broker_mt5_password
        if self.broker_mt5_server is not None:
            mt5_overrides["server"] = self.broker_mt5_server
        if self.broker_mt5_timeout_milliseconds is not None:
            mt5_overrides["timeout_milliseconds"] = self.broker_mt5_timeout_milliseconds
        if self.broker_mt5_magic_number is not None:
            mt5_overrides["magic_number"] = self.broker_mt5_magic_number
        if self.broker_mt5_deviation_points is not None:
            mt5_overrides["deviation_points"] = self.broker_mt5_deviation_points
        if self.broker_mt5_base_units_per_lot is not None:
            mt5_overrides["base_units_per_lot"] = self.broker_mt5_base_units_per_lot
        if self.broker_mt5_min_volume_lots is not None:
            mt5_overrides["min_volume_lots"] = self.broker_mt5_min_volume_lots
        if self.broker_mt5_volume_step_lots is not None:
            mt5_overrides["volume_step_lots"] = self.broker_mt5_volume_step_lots
        if self.broker_mt5_history_lookback_hours is not None:
            mt5_overrides["history_lookback_hours"] = self.broker_mt5_history_lookback_hours
        if self.broker_mt5_account_mode is not None:
            mt5_overrides["account_mode"] = self.broker_mt5_account_mode
        if self.broker_mt5_order_comment_prefix is not None:
            mt5_overrides["order_comment_prefix"] = self.broker_mt5_order_comment_prefix
        if self.broker_mt5_symbol_map_json is not None:
            mt5_overrides["symbol_map"] = json.loads(self.broker_mt5_symbol_map_json)
        if not mt5_overrides:
            overrides["broker"].pop("mt5", None)
        if self.monitoring_health_enabled is not None:
            overrides.setdefault("monitoring", {})["health_enabled"] = self.monitoring_health_enabled
        if self.monitoring_metrics_enabled is not None:
            overrides.setdefault("monitoring", {})["metrics_enabled"] = self.monitoring_metrics_enabled
        if self.monitoring_service_name is not None:
            overrides.setdefault("monitoring", {})["service_name"] = self.monitoring_service_name
        if self.deployment_create_directories_on_startup is not None:
            overrides.setdefault("deployment", {})[
                "create_directories_on_startup"
            ] = self.deployment_create_directories_on_startup
        if self.deployment_fallback_to_paper_on_live_failure is not None:
            overrides.setdefault("deployment", {})[
                "fallback_to_paper_on_live_failure"
            ] = self.deployment_fallback_to_paper_on_live_failure
        if self.deployment_require_live_confirmation is not None:
            overrides.setdefault("deployment", {})[
                "require_live_confirmation"
            ] = self.deployment_require_live_confirmation
        if self.deployment_live_confirmation_phrase is not None:
            overrides.setdefault("deployment", {})[
                "live_confirmation_phrase"
            ] = self.deployment_live_confirmation_phrase

        return overrides


if BaseSettings is not None:

    class EnvOverrides(_EnvOverridesMixin, BaseSettings):
        """Environment variable overrides applied after YAML overlays."""

        model_config = SettingsConfigDict(
            env_prefix="SCALPER_AI_",
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore",
        )

        @classmethod
        def load(cls) -> "EnvOverrides":
            return cls.model_validate(_load_env_override_payload())

else:

    class EnvOverrides(_EnvOverridesMixin, BaseModel):
        """Fallback environment override loader when pydantic-settings is unavailable."""

        model_config = ConfigDict(extra="ignore", populate_by_name=True)

        @classmethod
        def load(cls) -> "EnvOverrides":
            return cls.model_validate(_load_env_override_payload())


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge two dictionaries without mutating inputs."""

    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def load_yaml_file(path: Path) -> dict[str, Any]:
    """Load a YAML file into a dictionary."""

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    if not isinstance(payload, MutableMapping):
        raise ValueError(f"Config file must contain a mapping at the top level: {path}")

    return dict(payload)


def load_app_config(config_name: Optional[str] = None, config_dir: Optional[Path] = None) -> AppConfig:
    """Load the application config from base YAML, overlay YAML, and env overrides."""

    repo_root = resolve_repo_root()
    env_overrides = EnvOverrides.load()

    resolved_config_dir = (config_dir or env_overrides.config_dir or (repo_root / "configs")).resolve()
    selected_config = config_name or env_overrides.env or "research"

    merged: dict[str, Any] = load_yaml_file(resolved_config_dir / "base.yaml")

    if selected_config != "base":
        overlay_path = resolved_config_dir / f"{selected_config}.yaml"
        merged = deep_merge(merged, load_yaml_file(overlay_path))

    merged = deep_merge(merged, env_overrides.to_nested_dict())
    return AppConfig.model_validate(merged)
