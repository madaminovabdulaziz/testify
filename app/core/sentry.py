"""Sentry initialization — silently skipped when no DSN is configured.

Per ARCHITECTURE_SPEC §15.2, PII must never leak: phone numbers, receipt
photo bytes, and ``bot_token`` / ``webhook_secret`` are all considered
sensitive. We rely on ``send_default_pii=False`` plus a ``before_send``
hook that drops common credential keys from any captured event.
"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings

# Substrings that mark a field as sensitive. Matched case-insensitively as
# substrings (CODE_REVIEW M24) so ``user_phone`` / ``phone_number`` are caught,
# not just an exact ``phone``.
_SENSITIVE_KEYS = frozenset(
    {
        "token",
        "secret",
        "password",
        "phone",
    }
)


def init_sentry(settings: Settings) -> None:
    """Initialize Sentry. No-op if ``SENTRY_DSN`` is unset or empty.

    ``sentry_sdk`` is imported lazily so this module loads cleanly in
    dev environments where the package isn't installed but Sentry isn't
    needed either. An empty-string DSN (``SENTRY_DSN=`` in ``.env``)
    counts as "unset" — pydantic-settings turns that into
    ``SecretStr("")``, not ``None``.
    """
    if settings.sentry_dsn is None:
        return
    dsn = settings.sentry_dsn.get_secret_value()
    if not dsn.strip():
        return

    import sentry_sdk  # local import — see docstring

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.env,
        # We're not after performance monitoring, just exception capture.
        traces_sample_rate=0.0,
        # Do not auto-attach IP / cookies / etc.
        send_default_pii=False,
        before_send=_scrub_event,
    )


def _scrub_event(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any] | None:
    """Strip values at known-sensitive keys from any captured event."""
    _scrub_mapping(event)
    return event


def _is_sensitive_key(key: object) -> bool:
    """True if ``key`` contains any sensitive substring (case-insensitive)."""
    return isinstance(key, str) and any(s in key.lower() for s in _SENSITIVE_KEYS)


def _scrub_mapping(obj: Any) -> None:
    """Recursively redact sensitive keys in nested dicts / lists in place."""
    if isinstance(obj, dict):
        for key in list(obj):
            if _is_sensitive_key(key):
                obj[key] = "***"
            else:
                _scrub_mapping(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            _scrub_mapping(item)
