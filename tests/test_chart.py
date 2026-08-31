"""Unit tests for candlestick chart generation and /chart bot command handler."""
from unittest.mock import AsyncMock, MagicMock
import pytest
from telegram import User, Message, Chat, Update
from telegram.ext import ContextTypes

from exchange.mexc_client import MexcClient
from services.chart_generator import generate_candlestick_chart, generate_multi_candlestick_chart
from bot.handlers.market import handle_chart


def make_mock_update(user_id: int, text: str) -> Update:
    user = User(id=user_id, first_name="ChartTester", is_bot=False, username="chart_tester")
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
    return update


def test_generate_candlestick_chart():
    # Construct synthetic 30-bar kline data
    base_time = 1700000000
    times = [base_time + (i * 900) for i in range(30)]
    opens = [100.0 + (i * 0.5) for i in range(30)]
    closes = [100.5 + (i * 0.5) for i in range(30)]
    highs = [101.5 + (i * 0.5) for i in range(30)]
    lows = [99.5 + (i * 0.5) for i in range(30)]
    vols = [1000.0 + (i * 50) for i in range(30)]

    kline_data = {
        "time": times,
        "open": opens,
        "close": closes,
        "high": highs,
        "low": lows,
        "vol": vols,
    }

    buf = generate_candlestick_chart("BTC_USDT", "15m", kline_data, num_candles=30)
    assert buf is not None
    img_bytes = buf.getvalue()
    assert len(img_bytes) > 20000
    # PNG signature check: \x89PNG\r\n\x1a\n
    assert img_bytes.startswith(b"\x89PNG\r\n\x1a\n")


def test_generate_multi_candlestick_chart():
    base_time = 1700000000
    k1 = {
        "time": [base_time + (i * 3600) for i in range(30)],
        "open": [2000.0 + i for i in range(30)],
        "close": [2005.0 + i for i in range(30)],
        "high": [2010.0 + i for i in range(30)],
        "low": [1995.0 + i for i in range(30)],
        "vol": [1000.0 + i for i in range(30)],
    }
    k2 = {
        "time": [base_time + (i * 14400) for i in range(30)],
        "open": [1950.0 + i * 2 for i in range(30)],
        "close": [1960.0 + i * 2 for i in range(30)],
        "high": [1970.0 + i * 2 for i in range(30)],
        "low": [1940.0 + i * 2 for i in range(30)],
        "vol": [5000.0 + i for i in range(30)],
    }

    buf = generate_multi_candlestick_chart("ETH_USDT", [("1h", k1), ("4h", k2)], num_candles=30)
    assert buf is not None
    img_bytes = buf.getvalue()
    assert len(img_bytes) > 30000
    assert img_bytes.startswith(b"\x89PNG\r\n\x1a\n")


def test_generate_candlestick_chart_insufficient_data():
    empty_data = {"time": [1700000000], "open": [100], "close": [100], "high": [100], "low": [100], "vol": [100]}
    buf = generate_candlestick_chart("BTC_USDT", "15m", empty_data)
    assert buf is None


@pytest.mark.asyncio
async def test_handle_chart_single_timeframe(mexc_client: MexcClient):
    user_id = 111222333
    update = make_mock_update(user_id, "/chart BTC 1h")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.args = ["BTC", "1h"]

    base_time = 1700000000
    mexc_client.get_kline = AsyncMock(return_value={
        "time": [base_time + (i * 3600) for i in range(25)],
        "open": [65000.0 + (i * 10) for i in range(25)],
        "close": [65020.0 + (i * 10) for i in range(25)],
        "high": [65100.0 + (i * 10) for i in range(25)],
        "low": [64950.0 + (i * 10) for i in range(25)],
        "vol": [5000.0 + i for i in range(25)],
    })
    mexc_client.get_ticker = AsyncMock(return_value={
        "symbol": "BTC_USDT",
        "lastPrice": 65250.0,
        "riseFallRate": 0.025,
    })

    status_mock = MagicMock(spec=Message)
    status_mock.edit_text = AsyncMock()
    status_mock.delete = AsyncMock()
    update.effective_message.reply_text = AsyncMock(return_value=status_mock)

    await handle_chart(update, context, mexc_client)

    update.effective_message.reply_photo.assert_called_once()
    photo_args = update.effective_message.reply_photo.call_args
    caption = photo_args[1]["caption"]

    assert "BTC_USDT" in caption
    assert "$65,250.0000" in caption


@pytest.mark.asyncio
async def test_handle_chart_multi_timeframe(mexc_client: MexcClient):
    user_id = 111222333
    update = make_mock_update(user_id, "/chart ETH 1h 4h")
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.args = ["ETH", "1h", "4h"]

    base_time = 1700000000
    mexc_client.get_kline = AsyncMock(return_value={
        "time": [base_time + (i * 3600) for i in range(25)],
        "open": [2500.0 + (i * 2) for i in range(25)],
        "close": [2510.0 + (i * 2) for i in range(25)],
        "high": [2520.0 + (i * 2) for i in range(25)],
        "low": [2490.0 + (i * 2) for i in range(25)],
        "vol": [1000.0 + i for i in range(25)],
    })
    mexc_client.get_ticker = AsyncMock(return_value={
        "symbol": "ETH_USDT",
        "lastPrice": 2510.0,
        "riseFallRate": 0.015,
    })

    status_mock = MagicMock(spec=Message)
    status_mock.edit_text = AsyncMock()
    status_mock.delete = AsyncMock()
    update.effective_message.reply_text = AsyncMock(return_value=status_mock)

    await handle_chart(update, context, mexc_client)

    update.effective_message.reply_photo.assert_called_once()
    photo_args = update.effective_message.reply_photo.call_args
    caption = photo_args[1]["caption"]

    assert "ETH_USDT" in caption
    assert "1H, 4H" in caption
    assert "$2,510.0000" in caption
