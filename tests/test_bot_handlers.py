"""Tests for Telegram Bot handlers, confirmation workflows, whitelist access control, and router handlers."""
from unittest.mock import AsyncMock, MagicMock
import pytest
from telegram import User, Message, Chat, Update, CallbackQuery
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler

from config.settings import Settings
from config.security import SecurityManager
from database.db import Database
from exchange.mexc_client import MexcClient
from risk.risk_engine import RiskEngine
from bot.telegram_bot import create_bot_app
from bot.middleware import restricted
from bot.handlers.account import handle_balance, handle_positions, handle_orders
from bot.handlers.market import handle_price, handle_watch, handle_watchlist, handle_unwatch, handle_chart
from bot.handlers.trading import (
    handle_open,
    handle_close,
    handle_setsl,
    handle_settp,
    handle_trade_callback,
    pending_manager,
)
from bot.handlers.panic import handle_panic, handle_panic_callback
from bot.handlers.admin import handle_start, handle_menu


def make_mock_update(user_id: int, text: str, args: list = None) -> Update:
    user = User(id=user_id, first_name="TestUser", is_bot=False, username="tester")
    chat = Chat(id=user_id, type=Chat.PRIVATE)
    message = MagicMock(spec=Message)
    message.text = text
    message.reply_text = AsyncMock()
    message.reply_photo = AsyncMock()
    message.edit_text = AsyncMock()
    message.delete = AsyncMock()
    message.from_user = user

    update = MagicMock(spec=Update)
    update.effective_user = user
    update.effective_chat = chat
    update.effective_message = message
    update.callback_query = None
    return update


@pytest.mark.asyncio
async def test_whitelist_middleware_authorized(settings: Settings, security_manager: SecurityManager, db: Database):
    user_id = 111222333  # In whitelist
    update = make_mock_update(user_id, "/balance")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    executed = False
    async def sample_handler(u, c):
        nonlocal executed
        executed = True

    decorated = restricted(settings, security_manager, db)(sample_handler)
    await decorated(update, context)

    assert executed is True
    logs = await db.get_recent_audit_logs(user_id)
    assert len(logs) == 1
    assert logs[0].status == "SUCCESS"


@pytest.mark.asyncio
async def test_whitelist_middleware_unauthorized(settings: Settings, security_manager: Settings, db: Database):
    unauthorized_id = 666777888  # NOT in whitelist
    update = make_mock_update(unauthorized_id, "/balance")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    executed = False
    async def sample_handler(u, c):
        nonlocal executed
        executed = True

    decorated = restricted(settings, security_manager, db)(sample_handler)
    await decorated(update, context)

    assert executed is False
    update.effective_message.reply_text.assert_called_once()
    assert "Unauthorized access" in update.effective_message.reply_text.call_args[0][0]

    logs = await db.get_recent_audit_logs(unauthorized_id)
    assert len(logs) == 1
    assert logs[0].status == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_handle_balance_flow(mexc_client: MexcClient, db: Database):
    update = make_mock_update(111222333, "/balance")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    await handle_balance(update, context, mexc_client, db)
    update.effective_message.reply_text.assert_called_once()
    reply_text = update.effective_message.reply_text.call_args[0][0]
    assert "MEXC FUTURES BALANCE" in reply_text
    assert "Total Equity:" in reply_text


@pytest.mark.asyncio
async def test_watchlist_flow(mexc_client: MexcClient, db: Database):
    user_id = 111222333
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    # 1. Add watch
    update_watch = make_mock_update(user_id, "/watch BTC above 70000")
    context.args = ["BTC", "above", "70000"]
    await handle_watch(update_watch, context, mexc_client, db)
    assert "Price alert created" in update_watch.effective_message.reply_text.call_args[0][0]

    # 2. View watchlist
    update_list = make_mock_update(user_id, "/watchlist")
    await handle_watchlist(update_list, context, db)
    list_text = update_list.effective_message.reply_text.call_args[0][0]
    assert "*BTC_USDT* ABOVE" in list_text

    # 3. Unwatch
    alerts = await db.get_user_watchlist(user_id)
    alert_id = alerts[0].id
    update_unwatch = make_mock_update(user_id, f"/unwatch {alert_id}")
    context.args = [str(alert_id)]
    await handle_unwatch(update_unwatch, context, db)
    assert f"Alert `#{alert_id}` removed" in update_unwatch.effective_message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_open_order_confirmation_and_double_click_protection(
    mexc_client: MexcClient,
    risk_engine: RiskEngine,
    settings: Settings,
    db: Database,
):
    # Mock ticker to a fixed price 65000
    mexc_client.get_ticker = AsyncMock(return_value={"symbol": "BTC_USDT", "lastPrice": 65000.0, "fairPrice": 65000.0})

    user_id = 111222333
    update = make_mock_update(user_id, "/open BTC long 100 10 market sl=60000 tp=75000")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.args = ["BTC", "long", "100", "10", "market", "sl=60000", "tp=75000"]

    # 1. User executes /open
    await handle_open(update, context, mexc_client, risk_engine, settings)
    update.effective_message.reply_text.assert_called_once()
    reply_args = update.effective_message.reply_text.call_args
    preview_msg = reply_args[0][0]
    keyboard = reply_args[1]["reply_markup"]

    assert "TRADE CONFIRMATION REQUIRED" in preview_msg
    assert "Stop Loss:" in preview_msg

    # Extract callback token
    confirm_button = keyboard.inline_keyboard[0][0]
    callback_data = confirm_button.callback_data
    assert callback_data.startswith("confirm_trade:")
    token = callback_data.split(":")[1]

    # 2. Simulate User Clicking "Confirm"
    cb_query = MagicMock(spec=CallbackQuery)
    cb_query.data = callback_data
    cb_query.from_user = User(id=user_id, first_name="TestUser", is_bot=False)
    cb_query.answer = AsyncMock()
    cb_query.edit_message_text = AsyncMock()

    cb_update = MagicMock(spec=Update)
    cb_update.callback_query = cb_query

    await handle_trade_callback(cb_update, context, mexc_client, db)
    cb_query.edit_message_text.assert_called()
    last_text = cb_query.edit_message_text.call_args[0][0]
    assert "ORDER EXECUTED SUCCESSFULLY" in last_text

    # 3. Simulate Double-Click / Race Condition
    cb_query2 = MagicMock(spec=CallbackQuery)
    cb_query2.data = callback_data
    cb_query2.from_user = User(id=user_id, first_name="TestUser", is_bot=False)
    cb_query2.answer = AsyncMock()
    cb_query2.edit_message_text = AsyncMock()
    cb_update2 = MagicMock(spec=Update)
    cb_update2.callback_query = cb_query2

    await handle_trade_callback(cb_update2, context, mexc_client, db)
    cb_query2.edit_message_text.assert_called_once()
    double_click_text = cb_query2.edit_message_text.call_args[0][0]
    assert "Action expired or already processed" in double_click_text


@pytest.mark.asyncio
async def test_panic_kill_switch_flow(mexc_client: MexcClient):
    user_id = 111222333
    update = make_mock_update(user_id, "/panic confirm")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.args = ["confirm"]

    await handle_panic(update, context, mexc_client)
    update.effective_message.reply_text.assert_called_once()
    summary = update.effective_message.reply_text.call_args[0][0]
    assert "KILL SWITCH EXECUTED" in summary
    assert "All Limit & Trigger Orders" in summary


@pytest.mark.asyncio
async def test_autopos_command_flow(settings: Settings):
    from bot.handlers.admin import handle_autopos

    user_id = 111222333
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    # 1. Check status
    update_check = make_mock_update(user_id, "/autopos")
    context.args = []
    await handle_autopos(update_check, context, settings)
    status_text = update_check.effective_message.reply_text.call_args[0][0]
    assert "AUTOMATED HOURLY POSITIONS SCHEDULE" in status_text

    # 2. Change interval
    update_change = make_mock_update(user_id, "/autopos 30")
    context.args = ["30"]
    await handle_autopos(update_change, context, settings)
    assert settings.AUTO_POSITIONS_INTERVAL_MINUTES == 30
    assert settings.AUTO_POSITIONS_ENABLED is True

    # 3. Disable
    update_off = make_mock_update(user_id, "/autopos off")
    context.args = ["off"]
    await handle_autopos(update_off, context, settings)
    assert settings.AUTO_POSITIONS_ENABLED is False


@pytest.mark.asyncio
async def test_menu_and_navigation_flow(
    settings: Settings,
    security_manager: SecurityManager,
    db: Database,
    mexc_client: MexcClient,
    risk_engine: RiskEngine,
):
    app = create_bot_app(
        settings=settings,
        security_manager=security_manager,
        db=db,
        mexc_client=mexc_client,
        risk_engine=risk_engine,
    )
    user_id = 111222333
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    # Test /menu
    update_menu = make_mock_update(user_id, "/menu")
    await handle_menu(update_menu, context)
    update_menu.effective_message.reply_text.assert_called_once()
    assert "MAIN CONTROL DASHBOARD" in update_menu.effective_message.reply_text.call_args[0][0]

    # Test Text Button clicks on MessageHandler
    text_handlers = [h for h in app.handlers.get(0, []) if isinstance(h, MessageHandler)]
    assert len(text_handlers) >= 1
    text_handler = text_handlers[0]

@pytest.mark.asyncio
async def test_setsl_and_settp_by_roi_percentage(mexc_client: MexcClient, risk_engine: RiskEngine):
    user_id = 111222333
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    # Mock open position: Long 20x at entry $100.00
    mexc_client.get_open_positions = AsyncMock(return_value=[{
        "symbol": "BTC_USDT",
        "positionType": 1,  # Long
        "leverage": 20,
        "openAvgPrice": 100.0,
    }])

    # 1. Test /setsl BTC 120% -> 120% / 20 = 6% drop -> SL = 100 * (1 - 0.06) = $94.00
    update_sl = make_mock_update(user_id, "/setsl BTC 120%")
    context.args = ["BTC", "120%"]
    await handle_setsl(update_sl, context, mexc_client, risk_engine)
    update_sl.effective_message.reply_text.assert_called_once()
    sl_text = update_sl.effective_message.reply_text.call_args[0][0]
    assert "$94.0000" in sl_text
    assert "-120.0%" in sl_text

    # 2. Test /settp BTC 200% -> 200% / 20 = 10% gain -> TP = 100 * (1 + 0.10) = $110.00
    update_tp = make_mock_update(user_id, "/settp BTC 200%")
    context.args = ["BTC", "200%"]
    await handle_settp(update_tp, context, mexc_client, risk_engine)
    update_tp.effective_message.reply_text.assert_called_once()
    tp_text = update_tp.effective_message.reply_text.call_args[0][0]
    assert "$110.0000" in tp_text
    assert "+200.0%" in tp_text

    # 3. Test /setsltp BTC 120% 200%
    from bot.handlers.trading import handle_setsltp
    update_sltp = make_mock_update(user_id, "/setsltp BTC 120% 200%")
    context.args = ["BTC", "120%", "200%"]
    await handle_setsltp(update_sltp, context, mexc_client, risk_engine)
    update_sltp.effective_message.reply_text.assert_called_once()
    sltp_text = update_sltp.effective_message.reply_text.call_args[0][0]
    assert "$94.0000" in sltp_text
    assert "$110.0000" in sltp_text


@pytest.mark.asyncio
async def test_app_router_handlers_registration(
    settings: Settings,
    security_manager: SecurityManager,
    db: Database,
    mexc_client: MexcClient,
    risk_engine: RiskEngine,
):
    """Verify that all router command callbacks are properly defined and callable without NameErrors."""
    app = create_bot_app(
        settings=settings,
        security_manager=security_manager,
        db=db,
        mexc_client=mexc_client,
        risk_engine=risk_engine,
    )

    user_id = 111222333
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.args = ["BTC", "15m"]

    # Mock get_kline and get_ticker for chart callback
    mexc_client.get_kline = AsyncMock(return_value={
        "time": [1700000000 + i * 900 for i in range(20)],
        "open": [65000.0 + i for i in range(20)],
        "close": [65010.0 + i for i in range(20)],
        "high": [65050.0 + i for i in range(20)],
        "low": [64990.0 + i for i in range(20)],
        "vol": [1000.0 + i for i in range(20)],
    })
    mexc_client.get_ticker = AsyncMock(return_value={"symbol": "BTC_USDT", "lastPrice": 65000.0, "riseFallRate": 0.01})

    status_mock = MagicMock(spec=Message)
    status_mock.edit_text = AsyncMock()
    status_mock.delete = AsyncMock()

    # Find and execute all command handlers registered on app
    command_handlers = [h for h in app.handlers.get(0, []) if isinstance(h, CommandHandler)]
    assert len(command_handlers) >= 15

    for h in command_handlers:
        cmd_name = list(h.commands)[0]
        update = make_mock_update(user_id, f"/{cmd_name} BTC")
        update.effective_message.reply_text = AsyncMock(return_value=status_mock)
        # Execute the callback function to verify no NameError or runtime binding error
        await h.callback(update, context)
