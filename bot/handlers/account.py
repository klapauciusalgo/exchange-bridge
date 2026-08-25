"""Account and balance Telegram handlers with quick action buttons."""
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config.settings import Settings
from database.db import Database
from exchange.mexc_client import MexcClient
from bot.formatters import format_balance, format_positions, format_orders

logger = logging.getLogger(__name__)


async def handle_balance(update: Update, context: ContextTypes.DEFAULT_TYPE, client: MexcClient, db: Database) -> None:
    """Handle /balance command."""
    user_id = update.effective_user.id
    try:
        assets = await client.get_account_assets()
        stats = await db.get_or_create_daily_stats(user_id)
        msg = format_balance(assets, stats)

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Refresh Balance", callback_data="nav:balance"),
                InlineKeyboardButton("📊 View Positions", callback_data="nav:positions"),
            ],
            [
                InlineKeyboardButton("📋 Active Orders", callback_data="nav:orders"),
                InlineKeyboardButton("📈 Chart BTC", callback_data="nav:chart_btc"),
            ]
        ])

        if update.callback_query and update.callback_query.message:
            try:
                await update.callback_query.edit_message_text(msg, reply_markup=keyboard, parse_mode="Markdown")
                return
            except Exception:
                pass
        await update.effective_message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error fetching balance: {e}")
        await update.effective_message.reply_text(f"❌ *Failed to fetch balance:* `{e}`", parse_mode="Markdown")


async def handle_positions(update: Update, context: ContextTypes.DEFAULT_TYPE, client: MexcClient) -> None:
    """Handle /positions command."""
    try:
        positions = await client.get_open_positions()
        plan_orders = await client.get_plan_orders()
        contract_details = await client.get_contract_details()
        msg = format_positions(positions, plan_orders, contract_details)

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Refresh Positions", callback_data="nav:positions"),
                InlineKeyboardButton("💰 View Balance", callback_data="nav:balance"),
            ],
            [
                InlineKeyboardButton("📋 Active Orders", callback_data="nav:orders"),
                InlineKeyboardButton("📈 Chart BTC", callback_data="nav:chart_btc"),
            ]
        ])

        if update.callback_query and update.callback_query.message:
            try:
                await update.callback_query.edit_message_text(msg, reply_markup=keyboard, parse_mode="Markdown")
                return
            except Exception:
                pass
        await update.effective_message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error fetching positions: {e}")
        await update.effective_message.reply_text(f"❌ *Failed to fetch positions:* `{e}`", parse_mode="Markdown")


async def handle_orders(update: Update, context: ContextTypes.DEFAULT_TYPE, client: MexcClient) -> None:
    """Handle /orders command."""
    try:
        open_orders = await client.get_open_orders()
        plan_orders = await client.get_plan_orders()
        msg = format_orders(open_orders, plan_orders)

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Refresh Orders", callback_data="nav:orders"),
                InlineKeyboardButton("📊 View Positions", callback_data="nav:positions"),
            ]
        ])

        if update.callback_query and update.callback_query.message:
            try:
                await update.callback_query.edit_message_text(msg, reply_markup=keyboard, parse_mode="Markdown")
                return
            except Exception:
                pass
        await update.effective_message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error fetching orders: {e}")
        await update.effective_message.reply_text(f"❌ *Failed to fetch orders:* `{e}`", parse_mode="Markdown")


async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, client: MexcClient) -> None:
    """Handle /cancel <order_id> command."""
    if not context.args:
        await update.effective_message.reply_text("Usage: `/cancel <order_id>`", parse_mode="Markdown")
        return

    order_id = context.args[0].strip()
    try:
        # Try canceling standard order or plan order
        try:
            await client.cancel_order(order_id)
        except Exception:
            await client.cancel_plan_order(order_id)

        await update.effective_message.reply_text(f"✅ Order `{order_id}` successfully canceled.", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to cancel order {order_id}: {e}")
        await update.effective_message.reply_text(f"❌ *Failed to cancel order:* `{e}`", parse_mode="Markdown")
