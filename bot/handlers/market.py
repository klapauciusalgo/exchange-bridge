"""Market data, price lookup, orderbook, watchlist, and chart handlers."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.db import Database
from database.models import WatchlistAlert
from exchange.mexc_client import MexcClient
from bot.formatters import format_ticker, format_market, format_orderbook, format_watchlist, format_scan_results
from services.chart_generator import generate_candlestick_chart
from services.market_scanner import MarketScanner

logger = logging.getLogger(__name__)


async def handle_price(update: Update, context: ContextTypes.DEFAULT_TYPE, client: MexcClient) -> None:
    """Handle /price <symbol>."""
    if not context.args:
        await update.effective_message.reply_text("Usage: `/price <symbol>` (e.g. `/price BTC` or `/price ETH_USDT`)", parse_mode="Markdown")
        return

    symbol = context.args[0].strip()
    try:
        data = await client.get_ticker(symbol)
        if isinstance(data, list) and data:
            data = data[0]
        if not data or not isinstance(data, dict):
            await update.effective_message.reply_text(f"❌ Could not find ticker for symbol `{symbol}`.", parse_mode="Markdown")
            return

        msg = format_ticker(data)
        await update.effective_message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error fetching price for {symbol}: {e}")
        await update.effective_message.reply_text(f"❌ *Failed to fetch price:* `{e}`", parse_mode="Markdown")


async def handle_market(update: Update, context: ContextTypes.DEFAULT_TYPE, client: MexcClient) -> None:
    """Handle /market <symbol>."""
    if not context.args:
        await update.effective_message.reply_text("Usage: `/market <symbol>` (e.g. `/market BTC`)", parse_mode="Markdown")
        return

    symbol = client.normalize_symbol(context.args[0].strip())
    try:
        detail = await client.get_symbol_detail(symbol) or {"symbol": symbol}
        funding = await client.get_funding_rate(symbol)
        ticker = await client.get_ticker(symbol)
        if isinstance(ticker, list) and ticker:
            ticker = ticker[0]

        msg = format_market(detail, funding, ticker or {})
        await update.effective_message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error fetching market detail for {symbol}: {e}")
        await update.effective_message.reply_text(f"❌ *Failed to fetch market details:* `{e}`", parse_mode="Markdown")


async def handle_orderbook(update: Update, context: ContextTypes.DEFAULT_TYPE, client: MexcClient) -> None:
    """Handle /orderbook <symbol>."""
    if not context.args:
        await update.effective_message.reply_text("Usage: `/orderbook <symbol>` (e.g. `/orderbook BTC`)", parse_mode="Markdown")
        return

    symbol = context.args[0].strip()
    try:
        depth = await client.get_depth(symbol, limit=6)
        if not depth:
            await update.effective_message.reply_text(f"❌ Orderbook not available for `{symbol}`.", parse_mode="Markdown")
            return
        depth["symbol"] = client.normalize_symbol(symbol)
        msg = format_orderbook(depth, limit=6)
        await update.effective_message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error fetching orderbook for {symbol}: {e}")
        await update.effective_message.reply_text(f"❌ *Failed to fetch orderbook:* `{e}`", parse_mode="Markdown")


async def handle_chart(update: Update, context: ContextTypes.DEFAULT_TYPE, client: MexcClient) -> None:
    """
    Handle /chart <symbol> [interval] [candle_count]
    Generates and sends a high-resolution dark-themed candlestick chart with volume and MAs.
    Examples: /chart BTC, /chart ETH 1h, /chart SOL 15m 120, /chart WLD 1d 150
    """
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "📖 *Usage:* `/chart <symbol> [interval] [candles]`\n\n"
            "*Examples:*\n"
            "• `/chart BTC` (Default: 15m, 100 bars)\n"
            "• `/chart ETH 1h` (1-hour timeframe)\n"
            "• `/chart SOL 15m 150` (150 candles)\n"
            "• `/chart WLD 1d 120` (120 daily candles)\n\n"
            "*Intervals:* `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`, `1w`",
            parse_mode="Markdown"
        )
        return

    symbol_arg = args[0].strip()
    symbol = client.normalize_symbol(symbol_arg)
    interval_str = "15m"
    candle_count = 100

    # Parse optional interval and candle count
    if len(args) > 1:
        arg1 = args[1].strip().lower()
        if arg1.isdigit():
            candle_count = int(arg1)
        else:
            interval_str = arg1
            if len(args) > 2 and args[2].strip().isdigit():
                candle_count = int(args[2].strip())

    candle_count = max(20, min(200, candle_count))

    status_msg = await update.effective_message.reply_text(f"⏳ *Generating {candle_count}-bar chart for {symbol} ({interval_str})...*", parse_mode="Markdown")

    try:
        klines = await client.get_kline(symbol, interval=interval_str)
        if not klines or not isinstance(klines, dict) or len(klines.get("time", [])) < 5:
            await status_msg.edit_text(f"❌ Could not retrieve chart data for `{symbol}` ({interval_str}).", parse_mode="Markdown")
            return

        chart_buf = generate_candlestick_chart(symbol, interval_str, klines, num_candles=candle_count)
        if not chart_buf:
            await status_msg.edit_text(f"❌ Failed to render chart for `{symbol}`.", parse_mode="Markdown")
            return

        # Fetch ticker for caption summary
        ticker = await client.get_ticker(symbol)
        if isinstance(ticker, list) and ticker:
            ticker = ticker[0]
        last_price = float(ticker.get("lastPrice", 0.0)) if ticker else 0.0
        change_24h = float(ticker.get("riseFallRate", 0.0)) * 100.0 if ticker else 0.0
        chg_sign = "🟢 +" if change_24h >= 0 else "🔴 "

        caption = (
            f"📊 *{symbol}* ({interval_str.upper()})\n"
            f"• *Last Price:* `${last_price:,.4f}`\n"
            f"• *24h Change:* {chg_sign}`{change_24h:.2f}%`"
        )

        await update.effective_message.reply_photo(
            photo=chart_buf,
            caption=caption,
            parse_mode="Markdown"
        )
        try:
            await status_msg.delete()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Error generating chart for {symbol}: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ *Chart Error:* `{e}`", parse_mode="Markdown")


async def handle_watch(update: Update, context: ContextTypes.DEFAULT_TYPE, client: MexcClient, db: Database) -> None:
    """Handle /watch <symbol> <above|below> <price>."""
    if len(context.args) < 3:
        await update.effective_message.reply_text("Usage: `/watch <symbol> <above|below> <target_price>`\nExample: `/watch BTC above 70000`", parse_mode="Markdown")
        return

    symbol = client.normalize_symbol(context.args[0].strip())
    condition = context.args[1].strip().upper()
    if condition not in ["ABOVE", "BELOW"]:
        await update.effective_message.reply_text("Condition must be `above` or `below`.", parse_mode="Markdown")
        return

    try:
        target_price = float(context.args[2].strip().replace(",", "").replace("$", ""))
    except ValueError:
        await update.effective_message.reply_text("Invalid target price number.", parse_mode="Markdown")
        return

    user_id = update.effective_user.id
    alert = WatchlistAlert(
        user_id=user_id,
        symbol=symbol,
        condition=condition,
        target_price=target_price,
    )
    alert_id = await db.add_watchlist_alert(alert)

    await update.effective_message.reply_text(
        f"🎯 *Price alert created* (ID: `#{alert_id}`)\n"
        f"You will be alerted when *{symbol}* goes {condition} `${target_price:,.4f}`.",
        parse_mode="Markdown"
    )


async def handle_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database) -> None:
    """Handle /watchlist."""
    user_id = update.effective_user.id
    alerts = await db.get_user_watchlist(user_id)
    msg = format_watchlist(alerts)
    await update.effective_message.reply_text(msg, parse_mode="Markdown")


async def handle_unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Database) -> None:
    """Handle /unwatch <id>."""
    if not context.args:
        await update.effective_message.reply_text("Usage: `/unwatch <alert_id>`", parse_mode="Markdown")
        return

    try:
        alert_id = int(context.args[0].strip().replace("#", ""))
    except ValueError:
        await update.effective_message.reply_text("Invalid alert ID.", parse_mode="Markdown")
        return

    user_id = update.effective_user.id
    success = await db.delete_watchlist_alert(alert_id, user_id)
    if success:
        await update.effective_message.reply_text(f"✅ Alert `#{alert_id}` removed.", parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(f"❌ Alert `#{alert_id}` not found or already deleted.", parse_mode="Markdown")


async def handle_scan4h(update: Update, context: ContextTypes.DEFAULT_TYPE, client: MexcClient) -> None:
    """
    Handle /scan4h [long|short]
    Scans all MEXC futures pairs for 4H RSI and Funding Rate trading setups.
    """
    status_msg = await update.effective_message.reply_text(
        "🔍 *Scanning MEXC 4H Futures markets for Long/Short setups...*\n_Analyzing RSI(14) and funding rates across all pairs._",
        parse_mode="Markdown"
    )

    side_filter: Optional[str] = None
    if context.args:
        arg = context.args[0].strip().upper()
        if arg in ["LONG", "BUY"]:
            side_filter = "LONG"
        elif arg in ["SHORT", "SELL"]:
            side_filter = "SHORT"

    try:
        scanner = MarketScanner(client)
        data = await scanner.scan_4h(side_filter=side_filter)
        card_text = format_scan_results(data, timeframe="4H")

        # Quick navigation buttons for top signals
        buttons = []
        top_long = data["longs"][0]["symbol"].replace("_USDT", "") if data.get("longs") else None
        top_short = data["shorts"][0]["symbol"].replace("_USDT", "") if data.get("shorts") else None

        row = []
        if top_long:
            row.append(InlineKeyboardButton(f"📈 Chart {top_long}", callback_data=f"nav:chart_{top_long}"))
        if top_short:
            row.append(InlineKeyboardButton(f"📉 Chart {top_short}", callback_data=f"nav:chart_{top_short}"))
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🔄 Rescan 4H", callback_data="nav:scan4h")])

        keyboard = InlineKeyboardMarkup(buttons)
        await status_msg.edit_text(card_text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error executing 4h scan: {e}")
        await status_msg.edit_text(f"❌ *Scanner Error:* `{e}`", parse_mode="Markdown")
