"""Tests for logging bootstrap utilities."""

from __future__ import annotations

import json
import logging

from scalper_ai.config.models import LoggingConfig
from scalper_ai.utils.logging import JsonFormatter, build_logging_config


def test_json_formatter_emits_expected_fields() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="scalper_ai.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=12,
        msg="bootstrap log",
        args=(),
        exc_info=None,
    )

    payload = json.loads(formatter.format(record))

    assert payload["message"] == "bootstrap log"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "scalper_ai.test"
    assert payload["ts"].endswith("Z")


def test_build_logging_config_switches_to_json_formatter() -> None:
    config = LoggingConfig(level="INFO", json=True, logger_name="scalper_ai")

    payload = build_logging_config(config)

    assert payload["handlers"]["default"]["formatter"] == "json"
    assert payload["loggers"]["scalper_ai"]["level"] == "INFO"
