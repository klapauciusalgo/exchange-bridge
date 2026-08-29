"""Markdown message formatters for Telegram Bot UI."""
from datetime import datetime, timezone
from typing import Dict, List, Optional
from database.models import UserRiskConfig, WatchlistAlert, DailyTradingStats
from risk.risk_engine import ValidationResult


def format_balance(assets: List[dict], daily_stats: Optional[DailyTradingStats] = None) -> str:
    """Format account balance and equity summary."""
    usdt = next((a for a in assets if a.get("currency") == "USDT"), {})
    equity = float(usdt.get("equity", 0.0))
    available = float(usdt.get("availableBalance", 0.0))
    frozen = float(usdt.get("positionMargin", usdt.get("frozenBalance", 0.0)))
    unrealized_pnl = float(usdt.get("unrealized", usdt.get("unRealizedPnl", usdt.get("unrealized(positionPnl)", 0.0))))
    pnl_sign = "🟢 +" if unrealized_pnl >= 0 else "🔴 "

    lines = [
        "💰 *MEXC FUTURES BALANCE*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"• *Total Equity:* `${equity:,.2f} USDT`",
        f"• *Available Margin:* `${available:,.2f} USDT`",
        f"• *Position Margin:* `${frozen:,.2f} USDT`",
        f"• *Unrealized PnL:* {pnl_sign}`${unrealized_pnl:,.2f} USDT`",
    ]

    if daily_stats:
        net_daily = daily_stats.realized_pnl - daily_stats.fees_paid
        d_sign = "🟢 +" if net_daily >= 0 else "🔴 "
        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━",
            f"• *Today's Realized PnL:* {d_sign}`${net_daily:,.2f} USDT`",
            f"• *Trades Today:* `{daily_stats.total_trades}` (W: {daily_stats.winning_trades} / L: {daily_stats.losing_trades})",
            f"• *Daily Limit Status:* `{'🚨 LOCKED' if daily_stats.is_limit_exceeded else '✅ OK'}`",
        ])

    return "\n".join(lines)


def format_ticker(t: dict) -> str:
    """Format single ticker details."""
    symbol = t.get("symbol", "N/A")
    last_price = float(t.get("lastPrice", 0.0))
    change_rate = float(t.get("riseFallRate", 0.0)) * 100.0
    high_24h = float(t.get("high24Price", 0.0))
    low_24h = float(t.get("low24Price", 0.0))
    vol_24h = float(t.get("volume24", t.get("amount24", 0.0)))
    fair_price = float(t.get("fairPrice", last_price))

    change_emoji = "🟢" if change_rate >= 0 else "🔴"
    return (
        f"📊 *TICKER: {symbol}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• *Last Price:* `${last_price:,.4f}`\n"
        f"• *24h Change:* {change_emoji} `{change_rate:+.2f}%`\n"
        f"• *Fair/Mark Price:* `${fair_price:,.4f}`\n"
        f"• *24h High / Low:* `${high_24h:,.4f}` / `${low_24h:,.4f}`\n"
        f"• *24h Volume:* `${vol_24h:,.0f} USDT`\n"
    )


def format_market(detail: dict, funding: dict, ticker: dict) -> str:
    """Format full market & funding rate summary."""
    symbol = detail.get("symbol", "N/A")
    last_price = float(ticker.get("lastPrice", 0.0))
    mark_price = float(ticker.get("fairPrice", last_price))
    index_price = float(ticker.get("indexPrice", last_price))
    funding_rate = float(funding.get("fundingRate", 0.0)) * 100.0
    next_time = funding.get("nextSettleTime", 0)

    time_str = "N/A"
    if next_time:
        try:
            dt = datetime.fromtimestamp(next_time / 1000, timezone.utc)
            time_str = dt.strftime("%H:%M UTC")
        except Exception:
            pass

    max_lev = detail.get("maxLeverage", "N/A")

    return (
        f"🌐 *MARKET METRICS: {symbol}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• *Last Price:* `${last_price:,.4f}`\n"
        f"• *Mark Price:* `${mark_price:,.4f}`\n"
        f"• *Index Price:* `${index_price:,.4f}`\n"
        f"• *Funding Rate:* `{funding_rate:+.4f}%` (Next: {time_str})\n"
        f"• *Max Exchange Leverage:* `{max_lev}x`\n"
    )


def format_orderbook(depth: dict, limit: int = 5) -> str:
    """Format top bids and asks."""
    symbol = depth.get("symbol", "")
    bids = depth.get("bids", [])[:limit]
    asks = depth.get("asks", [])[:limit]

    lines = [f"📖 *ORDERBOOK: {symbol}*", "━━━━━━━━━━━━━━━━━━━━", "*ASKS (Sell)*"]
    # Show asks descending
    for item in reversed(asks):
        price, vol = float(item[0]), float(item[1])
        lines.append(f"  🔴 `${price:,.4f}` │ `{vol:,.2f}`")

    lines.append("────────────────────")
    lines.append("*BIDS (Buy)*")
    for item in bids:
        price, vol = float(item[0]), float(item[1])
        lines.append(f"  🟢 `${price:,.4f}` │ `{vol:,.2f}`")

    return "\n".join(lines)


def format_positions(
    positions: List[dict],
    plan_orders: Optional[List[dict]] = None,
    contract_details: Optional[Dict[str, dict]] = None,
) -> str:
    """Format list of open positions with active SL/TP and size in USDT."""
    if not positions:
        return "📭 *No active open positions.*"

    plans_by_symbol = {}
    if plan_orders:
        for plan in plan_orders:
            sym = plan.get("symbol")
            if sym not in plans_by_symbol:
                plans_by_symbol[sym] = []
            plans_by_symbol[sym].append(plan)

    cards = ["📊 *OPEN POSITIONS*", "━━━━━━━━━━━━━━━━━━━━"]
    for pos in positions:
        symbol = pos.get("symbol", "N/A")
        side_type = pos.get("positionType", 1)  # 1: Long, 2: Short
        side_str = "🟢 LONG" if side_type == 1 else "🔴 SHORT"
        vol = float(pos.get("holdVol", 0.0))
        entry_price = float(pos.get("openAvgPrice", pos.get("holdAvgPrice", pos.get("openPrice", 0.0))))
        mark_price = float(pos.get("markPrice", pos.get("fairPrice", 0.0)))
        liq_price = float(pos.get("liquidatePrice", pos.get("liquidPrice", 0.0)))
        pnl = float(pos.get("unRealizedPnl", pos.get("unrealizedPnl", pos.get("unrealized", 0.0))))
        leverage = pos.get("leverage", 1)
        margin = float(pos.get("im", pos.get("oim", pos.get("positionMargin", pos.get("margin", 0.0)))))

        # Compute position size in USDT
        contract_size = 1.0
        if contract_details and symbol in contract_details:
            contract_size = float(contract_details[symbol].get("contractSize", 1.0))
        elif pos.get("contractSize"):
            contract_size = float(pos.get("contractSize", 1.0))

        if vol > 0 and entry_price > 0:
            size_usdt = vol * contract_size * entry_price
        elif margin > 0 and leverage:
            size_usdt = margin * float(leverage)
        else:
            size_usdt = float(pos.get("positionValue", pos.get("amount", 0.0)))

        pnl_pct = (pnl / margin * 100.0) if margin > 0 else 0.0
        pnl_sign = "🟢 +" if pnl >= 0 else "🔴 "

        # Check for active plan SL/TP or stop order SL/TP
        pos_id = str(pos.get("positionId", ""))
        symbol_plans = plans_by_symbol.get(symbol, [])
        sl_val: Optional[float] = None
        tp_val: Optional[float] = None

        for p in symbol_plans:
            p_pos_id = str(p.get("positionId", ""))
            # Check stoporder fields (stopLossPrice / takeProfitPrice)
            if p_pos_id and pos_id and p_pos_id == pos_id:
                if p.get("stopLossPrice") and float(p.get("stopLossPrice")) > 0:
                    sl_val = float(p.get("stopLossPrice"))
                if p.get("takeProfitPrice") and float(p.get("takeProfitPrice")) > 0:
                    tp_val = float(p.get("takeProfitPrice"))
            elif p.get("stopLossPrice") and float(p.get("stopLossPrice")) > 0 and sl_val is None:
                sl_val = float(p.get("stopLossPrice"))
            elif p.get("takeProfitPrice") and float(p.get("takeProfitPrice")) > 0 and tp_val is None:
                tp_val = float(p.get("takeProfitPrice"))

            # Check trigger plan order fields (triggerPrice / trend)
            trig_price = p.get("triggerPrice")
            trend = p.get("trend")
            if trig_price and float(trig_price) > 0:
                t_val = float(trig_price)
                if side_type == 1:
                    if trend == 2 or t_val < entry_price:
                        if sl_val is None:
                            sl_val = t_val
                    elif trend == 1 or t_val > entry_price:
                        if tp_val is None:
                            tp_val = t_val
                else:
                    if trend == 1 or t_val > entry_price:
                        if sl_val is None:
                            sl_val = t_val
                    elif trend == 2 or t_val < entry_price:
                        if tp_val is None:
                            tp_val = t_val

        sl_str = f"${sl_val:,.4f}" if sl_val is not None and sl_val > 0 else "None"
        tp_str = f"${tp_val:,.4f}" if tp_val is not None and tp_val > 0 else "None"

        open_type = pos.get("openType", 2)
        margin_mode = "Cross" if open_type == 2 else "Isolated"

        cards.extend([
            f"*{symbol}* │ {side_str} `{leverage}x` ({margin_mode})",
            f"• *Size:* `${size_usdt:,.2f} USDT` (Margin: `${margin:,.2f}`)",
            f"• *Entry:* `${entry_price:,.4f}`" + (f" │ *Mark:* `${mark_price:,.4f}`" if mark_price > 0 else ""),
            f"• *PnL:* {pnl_sign}`${pnl:,.2f}` (`{pnl_pct:+.2f}%`)",
            f"• *Liq Price:* `${liq_price:,.4f}`",
            f"• *SL:* `{sl_str}` │ *TP:* `{tp_str}`",
            "────────────────────"
        ])

    return "\n".join(cards)


def format_orders(open_orders: List[dict], plan_orders: List[dict]) -> str:
    """Format active limit orders and trigger SL/TP plan orders."""
    if not open_orders and not plan_orders:
        return "📭 *No active open or trigger orders.*"

    lines = ["📋 *ACTIVE ORDERS*", "━━━━━━━━━━━━━━━━━━━━"]
    if open_orders:
        lines.append("*LIMIT ORDERS:*")
        for o in open_orders:
            order_id = o.get("orderId", o.get("id"))
            symbol = o.get("symbol")
            side = "BUY" if o.get("side") in [1, 4] else "SELL"
            price = float(o.get("price", 0.0))
            vol = float(o.get("vol", 0.0))
            lines.append(f"• `{order_id}` │ `{symbol}` │ {side} `{vol}` @ `${price:,.4f}`")

    if plan_orders:
        if open_orders:
            lines.append("────────────────────")
        lines.append("*STOP LOSS & TRIGGER PLAN ORDERS:*")
        for p in plan_orders:
            plan_id = p.get("orderId", p.get("id"))
            symbol = p.get("symbol")
            sl_p = float(p.get("stopLossPrice", 0.0))
            tp_p = float(p.get("takeProfitPrice", 0.0))
            trig_p = float(p.get("triggerPrice", 0.0))

            desc_parts = []
            if sl_p > 0:
                desc_parts.append(f"SL @ `${sl_p:,.4f}`")
            if tp_p > 0:
                desc_parts.append(f"TP @ `${tp_p:,.4f}`")
            if trig_p > 0:
                desc_parts.append(f"Trigger @ `${trig_p:,.4f}`")

            desc = " │ ".join(desc_parts) if desc_parts else "Active Trigger"
            lines.append(f"• `#{plan_id}` │ `{symbol}` │ {desc}")

    return "\n".join(lines)


def format_order_preview(
    symbol: str,
    side_str: str,
    size_usdt: float,
    leverage: int,
    order_type_str: str,
    price: float,
    vr: ValidationResult,
    sl_price: Optional[float] = None,
    tp_price: Optional[float] = None,
    is_dry_run: bool = False,
    margin_mode: str = "Cross",
) -> str:
    """Format detailed pre-trade confirmation summary."""
    mode_badge = "🧪 *[DRY RUN / SIMULATION]*\n" if is_dry_run else ""
    rr_str = f"`1:{vr.risk_reward_ratio:.2f}`" if vr.risk_reward_ratio else "`N/A`"

    lines = [
        f"{mode_badge}⚠️ *TRADE CONFIRMATION REQUIRED*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"• *Symbol:* `{symbol}`",
        f"• *Action:* *{side_str.upper()}* (`{leverage}x` Leverage │ 🌐 `{margin_mode}`)",
        f"• *Order Type:* `{order_type_str.upper()}`" + (f" @ `${price:,.4f}`" if price > 0 else " (Market)"),
        f"• *Position Value:* `${size_usdt:,.2f} USDT`",
        f"• *Required Margin:* `${vr.estimated_margin:,.2f} USDT` (`{vr.margin_ratio_pct:.1f}%` of Equity)",
        f"• *Est. Liquidation Price:* `${vr.estimated_liq_price:,.4f}`",
    ]

    if sl_price:
        lines.append(f"• *Stop Loss:* `${sl_price:,.4f}`")
    if tp_price:
        lines.append(f"• *Take Profit:* `${tp_price:,.4f}`")
    if sl_price and tp_price:
        lines.append(f"• *Risk / Reward:* {rr_str}")

    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━",
        "Tap *Confirm* below to execute, or *Cancel* to abort."
    ])

    return "\n".join(lines)


def format_help() -> str:
    """Format full command guide."""
    return (
        "🤖 *MEXC TELEGRAM TRADING BRIDGE*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "*Account & Market:*\n"
        "• `/balance` — View total equity, margins & daily PnL\n"
        "• `/positions` — View active open positions & SL/TP\n"
        "• `/orders` — View open limit & plan orders\n"
        "• `/price <symbol>` — Price, 24h change & volume\n"
        "• `/market <symbol>` — Funding rate, OI & mark price\n"
        "• `/orderbook <symbol>` — Top bids and asks\n"
        "• `/chart <sym> [interval]` — Candlestick & volume chart\n\n"
        "*Trading Execution:*\n"
        "• `/open <sym> <long|short> <size_usdt> <lev> [mkt|lmt] [price] [sl=..] [tp=..]`\n"
        "• `/close <symbol> [%]` — Close position (e.g. `/close BTC 100`)\n"
        "• `/setsl <symbol> <price|ROI%>` — Set/update Stop Loss (e.g. `/setsl BTC 120%`)\n"
        "• `/settp <symbol> <price|ROI%>` — Set/update Take Profit (e.g. `/settp BTC 200%`)\n"
        "• `/cancel <order_id>` — Cancel specific order\n"
        "• `/panic` or `/closeall` — 🚨 Emergency kill switch (closes all & cancels all)\n\n"
        "*Alerts & Watchlist:*\n"
        "• `/similar <symbol> [30m|4h|1d]` — 🎯 Find similar pre-move setups\n"
        "• `/scan4h` — 🔍 Scan 4H markets for Long/Short setups\n"
        "• `/watch <symbol> <above|below> <price>` — Set price alert\n"
        "• `/watchlist` — View active price alerts\n"
        "• `/unwatch <id>` — Remove alert\n\n"
        "*Risk & Admin:*\n"
        "• `/risklimit` — View / edit risk limits\n"
        "• `/dryrun [on|off]` — Toggle simulation mode\n"
        "• `/autopos [on|off|min|now]` — Hourly /positions broadcast\n"
        "• `/auth <pin>` — Unlock PIN trading session\n"
    )


def format_risk_settings(cfg: UserRiskConfig) -> str:
    """Format current risk limits."""
    return (
        "🛡️ *RISK CONTROL SETTINGS*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• *Max Leverage:* `{cfg.max_leverage}x`\n"
        f"• *Max Position % of Equity:* `{cfg.max_position_pct}%`\n"
        f"• *Daily Loss Limit (USDT):* `${cfg.daily_loss_limit_usdt:,.2f}`\n"
        f"• *Max Daily Loss %:* `{cfg.max_daily_loss_pct}%`\n"
        f"• *Dry Run (Simulation):* `{'ON' if cfg.dry_run else 'OFF'}`\n"
        f"• *PIN Security:* `{'ENABLED' if cfg.require_pin else 'DISABLED'}`\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "To update: `/risklimit <max_lev> <max_pos_pct> <daily_loss_usdt>`"
    )


def format_watchlist(alerts: List[WatchlistAlert]) -> str:
    """Format active watchlist alerts."""
    if not alerts:
        return "📭 *No active price alerts in watchlist.*"

    lines = ["🎯 *ACTIVE PRICE ALERTS*", "━━━━━━━━━━━━━━━━━━━━"]
    for a in alerts:
        lines.append(f"• `#{a.id}` │ *{a.symbol}* {a.condition} `${a.target_price:,.4f}`")
    lines.append("\nTo remove an alert: `/unwatch <id>`")
    return "\n".join(lines)


def format_scan_results(data: dict, timeframe: str = "4H") -> str:
    """Format market scanner results into a readable card."""
    longs = data.get("longs", [])
    shorts = data.get("shorts", [])
    total_scanned = data.get("total_scanned", 0)

    lines = [
        f"🔍 *MEXC {timeframe} MARKET SCANNER*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📊 *Criteria:* RSI(14) & Funding Rate Filters",
        f"• Total Pairs Scanned: `{total_scanned}`",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    # Long Section
    lines.append("🟢 *LONG SETUPS (RSI > 55 │ 0.001% ≤ FR ≤ 0.01%)*")
    if longs:
        for x in longs[:8]:
            sym = x["symbol"]
            rsi = x["rsi"]
            fr = x["funding_pct"]
            price = x["price"]
            vol_m = x["vol24"] / 1e6
            chg = x["change24"]
            lines.append(f"• *{sym}* │ `${price:,.4f}` ({chg:+.2f}%)")
            lines.append(f"   RSI: `{rsi:.1f}` │ FR: `{fr:+.4f}%` │ Vol: `${vol_m:.1f}M`")
    else:
        lines.append("  _No Long candidates matching criteria currently._")

    lines.append("────────────────────")

    # Short Section
    lines.append("🔴 *SHORT SETUPS (RSI < 45 │ FR > 0.1% or FR < 0%)*")
    if shorts:
        for x in shorts[:8]:
            sym = x["symbol"]
            rsi = x["rsi"]
            fr = x["funding_pct"]
            price = x["price"]
            vol_m = x["vol24"] / 1e6
            chg = x["change24"]
            lines.append(f"• *{sym}* │ `${price:,.4f}` ({chg:+.2f}%)")
            lines.append(f"   RSI: `{rsi:.1f}` │ FR: `{fr:+.4f}%` │ Vol: `${vol_m:.1f}M`")
    else:
        lines.append("  _No Short candidates matching criteria currently._")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 _Use `/open <sym> <long|short> <size> <lev>` to execute._")

    return "\n".join(lines)


def format_similar_recommendations(data: dict) -> str:
    """Format CPDE similar pattern recommendations into a readable Telegram card."""
    target = data.get("target_symbol", "N/A")
    tf = data.get("timeframe", "4H")
    status = data.get("pre_move_status", "Reference State")
    target_rsi = data.get("target_rsi", 50.0)
    target_bb = data.get("target_bb_width", 5.0)
    candidates = data.get("top_candidates", [])

    lines = [
        f"🎯 *PATTERN DISCOVERY: SIMILAR SETUPS*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📌 *Reference Asset:* `{target}` ({tf})",
        f"• *State:* _{status}_",
        f"• *Ref RSI:* `{target_rsi:.1f}` │ *Ref BB Width:* `{target_bb:.2f}%`",
        "━━━━━━━━━━━━━━━━━━━━",
        "🔮 *TOP 5 SIMILAR PRE-MOVE CANDIDATES:*",
        "",
    ]

    if not candidates:
        lines.append("_No qualifying pre-move setups found matching criteria._")
    else:
        medals = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        for idx, c in enumerate(candidates):
            badge = medals[idx] if idx < len(medals) else f"#{idx+1}"
            sym = c["symbol"]
            sim = c["similarity_score"]
            score = c["recommendation_score"]
            conf = c["confidence"]
            price = c["price"]
            chg = c["chg24"]
            vol_m = c["vol24"] / 1e6
            rsi = c["rsi"]
            bb = c["bb_width"]
            reasons_str = ", ".join(c.get("reasons", ["Matching structure"]))

            lines.append(f"{badge} *{sym}* │ `${price:,.4f}` ({chg:+.2f}%)")
            lines.append(f"   • *Similarity:* `{sim:.1f}%` │ *Score:* `{score:.1f}/100` ({conf})")
            lines.append(f"   • *RSI:* `{rsi:.1f}` │ *BB Width:* `{bb:.2f}%` │ *24h Vol:* `${vol_m:.1f}M`")
            lines.append(f"   • _Why:_ {reasons_str}")
            lines.append("────────────────────")

    lines.append("💡 _Objective: Find the next setup, not the next pump._")
    return "\n".join(lines)
