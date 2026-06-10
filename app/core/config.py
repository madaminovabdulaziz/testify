"""Application configuration loaded from environment variables.

Mirrors ARCHITECTURE_SPEC §5 one-for-one. ``SecretStr`` is used for
anything that should not show up in logs or repr output. Runtime-mutable
copy (welcome message, payment amount, etc.) is **not** here — that lives
in the ``settings`` table and is read via ``SettingsService``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import cached_property
from typing import Literal

from pydantic import AliasChoices, Field, HttpUrl, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated, immutable view of every required env var."""

    # ---------- Telegram ----------
    bot_token: SecretStr
    # Optional so `make dev` (polling) doesn't need them; required in
    # staging/prod via the validator below (CODE_REVIEW L11/N10).
    webhook_url: HttpUrl | None = None
    webhook_path: str = "/webhook"
    webhook_secret: SecretStr | None = None
    # Telegram supergroup IDs are large negative integers; ``int`` is correct.
    admin_group_id: int

    # ---------- Database ----------
    # Provide EITHER a full connection URL — DATABASE_URL, or Railway's injected
    # MYSQL_URL — OR the discrete DB_* fields below. The URL form is preferred on
    # Railway: one variable (`${{ MySQL.MYSQL_URL }}`) instead of five, and far
    # harder to misconfigure. Whatever driver the URL names is normalized to
    # asyncmy. ``_require_database_config`` enforces that one of the two is set.
    database_url: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL", "MYSQL_URL"),
    )
    db_host: str | None = None
    db_port: int = 3306
    db_user: str | None = None
    db_password: SecretStr | None = None
    db_name: str | None = None
    # 20 + 5 overflow gives headroom for bursty concurrent attempts without
    # starving the pool (CODE_REVIEW N11).
    db_pool_size: int = 20
    db_pool_max_overflow: int = 5

    # ---------- Redis ----------
    redis_url: RedisDsn

    # ---------- App ----------
    env: Literal["dev", "staging", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    sentry_dsn: SecretStr | None = None

    # ---------- Bot behavior ----------
    test_duration_seconds: int = 3200
    receipt_max_pending_per_user: int = 3
    receipt_reminder_after_hours: int = 24
    broadcast_concurrency: int = 20
    broadcast_messages_per_second: int = 25

    # ---------- Web panel ----------
    # All optional with defaults — existing deploys need no new env vars.
    # ``panel_base_url`` overrides the URL shown by /weblogin; when unset it
    # is derived from ``webhook_url`` (scheme + host) or localhost in dev.
    panel_base_url: HttpUrl | None = None
    web_session_ttl_days: int = 30
    web_login_code_ttl_seconds: int = 300
    web_login_max_attempts: int = 10

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Unknown env vars are tolerated — the host machine almost always
        # has dozens of unrelated entries in its environment.
        extra="ignore",
    )

    @model_validator(mode="after")
    def _require_webhook_config_outside_dev(self) -> Settings:
        """Webhook URL + secret are mandatory once we run in webhook mode."""
        if self.env != "dev" and (self.webhook_url is None or self.webhook_secret is None):
            raise ValueError("webhook_url and webhook_secret are required when env != 'dev'")
        return self

    @model_validator(mode="after")
    def _require_database_config(self) -> Settings:
        """One DB config style must be present: a URL, or the discrete fields."""
        if self.database_url is None and not all(
            (self.db_host, self.db_user, self.db_password, self.db_name)
        ):
            raise ValueError(
                "Database not configured: set DATABASE_URL (or MYSQL_URL), or all of "
                "DB_HOST/DB_USER/DB_PASSWORD/DB_NAME."
            )
        return self

    @cached_property
    def db_url(self) -> SecretStr:
        """Async SQLAlchemy URL — from DATABASE_URL/MYSQL_URL or the discrete DB_* fields.

        Returned as ``SecretStr`` so the embedded password can't leak through an
        accidental ``repr``/log of the URL (CODE_REVIEW L13); callers use
        ``.get_secret_value()``. Always uses ``asyncmy`` per ARCHITECTURE_SPEC §1.
        """
        if self.database_url is not None:
            return SecretStr(to_asyncmy_url(self.database_url.get_secret_value()))
        # Guaranteed non-None by ``_require_database_config``.
        assert self.db_host and self.db_user and self.db_password and self.db_name
        password = self.db_password.get_secret_value()
        return SecretStr(
            f"mysql+asyncmy://{self.db_user}:{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


def to_asyncmy_url(url: str) -> str:
    """Rewrite any MySQL connection URL to the async ``asyncmy`` driver scheme.

    Railway's MySQL plugin injects ``MYSQL_URL`` as ``mysql://…``; SQLAlchemy's
    async engine needs ``mysql+asyncmy://…``. Whatever driver the scheme names
    (``mysql``, ``mysql+pymysql``, …) is normalized to ``mysql+asyncmy``.
    """
    _, sep, rest = url.partition("://")
    if not sep:
        raise ValueError(f"Not a valid database URL (missing '://'): {url!r}")
    return f"mysql+asyncmy://{rest}"


def build_async_mysql_url(env: Mapping[str, str] | None = None) -> str:
    """Async MySQL URL from a raw environment mapping — for alembic + standalone scripts.

    Mirrors ``Settings.db_url`` but reads the environment directly (default
    ``os.environ``) so migrations and the seed scripts don't need the full
    Telegram config to run. Accepts a full ``DATABASE_URL``/``MYSQL_URL`` or the
    discrete ``DB_*`` vars, and raises a clear error if neither is present.
    """
    env = os.environ if env is None else env
    url = env.get("DATABASE_URL") or env.get("MYSQL_URL")
    if url:
        return to_asyncmy_url(url)
    missing = [k for k in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME") if not env.get(k)]
    if missing:
        raise RuntimeError(
            "Database not configured. Set DATABASE_URL (or MYSQL_URL) — e.g. Railway's "
            "${{ MySQL.MYSQL_URL }} — or all of DB_HOST/DB_USER/DB_PASSWORD/DB_NAME "
            "(missing: " + ", ".join(missing) + "). Locally, source .env first."
        )
    return (
        f"mysql+asyncmy://{env['DB_USER']}:{env['DB_PASSWORD']}"
        f"@{env['DB_HOST']}:{env.get('DB_PORT', '3306')}/{env['DB_NAME']}"
    )
