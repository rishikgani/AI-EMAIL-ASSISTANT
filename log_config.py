"""Structured logging configuration for AI Email Assistant."""

import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from config import get_settings


def add_log_level(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Add log level to event dict."""
    event_dict["level"] = method_name.upper()
    return event_dict


def add_timestamp(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Add ISO timestamp to event dict."""
    import datetime
    event_dict["timestamp"] = datetime.datetime.now(datetime.UTC).isoformat()
    return event_dict


def add_app_context(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Add application context to event dict."""
    settings = get_settings()
    event_dict["app"] = getattr(settings, "app_name", "AI Email Assistant")
    event_dict["version"] = getattr(settings, "app_version", "0.1.0")
    return event_dict


def filter_secrets(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Filter out sensitive fields from logs."""
    sensitive_keys = {
        "password", "secret", "token", "api_key", "apikey",
        "authorization", "auth", "credential", "key"
    }
    filtered = {}
    for key, value in event_dict.items():
        if any(sk in key.lower() for sk in sensitive_keys):
            filtered[key] = "***REDACTED***"
        else:
            filtered[key] = value
    return filtered


def configure_logging() -> None:
    """Configure structlog with JSON output."""
    settings = get_settings()

    # Determine log level (use standard logging levels)
    import logging
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Shared processors
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        add_app_context,
        add_timestamp,
        add_log_level,
        filter_secrets,
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    # Use console renderer for development (INFO level or DEBUG)
    # JSON output for production (WARNING or ERROR)
    if log_level <= logging.INFO:
        # Pretty console output for development
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    else:
        # JSON output for production
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """Get a structured logger instance."""
    return structlog.get_logger(name)


# Configure on import
configure_logging()
