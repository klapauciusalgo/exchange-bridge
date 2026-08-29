"""Unit and integration tests for Crypto Pattern Discovery Engine (CPDE)."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from telegram import Update, Message
from telegram.ext import ContextTypes

from services.pattern_discovery import (
    PatternDiscoveryEngine,
    compute_rsi,
    compute_bb_width,
    compute_volume_ratio,
    normalize_series,
)
from exchange.mexc_client import MexcClient
from bot.handlers.market import handle_similar
from bot.formatters import format_similar_recommendations


def test_feature_engineering_helpers():
    """Verify normalization, BB width, and volume ratio helpers."""
    prices = [10.0, 20.0, 30.0, 40.0, 50.0]
    norm = normalize_series(prices)
    assert norm[0] == 0.0
    assert norm[-1] == 1.0

    # Flat prices
    flat = [100.0] * 25
    assert compute_bb_width(flat) == 0.0

    # Volume ratio
    vols = [100.0] * 19 + [200.0]
    vr = compute_volume_ratio(vols, period=20)
    assert vr > 1.8


def test_detect_pre_move_window_pump_detection():
    """Verify that pump starting point is detected and pre-move window is isolated."""
    # 25 candles of baseline consolidation around 100.0, then pump to 130.0 in last 5 candles
    baseline = [100.0 + (i % 2) * 0.5 for i in range(30)]
    pump = [105.0, 112.0, 120.0, 128.0, 130.0]
    closes = baseline + pump
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    vols = [1000.0] * len(closes)

    anchor_idx, status, move_pct = PatternDiscoveryEngine.detect_pre_move_window(
        closes, highs, lows, vols, lookback=25
    )

    assert move_pct >= 20.0
    assert anchor_idx <= len(closes) - 5  # Anchor is at or before the pump start
    assert "Pre-pump" in status


@pytest.mark.asyncio
async def test_find_similar_setups_engine(mexc_client: MexcClient):
    """Verify PatternDiscoveryEngine evaluates and ranks candidates correctly."""
    # 1. Target coin (SUI)
    sui_closes = [1.0 + (i % 3) * 0.01 for i in range(40)]
    mexc_client.get_kline = AsyncMock()

    # 2. Tickers response
    mexc_client._request = AsyncMock(return_value=[
        {"symbol": "SEI_USDT", "amount24": 5_000_000.0, "riseFallRate": 0.01, "lastPrice": 0.5, "fundingRate": 0.0001},
        {"symbol": "TIA_USDT", "amount24": 3_000_000.0, "riseFallRate": 0.02, "lastPrice": 5.0, "fundingRate": 0.0001},
        {"symbol": "PUMPED_USDT", "amount24": 8_000_000.0, "riseFallRate": 0.25, "lastPrice": 10.0, "fundingRate": 0.0001}, # Should be filtered out (> 8%)
    ])

    async def mock_kline(symbol, **kwargs):
        if "SUI" in symbol:
            return {"close": sui_closes, "high": sui_closes, "low": sui_closes, "vol": [100.0] * len(sui_closes)}
        elif "SEI" in symbol:
            # Very similar shape to SUI
            return {"close": sui_closes[-25:], "high": sui_closes[-25:], "low": sui_closes[-25:], "vol": [100.0] * 25}
        elif "TIA" in symbol:
            # Slightly different shape
            t_c = [5.0 + i * 0.05 for i in range(25)]
            return {"close": t_c, "high": t_c, "low": t_c, "vol": [100.0] * 25}
        return {"close": [10.0] * 25, "high": [10.0] * 25, "low": [10.0] * 25, "vol": [100.0] * 25}

    mexc_client.get_kline.side_effect = mock_kline

    engine = PatternDiscoveryEngine(mexc_client)
    res = await engine.find_similar_setups("SUI", timeframe="4h")

    assert res["target_symbol"] == "SUI_USDT"
    assert len(res["top_candidates"]) >= 1
    # SEI should rank highest due to matching shape
    assert res["top_candidates"][0]["symbol"] == "SEI_USDT"
    assert res["top_candidates"][0]["similarity_score"] > 80.0
    # PUMPED_USDT must be excluded
    candidate_syms = [c["symbol"] for c in res["top_candidates"]]
    assert "PUMPED_USDT" not in candidate_syms


@pytest.mark.asyncio
async def test_handle_similar_command_flow(mexc_client: MexcClient):
    """Verify Telegram /similar command execution and formatting."""
    mexc_client._request = AsyncMock(return_value=[
        {"symbol": "SEI_USDT", "amount24": 5_000_000.0, "riseFallRate": 0.01, "lastPrice": 0.5, "fundingRate": 0.0001},
    ])
    mexc_client.get_kline = AsyncMock(return_value={
        "close": [1.0 + (i % 2) * 0.01 for i in range(35)],
        "high": [1.05] * 35,
        "low": [0.95] * 35,
        "vol": [100.0] * 35,
    })

    status_mock = MagicMock(spec=Message)
    status_mock.edit_text = AsyncMock()

    update = MagicMock(spec=Update)
    update.effective_message.reply_text = AsyncMock(return_value=status_mock)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.args = ["SUI", "4h"]

    await handle_similar(update, context, mexc_client)

    update.effective_message.reply_text.assert_called_once()
    status_mock.edit_text.assert_called_once()

    edited_msg = status_mock.edit_text.call_args[0][0]
    keyboard = status_mock.edit_text.call_args[1]["reply_markup"]

    assert "PATTERN DISCOVERY: SIMILAR SETUPS" in edited_msg
    assert "SUI_USDT" in edited_msg
    assert "SEI_USDT" in edited_msg
    assert keyboard is not None
