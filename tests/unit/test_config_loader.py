"""Tests for the bootstrap configuration loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from scalper_ai.config.loader import deep_merge, load_app_config
from scalper_ai.utils.paths import resolve_repo_root


def test_deep_merge_preserves_nested_values() -> None:
    base = {"runtime": {"mode": "research", "paper_trading_default": True}}
    override = {"runtime": {"mode": "live"}}

    merged = deep_merge(base, override)

    assert merged["runtime"]["mode"] == "live"
    assert merged["runtime"]["paper_trading_default"] is True


def test_load_app_config_applies_overlay() -> None:
    config = load_app_config(config_name="live", config_dir=resolve_repo_root() / "configs")

    assert config.environment == "live"
    assert config.runtime.mode == "live"
    assert config.runtime.paper_trading_default is False
    assert config.logging.json_enabled is True
    assert config.broker.live_enabled is True
    assert config.broker.live_adapter == "mt5"
    assert config.broker.mt5.require_stop_loss is True
    assert config.risk.max_weekly_loss is None
    assert config.risk.max_risk_per_trade is None
    assert config.risk.max_open_positions is None
    assert config.risk.min_margin_level_percent is None
    assert config.risk.max_leverage is None
    assert config.deployment.fallback_to_paper_on_live_failure is False


def test_load_app_config_supports_mt5_overlay() -> None:
    config = load_app_config(config_name="mt5", config_dir=resolve_repo_root() / "configs")

    assert config.environment == "mt5"
    assert config.runtime.mode == "live"
    assert config.broker.live_enabled is True
    assert config.broker.live_adapter == "mt5"
    assert config.broker.mt5.account_mode == "hedging"


def test_load_app_config_applies_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCALPER_AI_ENV", "research")
    monkeypatch.setenv("SCALPER_AI_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("SCALPER_AI_MAX_POSITION_SIZE", "250000")
    monkeypatch.setenv("SCALPER_AI_MAX_WEEKLY_LOSS", "1500")
    monkeypatch.setenv("SCALPER_AI_MAX_RISK_PER_TRADE", "75")
    monkeypatch.setenv("SCALPER_AI_MAX_OPEN_POSITIONS", "2")
    monkeypatch.setenv("SCALPER_AI_MIN_MARGIN_LEVEL_PERCENT", "125")
    monkeypatch.setenv("SCALPER_AI_MAX_LEVERAGE", "12.5")

    config = load_app_config(config_dir=resolve_repo_root() / "configs")

    assert config.environment == "research"
    assert config.logging.level == "WARNING"
    assert config.risk.max_position_size == 250000.0
    assert config.risk.max_weekly_loss == 1500.0
    assert config.risk.max_risk_per_trade == 75.0
    assert config.risk.max_open_positions == 2
    assert config.risk.min_margin_level_percent == 125.0
    assert config.risk.max_leverage == 12.5


def test_load_app_config_applies_mt5_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCALPER_AI_ENV", "mt5")
    monkeypatch.setenv("SCALPER_AI_BROKER_MT5_LOGIN", "777777")
    monkeypatch.setenv("SCALPER_AI_BROKER_MT5_SERVER", "MetaQuotes-Demo")
    monkeypatch.setenv("SCALPER_AI_BROKER_MT5_PASSWORD", "secret")
    monkeypatch.setenv("SCALPER_AI_BROKER_MT5_REQUIRE_STOP_LOSS", "true")
    monkeypatch.setenv("SCALPER_AI_BROKER_MT5_REQUIRE_TAKE_PROFIT", "true")
    monkeypatch.setenv("SCALPER_AI_BROKER_MT5_RECONNECT_ENABLED", "false")
    monkeypatch.setenv("SCALPER_AI_BROKER_MT5_RECONNECT_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("SCALPER_AI_BROKER_MT5_SYMBOL_MAP_JSON", '{"EURUSD":"EURUSD.a"}')

    config = load_app_config(config_dir=resolve_repo_root() / "configs")

    assert config.environment == "mt5"
    assert config.broker.mt5.login == 777777
    assert config.broker.mt5.server == "MetaQuotes-Demo"
    assert config.broker.mt5.password == "secret"
    assert config.broker.mt5.require_stop_loss is True
    assert config.broker.mt5.require_take_profit is True
    assert config.broker.mt5.reconnect_enabled is False
    assert config.broker.mt5.reconnect_max_attempts == 2
    assert config.broker.mt5.symbol_map == {"EURUSD": "EURUSD.a"}


def test_load_app_config_applies_alert_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCALPER_AI_ENV", "research")
    monkeypatch.setenv(
        "SCALPER_AI_MONITORING_ALERT_WEBHOOK_URL",
        "https://alerts.example.test/hooks/scalper",
    )
    monkeypatch.setenv("SCALPER_AI_MONITORING_ALERT_WEBHOOK_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("SCALPER_AI_MONITORING_ALERT_INCLUDE_WARNINGS", "false")

    config = load_app_config(config_dir=resolve_repo_root() / "configs")

    assert config.monitoring.alert_webhook_url == "https://alerts.example.test/hooks/scalper"
    assert config.monitoring.alert_webhook_timeout_seconds == 2.5
    assert config.monitoring.alert_include_warnings is False


def test_runtime_timezone_must_stay_utc(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "base.yaml").write_text(
        """
project_name: forex-scalper-ai
environment: base
runtime:
  mode: research
  timezone: Europe/Moscow
  paper_trading_default: true
logging:
  level: INFO
  json: false
  logger_name: scalper_ai
redis:
  host: localhost
  port: 6379
  db: 0
  stream_prefix: scalper_ai
directories:
  raw_dir: data/raw
  processed_dir: data/processed
  artifacts_dir: data/artifacts
risk:
  kill_switch_enabled: true
  max_position_size: 100000.0
  max_daily_drawdown: 0.02
  max_spread_pips: 1.5
  stale_quote_seconds: 2.0
  max_order_frequency_per_minute: 30
  cooldown_after_loss_burst_seconds: 300
  loss_burst_threshold: 3
  volatility_filter_enabled: true
  news_filter_enabled: true
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="UTC"):
        load_app_config(config_name="base", config_dir=config_dir)
