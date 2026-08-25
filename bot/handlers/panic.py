"""Emergency Kill Switch (/panic, /closeall) handler."""
import asyncio
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, CallbackQuery, Message
from telegram.ext import ContextTypes

from exchange.mexc_client import MexcClient
from bot.handlers.trading import pending_manager

logger = logging.getLogger(__name__)


async def handle_panic(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    client: MexcClient,
) -> None:
    """
    Handle /panic or /closeall.
    Cancels all open orders and market closes all active positions immediately.
    """
    args = context.args or []
    is_forced_immediate = len(args) > 0 and args[0].lower() == "confirm"

    user_id = update.effective_user.id

    if is_forced_immediate:
        await execute_panic(update.effective_message, client, is_callback=False)
        return

    # Store pending action for confirmation button
    token = await pending_manager.store_action({
        "action": "PANIC",
        "user_id": user_id,
    })

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚨 EXECUTE KILL SWITCH NOW 🚨", callback_data=f"confirm_panic:{token}"),
        ],
        [
            InlineKeyboardButton("❌ Abort", callback_data=f"cancel_panic:{token}"),
        ]
    ])

    await update.effective_message.reply_text(
        "⚠️ *EMERGENCY KILL SWITCH ACTIVATION*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "This action will immediately:\n"
        "1. *Cancel ALL open limit orders*\n"
        "2. *Cancel ALL trigger SL/TP plan orders*\n"
        "3. *Market Close ALL open futures positions*\n\n"
        "Tap the button below to confirm, or `/panic confirm` for instant execution.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


async def handle_panic_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    client: MexcClient,
) -> None:
    """Handle callback for panic button."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if not (data.startswith("confirm_panic:") or data.startswith("cancel_panic:")):
        return

    action_type, token = data.split(":", 1)
    action = await pending_manager.pop_action(token)
    if not action:
        await query.edit_message_text("⚠️ *Kill switch action expired or already processed.*", parse_mode="Markdown")
        return

    if action_type == "cancel_panic":
        await query.edit_message_text("❌ *Kill switch aborted.*", parse_mode="Markdown")
        return

    await query.edit_message_text("🚨 *EXECUTING EMERGENCY KILL SWITCH...*", parse_mode="Markdown")
    await execute_panic(query, client, is_callback=True)


async def execute_panic(target, client: MexcClient, is_callback: bool = False) -> None:
    """Perform actual cancellation and position liquidation."""
    closed_count = 0
    canceled_orders = 0
    errors = []

    # 1. Cancel All Open Limit Orders
    try:
        await client.cancel_all_orders()
        canceled_orders += 1
    except Exception as e:
        logger.error(f"Panic: Error cancelling limit orders: {e}")
        errors.append(f"Cancel Orders: {e}")

    # 2. Cancel All Plan Orders
    try:
        await client.cancel_all_plan_orders()
        canceled_orders += 1
    except Exception as e:
        logger.error(f"Panic: Error cancelling plan orders: {e}")
        errors.append(f"Cancel Plan Orders: {e}")

    # 3. Fetch & Market Close All Open Positions
    try:
        positions = await client.get_open_positions()
        for pos in positions:
            sym = pos.get("symbol")
            hold_vol = float(pos.get("holdVol", 0.0))
            pos_type = pos.get("positionType", 1)
            side_code = 4 if pos_type == 1 else 2  # 4: Close Long, 2: Close Short

            if hold_vol > 0 and sym:
                try:
                    await client.submit_order(
                        symbol=sym,
                        side=side_code,
                        vol=int(hold_vol),
                        leverage=pos.get("leverage", 1),
                        order_type=5,  # Market Close
                    )
                    closed_count += 1
                except Exception as pos_err:
                    logger.error(f"Panic: Failed to close position {sym}: {pos_err}")
                    errors.append(f"Close {sym}: {pos_err}")
    except Exception as e:
        logger.error(f"Panic: Error fetching positions: {e}")
        errors.append(f"Fetch Positions: {e}")

    # Summary response
    error_section = ("\n\n*Warnings / Errors:*\n" + "\n".join(f"• `{err}`" for err in errors)) if errors else ""
    summary_text = (
        f"🚨 *KILL SWITCH EXECUTED*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• *Orders Canceled:* `All Limit & Trigger Orders`\n"
        f"• *Positions Closed:* `{closed_count}`\n"
        f"{error_section}"
    )

    if is_callback and hasattr(target, "edit_message_text"):
        await target.edit_message_text(summary_text, parse_mode="Markdown")
    elif hasattr(target, "reply_text"):
        await target.reply_text(summary_text, parse_mode="Markdown")
