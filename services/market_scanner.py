"""Market scanner for identifying Long and Short trading opportunities on MEXC Futures."""
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from exchange.mexc_client import MexcClient
from services.pattern_discovery import is_excluded_symbol
from services.chart_generator import compute_macd

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
        try:
            tickers = await self.client._request("GET", "/api/v1/contract/ticker")
            if not isinstance(tickers, list):
                tickers = []
        except Exception as e:
            logger.error(f"Failed to fetch contract tickers for scan: {e}")
            return {"longs": [], "shorts": []}

        now = int(time.time())
        start_ts = now - (35 * 4 * 3600)

        long_pre: List[dict] = []
        short_pre: List[dict] = []

        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("_USDT") or is_excluded_symbol(sym):
                continue

            vol24 = float(t.get("amount24", t.get("volume24", 0.0)))
            if vol24 < min_volume_usdt:
                continue

            fr = float(t.get("fundingRate", 0.0))
            fr_pct = fr * 100.0

            if 0.001 <= fr_pct <= 0.01:
                long_pre.append(t)
            elif fr_pct > 0.1 or fr_pct < 0.0:
                short_pre.append(t)

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

        longs.sort(key=lambda x: x["vol24"], reverse=True)
        shorts.sort(key=lambda x: x["vol24"], reverse=True)

        return {
            "longs": longs,
            "shorts": shorts,
            "total_scanned": len(tickers),
            "timestamp": now,
        }

    async def scan_macd_confluence(
        self,
        side_filter: Optional[str] = None,  # 'LONG', 'SHORT', or None (both)
        target_symbol: Optional[str] = None,
        min_volume_usdt: float = 200_000.0,
        max_candidates: int = 50,
    ) -> Dict[str, Any]:
        """
        Scan MEXC Perpetual Futures pairs for Dual-Timeframe (1H & 4H) MACD Confluence.

        Long Conditions (BOTH 1H & 4H):
        - 1H MACD > 0 and 1H Signal > 0
        - 4H MACD > 0 and 4H Signal > 0

        Short Conditions (BOTH 1H & 4H):
        - 1H MACD < 0 and 1H Signal < 0
        - 4H MACD < 0 and 4H Signal < 0
        """
        now = int(time.time())
        try:
            tickers = await self.client._request("GET", "/api/v1/contract/ticker")
            if not isinstance(tickers, list):
                tickers = []
        except Exception as e:
            logger.error(f"Failed to fetch tickers for MACD scan: {e}")
            return {"longs": [], "shorts": [], "target_eval": None, "total_scanned": 0, "timestamp": now}

        ticker_map = {t.get("symbol"): t for t in tickers if t.get("symbol")}

        # 1. Evaluate target symbol specifically if provided
        target_eval = None
        if target_symbol:
            norm_target = self.client.normalize_symbol(target_symbol)
            t_data = ticker_map.get(norm_target)
            if t_data:
                try:
                    k1 = await self.client.get_kline(norm_target, interval="1h")
                    k4 = await self.client.get_kline(norm_target, interval="4h")
                    c1 = [float(x) for x in k1.get("close", [])]
                    c4 = [float(x) for x in k4.get("close", [])]

                    if len(c1) >= 26 and len(c4) >= 26:
                        m1, s1, h1 = compute_macd(c1)
                        m4, s4, h4 = compute_macd(c4)

                        is_long = (m1[-1] > 0 and s1[-1] > 0 and m4[-1] > 0 and s4[-1] > 0)
                        is_short = (m1[-1] < 0 and s1[-1] < 0 and m4[-1] < 0 and s4[-1] < 0)

                        status_text = "BULLISH CONFLUENCE (LONG)" if is_long else ("BEARISH CONFLUENCE (SHORT)" if is_short else "MIXED / NO CONFLUENCE")

                        target_eval = {
                            "symbol": norm_target,
                            "price": float(t_data.get("lastPrice", 0.0)),
                            "change24": float(t_data.get("riseFallRate", 0.0)) * 100.0,
                            "vol24": float(t_data.get("amount24", t_data.get("volume24", 0.0))),
                            "is_long": is_long,
                            "is_short": is_short,
                            "status": status_text,
                            "1h_macd": m1[-1],
                            "1h_sig": s1[-1],
                            "1h_hist": h1[-1],
                            "4h_macd": m4[-1],
                            "4h_sig": s4[-1],
                            "4h_hist": h4[-1],
                        }
                except Exception as ex:
                    logger.debug(f"Target eval error for {norm_target}: {ex}")

        # 2. Build candidate pool from liquid crypto tickers
        candidate_pool: List[dict] = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("_USDT") or is_excluded_symbol(sym):
                continue
            vol24 = float(t.get("amount24", t.get("volume24", 0.0)))
            if vol24 < min_volume_usdt:
                continue

            candidate_pool.append({
                "symbol": sym,
                "vol24": vol24,
                "price": float(t.get("lastPrice", 0.0)),
                "change24": float(t.get("riseFallRate", 0.0)) * 100.0,
                "funding_rate": float(t.get("fundingRate", 0.0)) * 100.0,
            })

        # Sort by 24h volume and limit to top candidates for fast concurrent scanning
        candidate_pool.sort(key=lambda x: x["vol24"], reverse=True)
        eval_pool = candidate_pool[:max_candidates]

        sem = asyncio.Semaphore(20)

        async def evaluate_macd_item(cand: dict) -> Optional[dict]:
            sym = cand["symbol"]
            async with sem:
                try:
                    k1 = await self.client.get_kline(sym, interval="1h")
                    k4 = await self.client.get_kline(sym, interval="4h")
                    c1 = [float(x) for x in k1.get("close", [])]
                    c4 = [float(x) for x in k4.get("close", [])]
                    if len(c1) < 26 or len(c4) < 26:
                        return None

                    m1, s1, h1 = compute_macd(c1)
                    m4, s4, h4 = compute_macd(c4)

                    cur_m1, cur_s1, cur_h1 = m1[-1], s1[-1], h1[-1]
                    cur_m4, cur_s4, cur_h4 = m4[-1], s4[-1], h4[-1]

                    is_long = (cur_m1 > 0 and cur_s1 > 0 and cur_m4 > 0 and cur_s4 > 0)
                    is_short = (cur_m1 < 0 and cur_s1 < 0 and cur_m4 < 0 and cur_s4 < 0)

                    if not is_long and not is_short:
                        return None

                    if side_filter == "LONG" and not is_long:
                        return None
                    if side_filter == "SHORT" and not is_short:
                        return None

                    return {
                        "symbol": sym,
                        "price": cand["price"],
                        "change24": cand["change24"],
                        "vol24": cand["vol24"],
                        "funding_rate": cand["funding_rate"],
                        "is_long": is_long,
                        "is_short": is_short,
                        "1h_macd": cur_m1,
                        "1h_sig": cur_s1,
                        "1h_hist": cur_h1,
                        "4h_macd": cur_m4,
                        "4h_sig": cur_s4,
                        "4h_hist": cur_h4,
                    }
                except Exception as ex:
                    logger.debug(f"MACD scan error for {sym}: {ex}")
                    return None

        results = [r for r in await asyncio.gather(*[evaluate_macd_item(c) for c in eval_pool]) if r]

        longs = [r for r in results if r["is_long"]]
        shorts = [r for r in results if r["is_short"]]

        longs.sort(key=lambda x: x["vol24"], reverse=True)
        shorts.sort(key=lambda x: x["vol24"], reverse=True)

        return {
            "longs": longs,
            "shorts": shorts,
            "target_eval": target_eval,
            "side_filter": side_filter,
            "total_scanned": len(tickers),
            "timestamp": now,
        }
