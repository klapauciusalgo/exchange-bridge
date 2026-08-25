"""Candlestick and Volume chart generator using Matplotlib with dark financial theme and MA 25 / MA 50."""
import io
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional
import matplotlib
matplotlib.use("Agg")  # Headless backend
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

logger = logging.getLogger(__name__)


def generate_candlestick_chart(
    symbol: str,
    interval_label: str,
    kline_data: Dict[str, list],
    num_candles: int = 100,
) -> Optional[io.BytesIO]:
    """
    Generate high-resolution dark-themed candlestick chart with volume and MA 25 / MA 50.
    Default period length is 100 candles (supports 20 to 200).
    Returns BytesIO buffer containing PNG image data.
    """
    raw_times = kline_data.get("time", [])
    raw_opens = kline_data.get("open", [])
    raw_closes = kline_data.get("close", [])
    raw_highs = kline_data.get("high", [])
    raw_lows = kline_data.get("low", [])
    raw_vols = kline_data.get("vol", [])

    if not raw_times or len(raw_times) < 5:
        logger.warning(f"Not enough kline data for {symbol} (count: {len(raw_times)})")
        return None

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

    # Calculate MA 25 (25 SMA)
    for i in range(len(full_closes) - n, len(full_closes)):
        window = full_closes[max(0, i - 24):i + 1]
        ma_25.append(np.mean(window) if len(window) >= 5 else np.nan)

    # Calculate MA 50 (50 SMA)
    for i in range(len(full_closes) - n, len(full_closes)):
        window = full_closes[max(0, i - 49):i + 1]
        ma_50.append(np.mean(window) if len(window) >= 5 else np.nan)

    # Colors
    bg_color = "#131722"
    panel_color = "#1e222d"
    grid_color = "#2a2e39"
    bull_color = "#089981"  # Emerald green
    bear_color = "#f23645"  # Soft red
    text_color = "#d1d4dc"
    subtext_color = "#787b86"
    ma25_color = "#2962ff"  # TradingView Blue
    ma50_color = "#ff9800"  # Amber Orange

    fig, (ax1, ax2) = plt.subplots(
        2, 1,
        figsize=(12, 6.5),
        dpi=150,
        gridspec_kw={"height_ratios": [3.5, 1], "hspace": 0.05},
        facecolor=bg_color
    )

    for ax in (ax1, ax2):
        ax.set_facecolor(panel_color)
        ax.grid(True, linestyle="--", linewidth=0.5, color=grid_color, alpha=0.7)
        ax.tick_params(colors=text_color, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(grid_color)

    # 1. Plot Candlesticks
    candle_width = 0.65
    wick_width = 1.0 if n <= 100 else 0.8

    for i in range(n):
        o, c, h, l = opens[i], closes[i], highs[i], lows[i]
        is_bullish = c >= o
        color = bull_color if is_bullish else bear_color

        # Draw Wick
        ax1.vlines(i, l, h, color=color, linewidth=wick_width, zorder=2)

        # Draw Body
        body_bottom = min(o, c)
        body_height = max(abs(c - o), (highs[i] - lows[i]) * 0.005)  # min height so doji is visible
        rect = patches.Rectangle(
            (i - candle_width / 2, body_bottom),
            candle_width,
            body_height,
            facecolor=color,
            edgecolor=color,
            zorder=3
        )
        ax1.add_patch(rect)

    # Plot Moving Averages (MA 25 & MA 50)
    ax1.plot(indices, ma_25, color=ma25_color, linewidth=1.3, label="MA 25", zorder=4)
    ax1.plot(indices, ma_50, color=ma50_color, linewidth=1.3, label="MA 50", zorder=4)

    # Highlight High and Low
    max_idx = int(np.argmax(highs))
    min_idx = int(np.argmin(lows))
    max_val = highs[max_idx]
    min_val = lows[min_idx]

    ax1.scatter([max_idx], [max_val], color="#ffffff", s=15, zorder=5)
    ax1.annotate(
        f"H: ${max_val:,.4f}" if max_val < 10 else f"H: ${max_val:,.2f}",
        xy=(max_idx, max_val),
        xytext=(0, 7),
        textcoords="offset points",
        ha="center",
        fontsize=8,
        fontweight="bold",
        color="#ffffff",
        bbox=dict(boxstyle="round,pad=0.2", fc=bg_color, ec=grid_color, lw=0.5)
    )

    ax1.scatter([min_idx], [min_val], color="#ffffff", s=15, zorder=5)
    ax1.annotate(
        f"L: ${min_val:,.4f}" if min_val < 10 else f"L: ${min_val:,.2f}",
        xy=(min_idx, min_val),
        xytext=(0, -12),
        textcoords="offset points",
        ha="center",
        fontsize=8,
        fontweight="bold",
        color="#ffffff",
        bbox=dict(boxstyle="round,pad=0.2", fc=bg_color, ec=grid_color, lw=0.5)
    )

    # Last Price Horizontal Guideline
    last_price = closes[-1]
    first_price = opens[0]
    change_pct = ((last_price - first_price) / first_price) * 100.0
    last_color = bull_color if last_price >= first_price else bear_color

    ax1.axhline(last_price, color=last_color, linestyle=":", linewidth=1.0, alpha=0.8)

    # 2. Plot Volume
    bar_colors = [bull_color if closes[i] >= opens[i] else bear_color for i in range(n)]
    ax2.bar(indices, vols, color=bar_colors, width=candle_width, alpha=0.85, zorder=2)

    # Volume SMA 20
    vol_sma = []
    for i in range(n):
        v_win = vols[max(0, i - 19):i + 1]
        vol_sma.append(np.mean(v_win))
    ax2.plot(indices, vol_sma, color="#9c27b0", linewidth=1.0, label="Vol MA", zorder=3)

    # X-Axis Time Labels (Dynamic step based on candle count)
    step = max(1, n // 7)
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

    ax1.set_xticks([])  # Hide top x-axis ticks
    ax2.set_xticks(x_ticks)
    ax2.set_xticklabels(x_labels, fontsize=8)
    ax2.set_xlim(-0.8, n - 0.2)
    ax1.set_xlim(-0.8, n - 0.2)

    # Y-Axis Formatting
    y_margin = (max_val - min_val) * 0.08
    ax1.set_ylim(min_val - y_margin, max_val + y_margin)
    ax1.yaxis.tick_right()
    ax2.yaxis.tick_right()

    # Title & Legend
    change_sign = "+" if change_pct >= 0 else ""
    title_text = f"{symbol}  •  {interval_label.upper()}  •  ${last_price:,.4f}" if last_price < 10 else f"{symbol}  •  {interval_label.upper()}  •  ${last_price:,.2f}"
    sub_title = f"{change_sign}{change_pct:.2f}% ({n} bars)"

    ax1.text(
        0.015, 0.94,
        f"{title_text} ({sub_title})",
        transform=ax1.transAxes,
        fontsize=11,
        fontweight="bold",
        color=last_color,
        va="top"
    )

    ax1.legend(
        loc="upper right",
        facecolor=bg_color,
        edgecolor=grid_color,
        fontsize=7,
        labelcolor=text_color,
        framealpha=0.8
    )

    buf = io.BytesIO()
    plt.savefig(buf, format="png", facecolor=bg_color, edgecolor="none", bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    buf.seek(0)
    return buf
