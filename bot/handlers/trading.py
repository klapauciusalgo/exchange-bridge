"""Trading execution handlers with pre-trade preview, risk checks, and inline confirmation."""
import asyncio
import logging
import re
import time
import uuid
from typing import Any, Dict, Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.settings import Settings
from database.db import Database
from database.models import OrderLogEntry
from exchange.mexc_client import MexcClient
from risk.risk_engine import RiskEngine
from bot.formatters import format_order_preview

logger = logging.getLogger(__name__)


class PendingActionManager:
    """Manages pending pre-trade confirmation actions with TTL and atomic locking."""

    def __init__(self, ttl_seconds: int = 90):
        self._actions: Dict[str, dict] = {}
        self._ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()

    async def store_action(self, action_data: dict) -> str:
        """Store pending action and return a unique token."""
        token = uuid.uuid4().hex[:12]
        action_data["created_at"] = time.monotonic()
        async with self._lock:
            self._actions[token] = action_data
        return token

    async def pop_action(self, token: str) -> Optional[dict]:
        """Atomically pop pending action. Returns None if already executed or expired."""
        async with self._lock:
            action = self._actions.pop(token, None)
            if not action:
                return None
            # Check TTL
            if time.monotonic() - action["created_at"] > self._ttl_seconds:
                return None
            return action


# Global pending action manager
pending_manager = PendingActionManager(ttl_seconds=90)


async def handle_open(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    client: MexcClient,
    risk_engine: RiskEngine,
    settings: Settings,
) -> None:
    """
    Handle /open command.
    Usage: /open <symbol> <long|short> <size_usdt> <leverage> [market|limit] [price] [sl=price] [tp=price]
    """
    args = context.args or []
    if len(args) < 4:
        await update.effective_message.reply_text(
            "📖 *Usage:*\n"
            "`/open <symbol> <long|short> <size_usdt> <leverage> [market|limit] [price] [sl=..] [tp=..]`\n\n"
            "Example:\n"
            "`/open BTC long 100 10 market sl=64000 tp=72000`",
            parse_mode="Markdown"
        )
        return

    symbol = client.normalize_symbol(args[0])
    side_raw = args[1].lower()
    if side_raw not in ["long", "short", "buy", "sell"]:
        await update.effective_message.reply_text("Side must be `long` or `short`.", parse_mode="Markdown")
        return
    side_is_long = side_raw in ["long", "buy"]

    try:
        size_usdt = float(args[2].replace("$", "").replace(",", ""))
        leverage = int(args[3].replace("x", ""))
    except ValueError:
        await update.effective_message.reply_text("Size and leverage must be valid numbers.", parse_mode="Markdown")
        return

    order_type_str = "market"
    limit_price = 0.0
    sl_raw: Optional[str] = None
    tp_raw: Optional[str] = None

    # Parse remaining optional arguments
    for arg in args[4:]:
        arg_lower = arg.lower()
        if arg_lower in ["market", "limit"]:
            order_type_str = arg_lower
        elif arg_lower.startswith("sl="):
            sl_raw = arg_lower.split("=")[1]
        elif arg_lower.startswith("tp="):
            tp_raw = arg_lower.split("=")[1]
        else:
            try:
                limit_price = float(arg)
            except ValueError:
                pass

    # Fetch current market price
    try:
        ticker = await client.get_ticker(symbol)
        if isinstance(ticker, list) and ticker:
            ticker = ticker[0]
        current_price = float(ticker.get("lastPrice", ticker.get("fairPrice", 0.0)))
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Failed to fetch current price for `{symbol}`: `{e}`", parse_mode="Markdown")
        return

    check_price = limit_price if (order_type_str == "limit" and limit_price > 0) else current_price

    # Compute SL/TP from price or ROI %
    sl_price: Optional[float] = None
    if sl_raw:
        if sl_raw.endswith("%"):
            try:
                sl_roi = float(sl_raw.replace("%", ""))
                price_move_pct = sl_roi / leverage
                sl_price = check_price * (1.0 - price_move_pct / 100.0) if side_is_long else check_price * (1.0 + price_move_pct / 100.0)
            except ValueError:
                pass
        else:
            try:
                sl_price = float(sl_raw)
            except ValueError:
                pass

    tp_price: Optional[float] = None
    if tp_raw:
        if tp_raw.endswith("%"):
            try:
                tp_roi = float(tp_raw.replace("%", ""))
                price_move_pct = tp_roi / leverage
                tp_price = check_price * (1.0 + price_move_pct / 100.0) if side_is_long else check_price * (1.0 - price_move_pct / 100.0)
            except ValueError:
                pass
        else:
            try:
                tp_price = float(tp_raw)
            except ValueError:
                pass

    # 1. Run through Risk Engine
    user_id = update.effective_user.id
    vr = await risk_engine.validate_open_order(
        user_id=user_id,
        symbol=symbol,
        side_is_long=side_is_long,
        size_usdt=size_usdt,
        leverage=leverage,
        current_price=check_price,
        stop_loss_price=sl_price,
        take_profit_price=tp_price,
    )

    if not vr.is_valid:
        await update.effective_message.reply_text(f"🛑 *Order Rejected by Risk Engine:*\n{vr.reason}", parse_mode="Markdown")
        return

    # 2. Store pending order
    try:
        details = await client.get_contract_details()
        detail = details.get(symbol, {})
        contract_size = float(detail.get("contractSize", 1.0))
        vol_scale = int(detail.get("volScale", 0))
        min_vol = float(detail.get("minVol", 1.0))
    except Exception:
        contract_size = 1.0
        vol_scale = 0
        min_vol = 1.0

    raw_vol = size_usdt / (check_price * contract_size)
    if vol_scale == 0:
        contract_vol = max(int(min_vol), int(round(raw_vol)))
    else:
        contract_vol = max(min_vol, round(raw_vol, vol_scale))

    action_data = {
        "action": "OPEN",
        "user_id": user_id,
        "symbol": symbol,
        "side_is_long": side_is_long,
        "size_usdt": size_usdt,
        "vol": contract_vol,
        "leverage": leverage,
        "order_type": 1 if order_type_str == "limit" else 5,
        "order_type_str": order_type_str,
        "price": limit_price if order_type_str == "limit" else 0.0,
        "current_price": current_price,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "is_dry_run": settings.DRY_RUN,
    }
    token = await pending_manager.store_action(action_data)

    # 3. Render confirmation UI
    preview_msg = format_order_preview(
        symbol=symbol,
        side_str="LONG" if side_is_long else "SHORT",
        size_usdt=size_usdt,
        leverage=leverage,
        order_type_str=order_type_str,
        price=check_price,
        vr=vr,
        sl_price=sl_price,
        tp_price=tp_price,
        is_dry_run=settings.DRY_RUN,
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm Open", callback_data=f"confirm_trade:{token}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_trade:{token}"),
        ]
    ])

    await update.effective_message.reply_text(preview_msg, reply_markup=keyboard, parse_mode="Markdown")


async def handle_close(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    client: MexcClient,
    settings: Settings,
) -> None:
    """
    Handle /close <symbol> [percentage=100].
    """
    if not context.args:
        await update.effective_message.reply_text("Usage: `/close <symbol> [percentage]`\nExample: `/close BTC` or `/close BTC 50`", parse_mode="Markdown")
        return

    symbol = client.normalize_symbol(context.args[0])
    pct = 100.0
    if len(context.args) > 1:
        try:
            pct = float(context.args[1].replace("%", ""))
        except ValueError:
            pct = 100.0

    user_id = update.effective_user.id
    action_data = {
        "action": "CLOSE",
        "user_id": user_id,
        "symbol": symbol,
        "percentage": pct,
        "is_dry_run": settings.DRY_RUN,
    }
    token = await pending_manager.store_action(action_data)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"✅ Confirm Close ({pct:.0f}%)", callback_data=f"confirm_trade:{token}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_trade:{token}"),
        ]
    ])

    await update.effective_message.reply_text(
        f"⚠️ *CONFIRM POSITION CLOSE*\n\n"
        f"• *Symbol:* `{symbol}`\n"
        f"• *Close Amount:* `{pct:.0f}%` of position\n"
        f"• *Execution:* Market Order\n\n"
        f"Tap *Confirm* to execute close order.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


async def handle_setsl(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    client: MexcClient,
    risk_engine: RiskEngine,
) -> None:
    """
    Handle /setsl <symbol> <price|ROI%>
    Supports exact price (e.g. /setsl BTC 62000) or ROI% (e.g. /setsl BTC 120% -> 6% drop at 20x).
    """
    if len(context.args or []) < 2:
        await update.effective_message.reply_text(
            "📖 *Usage:* `/setsl <symbol> <price|ROI%>`\n\n"
            "*Examples:*\n"
            "• `/setsl BTC 62000` (Exact Price)\n"
            "• `/setsl BTC 120%` (ROI Percentage: 120% target loss on margin / leverage)",
            parse_mode="Markdown"
        )
        return

    symbol = client.normalize_symbol(context.args[0])
    raw_arg = context.args[1].strip()
    is_percentage = raw_arg.endswith("%")

    positions = await client.get_open_positions(symbol)
    pos = positions[0] if positions else None

    if is_percentage:
        if not pos:
            await update.effective_message.reply_text(
                f"❌ *No active position found for `{symbol}`.* To set SL by percentage, an open position is required.",
                parse_mode="Markdown"
            )
            return

        try:
            roi_pct = float(raw_arg.replace("%", "").replace("$", ""))
        except ValueError:
            await update.effective_message.reply_text("Invalid percentage format. Example: `/setsl BTC 120%`", parse_mode="Markdown")
            return

        entry_price = float(pos.get("openAvgPrice", pos.get("holdAvgPrice", pos.get("openPrice", 0.0))))
        leverage = float(pos.get("leverage", 1))
        pos_type = pos.get("positionType", 1)  # 1: Long, 2: Short

        if entry_price <= 0 or leverage <= 0:
            await update.effective_message.reply_text("❌ Could not determine entry price or leverage for position.", parse_mode="Markdown")
            return

        price_move_pct = roi_pct / leverage
        if pos_type == 1:  # Long: SL triggers below entry
            sl_price = entry_price * (1.0 - (price_move_pct / 100.0))
        else:  # Short: SL triggers above entry
            sl_price = entry_price * (1.0 + (price_move_pct / 100.0))

        extra_info = (
            f"• *Side:* `{'🟢 LONG' if pos_type == 1 else '🔴 SHORT'}` `{int(leverage)}x`\n"
            f"• *Entry Price:* `${entry_price:,.4f}`\n"
            f"• *ROI Target:* `-{roi_pct:.1f}%` (Price Move: `{-price_move_pct:.2f}%`)\n"
        )
    else:
        try:
            sl_price = float(raw_arg.replace("$", ""))
            extra_info = ""
        except ValueError:
            await update.effective_message.reply_text("Invalid SL price or percentage. Examples:\n• `/setsl BTC 62000` (Price)\n• `/setsl BTC 120%` (ROI%)", parse_mode="Markdown")
            return

    user_id = update.effective_user.id
    action_data = {
        "action": "SETSL",
        "user_id": user_id,
        "symbol": symbol,
        "sl_price": sl_price,
    }
    token = await pending_manager.store_action(action_data)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"✅ Confirm SL @ ${sl_price:,.4f}", callback_data=f"confirm_trade:{token}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_trade:{token}"),
        ]
    ])
    await update.effective_message.reply_text(
        f"🛡️ *CONFIRM STOP LOSS ORDER*\n\n"
        f"• *Symbol:* `{symbol}`\n"
        f"{extra_info}"
        f"• *Stop Price:* `${sl_price:,.4f}`",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


async def handle_settp(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    client: MexcClient,
    risk_engine: RiskEngine,
) -> None:
    """
    Handle /settp <symbol> <price|ROI%>
    Supports exact price (e.g. /settp BTC 72000) or ROI% (e.g. /settp BTC 200% -> 10% gain at 20x).
    """
    if len(context.args or []) < 2:
        await update.effective_message.reply_text(
            "📖 *Usage:* `/settp <symbol> <price|ROI%>`\n\n"
            "*Examples:*\n"
            "• `/settp BTC 72000` (Exact Price)\n"
            "• `/settp BTC 200%` (ROI Percentage: 200% target profit on margin / leverage)",
            parse_mode="Markdown"
        )
        return

    symbol = client.normalize_symbol(context.args[0])
    raw_arg = context.args[1].strip()
    is_percentage = raw_arg.endswith("%")

    positions = await client.get_open_positions(symbol)
    pos = positions[0] if positions else None

    if is_percentage:
        if not pos:
            await update.effective_message.reply_text(
                f"❌ *No active position found for `{symbol}`.* To set TP by percentage, an open position is required.",
                parse_mode="Markdown"
            )
            return

        try:
            roi_pct = float(raw_arg.replace("%", "").replace("$", ""))
        except ValueError:
            await update.effective_message.reply_text("Invalid percentage format. Example: `/settp BTC 200%`", parse_mode="Markdown")
            return

        entry_price = float(pos.get("openAvgPrice", pos.get("holdAvgPrice", pos.get("openPrice", 0.0))))
        leverage = float(pos.get("leverage", 1))
        pos_type = pos.get("positionType", 1)  # 1: Long, 2: Short

        if entry_price <= 0 or leverage <= 0:
            await update.effective_message.reply_text("❌ Could not determine entry price or leverage for position.", parse_mode="Markdown")
            return

        price_move_pct = roi_pct / leverage
        if pos_type == 1:  # Long: TP triggers above entry
            tp_price = entry_price * (1.0 + (price_move_pct / 100.0))
        else:  # Short: TP triggers below entry
            tp_price = entry_price * (1.0 - (price_move_pct / 100.0))

        extra_info = (
            f"• *Side:* `{'🟢 LONG' if pos_type == 1 else '🔴 SHORT'}` `{int(leverage)}x`\n"
            f"• *Entry Price:* `${entry_price:,.4f}`\n"
            f"• *ROI Target:* `+{roi_pct:.1f}%` (Price Move: `+{price_move_pct:.2f}%`)\n"
        )
    else:
        try:
            tp_price = float(raw_arg.replace("$", ""))
            extra_info = ""
        except ValueError:
            await update.effective_message.reply_text("Invalid TP price or percentage. Examples:\n• `/settp BTC 72000` (Price)\n• `/settp BTC 200%` (ROI%)", parse_mode="Markdown")
            return

    user_id = update.effective_user.id
    action_data = {
        "action": "SETTP",
        "user_id": user_id,
        "symbol": symbol,
        "tp_price": tp_price,
    }
    token = await pending_manager.store_action(action_data)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"✅ Confirm TP @ ${tp_price:,.4f}", callback_data=f"confirm_trade:{token}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_trade:{token}"),
        ]
    ])
    await update.effective_message.reply_text(
        f"🎯 *CONFIRM TAKE PROFIT ORDER*\n\n"
        f"• *Symbol:* `{symbol}`\n"
        f"{extra_info}"
        f"• *Take Profit Price:* `${tp_price:,.4f}`",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


async def handle_setsltp(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    client: MexcClient,
    risk_engine: RiskEngine,
) -> None:
    """
    Handle /setsltp <symbol> <sl_price|sl_ROI%> <tp_price|tp_ROI%>
    Simultaneously updates both Stop Loss and Take Profit.
    Examples:
    • /setsltp BTC 120% 200%
    • /setsltp BTC 58000 68000
    • /setsltp HOLO 140% 200%
    """
    args = context.args or []
    if len(args) < 3:
        await update.effective_message.reply_text(
            "📖 *Usage:* `/setsltp <symbol> <sl_price|sl_ROI%> <tp_price|tp_ROI%>`\n\n"
            "*Examples:*\n"
            "• `/setsltp BTC 120% 200%` (ROI% on Margin)\n"
            "• `/setsltp BTC 58000 68000` (Exact Prices)",
            parse_mode="Markdown"
        )
        return

    symbol = client.normalize_symbol(args[0])
    sl_raw = args[1].strip()
    tp_raw = args[2].strip()

    positions = await client.get_open_positions(symbol)
    pos = positions[0] if positions else None
    if not pos:
        await update.effective_message.reply_text(
            f"❌ *No active position found for `{symbol}`.*",
            parse_mode="Markdown"
        )
        return

    entry_price = float(pos.get("openAvgPrice", pos.get("holdAvgPrice", pos.get("openPrice", 0.0))))
    leverage = float(pos.get("leverage", 1))
    pos_type = pos.get("positionType", 1)  # 1: Long, 2: Short

    # Calculate SL
    if sl_raw.endswith("%"):
        try:
            sl_roi = float(sl_raw.replace("%", "").replace("$", ""))
            price_move_sl = sl_roi / leverage
            sl_price = entry_price * (1.0 - price_move_sl / 100.0) if pos_type == 1 else entry_price * (1.0 + price_move_sl / 100.0)
        except ValueError:
            await update.effective_message.reply_text("Invalid SL percentage format.", parse_mode="Markdown")
            return
    else:
        try:
            sl_price = float(sl_raw.replace("$", ""))
        except ValueError:
            await update.effective_message.reply_text("Invalid SL price.", parse_mode="Markdown")
            return

    # Calculate TP
    if tp_raw.endswith("%"):
        try:
            tp_roi = float(tp_raw.replace("%", "").replace("$", ""))
            price_move_tp = tp_roi / leverage
            tp_price = entry_price * (1.0 + price_move_tp / 100.0) if pos_type == 1 else entry_price * (1.0 - price_move_tp / 100.0)
        except ValueError:
            await update.effective_message.reply_text("Invalid TP percentage format.", parse_mode="Markdown")
            return
    else:
        try:
            tp_price = float(tp_raw.replace("$", ""))
        except ValueError:
            await update.effective_message.reply_text("Invalid TP price.", parse_mode="Markdown")
            return

    user_id = update.effective_user.id
    action_data = {
        "action": "SETSLTP",
        "user_id": user_id,
        "symbol": symbol,
        "sl_price": sl_price,
        "tp_price": tp_price,
    }
    token = await pending_manager.store_action(action_data)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm SL & TP", callback_data=f"confirm_trade:{token}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_trade:{token}"),
        ]
    ])
    await update.effective_message.reply_text(
        f"🛡️ *CONFIRM STOP LOSS & TAKE PROFIT*\n\n"
        f"• *Symbol:* `{symbol}` ({'🟢 LONG' if pos_type == 1 else '🔴 SHORT'} `{int(leverage)}x`)\n"
        f"• *Entry Price:* `${entry_price:,.4f}`\n"
        f"• *Stop Loss Price:* `${sl_price:,.4f}`\n"
        f"• *Take Profit Price:* `${tp_price:,.4f}`",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


def _extract_order_id(res: Any) -> str:
    """Safely extract order ID string from dictionary, scalar int/str, or response object."""
    if isinstance(res, dict):
        return str(res.get("orderId", res.get("id", "")))
    return str(res) if res is not None else ""


async def handle_trade_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    client: MexcClient,
    db: Database,
) -> None:
    """
    Handle confirmation button callback queries.
    Prevents double clicks and race conditions by atomically popping the action token.
    """
    query = update.callback_query
    await query.answer()

    data = query.data
    if not (data.startswith("confirm_trade:") or data.startswith("cancel_trade:")):
        return

    action_type, token = data.split(":", 1)

    # Atomically retrieve and remove the pending action
    action = await pending_manager.pop_action(token)
    if not action:
        await query.edit_message_text("⚠️ *Action expired or already processed.*", parse_mode="Markdown")
        return

    # Check caller identity
    if query.from_user.id != action.get("user_id"):
        await query.answer("You are not authorized to confirm this action.", show_alert=True)
        return

    if action_type == "cancel_trade":
        await query.edit_message_text("❌ *Trade execution cancelled by user.*", parse_mode="Markdown")
        return

    # Execution Flow
    await query.edit_message_text("⏳ *Executing order on MEXC...*", parse_mode="Markdown")

    act = action.get("action")
    symbol = action.get("symbol")

    try:
        if act == "OPEN":
            # 1. Set leverage
            leverage = action["leverage"]
            pos_type = 1 if action["side_is_long"] else 2
            try:
                await client.set_leverage(symbol, leverage, pos_type)
            except Exception as e:
                logger.warning(f"Could not update leverage: {e}")

            # 2. Submit order
            side_code = 1 if action["side_is_long"] else 3
            res = await client.submit_order(
                symbol=symbol,
                side=side_code,
                vol=action["vol"],
                leverage=leverage,
                order_type=action["order_type"],
                price=action["price"],
                stop_loss_price=action.get("sl_price"),
                take_profit_price=action.get("tp_price"),
            )

            order_id = _extract_order_id(res)

            # Record in DB
            await db.record_order(OrderLogEntry(
                mexc_order_id=order_id,
                symbol=symbol,
                side="OPEN_LONG" if action["side_is_long"] else "OPEN_SHORT",
                order_type=action["order_type_str"].upper(),
                price=action["price"],
                volume=action["vol"],
                leverage=leverage,
                status="NEW",
                is_dry_run=action.get("is_dry_run", False),
            ))

            mode_badge = "🧪 [SIMULATED] " if action.get("is_dry_run") else ""
            msg = (
                f"✅ {mode_badge}*ORDER EXECUTED SUCCESSFULLY*\n\n"
                f"• *Symbol:* `{symbol}`\n"
                f"• *Action:* `{'OPEN LONG' if action['side_is_long'] else 'OPEN SHORT'}`\n"
                f"• *Leverage:* `{leverage}x`\n"
                f"• *Order ID:* `{order_id}`"
            )
            await query.edit_message_text(msg, parse_mode="Markdown")

        elif act == "CLOSE":
            # Fetch open position to get hold volume and position type
            positions = await client.get_open_positions(symbol)
            if not positions:
                await query.edit_message_text(f"❌ *No open position found for `{symbol}` to close.*", parse_mode="Markdown")
                return

            pos = positions[0]
            pos_type = pos.get("positionType", 1)  # 1: Long, 2: Short
            hold_vol = float(pos.get("holdVol", 0.0))
            pct = action.get("percentage", 100.0)
            if pct >= 100.0:
                close_vol = int(hold_vol)
            else:
                close_vol = max(1, int(round(hold_vol * (pct / 100.0))))
            side_code = 4 if pos_type == 1 else 2  # 4: Close Long, 2: Close Short

            res = await client.submit_order(
                symbol=symbol,
                side=side_code,
                vol=close_vol,
                leverage=pos.get("leverage", 1),
                order_type=5,  # Market close
            )
            order_id = _extract_order_id(res)

            await query.edit_message_text(
                f"✅ *POSITION CLOSED*\n\n"
                f"• *Symbol:* `{symbol}`\n"
                f"• *Closed Amount:* `{close_vol:,.0f}` contracts\n"
                f"• *Order ID:* `{order_id}`",
                parse_mode="Markdown"
            )

        elif act == "SETSL":
            sl_price = action["sl_price"]
            positions = await client.get_open_positions(symbol)
            if not positions:
                if client.dry_run or action.get("is_dry_run"):
                    pos_id = "sim_pos_1"
                    pos_type = 1
                else:
                    await query.edit_message_text(f"❌ *No open position found for `{symbol}`.*", parse_mode="Markdown")
                    return
            else:
                pos = positions[0]
                pos_id = pos.get("positionId")
                pos_type = pos.get("positionType", 1)
            loss_trend = 1 if pos_type == 1 else 2

            res = await client.place_stop_order(
                symbol=symbol,
                position_id=pos_id,
                stop_loss_price=sl_price,
                loss_trend=loss_trend,
            )
            order_id = _extract_order_id(res)
            await query.edit_message_text(
                f"🛡️ *STOP LOSS CONFIGURED*\n\n"
                f"• *Symbol:* `{symbol}`\n"
                f"• *Stop Loss Price:* `${sl_price:,.4f}`\n"
                f"• *Stop Order ID:* `{order_id}`",
                parse_mode="Markdown"
            )

        elif act == "SETTP":
            tp_price = action["tp_price"]
            positions = await client.get_open_positions(symbol)
            if not positions:
                if client.dry_run or action.get("is_dry_run"):
                    pos_id = "sim_pos_1"
                    pos_type = 1
                else:
                    await query.edit_message_text(f"❌ *No open position found for `{symbol}`.*", parse_mode="Markdown")
                    return
            else:
                pos = positions[0]
                pos_id = pos.get("positionId")
                pos_type = pos.get("positionType", 1)
            profit_trend = 1 if pos_type == 1 else 2

            res = await client.place_stop_order(
                symbol=symbol,
                position_id=pos_id,
                take_profit_price=tp_price,
                profit_trend=profit_trend,
            )
        elif act == "SETSLTP":
            sl_price = action["sl_price"]
            tp_price = action["tp_price"]
            positions = await client.get_open_positions(symbol)
            if not positions:
                if client.dry_run or action.get("is_dry_run"):
                    pos_id = "sim_pos_1"
                    pos_type = 1
                else:
                    await query.edit_message_text(f"❌ *No open position found for `{symbol}`.*", parse_mode="Markdown")
                    return
            else:
                pos = positions[0]
                pos_id = pos.get("positionId")
                pos_type = pos.get("positionType", 1)
            loss_trend = 1 if pos_type == 1 else 2
            profit_trend = 1 if pos_type == 1 else 2

            res = await client.place_stop_order(
                symbol=symbol,
                position_id=pos_id,
                stop_loss_price=sl_price,
                take_profit_price=tp_price,
                loss_trend=loss_trend,
                profit_trend=profit_trend,
            )
            order_id = _extract_order_id(res)
            await query.edit_message_text(
                f"🛡️ *SL & TP CONFIGURED SUCCESSFULLY*\n\n"
                f"• *Symbol:* `{symbol}`\n"
                f"• *Stop Loss Price:* `${sl_price:,.4f}`\n"
                f"• *Take Profit Price:* `${tp_price:,.4f}`\n"
                f"• *Stop Order ID:* `{order_id}`",
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"Error executing trade action {act}: {e}")
        await query.edit_message_text(f"❌ *Trade Execution Failed:* `{e}`", parse_mode="Markdown")
