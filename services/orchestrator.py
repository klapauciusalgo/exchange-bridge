"""System Orchestrator coordinating lifecycle of DB, exchange clients, bot, and services."""
import asyncio
import logging
import signal
import sys
from typing import Optional

from config.settings import Settings, get_settings
from config.security import SecurityManager, RedactingFilter
from database.db import Database
from exchange.rate_limiter import RateLimiter
from exchange.mexc_client import MexcClient
from exchange.mexc_ws import MexcWebSocketClient
from exchange.reconciliation import StateReconciler
from risk.daily_tracker import DailyTracker
from risk.risk_engine import RiskEngine
from services.notification import NotificationService
from bot.telegram_bot import create_bot_app

logger = logging.getLogger(__name__)


class BridgeOrchestrator:
    """Coordinates lifecycle of all components of MEXC Telegram Bridge."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.security = SecurityManager(master_key=self.settings.MASTER_ENCRYPTION_KEY)

        # Decrypt credentials if encrypted at rest
        self.api_key = self.security.decrypt(self.settings.MEXC_API_KEY)
        self.secret_key = self.security.decrypt(self.settings.MEXC_SECRET_KEY)

        # Configure redacting logger filter
        root_logger = logging.getLogger()
        redacting_filter = RedactingFilter(patterns_to_redact=[
            self.api_key,
            self.secret_key,
            self.settings.TELEGRAM_BOT_TOKEN,
        ])
        for handler in root_logger.handlers:
            handler.addFilter(redacting_filter)

        self.db = Database(db_path=self.settings.DB_PATH)
        self.rate_limiter = RateLimiter(
            max_requests=self.settings.RATE_LIMIT_MAX_REQUESTS,
            window_seconds=self.settings.RATE_LIMIT_WINDOW_SECONDS,
        )
        self.mexc_client = MexcClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            base_url=self.settings.MEXC_REST_URL,
            rate_limiter=self.rate_limiter,
            dry_run=self.settings.DRY_RUN,
        )
        self.ws_client = MexcWebSocketClient(
            ws_url=self.settings.MEXC_WS_URL,
            api_key=self.api_key,
            secret_key=self.secret_key,
        )
        self.reconciler = StateReconciler(
            mexc_client=self.mexc_client,
            ws_client=self.ws_client,
            db=self.db,
            sync_interval=60,
        )
        self.daily_tracker = DailyTracker(db=self.db)
        self.risk_engine = RiskEngine(
            settings=self.settings,
            db=self.db,
            mexc_client=self.mexc_client,
            daily_tracker=self.daily_tracker,
        )
        self.notification_service = NotificationService(
            settings=self.settings,
            db=self.db,
            mexc_client=self.mexc_client,
            ws_client=self.ws_client,
        )
        self.bot_app = create_bot_app(
            settings=self.settings,
            security_manager=self.security,
            db=self.db,
            mexc_client=self.mexc_client,
            risk_engine=self.risk_engine,
            notification_service=self.notification_service,
        )
        self.notification_service.set_bot_app(self.bot_app)
        self._is_running = False

    async def start(self) -> None:
        """Start all services."""
        logger.info("Initializing MEXC ↔ Telegram Trading Bridge...")
        self._is_running = True

        # 1. Connect Database
        await self.db.connect()

        # 2. Start WebSocket & Reconciler
        await self.ws_client.start()
        await self.reconciler.start()

        # 3. Start Notification Worker
        await self.notification_service.start()

        # 4. Initialize Telegram Bot
        if self.settings.TELEGRAM_BOT_TOKEN:
            logger.info("Starting Telegram Bot long-polling...")
            await self.bot_app.initialize()
            await self.bot_app.start()
            await self.bot_app.updater.start_polling(drop_pending_updates=True)
            logger.info("Telegram Bot is online and polling for commands.")
        else:
            logger.warning("No TELEGRAM_BOT_TOKEN provided. Telegram polling skipped (Mock/Test mode).")

        logger.info("MEXC ↔ Telegram Trading Bridge is fully operational.")

    async def stop(self) -> None:
        """Gracefully stop all services."""
        if not self._is_running:
            return
        logger.info("Stopping MEXC ↔ Telegram Trading Bridge...")
        self._is_running = False

        if self.settings.TELEGRAM_BOT_TOKEN and self.bot_app.updater:
            try:
                await self.bot_app.updater.stop()
                await self.bot_app.stop()
                await self.bot_app.shutdown()
            except Exception as e:
                logger.error(f"Error stopping Telegram Bot: {e}")

        await self.notification_service.stop()
        await self.reconciler.stop()
        await self.ws_client.stop()
        await self.mexc_client.close()
        await self.db.close()
        logger.info("MEXC ↔ Telegram Trading Bridge stopped cleanly.")
