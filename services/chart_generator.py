"""Candlestick, Volume, and MACD chart generator using Matplotlib with dark financial theme, exact data badges, and multi-timeframe support."""
import io
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import matplotlib
matplotlib.use("Agg")  # Headless backend
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.gridspec as gridspec
import numpy as np

logger = logging.getLogger(__name__)

# Styling Constants
BG_COLOR = "#131722"
PANEL_COLOR = "#1e222d"
GRID_COLOR = "#2a2e39"
BULL_COLOR = "#089981"  # Emerald green
BEAR_COLOR = "#f23645"  # Soft red
TEXT_COLOR = "#d1d4dc"
SUBTEXT_COLOR = "#787b86"
MA25_COLOR = "#2962ff"  # TradingView Blue
MA50_COLOR = "#ff9800"  # Amber Orange
VOL_SMA_COLOR = "#9c27b0"  # Purple
MACD_COLOR = "#2962ff"  # Blue
SIGNAL_COLOR = "#ff9800"  # Orange
ZERO_LINE_COLOR = "#787b86"


def _format_price(val: float) -> str:
    """Format price number with appropriate decimal precision."""
    if abs(val) < 0.001:
        return f"{val:,.6f}"
    elif abs(val) < 1.0:
        return f"{val:,.4f}"
    else:
        return f"{val:,.2f}"


def _format_vol(val: float) -> str:
    """Format volume number with K/M/B suffixes."""
    abs_v = abs(val)
    if abs_v >= 1_000_000_000:
        return f"{val / 1e9:.2f}B"
    elif abs_v >= 1_000_000:
        return f"{val / 1e6:.2f}M"
    elif abs_v >= 1_000:
        return f"{val / 1e3:.2f}K"
    else:
        return f"{val:.1f}"


def _format_macd(val: float) -> str:
    """Format MACD, Signal, and Histogram values dynamically based on scale."""
    if abs(val) == 0:
        return "0.00"
    abs_v = abs(val)
    if abs_v >= 10.0:
        return f"{val:+.2f}"
    elif abs_v >= 1.0:
        return f"{val:+.2f}"
    elif abs_v >= 0.01:
        return f"{val:+.4f}"
    elif abs_v >= 0.0001:
        return f"{val:+.6f}"
    else:
        return f"{val:+.8f}"


def compute_ema(series: List[float], period: int) -> List[float]:
    """Compute Exponential Moving Average using standard Wilder/TradingView smoothing."""
    if not series:
        return []
    n = len(series)
    ema = [0.0] * n
    ema[0] = series[0]
    multiplier = 2.0 / (period + 1.0)
    for i in range(1, n):
        ema[i] = (series[i] - ema[i - 1]) * multiplier + ema[i - 1]
    return ema


def compute_macd(
    prices: List[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> Tuple[List[float], List[float], List[float]]:
    """
    Compute standard MACD (12, 26, 9):
    Returns (macd_line, signal_line, histogram).
    """
    if len(prices) < slow_period:
        zeros = [0.0] * len(prices)
        return zeros, zeros, zeros

    ema_fast = compute_ema(prices, fast_period)
    ema_slow = compute_ema(prices, slow_period)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = compute_ema(macd_line, signal_period)
    hist = [m - s for m, s in zip(macd_line, signal_line)]
    return macd_line, signal_line, hist


def compute_normalized_macd(
    prices: List[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> Tuple[List[float], List[float], List[float]]:
    """
    Compute Percentage Price Oscillator (Normalized MACD %):
    MACD% = ((EMA_fast - EMA_slow) / EMA_slow) * 100%
    Returns (macd_pct_line, signal_pct_line, hist_pct_line).
    """
    if len(prices) < slow_period:
        zeros = [0.0] * len(prices)
        return zeros, zeros, zeros

    ema_fast = compute_ema(prices, fast_period)
    ema_slow = compute_ema(prices, slow_period)
    macd_pct = [((f - s) / s * 100.0) if s != 0 else 0.0 for f, s in zip(ema_fast, ema_slow)]
    signal_pct = compute_ema(macd_pct, signal_period)
    hist_pct = [m - s for m, s in zip(macd_pct, signal_pct)]
    return macd_pct, signal_pct, hist_pct


def _render_chart_panel(
    ax_candle: plt.Axes,
    ax_vol: plt.Axes,
    ax_macd: plt.Axes,
    symbol: str,
    interval_label: str,
    kline_data: Dict[str, list],
    num_candles: int = 80,
) -> None:
    """Render a 3-tier candlestick + volume + MACD panel with exact numerical badges."""
    raw_times = kline_data.get("time", [])
    raw_opens = kline_data.get("open", [])
    raw_closes = kline_data.get("close", [])
    raw_highs = kline_data.get("high", [])
    raw_lows = kline_data.get("low", [])
    raw_vols = kline_data.get("vol", [])

    if not raw_times or len(raw_times) < 5:
        ax_candle.text(0.5, 0.5, f"Insufficient data for {interval_label.upper()}", ha="center", va="center", color=TEXT_COLOR)
        return

    # Clamp candle count between 20 and 200
    target_count = max(20, min(200, num_candles))

    # Slice the last `target_count`
    times = raw_times[-target_count:]
    opens = [float(x) for x in raw_opens[-target_count:]]
    closes = [float(x) for x in raw_closes[-target_count:]]
    highs = [float(x) for x in raw_highs[-target_count:]]
    lows = [float(x) for x in raw_lows[-target_count:]]
    vols = [float(x) for x in raw_vols[-target_count:]]

    n = len(times)
    indices = np.arange(n)

    # Calculate Moving Averages (MA 25 & MA 50) on full closes
    full_closes = [float(x) for x in raw_closes]
    ma_25 = []
    ma_50 = []

    for i in range(len(full_closes) - n, len(full_closes)):
        window25 = full_closes[max(0, i - 24):i + 1]
        ma_25.append(np.mean(window25) if len(window25) >= 5 else np.nan)
        window50 = full_closes[max(0, i - 49):i + 1]
        ma_50.append(np.mean(window50) if len(window50) >= 5 else np.nan)

    # Calculate MACD on full closes, take the last n elements
    full_macd, full_sig, full_hist = compute_macd(full_closes)
    macd_line = full_macd[-n:]
    signal_line = full_sig[-n:]
    hist_line = full_hist[-n:]

    # Configure axes appearance
    for ax in (ax_candle, ax_vol, ax_macd):
        ax.set_facecolor(PANEL_COLOR)
        ax.grid(True, linestyle="--", linewidth=0.5, color=GRID_COLOR, alpha=0.7)
        ax.tick_params(colors=TEXT_COLOR, labelsize=7.5)
        for spine in ax.spines.values():
            spine.set_color(GRID_COLOR)

    # 1. Plot Candlesticks
    candle_width = 0.65
    wick_width = 1.0 if n <= 100 else 0.8

    for i in range(n):
        o, c, h, l = opens[i], closes[i], highs[i], lows[i]
        is_bullish = c >= o
        color = BULL_COLOR if is_bullish else BEAR_COLOR

        # Draw Wick
        ax_candle.vlines(i, l, h, color=color, linewidth=wick_width, zorder=2)

        # Draw Body
        body_bottom = min(o, c)
        body_height = max(abs(c - o), (highs[i] - lows[i]) * 0.005)
        rect = patches.Rectangle(
            (i - candle_width / 2, body_bottom),
            candle_width,
            body_height,
            facecolor=color,
            edgecolor=color,
            zorder=3
        )
        ax_candle.add_patch(rect)

    # Plot Moving Averages (MA 25 & MA 50)
    ax_candle.plot(indices, ma_25, color=MA25_COLOR, linewidth=1.2, label="MA 25", zorder=4)
    ax_candle.plot(indices, ma_50, color=MA50_COLOR, linewidth=1.2, label="MA 50", zorder=4)

    # Highlight High and Low Markers
    max_idx = int(np.argmax(highs))
    min_idx = int(np.argmin(lows))
    max_val = highs[max_idx]
    min_val = lows[min_idx]

    ax_candle.scatter([max_idx], [max_val], color="#ffffff", s=14, zorder=5)
    ax_candle.annotate(
        f"H: ${_format_price(max_val)}",
        xy=(max_idx, max_val),
        xytext=(0, 6),
        textcoords="offset points",
        ha="center",
        fontsize=7.5,
        fontweight="bold",
        color="#ffffff",
        bbox=dict(boxstyle="round,pad=0.2", fc=BG_COLOR, ec=GRID_COLOR, lw=0.5)
    )

    ax_candle.scatter([min_idx], [min_val], color="#ffffff", s=14, zorder=5)
    ax_candle.annotate(
        f"L: ${_format_price(min_val)}",
        xy=(min_idx, min_val),
        xytext=(0, -11),
        textcoords="offset points",
        ha="center",
        fontsize=7.5,
        fontweight="bold",
        color="#ffffff",
        bbox=dict(boxstyle="round,pad=0.2", fc=BG_COLOR, ec=GRID_COLOR, lw=0.5)
    )

    # Last Price Line & Change
    last_price = closes[-1]
    first_price = opens[0]
    change_pct = ((last_price - first_price) / first_price) * 100.0
    last_color = BULL_COLOR if last_price >= first_price else BEAR_COLOR
    ax_candle.axhline(last_price, color=last_color, linestyle=":", linewidth=1.0, alpha=0.8)

    # Right Axis Price Badge (TradingView Style)
    ax_candle.text(
        1.01, last_price,
        f" ${_format_price(last_price)} ",
        transform=ax_candle.get_yaxis_transform(),
        va="center", ha="left",
        fontsize=7.5, fontweight="bold",
        color="#ffffff",
        bbox=dict(boxstyle="square,pad=0.2", fc=last_color, ec="none")
    )

    # 2. Plot Volume
    bar_colors = [BULL_COLOR if closes[i] >= opens[i] else BEAR_COLOR for i in range(n)]
    ax_vol.bar(indices, vols, color=bar_colors, width=candle_width, alpha=0.85, zorder=2)

    # Volume SMA 20
    vol_sma = [np.mean(vols[max(0, i - 19):i + 1]) for i in range(n)]
    ax_vol.plot(indices, vol_sma, color=VOL_SMA_COLOR, linewidth=1.0, label="Vol MA", zorder=3)

    # 3. Plot MACD Indicator (MACD Line, Signal Line, and Colored Histogram)
    hist_colors = [BULL_COLOR if h >= 0 else BEAR_COLOR for h in hist_line]
    ax_macd.bar(indices, hist_line, color=hist_colors, width=candle_width, alpha=0.65, label="Hist", zorder=2)
    ax_macd.plot(indices, macd_line, color=MACD_COLOR, linewidth=1.1, label="MACD", zorder=4)
    ax_macd.plot(indices, signal_line, color=SIGNAL_COLOR, linewidth=1.1, label="Signal", zorder=4)
    ax_macd.axhline(0, color=ZERO_LINE_COLOR, linestyle="--", linewidth=0.6, alpha=0.6)

    # X-Axis Time Labels
    step = max(1, n // 6)
    x_ticks = indices[::step]
    x_labels = []
    for idx in x_ticks:
        ts = times[idx]
        try:
            dt = datetime.fromtimestamp(ts, timezone.utc)
            if interval_label.lower() in ["1d", "day1", "1w", "week1"]:
                x_labels.append(dt.strftime("%b %d"))
            else:
                x_labels.append(dt.strftime("%m-%d %H:%M" if n > 80 else "%H:%M"))
        except Exception:
            x_labels.append(str(idx))

    ax_candle.set_xticks([])
    ax_vol.set_xticks([])
    ax_macd.set_xticks(x_ticks)
    ax_macd.set_xticklabels(x_labels, fontsize=7.5)

    for ax in (ax_candle, ax_vol, ax_macd):
        ax.set_xlim(-0.8, n - 0.2)
        ax.yaxis.tick_right()

    # Y-Axis Scaling & Padding
    y_margin = (max_val - min_val) * 0.08 if max_val > min_val else 1.0
    ax_candle.set_ylim(min_val - y_margin, max_val + y_margin)

    # --- EXACT NUMERICAL BADGES & HEADERS ---
    # 1. Main Candlestick Title & Latest Exact OHLC
    change_sign = "+" if change_pct >= 0 else ""
    title_text = f"{symbol}  •  {interval_label.upper()}  •  ${_format_price(last_price)} ({change_sign}{change_pct:.2f}%)"
    ax_candle.text(0.015, 0.95, title_text, transform=ax_candle.transAxes, fontsize=9.5, fontweight="bold", color=last_color, va="top")

    cur_o, cur_h, cur_l, cur_c = opens[-1], highs[-1], lows[-1], closes[-1]
    cur_ma25 = ma_25[-1] if ma_25 and not np.isnan(ma_25[-1]) else cur_c
    cur_ma50 = ma_50[-1] if ma_50 and not np.isnan(ma_50[-1]) else cur_c
    ohlc_str = f"O: ${_format_price(cur_o)}   H: ${_format_price(cur_h)}   L: ${_format_price(cur_l)}   C: ${_format_price(cur_c)}"
    ma_str = f"MA(25): ${_format_price(cur_ma25)}   MA(50): ${_format_price(cur_ma50)}"
    ax_candle.text(0.015, 0.86, ohlc_str, transform=ax_candle.transAxes, fontsize=7.5, color=TEXT_COLOR, va="top")
    ax_candle.text(0.015, 0.77, ma_str, transform=ax_candle.transAxes, fontsize=7.5, color="#80d8ff", va="top")

    # 2. Volume Header with exact numbers
    cur_vol = vols[-1]
    cur_vol_ma = vol_sma[-1]
    vol_title = f"Volume: {_format_vol(cur_vol)}   Vol MA(20): {_format_vol(cur_vol_ma)}"
    ax_vol.text(0.015, 0.88, vol_title, transform=ax_vol.transAxes, fontsize=7.5, fontweight="bold", color=TEXT_COLOR, va="top")

    # 3. MACD Header with exact adaptive precision
    cur_m = macd_line[-1]
    cur_s = signal_line[-1]
    cur_h = hist_line[-1]
    macd_title = f"MACD(12,26,9): {_format_macd(cur_m)}   Signal: {_format_macd(cur_s)}   Hist: {_format_macd(cur_h)}"
    ax_macd.text(0.015, 0.88, macd_title, transform=ax_macd.transAxes, fontsize=7.5, fontweight="bold", color="#ffcc80", va="top")


def generate_candlestick_chart(
    symbol: str,
    interval_label: str,
    kline_data: Dict[str, list],
    num_candles: int = 100,
) -> Optional[io.BytesIO]:
    """
    Generate single high-resolution candlestick chart with Volume, MA 25/50, and MACD (12,26,9) with exact data badges.
    """
    if not kline_data or len(kline_data.get("time", [])) < 5:
        logger.warning(f"Not enough kline data for {symbol}")
        return None

    fig = plt.figure(figsize=(12, 8.5), dpi=150, facecolor=BG_COLOR)
    gs = gridspec.GridSpec(3, 1, height_ratios=[3.0, 0.85, 1.15], hspace=0.08)

    ax_candle = fig.add_subplot(gs[0, 0])
    ax_vol = fig.add_subplot(gs[1, 0])
    ax_macd = fig.add_subplot(gs[2, 0])

    _render_chart_panel(ax_candle, ax_vol, ax_macd, symbol, interval_label, kline_data, num_candles=num_candles)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", facecolor=BG_COLOR, edgecolor="none", bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_multi_candlestick_chart(
    symbol: str,
    intervals_data: List[Tuple[str, Dict[str, list]]],
    num_candles: int = 80,
) -> Optional[io.BytesIO]:
    """
    Generate combined multi-timeframe chart with Candlestick, Volume, and MACD on a single unified canvas with exact data badges.
    Supports 1, 2, or 3 timeframe panels side-by-side.
    """
    num_panels = len(intervals_data)
    if num_panels == 0:
        return None
    if num_panels == 1:
        interval_label, kline = intervals_data[0]
        return generate_candlestick_chart(symbol, interval_label, kline, num_candles=num_candles)

    width = 8.5 * num_panels
    height = 8.8

    fig = plt.figure(figsize=(width, height), dpi=150, facecolor=BG_COLOR)
    gs = gridspec.GridSpec(3, num_panels, height_ratios=[3.0, 0.85, 1.15], hspace=0.08, wspace=0.15)

    for col, (interval_label, kline) in enumerate(intervals_data):
        ax_candle = fig.add_subplot(gs[0, col])
        ax_vol = fig.add_subplot(gs[1, col])
        ax_macd = fig.add_subplot(gs[2, col])
        _render_chart_panel(ax_candle, ax_vol, ax_macd, symbol, interval_label, kline, num_candles=num_candles)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", facecolor=BG_COLOR, edgecolor="none", bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    buf.seek(0)
    return buf
