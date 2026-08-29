"""Crypto Historical Pattern Discovery & Recommendation Engine (CPDE).

Identifies crypto assets with a current market state that is statistically and technically
similar to the pre-breakout / pre-move state of a target reference asset.
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple
import numpy as np

from exchange.mexc_client import MexcClient

logger = logging.getLogger(__name__)

# Excluded non-crypto, equities, commodities, and stablecoin assets
EXCLUDED_SYMBOLS = {
    "USDC_USDT", "FDUSD_USDT", "TUSD_USDT", "BUSD_USDT", "DAI_USDT",
    "USDE_USDT", "EUR_USDT", "GBP_USDT", "XAU_USDT", "XAG_USDT",
    "SPX500_USDT", "NAS100_USDT", "NVIDIA_USDT", "SPY_USDT", "SOXL_USDT",
    "QQQ_USDT", "TSLA_USDT", "AAPL_USDT", "MSFT_USDT", "AMZN_USDT",
    "GOOGL_USDT", "META_USDT", "AMD_USDT", "COIN_USDT", "MSTR_USDT",
    "DJI_USDT", "UKOIL_USDT", "USOIL_USDT", "COPPER_USDT",
}


def compute_rsi(prices: List[float], period: int = 14) -> float:
    """Calculate standard Wilder's RSI."""
    if len(prices) < period + 1:
        return 50.0

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    avg_gain = sum(d for d in deltas[:period] if d > 0) / period
    avg_loss = sum(-d for d in deltas[:period] if d < 0) / period

    for i in range(period, len(deltas)):
        gain = deltas[i] if deltas[i] > 0 else 0.0
        loss = -deltas[i] if deltas[i] < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def compute_bb_width(prices: List[float], period: int = 20) -> float:
    """Calculate Bollinger Band Width percentage ((Upper - Lower) / SMA * 100)."""
    if len(prices) < period:
        return 5.0
    p = np.array(prices[-period:], dtype=float)
    sma = np.mean(p)
    if sma == 0:
        return 5.0
    std = np.std(p)
    return float(((std * 4.0) / sma) * 100.0)


def compute_volume_ratio(volumes: List[float], period: int = 20) -> float:
    """Compute current volume compared to 20-period moving average."""
    if len(volumes) < period or sum(volumes[-period:]) == 0:
        return 1.0
    avg_vol = np.mean(volumes[-period:])
    if avg_vol == 0:
        return 1.0
    return float(volumes[-1] / avg_vol)


def normalize_series(prices: List[float]) -> np.ndarray:
    """Min-Max normalization of a price series to [0, 1]."""
    p = np.array(prices, dtype=float)
    p_min = np.min(p)
    p_max = np.max(p)
    if p_max == p_min:
        return np.zeros_like(p)
    return (p - p_min) / (p_max - p_min)


class PatternDiscoveryEngine:
    """
    Finds assets currently in a market state matching the pre-move structure
    of a reference asset.
    """

    def __init__(self, mexc_client: MexcClient):
        self.client = mexc_client

    @staticmethod
    def detect_pre_move_window(
        closes: List[float],
        highs: List[float],
        lows: List[float],
        volumes: List[float],
        lookback: int = 25,
    ) -> Tuple[int, str, float]:
        """
        Detects where a recent significant price acceleration / pump began,
        and returns the end index of the pre-move consolidation window.

        Returns:
            (anchor_end_idx, status_description, move_magnitude_pct)
        """
        n = len(closes)
        if n < lookback + 5:
            return n, "Current state", 0.0

        # Examine the last 15 candles for explosive upward move
        recent_window = min(15, n - lookback)
        search_start = n - recent_window

        # Peak in recent candles
        peak_offset = int(np.argmax(closes[search_start:]))
        peak_idx = search_start + peak_offset
        peak_price = closes[peak_idx]

        # Find the trough right before the peak (search backwards from peak)
        trough_idx = peak_idx
        for i in range(peak_idx - 1, max(lookback - 1, peak_idx - 15), -1):
            if closes[i] <= closes[trough_idx]:
                trough_idx = i

        trough_price = closes[trough_idx]
        move_pct = ((peak_price - trough_price) / trough_price) * 100.0 if trough_price > 0 else 0.0

        if move_pct >= 5.0 and trough_idx >= lookback:
            candles_ago = n - trough_idx
            status_desc = f"Pre-pump base ({candles_ago} candles ago before +{move_pct:.1f}% move)"
            return trough_idx, status_desc, move_pct

        return n, "Recent consolidation state", move_pct

    async def find_similar_setups(
        self,
        target_symbol: str,
        timeframe: str = "4h",
        lookback_candles: int = 25,
        max_results: int = 5,
        max_candidate_24h_return: float = 8.0,  # Ensure candidate hasn't pumped yet
        min_24h_volume: float = 200_000.0,
    ) -> Dict:
        """
        Search for top candidate assets currently resembling the reference asset's pre-move state.
        """
        norm_target = self.client.normalize_symbol(target_symbol)

        # 1. Fetch Target Asset Kline
        try:
            target_kline = await self.client.get_kline(norm_target, interval=timeframe)
            t_closes = target_kline.get("close", [])
            t_highs = target_kline.get("high", [])
            t_lows = target_kline.get("low", [])
            t_vols = target_kline.get("vol", target_kline.get("amount", []))

            if len(t_closes) < lookback_candles + 10:
                raise ValueError(f"Insufficient historical candle data for {norm_target} on {timeframe}")
        except Exception as e:
            logger.error(f"Failed to fetch reference kline for {norm_target}: {e}")
            raise ValueError(f"Could not load data for symbol `{target_symbol}`: {e}")

        # 2. Extract Pre-Move Reference Features
        anchor_idx, pre_move_status, move_pct = self.detect_pre_move_window(
            t_closes, t_highs, t_lows, t_vols, lookback=lookback_candles
        )

        ref_closes_slice = t_closes[anchor_idx - lookback_candles : anchor_idx]
        ref_vols_slice = t_vols[anchor_idx - lookback_candles : anchor_idx] if t_vols else [1.0] * lookback_candles

        ref_norm_curve = normalize_series(ref_closes_slice)
        ref_rsi = compute_rsi(ref_closes_slice)
        ref_bb_width = compute_bb_width(ref_closes_slice)
        ref_vol_ratio = compute_volume_ratio(ref_vols_slice)

        # 3. Fetch All Candidate Tickers
        tickers = await self.client._request("GET", "/api/v1/contract/ticker")
        if not isinstance(tickers, list):
            tickers = []

        candidate_pool: List[dict] = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("_USDT") or sym == norm_target or sym in EXCLUDED_SYMBOLS:
                continue

            vol24 = float(t.get("amount24", t.get("volume24", 0.0)))
            chg24 = float(t.get("riseFallRate", 0.0)) * 100.0
            price = float(t.get("lastPrice", 0.0))

            # Must have minimum liquidity and must NOT have already pumped
            if vol24 >= min_24h_volume and chg24 <= max_candidate_24h_return and price > 0:
                candidate_pool.append({
                    "symbol": sym,
                    "vol24": vol24,
                    "chg24": chg24,
                    "price": price,
                    "funding_rate": float(t.get("fundingRate", 0.0)) * 100.0,
                })

        # Sort candidate pool by 24h volume and limit to top 40 for fast parallel fetch
        candidate_pool.sort(key=lambda x: x["vol24"], reverse=True)
        eval_pool = candidate_pool[:45]

        # 4. Fetch Kline & Calculate Similarity Concurrently
        now = int(time.time())
        # Calculate start timestamp for lookback slice
        tf_seconds = {
            "30m": 30 * 60,
            "1h": 3600,
            "4h": 4 * 3600,
            "1d": 24 * 3600,
        }.get(timeframe.lower(), 4 * 3600)

        start_ts = now - (lookback_candles + 15) * tf_seconds
        sem = asyncio.Semaphore(20)

        async def evaluate_candidate(cand: dict) -> Optional[dict]:
            sym = cand["symbol"]
            async with sem:
                try:
                    kline = await self.client.get_kline(sym, interval=timeframe, start=start_ts, end=now)
                    closes = kline.get("close", [])
                    vols = kline.get("vol", kline.get("amount", []))
                    if len(closes) < lookback_candles:
                        return None

                    cand_closes_slice = closes[-lookback_candles:]
                    cand_vols_slice = vols[-lookback_candles:] if vols else [1.0] * lookback_candles

                    cand_norm_curve = normalize_series(cand_closes_slice)
                    cand_rsi = compute_rsi(cand_closes_slice)
                    cand_bb_width = compute_bb_width(cand_closes_slice)
                    cand_vol_ratio = compute_volume_ratio(cand_vols_slice)

                    # --- Multi-factor Similarity Calculations ---
                    # 1. Price Trajectory Correlation (0-100)
                    corr = np.corrcoef(ref_norm_curve, cand_norm_curve)[0, 1]
                    if np.isnan(corr):
                        corr = 0.0
                    price_sim = float(max(0.0, min(100.0, (corr + 1.0) * 50.0)))

                    # 2. RSI Proximity (0-100)
                    rsi_diff = abs(ref_rsi - cand_rsi)
                    rsi_sim = float(max(0.0, 100.0 - rsi_diff * 3.0))

                    # 3. Volatility Compression Similarity (0-100)
                    bb_diff = abs(ref_bb_width - cand_bb_width)
                    volatility_sim = float(max(0.0, 100.0 - bb_diff * 8.0))

                    # 4. Volume Structure Alignment (0-100)
                    vol_diff = abs(ref_vol_ratio - cand_vol_ratio)
                    volume_sim = float(max(0.0, 100.0 - vol_diff * 25.0))

                    # Composite Pattern Similarity
                    total_similarity = (
                        0.40 * price_sim +
                        0.25 * rsi_sim +
                        0.20 * volatility_sim +
                        0.15 * volume_sim
                    )

                    # Recommendation Score (Quality + Statistical edge)
                    rec_score = (
                        0.55 * total_similarity +
                        0.25 * rsi_sim +
                        0.10 * volatility_sim +
                        0.10 * min(100.0, (cand["vol24"] / 10_000_000.0) * 100.0)
                    )

                    # Confidence
                    if total_similarity >= 88.0 and rsi_diff <= 8.0:
                        confidence = "HIGH"
                    elif total_similarity >= 75.0:
                        confidence = "MEDIUM"
                    else:
                        confidence = "MODERATE"

                    # Explainability points
                    reasons = []
                    if price_sim >= 85:
                        reasons.append("Matching base consolidation curve")
                    if rsi_diff <= 6:
                        reasons.append(f"RSI aligned ({cand_rsi:.1f} vs {ref_rsi:.1f})")
                    if bb_diff <= 3.0:
                        reasons.append("Volatility squeeze / compression match")
                    if not reasons:
                        reasons.append("Correlated price action structure")

                    return {
                        "symbol": sym,
                        "similarity_score": round(total_similarity, 1),
                        "recommendation_score": round(rec_score, 1),
                        "confidence": confidence,
                        "price": cand["price"],
                        "chg24": cand["chg24"],
                        "vol24": cand["vol24"],
                        "funding_rate": cand["funding_rate"],
                        "rsi": round(cand_rsi, 1),
                        "bb_width": round(cand_bb_width, 2),
                        "price_sim": round(price_sim, 1),
                        "rsi_sim": round(rsi_sim, 1),
                        "reasons": reasons,
                    }
                except Exception as ex:
                    logger.debug(f"Candidate evaluation error for {sym}: {ex}")
                    return None

        eval_tasks = [evaluate_candidate(c) for c in eval_pool]
        matched_results = [r for r in await asyncio.gather(*eval_tasks) if r]

        # Sort by recommendation score descending
        matched_results.sort(key=lambda x: x["recommendation_score"], reverse=True)

        return {
            "target_symbol": norm_target,
            "timeframe": timeframe.upper(),
            "pre_move_status": pre_move_status,
            "target_rsi": round(ref_rsi, 1),
            "target_bb_width": round(ref_bb_width, 2),
            "target_move_pct": round(move_pct, 1),
            "top_candidates": matched_results[:max_results],
            "total_candidates_scanned": len(candidate_pool),
            "timestamp": now,
        }
