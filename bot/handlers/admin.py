"""Admin, help, risk configuration, PIN auth, auto positions, and quick menu command handlers."""
import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config.settings import Settings
from config.security import SecurityManager
from database.db import Database
from database.models import UserRiskConfig
from exchange.mexc_client import MexcClient
from bot.formatters import format_help, format_risk_settings

logger = logging.getLogger(__name__)


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Create persistent bottom button dashboard."""
    keyboard = [
        [KeyboardButton("📊 Positions"), KeyboardButton("💰 Balance")],
        [KeyboardButton("🔍 Scan 4H"), KeyboardButton("📈 Chart BTC")],
        [KeyboardButton("📋 Orders"), KeyboardButton("🎯 Watchlist")],
        [KeyboardButton("🛡️ Risk Limits"), KeyboardButton("❓ Help")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    user = update.effective_user
    welcome = (
        f"👋 Welcome to *MEXC ↔ Telegram Trading Bridge*, {user.first_name}!\n\n"
        f"Real-time monitoring, risk management, and order execution for MEXC Futures.\n\n"
        f"Use the buttons below for quick navigation or type any command."
    )
    inline_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 View Positions", callback_data="nav:positions"),
            InlineKeyboardButton("💰 View Balance", callback_data="nav:balance"),
        ],
        [
            InlineKeyboardButton("📋 Active Orders", callback_data="nav:orders"),
            InlineKeyboardButton("❓ Full Command Guide", callback_data="nav:help"),
        ]
    ])
    await update.effective_message.reply_text(
        welcome,
        reply_markup=get_main_reply_keyboard(),
        parse_mode="Markdown"
    )
    await update.effective_message.reply_text(
        "⚡ *QUICK ACTIONS*",
        reply_markup=inline_kb,
        parse_mode="Markdown"
    )


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /menu command to toggle the persistent button dashboard."""
    inline_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Positions", callback_data="nav:positions"),
            InlineKeyboardButton("💰 Balance", callback_data="nav:balance"),
        ],
        [
            InlineKeyboardButton("📋 Orders", callback_data="nav:orders"),
            InlineKeyboardButton("❓ Help", callback_data="nav:help"),
        ]
    ])
    await update.effective_message.reply_text(
        "🎛️ *MAIN CONTROL DASHBOARD*\nTap any button below to manage your futures account.",
        reply_markup=get_main_reply_keyboard(),
        parse_mode="Markdown"
    )


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    await update.effective_message.reply_text(format_help(), parse_mode="Markdown")


async def handle_risklimit(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db: Database,
    settings: Settings,
) -> None:
    """
    Handle /risklimit [max_lev] [max_pos_pct] [daily_loss_usdt].
    View or adjust user risk controls.
    """
    user_id = update.effective_user.id
    config = await db.get_user_risk_config(user_id)
    if not config:
        config = UserRiskConfig(
            user_id=user_id,
            max_leverage=settings.MAX_LEVERAGE,
            max_position_pct=settings.MAX_POSITION_EQUITY_PCT,
            daily_loss_limit_usdt=settings.DAILY_LOSS_LIMIT_USDT,
            max_daily_loss_pct=settings.MAX_DAILY_LOSS_PCT,
            dry_run=settings.DRY_RUN,
            require_pin=settings.REQUIRE_PIN,
        )

    args = context.args or []
    if not args:
        # View current settings
        msg = format_risk_settings(config)
        await update.effective_message.reply_text(msg, parse_mode="Markdown")
        return

    # Update risk settings: /risklimit <max_lev> <max_pos_pct> <daily_loss_usdt>
    try:
        if len(args) >= 1:
            config.max_leverage = int(args[0].replace("x", ""))
        if len(args) >= 2:
            config.max_position_pct = float(args[1].replace("%", ""))
        if len(args) >= 3:
            config.daily_loss_limit_usdt = float(args[2].replace("$", ""))

        await db.save_user_risk_config(config)
        await update.effective_message.reply_text(
            f"✅ *Risk settings updated successfully!*\n\n{format_risk_settings(config)}",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.effective_message.reply_text("Invalid numeric values for risk limits.", parse_mode="Markdown")


async def handle_dryrun(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    client: MexcClient,
    settings: Settings,
    db: Database,
) -> None:
    """Handle /dryrun [on|off]."""
    args = context.args or []
    user_id = update.effective_user.id
    config = await db.get_user_risk_config(user_id)
    if not config:
        config = UserRiskConfig(user_id=user_id, dry_run=settings.DRY_RUN)

    if not args:
        status_str = "ON (Simulated)" if client.dry_run else "OFF (Real Money)"
        await update.effective_message.reply_text(f"🧪 *Dry Run Mode is currently:* `{status_str}`\nUsage: `/dryrun on` or `/dryrun off`", parse_mode="Markdown")
        return

    state = args[0].lower()
    if state in ["on", "1", "true"]:
        client.dry_run = True
        config.dry_run = True
        await db.save_user_risk_config(config)
        await update.effective_message.reply_text("🧪 *Dry run mode ENABLED.* Orders will now be simulated without exchange execution.", parse_mode="Markdown")
    elif state in ["off", "0", "false"]:
        client.dry_run = False
        config.dry_run = False
        await db.save_user_risk_config(config)
        await update.effective_message.reply_text("⚠️ *Dry run mode DISABLED.* Orders will now execute with REAL MONEY on MEXC.", parse_mode="Markdown")
    else:
        await update.effective_message.reply_text("Usage: `/dryrun on` or `/dryrun off`", parse_mode="Markdown")


async def handle_autopos(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    notification_service=None,
) -> None:
    """Handle /autopos [on|off|minutes|now]."""
    args = context.args or []
    if not args:
        status_str = "ENABLED" if settings.AUTO_POSITIONS_ENABLED else "DISABLED"
        interval_str = f"{settings.AUTO_POSITIONS_INTERVAL_MINUTES} minutes" if settings.AUTO_POSITIONS_INTERVAL_MINUTES != 60 else "1 hour (60m)"
        await update.effective_message.reply_text(
            f"⏰ *AUTOMATED HOURLY POSITIONS SCHEDULE*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"• *Status:* `{status_str}`\n"
            f"• *Interval:* `{interval_str}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"• Enable: `/autopos on`\n"
            f"• Disable: `/autopos off`\n"
            f"• Change interval: `/autopos 60` (in minutes)\n"
            f"• Trigger immediately: `/autopos now`",
            parse_mode="Markdown"
        )
        return

    subcmd = args[0].lower()
    if subcmd in ["on", "true", "1", "enable"]:
        settings.AUTO_POSITIONS_ENABLED = True
        await update.effective_message.reply_text(
            f"✅ *Auto Positions Broadcast ENABLED.* You will receive `/positions` updates every {settings.AUTO_POSITIONS_INTERVAL_MINUTES} minutes.",
            parse_mode="Markdown"
        )
    elif subcmd in ["off", "false", "0", "disable"]:
        settings.AUTO_POSITIONS_ENABLED = False
        await update.effective_message.reply_text(
            "⏹️ *Auto Positions Broadcast DISABLED.*",
            parse_mode="Markdown"
        )
    elif subcmd in ["now", "trigger", "send"]:
        if notification_service:
            await update.effective_message.reply_text("⏳ *Broadcasting positions snapshot now...*", parse_mode="Markdown")
            await notification_service.broadcast_positions_snapshot()
        else:
            await update.effective_message.reply_text("❌ Notification service not available.", parse_mode="Markdown")
    else:
        try:
            minutes = int(subcmd.replace("m", "").replace("min", ""))
            if minutes < 1 or minutes > 1440:
                await update.effective_message.reply_text("Interval must be between 1 and 1440 minutes.", parse_mode="Markdown")
                return
            settings.AUTO_POSITIONS_INTERVAL_MINUTES = minutes
            settings.AUTO_POSITIONS_ENABLED = True
            await update.effective_message.reply_text(
                f"✅ *Auto Positions Interval set to {minutes} minutes (ENABLED).* Next broadcast will run on schedule.",
                parse_mode="Markdown"
            )
        except ValueError:
            await update.effective_message.reply_text("Usage: `/autopos [on|off|minutes|now]`", parse_mode="Markdown")


async def handle_auth(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    security_manager: SecurityManager,
    settings: Settings,
    db: Database,
) -> None:
    """Handle /auth <pin>."""
    if not context.args:
        await update.effective_message.reply_text("Usage: `/auth <PIN>`", parse_mode="Markdown")
        return

    entered_pin = context.args[0].strip()
    user_id = update.effective_user.id

    # Check user config or global settings for PIN
    config = await db.get_user_risk_config(user_id)
    if config and config.pin_hash and config.pin_salt:
        is_valid = security_manager.verify_pin(entered_pin, config.pin_salt, config.pin_hash)
    elif settings.PIN_HASH:
        import hashlib
        is_valid = (hashlib.sha256(entered_pin.encode("utf-8")).hexdigest() == settings.PIN_HASH)
    else:
        salt, p_hash = security_manager.hash_pin(entered_pin)
        if not config:
            config = UserRiskConfig(user_id=user_id)
        config.pin_salt = salt
        config.pin_hash = p_hash
        config.require_pin = True
        await db.save_user_risk_config(config)
        security_manager.refresh_session(user_id)
        await update.effective_message.reply_text("🔐 *PIN established and session unlocked.*", parse_mode="Markdown")
        return

    if is_valid:
        security_manager.refresh_session(user_id)
        await update.effective_message.reply_text(
            f"🔓 *Session unlocked for {settings.PIN_SESSION_TIMEOUT_MINUTES} minutes.* Trading commands authorized.",
            parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text("❌ *Incorrect PIN.* Access denied.", parse_mode="Markdown")
