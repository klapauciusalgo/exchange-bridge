"""Pytest configuration and shared fixtures for MEXC Telegram Bridge tests."""
import asyncio
import os
import tempfile
from typing import AsyncGenerator
import pytest
import pytest_asyncio

from config.settings import Settings
from config.security import SecurityManager
from database.db import Database
from exchange.rate_limiter import RateLimiter
from exchange.mexc_client import MexcClient
from exchange.mexc_ws import MexcWebSocketClient
from risk.daily_tracker import DailyTracker
from risk.risk_engine import RiskEngine
from services.notification import NotificationService


@pytest.fixture
def master_key() -> str:
    return SecurityManager.generate_master_key()


@pytest.fixture
def settings(master_key: str) -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="123456789:MockTelegramTokenForTesting1234567890",
        TELEGRAM_WHITELISTED_USERS=[111222333, 999888777],
        MEXC_API_KEY="test_mexc_api_key_12345",
        MEXC_SECRET_KEY="test_mexc_secret_key_67890",
        MASTER_ENCRYPTION_KEY=master_key,
        DRY_RUN=True,
        DB_PATH=":memory:",
        MAX_LEVERAGE=20,
        MAX_POSITION_EQUITY_PCT=30.0,
        DAILY_LOSS_LIMIT_USDT=100.0,
        MAX_DAILY_LOSS_PCT=10.0,
        LIQUIDATION_ALERT_DISTANCE_PCT=15.0,
        FUNDING_RATE_ALERT_THRESHOLD=0.0015,
        RATE_LIMIT_MAX_REQUESTS=10,
        RATE_LIMIT_WINDOW_SECONDS=2.0,
        REQUIRE_PIN=False,
    )


@pytest.fixture
def security_manager(settings: Settings) -> SecurityManager:
    return SecurityManager(master_key=settings.MASTER_ENCRYPTION_KEY)


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[Database, None]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    database = Database(db_path=db_path)
    await database.connect()
    try:
        yield database
    finally:
        await database.close()
        if os.path.exists(db_path):
            os.remove(db_path)


@pytest_asyncio.fixture
async def mexc_client(settings: Settings) -> AsyncGenerator[MexcClient, None]:
    limiter = RateLimiter(max_requests=20, window_seconds=1.0)
    client = MexcClient(
        api_key=settings.MEXC_API_KEY,
        secret_key=settings.MEXC_SECRET_KEY,
        base_url="https://contract.mexc.com",
        rate_limiter=limiter,
        dry_run=True,
    )
    try:
        yield client
    finally:
        await client.close()


@pytest_asyncio.fixture
async def ws_client(settings: Settings) -> AsyncGenerator[MexcWebSocketClient, None]:
    client = MexcWebSocketClient(
        ws_url="wss://contract.mexc.com/edge",
        api_key=settings.MEXC_API_KEY,
        secret_key=settings.MEXC_SECRET_KEY,
    )
    try:
        yield client
    finally:
        await client.stop()


@pytest.fixture
def daily_tracker(db: Database) -> DailyTracker:
    return DailyTracker(db=db)


@pytest.fixture
def risk_engine(settings: Settings, db: Database, mexc_client: MexcClient, daily_tracker: DailyTracker) -> RiskEngine:
    return RiskEngine(
        settings=settings,
        db=db,
        mexc_client=mexc_client,
        daily_tracker=daily_tracker,
    )


@pytest.fixture
def notification_service(
    settings: Settings,
    db: Database,
    mexc_client: MexcClient,
    ws_client: MexcWebSocketClient
) -> NotificationService:
    return NotificationService(
        settings=settings,
        db=db,
        mexc_client=mexc_client,
        ws_client=ws_client,
    )
