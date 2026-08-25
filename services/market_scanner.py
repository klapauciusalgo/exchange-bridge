"""Market scanner for identifying Long and Short trading opportunities on MEXC Futures."""
import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple
from exchange.mexc_client import MexcClient

logger = logging.getLogger(__name__)


def compute_rsi(prices: List[float], period: int = 14) -> float:
    """
    Calculate Relative Strength Index (RSI) using Wilder's smoothing.
    Returns standard 0-100 float.
    """
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
    return 100.0 - (100.0 / (1.0 + rs))


class MarketScanner:
    """Scans MEXC Perpetual Futures pairs for algorithmic setups."""

    def __init__(self, mexc_client: MexcClient):
        self.client = mexc_client

    async def scan_4h(
        self,
        min_volume_usdt: float = 200_000.0,
        max_candidates: int = 50,
        side_filter: Optional[str] = None,  # 'LONG', 'SHORT', or None (both)
    ) -> Dict[str, List[dict]]:
        """
        Scan all active MEXC USDT futures pairs on 4H timeframe.

        Long Conditions:
        - 4H RSI(14) > 55
        - 0.001% <= Funding Rate <= 0.01%

        Short Conditions:
        - 4H RSI(14) < 45
        - Funding Rate > 0.1% OR Funding Rate < 0% (Negative)
        """
        # 1. Fetch all tickers in one bulk request
        try:
            tickers = await self.client._request("GET", "/api/v1/contract/ticker")
            if not isinstance(tickers, list):
                tickers = []
        except Exception as e:
            logger.error(f"Failed to fetch contract tickers for scan: {e}")
            return {"longs": [], "shorts": []}

        now = int(time.time())
        # Fetch 35 bars of 4H data for fast calculation
        start_ts = now - (35 * 4 * 3600)

        long_pre: List[dict] = []
        short_pre: List[dict] = []

        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("_USDT"):
                continue

            vol24 = float(t.get("amount24", t.get("volume24", 0.0)))
            if vol24 < min_volume_usdt:
                continue

            fr = float(t.get("fundingRate", 0.0))
            fr_pct = fr * 100.0  # Percentage format (e.g. 0.01%)

            # Long condition: 0.001% <= fr_pct <= 0.01%
            if 0.001 <= fr_pct <= 0.01:
                long_pre.append(t)
            # Short condition: fr_pct > 0.1% or fr_pct < 0%
            elif fr_pct > 0.1 or fr_pct < 0.0:
                short_pre.append(t)

        # Sort by volume and take top candidates for fast kline processing
        long_pre.sort(key=lambda x: float(x.get("amount24", 0.0)), reverse=True)
        short_pre.sort(key=lambda x: float(x.get("amount24", 0.0)), reverse=True)

        scan_pool: List[Tuple[str, dict]] = []
        if side_filter != "SHORT":
            scan_pool.extend([("LONG", t) for t in long_pre[:max_candidates]])
        if side_filter != "LONG":
            scan_pool.extend([("SHORT", t) for t in short_pre[:max_candidates]])

        sem = asyncio.Semaphore(20)

        async def evaluate_candidate(side: str, item: dict) -> Optional[dict]:
            sym = item.get("symbol", "")
            async with sem:
                try:
                    kline = await self.client.get_kline(sym, interval="4h", start=start_ts, end=now)
                    closes = kline.get("close", [])
                    if len(closes) < 15:
                        return None

                    rsi = compute_rsi(closes, period=14)
                    fr_pct = float(item.get("fundingRate", 0.0)) * 100.0
                    price = float(item.get("lastPrice", closes[-1] if closes else 0.0))
                    vol24 = float(item.get("amount24", item.get("volume24", 0.0)))
                    chg24 = float(item.get("riseFallRate", 0.0)) * 100.0

                    if side == "LONG" and rsi > 55.0:
                        return {
                            "symbol": sym,
                            "side": "LONG",
                            "rsi": rsi,
                            "funding_pct": fr_pct,
                            "price": price,
                            "vol24": vol24,
                            "change24": chg24,
                        }
                    elif side == "SHORT" and rsi < 45.0:
                        return {
                            "symbol": sym,
                            "side": "SHORT",
                            "rsi": rsi,
                            "funding_pct": fr_pct,
                            "price": price,
                            "vol24": vol24,
                            "change24": chg24,
                        }
                except Exception as ex:
                    logger.debug(f"Scan error for {sym}: {ex}")
                    return None
            return None

        tasks = [evaluate_candidate(side, t) for side, t in scan_pool]
        results = [r for r in await asyncio.gather(*tasks) if r]

        longs = [r for r in results if r["side"] == "LONG"]
        shorts = [r for r in results if r["side"] == "SHORT"]

        # Sort by 24h volume descending
        longs.sort(key=lambda x: x["vol24"], reverse=True)
        shorts.sort(key=lambda x: x["vol24"], reverse=True)

        return {
            "longs": longs,
            "shorts": shorts,
            "total_scanned": len(tickers),
            "timestamp": now,
        }
