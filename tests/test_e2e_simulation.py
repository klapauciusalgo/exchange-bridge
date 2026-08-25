"""End-to-End simulation test iterating through a complete trader lifecycle."""
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
from bot.handlers.market import handle_price, handle_market, handle_watch
from bot.handlers.trading import handle_open, handle_setsl, handle_trade_callback
from bot.handlers.panic import handle_panic


def make_mock_update(user_id: int, text: str) -> Update:
    user = User(id=user_id, first_name="E2ETrader", is_bot=False, username="e2e_trader")
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
async def test_full_trading_lifecycle_simulation(
    settings: Settings,
    security_manager: SecurityManager,
    db: Database,
    mexc_client: MexcClient,
    ws_client: MexcWebSocketClient,
    risk_engine: RiskEngine,
    notification_service: NotificationService,
):
    # Set deterministic ticker return
    mexc_client.get_ticker = AsyncMock(return_value={
        "symbol": "BTC_USDT",
        "lastPrice": 65000.0,
        "fairPrice": 65000.0,
        "riseFallRate": 0.035,
        "high24Price": 66000.0,
        "low24Price": 64000.0,
        "volume24": 50000000.0,
        "indexPrice": 64990.0,
    })
    mexc_client.get_funding_rate = AsyncMock(return_value={
        "symbol": "BTC_USDT",
        "fundingRate": 0.0001,
        "nextSettleTime": 1771977600000,
    })
    mexc_client.get_symbol_detail = AsyncMock(return_value={
        "symbol": "BTC_USDT",
        "maxLeverage": 125,
        "state": 0,
    })

    user_id = 111222333
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    # ----------------------------------------------------
    # Step 1: User checks balance
    # ----------------------------------------------------
    update_bal = make_mock_update(user_id, "/balance")
    await handle_balance(update_bal, context, mexc_client, db)
    bal_text = update_bal.effective_message.reply_text.call_args[0][0]
    assert "MEXC FUTURES BALANCE" in bal_text
    assert "Total Equity:" in bal_text

    # ----------------------------------------------------
    # Step 2: User checks market data
    # ----------------------------------------------------
    update_mkt = make_mock_update(user_id, "/market BTC")
    context.args = ["BTC"]
    await handle_market(update_mkt, context, mexc_client)
    mkt_text = update_mkt.effective_message.reply_text.call_args[0][0]
    assert "MARKET METRICS: BTC_USDT" in mkt_text

    # ----------------------------------------------------
    # Step 3: User sets a price alert
    # ----------------------------------------------------
    update_watch = make_mock_update(user_id, "/watch BTC above 75000")
    context.args = ["BTC", "above", "75000"]
    await handle_watch(update_watch, context, mexc_client, db)
    watch_text = update_watch.effective_message.reply_text.call_args[0][0]
    assert "Price alert created" in watch_text

    # ----------------------------------------------------
    # Step 4: User opens a new LONG position
    # ----------------------------------------------------
    update_open = make_mock_update(user_id, "/open BTC long 200 10 market sl=60000 tp=80000")
    context.args = ["BTC", "long", "200", "10", "market", "sl=60000", "tp=80000"]
    await handle_open(update_open, context, mexc_client, risk_engine, settings)

    open_reply = update_open.effective_message.reply_text.call_args
    preview_card = open_reply[0][0]
    keyboard = open_reply[1]["reply_markup"]

    assert "TRADE CONFIRMATION REQUIRED" in preview_card
    assert "*LONG*" in preview_card
    assert "10x" in preview_card
    assert "*Stop Loss:* `$60,000.0000`" in preview_card
    assert "*Take Profit:* `$80,000.0000`" in preview_card

    # ----------------------------------------------------
    # Step 5: User confirms the order via inline button
    # ----------------------------------------------------
    confirm_callback_data = keyboard.inline_keyboard[0][0].callback_data
    cb_query = MagicMock(spec=CallbackQuery)
    cb_query.data = confirm_callback_data
    cb_query.from_user = User(id=user_id, first_name="E2ETrader", is_bot=False)
    cb_query.answer = AsyncMock()
    cb_query.edit_message_text = AsyncMock()

    cb_update = MagicMock(spec=Update)
    cb_update.callback_query = cb_query

    await handle_trade_callback(cb_update, context, mexc_client, db)
    exec_text = cb_query.edit_message_text.call_args[0][0]
    assert "ORDER EXECUTED SUCCESSFULLY" in exec_text

    # ----------------------------------------------------
    # Step 6: Order Fill notification is received from WebSocket
    # ----------------------------------------------------
    broadcast_mock = AsyncMock()
    notification_service.broadcast_alert = broadcast_mock

    ws_fill_event = {
        "id": "sim_ord_e2e_1",
        "symbol": "BTC_USDT",
        "status": 3,  # FILLED
        "side": 1,
        "dealAvgPrice": 65500.0,
        "dealVol": 0.03,
    }
    await notification_service._handle_ws_order_update(ws_fill_event)
    broadcast_mock.assert_called_once()
    fill_broadcast_msg = broadcast_mock.call_args[0][0]
    assert "ORDER FILLED" in fill_broadcast_msg
    assert "BTC_USDT" in fill_broadcast_msg

    # ----------------------------------------------------
    # Step 7: User updates Stop Loss
    # ----------------------------------------------------
    update_sl = make_mock_update(user_id, "/setsl BTC 63000")
    context.args = ["BTC", "63000"]
    await handle_setsl(update_sl, context, mexc_client, risk_engine)
    sl_reply = update_sl.effective_message.reply_text.call_args
    sl_card = sl_reply[0][0]
    sl_keyboard = sl_reply[1]["reply_markup"]

    assert "CONFIRM STOP LOSS ORDER" in sl_card
    assert "$63,000.0000" in sl_card

    # Confirm SL
    sl_cb_data = sl_keyboard.inline_keyboard[0][0].callback_data
    sl_cb_query = MagicMock(spec=CallbackQuery)
    sl_cb_query.data = sl_cb_data
    sl_cb_query.from_user = User(id=user_id, first_name="E2ETrader", is_bot=False)
    sl_cb_query.answer = AsyncMock()
    sl_cb_query.edit_message_text = AsyncMock()
    sl_cb_update = MagicMock(spec=Update)
    sl_cb_update.callback_query = sl_cb_query

    await handle_trade_callback(sl_cb_update, context, mexc_client, db)
    assert "STOP LOSS CONFIGURED" in sl_cb_query.edit_message_text.call_args[0][0]

    # ----------------------------------------------------
    # Step 8: Emergency Panic / Close All
    # ----------------------------------------------------
    update_panic = make_mock_update(user_id, "/panic confirm")
    context.args = ["confirm"]
    await handle_panic(update_panic, context, mexc_client)
    panic_text = update_panic.effective_message.reply_text.call_args[0][0]
    assert "KILL SWITCH EXECUTED" in panic_text

    # ----------------------------------------------------
    # Step 9: Verify Database Records
    # ----------------------------------------------------
    active_watchlist = await db.get_user_watchlist(user_id)
    assert len(active_watchlist) == 1
    assert active_watchlist[0].symbol == "BTC_USDT"

    stats = await db.get_or_create_daily_stats(user_id)
    assert stats.user_id == user_id
