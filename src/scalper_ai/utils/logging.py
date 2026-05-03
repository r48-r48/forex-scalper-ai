"""Logging helpers with UTC-aware text and JSON formatting."""

from __future__ import annotations

import json
import logging
import logging.config
import time
from collections.abc import Callable
from typing import Any

from scalper_ai.config.models import LoggingConfig


def _utc_time_converter(timestamp: float | None = None) -> time.struct_time:
    return time.gmtime(timestamp)


class UTCFormatter(logging.Formatter):
    """Base formatter that emits timestamps in UTC."""

    converter: Callable[[float | None], time.struct_time] = staticmethod(_utc_time_converter)

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )


class JsonFormatter(UTCFormatter):
    """Compact JSON formatter for machine-readable logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if hasattr(record, "component"):
            payload["component"] = record.component
        if hasattr(record, "event"):
            payload["event"] = record.event
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=True, default=str)


def build_logging_config(config: LoggingConfig) -> dict[str, Any]:
    """Build a dictConfig payload from typed logging settings."""

    formatter_name = "json" if config.json_enabled else "console"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "console": {"()": "scalper_ai.utils.logging.UTCFormatter"},
            "json": {"()": "scalper_ai.utils.logging.JsonFormatter"},
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": formatter_name,
                "level": config.level,
            }
        },
        "loggers": {
            config.logger_name: {
                "handlers": ["default"],
                "level": config.level,
                "propagate": False,
            }
        },
        "root": {"handlers": ["default"], "level": config.level},
    }


def configure_logging(config: LoggingConfig) -> None:
    """Configure application-wide logging."""

    logging.config.dictConfig(build_logging_config(config))


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a configured logger."""

    return logging.getLogger(name or "scalper_ai")
