"""Telegram Bot Application Factory and Dispatcher Setup with Buttons and Navigation."""
import logging
from typing import Optional
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config.settings import Settings
from config.security import SecurityManager
from database.db import Database
from exchange.mexc_client import MexcClient
from risk.risk_engine import RiskEngine
from bot.middleware import restricted
from bot.handlers import (
    handle_start,
    handle_menu,
    handle_help,
    handle_balance,
    handle_positions,
    handle_orders,
    handle_cancel,
    handle_price,
    handle_market,
    handle_orderbook,
    handle_chart,
    handle_scan4h,
    handle_similar,
    handle_macdscan,
    handle_watch,
    handle_watchlist,
    handle_unwatch,
    handle_open,
    handle_close,
    handle_setsl,
    handle_settp,
    handle_setsltp,
    handle_trade_callback,
    handle_panic,
    handle_panic_callback,
    handle_risklimit,
    handle_dryrun,
    handle_autopos,
    handle_auth,
)

logger = logging.getLogger(__name__)


def create_bot_app(
    settings: Settings,
    security_manager: SecurityManager,
    db: Database,
    mexc_client: MexcClient,
    risk_engine: RiskEngine,
    notification_service=None,
) -> Application:
    """Build and configure the Telegram Bot Application with handlers and middleware."""
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN is empty. Initializing dummy/mock bot instance.")
        app = Application.builder().token("123456789:MockTelegramTokenForTesting1234567890").build()
    else:
        app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

    # 1. Base / Admin Handlers (read-only)
    app.add_handler(CommandHandler("start", restricted(settings, security_manager, db)(handle_start)))
    app.add_handler(CommandHandler("menu", restricted(settings, security_manager, db)(handle_menu)))
    app.add_handler(CommandHandler("help", restricted(settings, security_manager, db)(handle_help)))

    async def _risklimit(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_risklimit(update, context, db, settings)
    app.add_handler(CommandHandler("risklimit", restricted(settings, security_manager, db)(_risklimit)))

    async def _dryrun(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_dryrun(update, context, mexc_client, settings, db)
    app.add_handler(CommandHandler("dryrun", restricted(settings, security_manager, db)(_dryrun)))

    async def _autopos(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_autopos(update, context, settings, notification_service)
    app.add_handler(CommandHandler("autopos", restricted(settings, security_manager, db)(_autopos)))
    app.add_handler(CommandHandler("autopositions", restricted(settings, security_manager, db)(_autopos)))

    async def _auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_auth(update, context, security_manager, settings, db)
    app.add_handler(CommandHandler("auth", restricted(settings, security_manager, db)(_auth)))

    # 2. Account & Info Handlers (read-only)
    async def _balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_balance(update, context, mexc_client, db)
    app.add_handler(CommandHandler("balance", restricted(settings, security_manager, db)(_balance)))

    async def _positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_positions(update, context, mexc_client)
    app.add_handler(CommandHandler("positions", restricted(settings, security_manager, db)(_positions)))

    async def _orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_orders(update, context, mexc_client)
    app.add_handler(CommandHandler("orders", restricted(settings, security_manager, db)(_orders)))

    # 3. Market & Watchlist Handlers
    async def _price(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_price(update, context, mexc_client)
    app.add_handler(CommandHandler("price", restricted(settings, security_manager, db)(_price)))

    async def _market(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_market(update, context, mexc_client)
    app.add_handler(CommandHandler("market", restricted(settings, security_manager, db)(_market)))

    async def _orderbook(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_orderbook(update, context, mexc_client)
    app.add_handler(CommandHandler("orderbook", restricted(settings, security_manager, db)(_orderbook)))

    async def _chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_chart(update, context, mexc_client)
    app.add_handler(CommandHandler("chart", restricted(settings, security_manager, db)(_chart)))
    app.add_handler(CommandHandler("c", restricted(settings, security_manager, db)(_chart)))

    async def _scan4h(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_scan4h(update, context, mexc_client)
    app.add_handler(CommandHandler("scan4h", restricted(settings, security_manager, db)(_scan4h)))
    app.add_handler(CommandHandler("scan", restricted(settings, security_manager, db)(_scan4h)))

    async def _similar(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_similar(update, context, mexc_client)
    app.add_handler(CommandHandler("similar", restricted(settings, security_manager, db)(_similar)))
    app.add_handler(CommandHandler("sim", restricted(settings, security_manager, db)(_similar)))

    async def _macdscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_macdscan(update, context, mexc_client)
    app.add_handler(CommandHandler("macdscan", restricted(settings, security_manager, db)(_macdscan)))
    app.add_handler(CommandHandler("ms", restricted(settings, security_manager, db)(_macdscan)))

    async def _watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_watch(update, context, mexc_client, db)
    app.add_handler(CommandHandler("watch", restricted(settings, security_manager, db)(_watch)))

    async def _watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_watchlist(update, context, db)
    app.add_handler(CommandHandler("watchlist", restricted(settings, security_manager, db)(_watchlist)))

    async def _unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_unwatch(update, context, db)
    app.add_handler(CommandHandler("unwatch", restricted(settings, security_manager, db)(_unwatch)))

    # 4. State-Changing Trade Execution Handlers (require PIN if enabled)
    async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_cancel(update, context, mexc_client)
    app.add_handler(CommandHandler("cancel", restricted(settings, security_manager, db, require_pin_session=True)(_cancel)))

    async def _open(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_open(update, context, mexc_client, risk_engine, settings)
    app.add_handler(CommandHandler("open", restricted(settings, security_manager, db, require_pin_session=True)(_open)))

    async def _close(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_close(update, context, mexc_client, settings)
    app.add_handler(CommandHandler("close", restricted(settings, security_manager, db, require_pin_session=True)(_close)))

    async def _setsl(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_setsl(update, context, mexc_client, risk_engine)
    app.add_handler(CommandHandler("setsl", restricted(settings, security_manager, db, require_pin_session=True)(_setsl)))
    app.add_handler(CommandHandler("updatesl", restricted(settings, security_manager, db, require_pin_session=True)(_setsl)))
    app.add_handler(CommandHandler("sl", restricted(settings, security_manager, db, require_pin_session=True)(_setsl)))

    async def _settp(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_settp(update, context, mexc_client, risk_engine)
    app.add_handler(CommandHandler("settp", restricted(settings, security_manager, db, require_pin_session=True)(_settp)))
    app.add_handler(CommandHandler("updatetp", restricted(settings, security_manager, db, require_pin_session=True)(_settp)))
    app.add_handler(CommandHandler("tp", restricted(settings, security_manager, db, require_pin_session=True)(_settp)))

    async def _setsltp(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_setsltp(update, context, mexc_client, risk_engine)
    app.add_handler(CommandHandler("setsltp", restricted(settings, security_manager, db, require_pin_session=True)(_setsltp)))
    app.add_handler(CommandHandler("updatesltp", restricted(settings, security_manager, db, require_pin_session=True)(_setsltp)))
    app.add_handler(CommandHandler("sltp", restricted(settings, security_manager, db, require_pin_session=True)(_setsltp)))

    async def _panic(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_panic(update, context, mexc_client)
    app.add_handler(CommandHandler("panic", restricted(settings, security_manager, db, require_pin_session=True)(_panic)))
    app.add_handler(CommandHandler("closeall", restricted(settings, security_manager, db, require_pin_session=True)(_panic)))

    # 5. Callback Query Handlers for Confirmations and Navigation
    async def _trade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_trade_callback(update, context, mexc_client, db)
    app.add_handler(CallbackQueryHandler(_trade_callback, pattern=r"^(confirm_trade|cancel_trade):"))

    async def _panic_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await handle_panic_callback(update, context, mexc_client)
    app.add_handler(CallbackQueryHandler(_panic_callback, pattern=r"^(confirm_panic|cancel_panic):"))

    async def _nav_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        if data == "nav:positions":
            await handle_positions(update, context, mexc_client)
        elif data == "nav:balance":
            await handle_balance(update, context, mexc_client, db)
        elif data == "nav:orders":
            await handle_orders(update, context, mexc_client)
        elif data == "nav:scan4h":
            await handle_scan4h(update, context, mexc_client)
        elif data.startswith("nav:macdscan_"):
            side = data.replace("nav:macdscan_", "").lower()
            context.args = [side] if side in ["long", "short"] else []
            await handle_macdscan(update, context, mexc_client)
        elif data.startswith("nav:similar_"):
            parts = data.replace("nav:similar_", "").split("_")
            sym = parts[0]
            tf = parts[1] if len(parts) > 1 else "4h"
            context.args = [sym, tf]
            await handle_similar(update, context, mexc_client)
        elif data.startswith("nav:chart_"):
            parts = data.replace("nav:chart_", "").split("_")
            sym = parts[0]
            tf = parts[1] if len(parts) > 1 else "4h"
            context.args = [sym, tf]
            await handle_chart(update, context, mexc_client)
        elif data == "nav:chart_btc":
            context.args = ["BTC", "15m"]
            await handle_chart(update, context, mexc_client)
        elif data == "nav:help":
            await handle_help(update, context)
    app.add_handler(CallbackQueryHandler(_nav_callback, pattern=r"^nav:"))

    # 6. Persistent Bottom Keyboard Button Handler
    async def _text_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (update.effective_message.text or "").strip()
        if text in ["📊 Positions", "Positions"]:
            await handle_positions(update, context, mexc_client)
        elif text in ["💰 Balance", "Balance"]:
            await handle_balance(update, context, mexc_client, db)
        elif text in ["🔍 Scan 4H", "Scan 4H", "Scan"]:
            await handle_scan4h(update, context, mexc_client)
        elif text in ["📋 Orders", "Orders"]:
            await handle_orders(update, context, mexc_client)
        elif text in ["📈 Chart BTC", "📈 Chart", "Chart"]:
            context.args = ["BTC", "15m"]
            await handle_chart(update, context, mexc_client)
        elif text in ["⏰ Auto Positions", "Auto Positions"]:
            await handle_autopos(update, context, settings, notification_service)
        elif text in ["🎯 Watchlist", "Watchlist"]:
            await handle_watchlist(update, context, db)
        elif text in ["🛡️ Risk Limits", "Risk Limits"]:
            await handle_risklimit(update, context, db, settings)
        elif text in ["❓ Help", "Help"]:
            await handle_help(update, context)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, restricted(settings, security_manager, db)(_text_button_handler)))

    # Global Error Handler
    async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error(f"Uncaught Telegram exception: {context.error}", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An unexpected internal error occurred. Please try again.",
                parse_mode="Markdown"
            )
    app.add_error_handler(_error_handler)

    return app
