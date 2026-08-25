"""Settings and environment configuration for MEXC Telegram Bridge."""
import os
from typing import List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = Field(default="", description="Telegram Bot Token from @BotFather")
    TELEGRAM_WHITELISTED_USERS: List[int] = Field(
        default_factory=list,
        description="Comma-separated Telegram User IDs allowed to access the bot"
    )

    # MEXC Credentials
    MEXC_API_KEY: str = Field(default="", description="MEXC Futures API Key")
    MEXC_SECRET_KEY: str = Field(default="", description="MEXC Futures Secret Key")
    MASTER_ENCRYPTION_KEY: Optional[str] = Field(
        default=None,
        description="Base64 32-byte key for Fernet encryption of secrets"
    )

    # MEXC Endpoints
    MEXC_REST_URL: str = Field(default="https://contract.mexc.com", description="MEXC Futures REST Base URL")
    MEXC_WS_URL: str = Field(default="wss://contract.mexc.com/edge", description="MEXC Futures WebSocket URL")

    # Mode & Storage
    DRY_RUN: bool = Field(default=False, description="Dry run mode without sending real orders")
    DB_PATH: str = Field(default="data/mexc_bridge.db", description="SQLite database path")
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")

    # Risk Controls
    MAX_LEVERAGE: int = Field(default=20, ge=1, le=100, description="Max allowed leverage")
    MAX_POSITION_EQUITY_PCT: float = Field(default=30.0, ge=1.0, le=100.0, description="Max margin allocation per position (%)")
    DAILY_LOSS_LIMIT_USDT: float = Field(default=100.0, ge=0.0, description="Max daily loss limit in USDT")
    MAX_DAILY_LOSS_PCT: float = Field(default=10.0, ge=1.0, le=100.0, description="Max daily loss limit as % of total equity")
    LIQUIDATION_ALERT_DISTANCE_PCT: float = Field(default=15.0, ge=1.0, le=50.0, description="Liq price alert threshold (%)")
    FUNDING_RATE_ALERT_THRESHOLD: float = Field(default=0.0015, description="High funding rate alert threshold (e.g. 0.15%)")

    # Rate Limiting
    RATE_LIMIT_MAX_REQUESTS: int = Field(default=10, description="Max requests allowed per window (MEXC limit is 20/2s)")
    RATE_LIMIT_WINDOW_SECONDS: float = Field(default=2.0, description="Window size in seconds")

    # Security & PIN
    REQUIRE_PIN: bool = Field(default=False, description="Require PIN before executing sensitive trading actions")
    PIN_HASH: Optional[str] = Field(default=None, description="SHA256 hash or Argon2/bcrypt hash of transaction PIN")
    PIN_SESSION_TIMEOUT_MINUTES: int = Field(default=30, description="Session timeout in minutes after entering PIN")

    # Automatic Periodic Position Broadcast (e.g. every 1 hour)
    AUTO_POSITIONS_ENABLED: bool = Field(default=True, description="Enable automatic periodic /positions broadcast")
    AUTO_POSITIONS_INTERVAL_MINUTES: int = Field(default=60, ge=1, le=1440, description="Interval in minutes for auto position updates")

    # Backup Alert Webhook (e.g., Discord/Slack/Generic webhook if Telegram is down)
    FALLBACK_WEBHOOK_URL: Optional[str] = Field(default=None, description="Optional fallback webhook URL for alerts")

    @field_validator("TELEGRAM_WHITELISTED_USERS", mode="before")
    @classmethod
    def parse_whitelisted_users(cls, v):
        if isinstance(v, str):
            if not v.strip():
                return []
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        elif isinstance(v, (int, float)):
            return [int(v)]
        elif isinstance(v, (list, tuple, set)):
            return [int(x) for x in v]
        return []


_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


def set_settings(settings: Settings) -> None:
    global _settings_instance
    _settings_instance = settings
