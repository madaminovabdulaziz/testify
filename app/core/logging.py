"""Structlog configuration.

Outputs JSON in prod/staging (for ingest into log aggregators) and a
human-readable colorized format in dev. Request-scoped context — e.g.
``request_id``, ``telegram_id``, ``update_type`` — is bound via
contextvars by the bot's ``LoggingMiddleware`` (added in Prompt 4) and
appears on every log line for the duration of that handler invocation.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from app.core.config import Settings


def configure_logging(settings: Settings) -> None:
    """Initialize ``structlog`` and the stdlib ``logging`` integration.

    Safe to call more than once — structlog's ``configure`` replaces the
    prior configuration. Call once at startup before any other code logs.
    """
    log_level = getattr(logging, settings.log_level)

    # Pipeline shared across both renderers.
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _scrub_secrets,
    ]

    if settings.log_format == "json":
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Funnel stdlib log records (sqlalchemy, aiohttp, apscheduler) through
    # the same pipeline so production logs are uniformly structured.
    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        force=True,
    )

    # APScheduler is chatty at INFO (a line per job add / fire). Quiet it so
    # the per-attempt timer churn doesn't drown real logs (CODE_REVIEW L14).
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def bind_request_context(**kwargs: Any) -> None:
    """Bind context vars for the current async task's log lines."""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_request_context() -> None:
    """Drop all bound context vars — call at the end of each handler."""
    structlog.contextvars.clear_contextvars()


# Substrings that mark a key as carrying credentials or PII. Matched
# case-insensitively as substrings (so ``phone_number`` / ``bot_token`` /
# ``sentry_dsn`` all hit) and applied recursively into nested dicts/lists so a
# ``update.model_dump()`` carrying ``contact.phone_number`` is scrubbed too
# (CODE_REVIEW M24/M25).
_REDACT_KEYS = frozenset(
    {
        "token",
        "secret",
        "password",
        "phone",
        "dsn",
    }
)


def _is_sensitive_key(key: object) -> bool:
    return isinstance(key, str) and any(s in key.lower() for s in _REDACT_KEYS)


def _scrub_value(value: Any) -> Any:
    """Return ``value`` with sensitive nested keys redacted (dicts/lists only)."""
    if isinstance(value, dict):
        return {k: ("***" if _is_sensitive_key(k) else _scrub_value(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    return value


def _scrub_secrets(_: Any, __: str, event_dict: EventDict) -> EventDict:
    """Redact sensitive keys anywhere in the (possibly nested) event dict."""
    for key in list(event_dict):
        if _is_sensitive_key(key):
            event_dict[key] = "***"
        else:
            event_dict[key] = _scrub_value(event_dict[key])
    return event_dict
