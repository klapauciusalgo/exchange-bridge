"""Middleware for Whitelist access control, PIN authorization, rate limiting, and audit logging."""
import functools
import logging
import time
from typing import Callable
from telegram import Update
from telegram.ext import ContextTypes

from config.settings import Settings
from config.security import SecurityManager
from database.db import Database
from database.models import AuditLogEntry

logger = logging.getLogger(__name__)


def restricted(
    settings: Settings,
    security_manager: SecurityManager,
    db: Database,
    require_pin_session: bool = False,
):
    """
    Decorator for Telegram command handlers.
    Enforces Telegram User Whitelisting, PIN authorization if enabled, and records Audit Logs.
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user = update.effective_user
            if not user:
                return

            user_id = user.id
            cmd_name = update.effective_message.text.split()[0] if update.effective_message and update.effective_message.text else func.__name__
            full_text = update.effective_message.text if update.effective_message else ""
            start_time = time.monotonic()

            # 1. Whitelist Check
            if settings.TELEGRAM_WHITELISTED_USERS and user_id not in settings.TELEGRAM_WHITELISTED_USERS:
                logger.warning(f"Unauthorized command access attempt from User ID: {user_id} ({user.username})")
                if update.effective_message:
                    await update.effective_message.reply_text("⛔ *Unauthorized access.*", parse_mode="Markdown")

                # Log unauthorized attempt
                await db.log_audit(AuditLogEntry(
                    telegram_user_id=user_id,
                    command=cmd_name,
                    payload=full_text,
                    status="UNAUTHORIZED",
                    latency_ms=(time.monotonic() - start_time) * 1000,
                    details=f"User {user_id} (@{user.username}) not in whitelist."
                ))
                return

            # 2. PIN Authorization Check (for state-changing trade commands)
            if require_pin_session and settings.REQUIRE_PIN:
                if not security_manager.is_session_active(user_id, settings.PIN_SESSION_TIMEOUT_MINUTES):
                    if update.effective_message:
                        await update.effective_message.reply_text(
                            "🔒 *Session locked.* Enter your PIN to unlock trading:\n`/auth <PIN>`",
                            parse_mode="Markdown"
                        )
                    await db.log_audit(AuditLogEntry(
                        telegram_user_id=user_id,
                        command=cmd_name,
                        payload=full_text,
                        status="PIN_REQUIRED",
                        latency_ms=(time.monotonic() - start_time) * 1000,
                        details="Action blocked: Inactive PIN session."
                    ))
                    return

            # 3. Execute Handler & Measure Latency
            status = "SUCCESS"
            details = None
            try:
                result = await func(update, context, *args, **kwargs)
                return result
            except Exception as e:
                status = "ERROR"
                details = str(e)
                logger.error(f"Error executing command {cmd_name} for user {user_id}: {e}", exc_info=True)
                if update.effective_message:
                    await update.effective_message.reply_text(f"❌ *Command Error:* `{str(e)}`", parse_mode="Markdown")
                raise
            finally:
                latency = (time.monotonic() - start_time) * 1000
                await db.log_audit(AuditLogEntry(
                    telegram_user_id=user_id,
                    command=cmd_name,
                    payload=full_text,
                    status=status,
                    latency_ms=latency,
                    details=details
                ))

        return wrapper
    return decorator
