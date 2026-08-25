"""Tests for 4H Market Scanner and RSI calculation."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from telegram import Update, Message, CallbackQuery, User
from telegram.ext import ContextTypes

from services.market_scanner import MarketScanner, compute_rsi
from exchange.mexc_client import MexcClient
from bot.handlers.market import handle_scan4h
from bot.formatters import format_scan_results


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
    # Mock bulk tickers
    mexc_client._request = AsyncMock(return_value=[
        # Candidate 1: Valid Long (FR 0.005%, High Volume)
        {
            "symbol": "AAA_USDT",
            "fundingRate": 0.00005,  # 0.005%
            "amount24": 5_000_000.0,
            "lastPrice": 10.0,
            "riseFallRate": 0.05,
        },
        # Candidate 2: Valid Short (FR -0.01%, High Volume)
        {
            "symbol": "BBB_USDT",
            "fundingRate": -0.0001,  # -0.01%
            "amount24": 2_000_000.0,
            "lastPrice": 5.0,
            "riseFallRate": -0.04,
        },
        # Candidate 3: Valid Short (High positive FR 0.15%)
        {
            "symbol": "CCC_USDT",
            "fundingRate": 0.0015,  # 0.15%
            "amount24": 1_000_000.0,
            "lastPrice": 1.0,
            "riseFallRate": -0.02,
        },
        # Candidate 4: Neutral (FR 0.05% -> excluded from both)
        {
            "symbol": "DDD_USDT",
            "fundingRate": 0.0005,  # 0.05%
            "amount24": 1_000_000.0,
            "lastPrice": 2.0,
            "riseFallRate": 0.01,
        },
    ])

    # Mock kline responses
    async def mock_get_kline(symbol, interval="4h", start=None, end=None):
        if symbol == "AAA_USDT":
            # Strong uptrend -> RSI > 55
            return {"close": [10.0 + i * 0.5 for i in range(25)]}
        elif symbol in ["BBB_USDT", "CCC_USDT"]:
            # Strong downtrend -> RSI < 45
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
