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
    is_excluded_symbol,
)
from exchange.mexc_client import MexcClient
from bot.handlers.market import handle_similar
from bot.formatters import format_similar_recommendations


def test_is_excluded_symbol():
    """Verify that %STOCK_USDT, indices, commodities, and stablecoins are excluded."""
    # Stocks (%STOCK_USDT)
    assert is_excluded_symbol("AAPLSTOCK_USDT") is True
    assert is_excluded_symbol("TSLASTOCK_USDT") is True
    assert is_excluded_symbol("NVDASTOCK_USDT") is True
    assert is_excluded_symbol("NVIDIA_USDT") is True

    # Indices & ETFs
    assert is_excluded_symbol("SPX500_USDT") is True
    assert is_excluded_symbol("NAS100_USDT") is True
    assert is_excluded_symbol("SPY_USDT") is True
    assert is_excluded_symbol("SOXL_USDT") is True

    # Commodities & Forex & Stablecoins
    assert is_excluded_symbol("UKOIL_USDT") is True
    assert is_excluded_symbol("XAU_USDT") is True
    assert is_excluded_symbol("USDC_USDT") is True

    # Real Crypto Pairs should NOT be excluded
    assert is_excluded_symbol("BTC_USDT") is False
    assert is_excluded_symbol("ETH_USDT") is False
    assert is_excluded_symbol("SOL_USDT") is False
    assert is_excluded_symbol("SUI_USDT") is False
    assert is_excluded_symbol("XPL_USDT") is False


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


def test_detect_pre_move_window_pump_and_dump():
    """Verify that both pump (LONG) and dump (SHORT) pre-move windows are detected."""
    # 1. Pump setup
    baseline = [100.0 + (i % 2) * 0.5 for i in range(30)]
    pump = [105.0, 112.0, 120.0, 128.0, 130.0]
    closes = baseline + pump
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    vols = [1000.0] * len(closes)

    anchor_idx, status, move_pct, p0, p1, direction = PatternDiscoveryEngine.detect_pre_move_window(
        closes, highs, lows, vols, lookback=25
    )

    assert move_pct >= 20.0
    assert anchor_idx <= len(closes) - 5
    assert direction == "LONG"
    assert "Base" in status

    # 2. Dump setup
    dump = [95.0, 90.0, 85.0, 80.0, 75.0]
    dump_closes = baseline + dump
    d_highs = [c + 1.0 for c in dump_closes]
    d_lows = [c - 1.0 for c in dump_closes]
    d_vols = [1000.0] * len(dump_closes)

    d_anchor, d_status, d_move, d_p0, d_p1, d_dir = PatternDiscoveryEngine.detect_pre_move_window(
        dump_closes, d_highs, d_lows, d_vols, lookback=25
    )

    assert d_move <= -20.0
    assert d_dir == "SHORT"
    assert "Breakdown" in d_status or "Top" in d_status


@pytest.mark.asyncio
async def test_find_similar_setups_engine(mexc_client: MexcClient):
    """Verify PatternDiscoveryEngine evaluates and ranks candidates correctly with Dual-Horizon."""
    # 1. Target coin (SUI) - 120 candles
    sui_closes = [1.0 + (i % 3) * 0.01 for i in range(120)]
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
            # Very similar shape to SUI (both macro and micro)
            return {"close": sui_closes[-100:], "high": sui_closes[-100:], "low": sui_closes[-100:], "vol": [100.0] * 100}
        elif "TIA" in symbol:
            # Slightly different shape
            t_c = [5.0 + i * 0.05 for i in range(100)]
            return {"close": t_c, "high": t_c, "low": t_c, "vol": [100.0] * 100}
        return {"close": [10.0] * 100, "high": [10.0] * 100, "low": [10.0] * 100, "vol": [100.0] * 100}

    mexc_client.get_kline.side_effect = mock_kline

    engine = PatternDiscoveryEngine(mexc_client)
    res = await engine.find_similar_setups("SUI", timeframe="4h")

    assert res["target_symbol"] == "SUI_USDT"
    assert res["macro_bars"] == 100
    assert res["micro_bars"] == 25
    assert len(res["top_candidates"]) >= 1
    # SEI should rank highest due to matching shape
    assert res["top_candidates"][0]["symbol"] == "SEI_USDT"
    assert res["top_candidates"][0]["similarity_score"] > 80.0
    assert "macro_sim" in res["top_candidates"][0]
    assert "micro_sim" in res["top_candidates"][0]
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
