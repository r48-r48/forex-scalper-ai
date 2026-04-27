"""Typed configuration models for the application bootstrap."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LoggingConfig(BaseModel):
    """Logging behavior for services and scripts."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    level: str = "INFO"
    json_enabled: bool = Field(default=False, alias="json")
    logger_name: str = "scalper_ai"

    @field_validator("level")
    @classmethod
    def normalize_level(cls, value: str) -> str:
        normalized = value.upper()
        supported = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in supported:
            raise ValueError(f"Unsupported log level: {value}")
        return normalized


class RuntimeConfig(BaseModel):
    """Runtime mode and deterministic settings."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["research", "paper", "live"] = "research"
    timezone: str = "UTC"
    seed: int = 42
    paper_trading_default: bool = True

    @field_validator("timezone")
    @classmethod
    def enforce_utc(cls, value: str) -> str:
        if value.upper() != "UTC":
            raise ValueError("Only UTC is allowed for runtime timezone configuration.")
        return "UTC"


class RedisConfig(BaseModel):
    """Redis transport and caching settings."""

    model_config = ConfigDict(extra="forbid")

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    stream_prefix: str = "scalper_ai"


class DirectoryConfig(BaseModel):
    """Filesystem directories used by the project."""

    model_config = ConfigDict(extra="forbid")

    raw_dir: Path = Path("data/raw")
    processed_dir: Path = Path("data/processed")
    artifacts_dir: Path = Path("data/artifacts")


class RiskConfig(BaseModel):
    """Hard guardrails that stay outside any model or policy logic."""

    model_config = ConfigDict(extra="forbid")

    kill_switch_enabled: bool = True
    max_position_size: float = Field(default=100000.0, gt=0)
    max_daily_drawdown: float = Field(default=0.02, gt=0)
    max_spread_pips: float = Field(default=1.5, gt=0)
    stale_quote_seconds: float = Field(default=2.0, gt=0)
    max_order_frequency_per_minute: int = Field(default=30, gt=0)
    cooldown_after_loss_burst_seconds: int = Field(default=300, ge=0)
    loss_burst_threshold: int = Field(default=3, gt=0)
    volatility_filter_enabled: bool = True
    news_filter_enabled: bool = True


class IngestionConfig(BaseModel):
    """Batching and raw persistence settings for market data ingestion."""

    model_config = ConfigDict(extra="forbid")

    batch_size: int = Field(default=5000, gt=0)
    flush_interval_seconds: float = Field(default=1.0, gt=0)
    parquet_compression: str = "zstd"
    replay_speed_multiplier: float = Field(default=0.0, ge=0)
    mt5_poll_interval_seconds: float = Field(default=0.5, gt=0)

    @field_validator("parquet_compression")
    @classmethod
    def normalize_compression(cls, value: str) -> str:
        normalized = value.lower()
        supported = {"zstd", "snappy", "gzip", "brotli", "lz4", "none"}
        if normalized not in supported:
            raise ValueError(f"Unsupported Parquet compression: {value}")
        return normalized


class Mt5BrokerConfig(BaseModel):
    """MetaTrader 5 broker-specific startup settings."""

    model_config = ConfigDict(extra="forbid")

    terminal_path: Optional[Path] = None
    login: Optional[int] = Field(default=None, ge=0)
    password: Optional[str] = None
    server: Optional[str] = None
    timeout_milliseconds: int = Field(default=10_000, gt=0)
    magic_number: int = Field(default=4_242_001, ge=0)
    deviation_points: int = Field(default=20, ge=0)
    base_units_per_lot: float = Field(default=100_000.0, gt=0)
    min_volume_lots: float = Field(default=0.01, gt=0)
    volume_step_lots: float = Field(default=0.01, gt=0)
    history_lookback_hours: int = Field(default=24, gt=0)
    account_mode: Literal["netting", "hedging"] = "netting"
    order_comment_prefix: str = "scalper_ai"
    symbol_map: dict[str, str] = Field(default_factory=dict)

    @field_validator("password", "server", "order_comment_prefix")
    @classmethod
    def validate_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Text MT5 configuration fields must be non-empty when provided.")
        return normalized

    @field_validator("symbol_map")
    @classmethod
    def validate_symbol_map(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for internal_symbol, broker_symbol in value.items():
            internal = internal_symbol.strip()
            broker = broker_symbol.strip()
            if not internal or not broker:
                raise ValueError("symbol_map entries must contain non-empty symbols.")
            normalized[internal] = broker
        return normalized


class BrokerConfig(BaseModel):
    """Execution-broker startup settings."""

    model_config = ConfigDict(extra="forbid")

    live_enabled: bool = False
    live_adapter: str = "unconfigured"
    allow_live_without_kill_switch: bool = False
    mt5: Mt5BrokerConfig = Field(default_factory=Mt5BrokerConfig)

    @field_validator("live_adapter")
    @classmethod
    def validate_live_adapter(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("live_adapter must be non-empty.")
        return normalized


class MonitoringConfig(BaseModel):
    """Operational health and metrics settings."""

    model_config = ConfigDict(extra="forbid")

    health_enabled: bool = True
    metrics_enabled: bool = True
    service_name: str = "scalper_ai_runtime"
    broker_snapshot_stale_after_seconds: float = Field(default=30.0, gt=0)

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("service_name must be non-empty.")
        return normalized


class DeploymentConfig(BaseModel):
    """Runtime bootstrap and safe fallback settings."""

    model_config = ConfigDict(extra="forbid")

    create_directories_on_startup: bool = True
    fallback_to_paper_on_live_failure: bool = True
    require_live_confirmation: bool = True
    live_confirmation_phrase: str = "ENABLE_LIVE_TRADING"

    @field_validator("live_confirmation_phrase")
    @classmethod
    def validate_confirmation_phrase(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("live_confirmation_phrase must be non-empty.")
        return normalized


class AppConfig(BaseModel):
    """Fully materialized application configuration."""

    model_config = ConfigDict(extra="forbid")

    project_name: str = "forex-scalper-ai"
    environment: str = "base"
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    directories: DirectoryConfig = Field(default_factory=DirectoryConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    deployment: DeploymentConfig = Field(default_factory=DeploymentConfig)

    @model_validator(mode="after")
    def validate_runtime_safety(self) -> AppConfig:
        if self.runtime.mode == "live" and self.runtime.paper_trading_default:
            raise ValueError("Live mode cannot default to paper_trading_default=True.")
        return self
