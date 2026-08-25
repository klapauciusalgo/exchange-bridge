"""Proactive Notification and Alert Service for fills, SL/TP triggers, liquidation risk, price alerts, and periodic positions."""
import asyncio
import logging
import time
from typing import Dict, Optional, Set
import aiohttp

from config.settings import Settings
from database.db import Database
from exchange.mexc_client import MexcClient
from exchange.mexc_ws import MexcWebSocketClient
from bot.formatters import format_positions

logger = logging.getLogger(__name__)


class NotificationService:
    """Dispatches proactive alerts to Telegram and optional backup webhooks."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        mexc_client: MexcClient,
        ws_client: MexcWebSocketClient,
        telegram_bot_app=None,
    ):
        self.settings = settings
        self.db = db
        self.client = mexc_client
        self.ws = ws_client
        self.bot_app = telegram_bot_app
        self._running: bool = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._positions_scheduler_task: Optional[asyncio.Task] = None
        self._alerted_liq_symbols: Dict[str, float] = {}  # symbol -> last_alert_time
        self._alerted_funding_symbols: Dict[str, float] = {}

        # Subscribe to WS events
        self.ws.add_order_listener(self._handle_ws_order_update)
        self.ws.add_position_listener(self._handle_ws_position_update)
        self.ws.add_ticker_listener(self._handle_ws_ticker_update)

    def set_bot_app(self, app) -> None:
        self.bot_app = app

    async def start(self) -> None:
        """Start proactive monitoring and periodic position broadcast background loops."""
        if self._running:
            return
        self._running = True
        self._monitor_task = asyncio.create_task(self._risk_monitor_loop())
        self._positions_scheduler_task = asyncio.create_task(self._auto_positions_loop())
        logger.info("Notification, alert, and auto-positions scheduler service started")

    async def stop(self) -> None:
        """Stop notification service."""
        self._running = False
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        if self._positions_scheduler_task and not self._positions_scheduler_task.done():
            self._positions_scheduler_task.cancel()
        logger.info("Notification and alert service stopped")

    async def broadcast_alert(self, message: str, is_critical: bool = False) -> None:
        """Send message to all whitelisted Telegram users and backup webhook if critical."""
        logger.info(f"Broadcasting alert: {message[:100]}...")

        # Send via Telegram Bot
        if self.bot_app:
            for user_id in self.settings.TELEGRAM_WHITELISTED_USERS:
                try:
                    await self.bot_app.bot.send_message(
                        chat_id=user_id,
                        text=message,
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Failed to send Telegram alert to {user_id}: {e}")

        # Send via Fallback Webhook if configured and critical
        if is_critical and self.settings.FALLBACK_WEBHOOK_URL:
            try:
                timeout = aiohttp.ClientTimeout(total=5)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    payload = {"content": f"🚨 **CRITICAL MEXC BRIDGE ALERT** 🚨\n{message}"}
                    await session.post(self.settings.FALLBACK_WEBHOOK_URL, json=payload)
            except Exception as e:
                logger.error(f"Failed to send fallback webhook alert: {e}")

    # ==========================================
    # Periodic Auto-Positions Broadcast Loop (e.g. Every 1 Hour)
    # ==========================================

    async def _auto_positions_loop(self) -> None:
        """
        Background loop to broadcast /positions on exact clock hours (e.g. 12:00, 13:00, 14:00).
        Calculates sleep duration dynamically to align to the exact top-of-the-hour boundary.
        """
        while self._running:
            try:
                interval_minutes = max(1, self.settings.AUTO_POSITIONS_INTERVAL_MINUTES)
                interval_sec = interval_minutes * 60

                # Compute remaining seconds until the next exact clock boundary
                now = time.time()
                sec_into_interval = int(now) % interval_sec
                seconds_to_wait = interval_sec - sec_into_interval
                if seconds_to_wait <= 0:
                    seconds_to_wait = interval_sec

                logger.debug(f"Auto-positions scheduler waiting {seconds_to_wait:.1f}s until next clock boundary.")
                await asyncio.sleep(seconds_to_wait)

                if self.settings.AUTO_POSITIONS_ENABLED and self._running:
                    await self.broadcast_positions_snapshot()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in auto positions broadcast loop: {e}")
                await asyncio.sleep(10)

    async def broadcast_positions_snapshot(self) -> None:
        """Fetch current positions and broadcast formatted snapshot to whitelisted users."""
        try:
            positions = await self.client.get_open_positions()
            plan_orders = await self.client.get_plan_orders()
            contract_details = await self.client.get_contract_details()

            formatted_body = format_positions(positions, plan_orders, contract_details)
            interval_str = f"{self.settings.AUTO_POSITIONS_INTERVAL_MINUTES}m" if self.settings.AUTO_POSITIONS_INTERVAL_MINUTES != 60 else "1 Hour"
            msg = f"⏰ *AUTOMATED POSITIONS SNAPSHOT* (Every {interval_str})\n\n{formatted_body}"
            await self.broadcast_alert(msg)
            logger.info("Automated periodic positions snapshot broadcasted successfully.")
        except Exception as e:
            logger.error(f"Failed to broadcast automated positions snapshot: {e}")

    # ==========================================
    # WS Event Handlers
    # ==========================================

    async def _handle_ws_order_update(self, order_data: dict) -> None:
        """Process order fill/cancel events from WebSocket."""
        try:
            symbol = order_data.get("symbol", "N/A")
            status = order_data.get("status")  # e.g., 3: filled, 4: partially filled, 2: canceled
            side = order_data.get("side")  # 1: Open Long, 2: Close Short, 3: Open Short, 4: Close Long
            price = order_data.get("price", order_data.get("dealAvgPrice", 0.0))
            vol = order_data.get("vol", order_data.get("dealVol", 0.0))
            order_id = str(order_data.get("id", order_data.get("orderId", "")))

            side_map = {1: "🟢 OPEN LONG", 2: "🔴 CLOSE SHORT", 3: "🔴 OPEN SHORT", 4: "🟢 CLOSE LONG"}
            side_str = side_map.get(side, f"Side {side}")

            if status in [3, "FILLED"]:
                msg = (
                    f"✅ *ORDER FILLED*\n\n"
                    f"• *Symbol:* `{symbol}`\n"
                    f"• *Action:* {side_str}\n"
                    f"• *Price:* `${price:.4f}`\n"
                    f"• *Volume:* `{vol}`\n"
                    f"• *Order ID:* `{order_id}`"
                )
                await self.broadcast_alert(msg)
                await self.db.update_order_status(order_id, status="FILLED", filled_price=float(price))

            elif status in [2, "CANCELED"]:
                msg = f"ℹ️ *ORDER CANCELED*\n\n• *Symbol:* `{symbol}`\n• *Order ID:* `{order_id}`"
                await self.broadcast_alert(msg)
                await self.db.update_order_status(order_id, status="CANCELED")
        except Exception as e:
            logger.error(f"Error handling WS order update: {e}")

    async def _handle_ws_position_update(self, pos_data: dict) -> None:
        """Check for SL/TP trigger or close updates on positions."""
        pass

    async def _handle_ws_ticker_update(self, symbol: str, ticker_data: dict) -> None:
        """Check watchlist price alerts against real-time tickers."""
        try:
            last_price = float(ticker_data.get("lastPrice", ticker_data.get("fairPrice", 0.0)))
            if last_price <= 0:
                return

            active_alerts = await self.db.get_active_watchlist(symbol)
            for alert in active_alerts:
                is_triggered = False
                if alert.condition == "ABOVE" and last_price >= alert.target_price:
                    is_triggered = True
                elif alert.condition == "BELOW" and last_price <= alert.target_price:
                    is_triggered = True

                if is_triggered:
                    msg = (
                        f"🎯 *PRICE ALERT TRIGGERED*\n\n"
                        f"• *Symbol:* `{alert.symbol}`\n"
                        f"• *Condition:* Price went {alert.condition} `${alert.target_price:.4f}`\n"
                        f"• *Current Price:* `${last_price:.4f}`"
                    )
                    await self.broadcast_alert(msg)
                    if alert.id:
                        await self.db.mark_watchlist_triggered(alert.id)
        except Exception as e:
            logger.error(f"Error in watchlist price check: {e}")

    # ==========================================
    # Periodic Risk Monitor Loop
    # ==========================================

    async def _risk_monitor_loop(self) -> None:
        """Background loop to check liquidation distance and funding rate warnings."""
        while self._running:
            try:
                await asyncio.sleep(10)
                await self._check_liquidation_risks()
                await self._check_extreme_funding_rates()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in risk monitor loop: {e}")

    async def _check_liquidation_risks(self) -> None:
        """Warn user if mark price is getting dangerously close to liquidation price."""
        positions = list(self.ws.positions.values())
        if not positions and not self.client.dry_run:
            try:
                positions = await self.client.get_open_positions()
            except Exception:
                positions = []

        now = time.time()
        for pos in positions:
            symbol = pos.get("symbol", "")
            mark_price = float(pos.get("markPrice", pos.get("fairPrice", 0.0)))
            liq_price = float(pos.get("liquidatePrice", pos.get("liquidPrice", 0.0)))
            hold_vol = float(pos.get("holdVol", pos.get("positionMargin", 0.0)))

            if hold_vol <= 0 or liq_price <= 0 or mark_price <= 0:
                continue

            # Calculate distance %
            distance_pct = abs(mark_price - liq_price) / mark_price * 100.0

            if distance_pct <= self.settings.LIQUIDATION_ALERT_DISTANCE_PCT:
                last_alert_time = self._alerted_liq_symbols.get(symbol, 0.0)
                if now - last_alert_time > 300:
                    self._alerted_liq_symbols[symbol] = now
                    msg = (
                        f"🚨 *LIQUIDATION RISK WARNING* 🚨\n\n"
                        f"• *Symbol:* `{symbol}`\n"
                        f"• *Mark Price:* `${mark_price:.4f}`\n"
                        f"• *Liquidation Price:* `${liq_price:.4f}`\n"
                        f"• *Distance to Liquidation:* `⚠️ {distance_pct:.2f}%`\n\n"
                        f"Consider adding margin or reducing position size with `/close {symbol}`."
                    )
                    await self.broadcast_alert(msg, is_critical=True)

    async def _check_extreme_funding_rates(self) -> None:
        """Warn user if funding rate on open position is unusually high."""
        open_symbols = list(self.ws.positions.keys())
        now = time.time()

        for symbol in open_symbols:
            try:
                last_alert_time = self._alerted_funding_symbols.get(symbol, 0.0)
                if now - last_alert_time < 3600:
                    continue

                funding_data = await self.client.get_funding_rate(symbol)
                rate = float(funding_data.get("fundingRate", 0.0))

                if abs(rate) >= self.settings.FUNDING_RATE_ALERT_THRESHOLD:
                    self._alerted_funding_symbols[symbol] = now
                    rate_pct = rate * 100.0
                    msg = (
                        f"⚠️ *HIGH FUNDING RATE ALERT*\n\n"
                        f"• *Symbol:* `{symbol}`\n"
                        f"• *Current Funding Rate:* `{rate_pct:+.4f}%`\n"
                        f"• You have an active open position on this symbol. High funding fees will be charged/credited at the next funding interval."
                    )
                    await self.broadcast_alert(msg)
            except Exception as e:
                logger.debug(f"Could not check funding rate for {symbol}: {e}")
