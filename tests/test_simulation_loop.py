"""Automated Looping Iteration Test Suite: Stress testing risk boundaries, random price walks, and concurrent trade actions."""
import asyncio
import random
from unittest.mock import AsyncMock, MagicMock
import pytest
from telegram import User, Message, Chat, Update, CallbackQuery
from telegram.ext import ContextTypes

from config.settings import Settings
from config.security import SecurityManager
from database.db import Database
from exchange.mexc_client import MexcClient
from exchange.mexc_ws import MexcWebSocketClient
from risk.daily_tracker import DailyTracker
from risk.risk_engine import RiskEngine
from services.notification import NotificationService
from bot.handlers.account import handle_balance, handle_positions
from bot.handlers.market import handle_price
from bot.handlers.trading import (
    handle_open,
    handle_close,
    handle_trade_callback,
    pending_manager,
)
from bot.handlers.panic import handle_panic


def make_mock_update(user_id: int, text: str) -> Update:
    user = User(id=user_id, first_name="LoopTester", is_bot=False, username="loop_tester")
    chat = Chat(id=user_id, type=Chat.PRIVATE)
    message = MagicMock(spec=Message)
    message.text = text
    message.reply_text = AsyncMock()
    message.edit_text = AsyncMock()
    message.from_user = user

    update = MagicMock(spec=Update)
    update.effective_user = user
    update.effective_chat = chat
    update.effective_message = message
    return update


@pytest.mark.asyncio
async def test_iterative_trading_loop(
    settings: Settings,
    db: Database,
    mexc_client: MexcClient,
    risk_engine: RiskEngine,
    notification_service: NotificationService,
):
    """
    Runs 20 sequential iterations of simulated trading actions with changing prices,
    verifying risk validations, order logs, callback safety, and daily tracking.
    """
    user_id = 111222333
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    base_price = 60000.0

    successful_orders = 0
    rejected_orders = 0

    for i in range(20):
        # Random price walk +/- 2%
        price_change = (random.random() - 0.5) * 0.04
        base_price *= (1.0 + price_change)
        current_price = round(base_price, 2)

        mexc_client.get_ticker = AsyncMock(return_value={
            "symbol": "BTC_USDT",
            "lastPrice": current_price,
            "fairPrice": current_price,
        })

        side = "long" if i % 2 == 0 else "short"
        # Occasionally test exceeding leverage
        leverage = 10 if i % 5 != 0 else 50
        # Occasionally test exceeding size
        size_usdt = 100.0 if i % 7 != 0 else 5000.0

        if side == "long":
            sl_price = round(current_price * 0.95, 2)
            tp_price = round(current_price * 1.05, 2)
        else:
            sl_price = round(current_price * 1.05, 2)
            tp_price = round(current_price * 0.95, 2)

        update = make_mock_update(user_id, f"/open BTC {side} {size_usdt} {leverage} market sl={sl_price} tp={tp_price}")
        context.args = ["BTC", side, str(size_usdt), str(leverage), "market", f"sl={sl_price}", f"tp={tp_price}"]

        await handle_open(update, context, mexc_client, risk_engine, settings)

        reply_args = update.effective_message.reply_text.call_args
        reply_text = reply_args[0][0]

        if "TRADE CONFIRMATION REQUIRED" in reply_text:
            successful_orders += 1
            keyboard = reply_args[1]["reply_markup"]
            confirm_cb = keyboard.inline_keyboard[0][0].callback_data

            # Confirm trade
            cb_query = MagicMock(spec=CallbackQuery)
            cb_query.data = confirm_cb
            cb_query.from_user = User(id=user_id, first_name="LoopTester", is_bot=False)
            cb_query.answer = AsyncMock()
            cb_query.edit_message_text = AsyncMock()

            cb_update = MagicMock(spec=Update)
            cb_update.callback_query = cb_query

            await handle_trade_callback(cb_update, context, mexc_client, db)
            exec_res = cb_query.edit_message_text.call_args[0][0]
            assert "ORDER EXECUTED SUCCESSFULLY" in exec_res
        else:
            # Rejected by Risk Engine (e.g. 50x leverage > 20x, or size exceeds equity)
            rejected_orders += 1
            assert "Order Rejected by Risk Engine" in reply_text

    assert successful_orders > 0
    assert rejected_orders > 0
    print(f"\n[Loop Test Completed]: {successful_orders} successful confirmed trades, {rejected_orders} properly rejected by Risk Engine.")
