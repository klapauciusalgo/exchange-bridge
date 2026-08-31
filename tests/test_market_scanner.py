"""Tests for 4H Market Scanner, MACD Confluence Scanner, and RSI calculation."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from telegram import Update, Message, CallbackQuery, User
from telegram.ext import ContextTypes

from services.market_scanner import MarketScanner, compute_rsi
from exchange.mexc_client import MexcClient
from bot.handlers.market import handle_scan4h, handle_macdscan
from bot.formatters import format_scan_results, format_macd_scan_results


def test_compute_rsi_calculation():
    """Verify RSI calculation logic across trending and ranging prices."""
    # 1. Monotonically increasing prices -> RSI should be high (> 80)
    uptrend = [100.0 + i * 2.0 for i in range(25)]
    rsi_up = compute_rsi(uptrend, period=14)
    assert rsi_up > 80.0

    # 2. Monotonically decreasing prices -> RSI should be low (< 20)
    downtrend = [100.0 - i * 2.0 for i in range(25)]
    rsi_down = compute_rsi(downtrend, period=14)
    assert rsi_down < 20.0

    # 3. Flat prices -> RSI 50
    flat = [100.0 for _ in range(25)]
    rsi_flat = compute_rsi(flat, period=14)
    assert rsi_flat == 50.0

    # 4. Insufficient data -> default 50.0
    assert compute_rsi([100.0, 101.0], period=14) == 50.0


@pytest.mark.asyncio
async def test_market_scanner_long_and_short_filtering(mexc_client: MexcClient):
    """Verify market scanner identifies Longs (RSI > 55, FR 0.001%-0.01%) and Shorts (RSI < 45, FR > 0.1% or < 0%)."""
    mexc_client._request = AsyncMock(return_value=[
        {
            "symbol": "AAA_USDT",
            "fundingRate": 0.00005,  # 0.005%
            "amount24": 5_000_000.0,
            "lastPrice": 10.0,
            "riseFallRate": 0.05,
        },
        {
            "symbol": "BBB_USDT",
            "fundingRate": -0.0001,  # -0.01%
            "amount24": 2_000_000.0,
            "lastPrice": 5.0,
            "riseFallRate": -0.04,
        },
        {
            "symbol": "CCC_USDT",
            "fundingRate": 0.0015,  # 0.15%
            "amount24": 1_000_000.0,
            "lastPrice": 1.0,
            "riseFallRate": -0.02,
        },
        {
            "symbol": "DDD_USDT",
            "fundingRate": 0.0005,  # 0.05%
            "amount24": 1_000_000.0,
            "lastPrice": 2.0,
            "riseFallRate": 0.01,
        },
    ])

    async def mock_get_kline(symbol, interval="4h", start=None, end=None):
        if symbol == "AAA_USDT":
            return {"close": [10.0 + i * 0.5 for i in range(25)]}
        elif symbol in ["BBB_USDT", "CCC_USDT"]:
            return {"close": [10.0 - i * 0.5 for i in range(25)]}
        return {"close": [10.0 for _ in range(25)]}

    mexc_client.get_kline = AsyncMock(side_effect=mock_get_kline)

    scanner = MarketScanner(mexc_client)
    res = await scanner.scan_4h(min_volume_usdt=100_000)

    assert len(res["longs"]) == 1
    assert res["longs"][0]["symbol"] == "AAA_USDT"
    assert res["longs"][0]["rsi"] > 55.0

    assert len(res["shorts"]) == 2
    short_symbols = [s["symbol"] for s in res["shorts"]]
    assert "BBB_USDT" in short_symbols
    assert "CCC_USDT" in short_symbols


@pytest.mark.asyncio
async def test_scan_macd_confluence_filtering(mexc_client: MexcClient):
    """Verify MACD confluence scanner correctly detects 1H & 4H > 0 (LONG) and < 0 (SHORT)."""
    mexc_client._request = AsyncMock(return_value=[
        {
            "symbol": "UNI_USDT",
            "amount24": 10_000_000.0,
            "lastPrice": 5.0,
            "riseFallRate": 0.03,
            "fundingRate": 0.0001,
        },
        {
            "symbol": "BTC_USDT",
            "amount24": 50_000_000.0,
            "lastPrice": 80000.0,
            "riseFallRate": -0.02,
            "fundingRate": 0.0001,
        },
    ])

    async def mock_get_kline(symbol, interval="1h", **kwargs):
        # 50 candles
        if "UNI" in symbol:
            # Uptrend with 0 < MACD < 2
            return {"close": [1.0 + i * 0.05 for i in range(50)]}
        else:
            # Downtrend with -2 < MACD < 0
            return {"close": [10.0 - i * 0.05 for i in range(50)]}

    mexc_client.get_kline = AsyncMock(side_effect=mock_get_kline)

    scanner = MarketScanner(mexc_client)
    res = await scanner.scan_macd_confluence(target_symbol="UNI", min_volume_usdt=100_000)

    assert len(res["longs"]) == 1
    assert res["longs"][0]["symbol"] == "UNI_USDT"
    assert 0 < res["longs"][0]["1h_macd"] < 2
    assert 0 < res["longs"][0]["4h_macd"] < 2

    assert len(res["shorts"]) == 1
    assert res["shorts"][0]["symbol"] == "BTC_USDT"
    assert -2 < res["shorts"][0]["1h_macd"] < 0
    assert -2 < res["shorts"][0]["4h_macd"] < 0

    # Check target_eval
    assert res["target_eval"] is not None
    assert res["target_eval"]["symbol"] == "UNI_USDT"
    assert res["target_eval"]["is_long"] is True


@pytest.mark.asyncio
async def test_handle_scan4h_command_flow(mexc_client: MexcClient):
    """Test Telegram /scan4h handler execution and message output."""
    mexc_client._request = AsyncMock(return_value=[
        {
            "symbol": "BTC_USDT",
            "fundingRate": 0.00008,  # 0.008%
            "amount24": 10_000_000.0,
            "lastPrice": 80000.0,
            "riseFallRate": 0.02,
        },
        {
            "symbol": "ETH_USDT",
            "fundingRate": -0.0002,  # -0.02%
            "amount24": 5_000_000.0,
            "lastPrice": 3000.0,
            "riseFallRate": -0.03,
        }
    ])
    mexc_client.get_kline = AsyncMock(side_effect=lambda sym, **kwargs: {
        "close": [80000.0 + i * 100 for i in range(25)] if "BTC" in sym else [3000.0 - i * 20 for i in range(25)]
    })

    status_mock = MagicMock(spec=Message)
    status_mock.edit_text = AsyncMock()

    update = MagicMock(spec=Update)
    update.effective_message.reply_text = AsyncMock(return_value=status_mock)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.args = []

    await handle_scan4h(update, context, mexc_client)

    update.effective_message.reply_text.assert_called_once()
    status_mock.edit_text.assert_called_once()

    edited_text = status_mock.edit_text.call_args[0][0]
    keyboard = status_mock.edit_text.call_args[1]["reply_markup"]

    assert "4H MARKET SCANNER" in edited_text
    assert "BTC_USDT" in edited_text
    assert "ETH_USDT" in edited_text
    assert keyboard is not None


@pytest.mark.asyncio
async def test_handle_macdscan_command_flow(mexc_client: MexcClient):
    """Test Telegram /macdscan handler execution and message output."""
    mexc_client._request = AsyncMock(return_value=[
        {
            "symbol": "ETH_USDT",
            "amount24": 20_000_000.0,
            "lastPrice": 2500.0,
            "riseFallRate": 0.015,
            "fundingRate": 0.0001,
        }
    ])
    mexc_client.get_kline = AsyncMock(return_value={
        "close": [2000.0 + i * 0.05 for i in range(50)]
    })

    status_mock = MagicMock(spec=Message)
    status_mock.edit_text = AsyncMock()

    update = MagicMock(spec=Update)
    update.effective_message.reply_text = AsyncMock(return_value=status_mock)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.args = ["long", "eth"]

    await handle_macdscan(update, context, mexc_client)

    update.effective_message.reply_text.assert_called_once()
    status_mock.edit_text.assert_called_once()

    edited_text = status_mock.edit_text.call_args[0][0]
    assert "MACD 1H & 4H DUAL CONFLUENCE SCANNER" in edited_text
    assert "ETH_USDT" in edited_text
