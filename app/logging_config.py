"""Strukturiertes Logging für die Anwendung."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    """Konfiguriert strukturiertes Logging."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    """Gibt einen strukturierten Logger zurück."""
    return structlog.get_logger(name)


def truncate_id(value: str, visible: int = 8) -> str:
    """Kürzt eine ID für sicheres Logging."""
    if len(value) <= visible:
        return value
    return f"{value[:visible]}…"


def truncate_text(value: str, max_len: int = 80) -> str:
    """Kürzt Text für sicheres Logging."""
    if len(value) <= max_len:
        return value
    return f"{value[:max_len]}…"
