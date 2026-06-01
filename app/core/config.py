"""Application configuration loaded from environment variables.

Mirrors ARCHITECTURE_SPEC §5 one-for-one. ``SecretStr`` is used for
anything that should not show up in logs or repr output. Runtime-mutable
copy (welcome message, payment amount, etc.) is **not** here — that lives
in the ``settings`` table and is read via ``SettingsService``.
"""

from __future__ import annotations

from functools import cached_property
from typing import Literal

from pydantic import HttpUrl, RedisDsn, SecretStr, model_validator
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
    db_host: str
    db_port: int = 3306
    db_user: str
    db_password: SecretStr
    db_name: str
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

    @cached_property
    def db_url(self) -> SecretStr:
        """Async SQLAlchemy URL built from the discrete DB_* fields.

        Returned as ``SecretStr`` so the embedded password can't leak through
        an accidental ``repr``/log of the URL (CODE_REVIEW L13); callers use
        ``.get_secret_value()``. Uses ``asyncmy`` per ARCHITECTURE_SPEC §1.
        """
        password = self.db_password.get_secret_value()
        return SecretStr(
            f"mysql+asyncmy://{self.db_user}:{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )
