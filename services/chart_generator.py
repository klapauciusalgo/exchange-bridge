"""Candlestick and Volume chart generator using Matplotlib with dark financial theme, MA 25 / MA 50, and Multi-Timeframe support."""
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
MA25_COLOR = "#2962ff"  # TradingView Blue
MA50_COLOR = "#ff9800"  # Amber Orange
VOL_SMA_COLOR = "#9c27b0"  # Purple


def _render_chart_panel(
    ax_candle: plt.Axes,
    ax_vol: plt.Axes,
    symbol: str,
    interval_label: str,
    kline_data: Dict[str, list],
    num_candles: int = 80,
) -> None:
    """Render a single candlestick + volume panel into the specified axes."""
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

    # Calculate MA 25 and MA 50 on full available closes
    full_closes = [float(x) for x in raw_closes]
    ma_25 = []
    ma_50 = []

    for i in range(len(full_closes) - n, len(full_closes)):
        window25 = full_closes[max(0, i - 24):i + 1]
        ma_25.append(np.mean(window25) if len(window25) >= 5 else np.nan)
        window50 = full_closes[max(0, i - 49):i + 1]
        ma_50.append(np.mean(window50) if len(window50) >= 5 else np.nan)

    # Configure axes appearance
    for ax in (ax_candle, ax_vol):
        ax.set_facecolor(PANEL_COLOR)
        ax.grid(True, linestyle="--", linewidth=0.5, color=GRID_COLOR, alpha=0.7)
        ax.tick_params(colors=TEXT_COLOR, labelsize=8)
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

    # Highlight High and Low
    max_idx = int(np.argmax(highs))
    min_idx = int(np.argmin(lows))
    max_val = highs[max_idx]
    min_val = lows[min_idx]

    ax_candle.scatter([max_idx], [max_val], color="#ffffff", s=14, zorder=5)
    ax_candle.annotate(
        f"H: ${max_val:,.4f}" if max_val < 10 else f"H: ${max_val:,.2f}",
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
        f"L: ${min_val:,.4f}" if min_val < 10 else f"L: ${min_val:,.2f}",
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

    # 2. Plot Volume
    bar_colors = [BULL_COLOR if closes[i] >= opens[i] else BEAR_COLOR for i in range(n)]
    ax_vol.bar(indices, vols, color=bar_colors, width=candle_width, alpha=0.85, zorder=2)

    # Volume SMA 20
    vol_sma = [np.mean(vols[max(0, i - 19):i + 1]) for i in range(n)]
    ax_vol.plot(indices, vol_sma, color=VOL_SMA_COLOR, linewidth=1.0, label="Vol MA", zorder=3)

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
    ax_vol.set_xticks(x_ticks)
    ax_vol.set_xticklabels(x_labels, fontsize=7.5)
    ax_vol.set_xlim(-0.8, n - 0.2)
    ax_candle.set_xlim(-0.8, n - 0.2)

    # Y-Axis Scaling & Padding
    y_margin = (max_val - min_val) * 0.08 if max_val > min_val else 1.0
    ax_candle.set_ylim(min_val - y_margin, max_val + y_margin)
    ax_candle.yaxis.tick_right()
    ax_vol.yaxis.tick_right()

    # Panel Title & Legend
    change_sign = "+" if change_pct >= 0 else ""
    title_price_str = f"${last_price:,.4f}" if last_price < 10 else f"${last_price:,.2f}"
    title_text = f"{symbol}  •  {interval_label.upper()}  •  {title_price_str}"
    sub_title = f"{change_sign}{change_pct:.2f}% ({n} bars)"

    ax_candle.text(
        0.015, 0.94,
        f"{title_text} ({sub_title})",
        transform=ax_candle.transAxes,
        fontsize=10,
        fontweight="bold",
        color=last_color,
        va="top"
    )

    ax_candle.legend(
        loc="upper right",
        facecolor=BG_COLOR,
        edgecolor=GRID_COLOR,
        fontsize=7,
        labelcolor=TEXT_COLOR,
        framealpha=0.8
    )


def generate_candlestick_chart(
    symbol: str,
    interval_label: str,
    kline_data: Dict[str, list],
    num_candles: int = 100,
) -> Optional[io.BytesIO]:
    """
    Generate single high-resolution candlestick chart with volume and MA 25 / MA 50.
    """
    if not kline_data or len(kline_data.get("time", [])) < 5:
        logger.warning(f"Not enough kline data for {symbol}")
        return None

    fig, (ax1, ax2) = plt.subplots(
        2, 1,
        figsize=(12, 6.5),
        dpi=150,
        gridspec_kw={"height_ratios": [3.5, 1], "hspace": 0.05},
        facecolor=BG_COLOR
    )

    _render_chart_panel(ax1, ax2, symbol, interval_label, kline_data, num_candles=num_candles)

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
    Generate combined multi-timeframe candlestick chart (e.g. 1H and 4H) on a single unified canvas.
    Supports 1, 2, or 3 timeframe panels side-by-side.
    """
    num_panels = len(intervals_data)
    if num_panels == 0:
        return None
    if num_panels == 1:
        interval_label, kline = intervals_data[0]
        return generate_candlestick_chart(symbol, interval_label, kline, num_candles=num_candles)

    # Dynamic sizing based on number of panels (2 columns: 17x7.5, 3 columns: 23x7.5)
    width = 8.5 * num_panels
    height = 7.2

    fig = plt.figure(figsize=(width, height), dpi=150, facecolor=BG_COLOR)
    gs = gridspec.GridSpec(2, num_panels, height_ratios=[3.5, 1], hspace=0.08, wspace=0.15)

    for col, (interval_label, kline) in enumerate(intervals_data):
        ax_candle = fig.add_subplot(gs[0, col])
        ax_vol = fig.add_subplot(gs[1, col])
        _render_chart_panel(ax_candle, ax_vol, symbol, interval_label, kline, num_candles=num_candles)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", facecolor=BG_COLOR, edgecolor="none", bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    buf.seek(0)
    return buf
