"""MEXC Futures WebSocket Client with private/public channels, auto-reconnect, and caching."""
import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set
import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


class MexcWebSocketClient:
    """Async WebSocket client for MEXC Futures."""

    def __init__(
        self,
        ws_url: str = "wss://contract.mexc.com/edge",
        api_key: str = "",
        secret_key: str = "",
        heartbeat_interval: int = 15,
    ):
        self.ws_url = ws_url
        self.api_key = api_key
        self.secret_key = secret_key
        self.heartbeat_interval = heartbeat_interval

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running: bool = False
        self._loop_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

        # Cache of real-time market and account data
        self.tickers: Dict[str, dict] = {}
        self.positions: Dict[str, dict] = {}
        self.assets: Dict[str, dict] = {}
        self.depths: Dict[str, dict] = {}

        # Subscribed symbols
        self._subscribed_ticker_symbols: Set[str] = set()
        self._subscribed_depth_symbols: Set[str] = set()

        # Callbacks
        self._order_listeners: List[Callable[[dict], Any]] = []
        self._position_listeners: List[Callable[[dict], Any]] = []
        self._asset_listeners: List[Callable[[dict], Any]] = []
        self._ticker_listeners: List[Callable[[str, dict], Any]] = []
        self._reconnect_listeners: List[Callable[[], Any]] = []

        self.is_connected: bool = False
        self.is_authenticated: bool = False
        self.reconnect_count: int = 0

    def add_order_listener(self, cb: Callable[[dict], Any]) -> None:
        self._order_listeners.append(cb)

    def add_position_listener(self, cb: Callable[[dict], Any]) -> None:
        self._position_listeners.append(cb)

    def add_asset_listener(self, cb: Callable[[dict], Any]) -> None:
        self._asset_listeners.append(cb)

    def add_ticker_listener(self, cb: Callable[[str, dict], Any]) -> None:
        self._ticker_listeners.append(cb)

    def add_reconnect_listener(self, cb: Callable[[], Any]) -> None:
        self._reconnect_listeners.append(cb)

    async def start(self) -> None:
        """Start the WebSocket listener loop in background."""
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._connection_loop())
        logger.info("MEXC WebSocket client background loop started")

    async def stop(self) -> None:
        """Stop WebSocket connection and background tasks."""
        self._running = False
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        self.is_connected = False
        logger.info("MEXC WebSocket client stopped")

    async def _connection_loop(self) -> None:
        backoff = 1.0
        while self._running:
            try:
                logger.info(f"Connecting to MEXC WebSocket at {self.ws_url}...")
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=None,  # We manage custom MEXC heartbeats
                    ping_timeout=None,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self.is_connected = True
                    backoff = 1.0  # Reset backoff on success
                    logger.info("MEXC WebSocket connected successfully")

                    # Login if API credentials are present
                    if self.api_key and self.secret_key:
                        await self._authenticate()

                    # Re-subscribe to all active subscriptions
                    await self._resubscribe()

                    # Start custom ping heartbeat
                    if self._heartbeat_task and not self._heartbeat_task.done():
                        self._heartbeat_task.cancel()
                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                    # Trigger reconnect handlers (for state reconciliation)
                    if self.reconnect_count > 0:
                        for cb in self._reconnect_listeners:
                            try:
                                if asyncio.iscoroutinefunction(cb):
                                    asyncio.create_task(cb())
                                else:
                                    cb()
                            except Exception as e:
                                logger.error(f"Error in reconnect listener: {e}")

                    # Listen for incoming frames
                    await self._message_reader()

            except (ConnectionClosed, asyncio.CancelledError, Exception) as e:
                self.is_connected = False
                self.is_authenticated = False
                self._ws = None
                if not self._running:
                    break
                self.reconnect_count += 1
                logger.warning(f"MEXC WebSocket disconnected ({e}). Reconnecting in {backoff:.1f}s (attempt #{self.reconnect_count})...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _authenticate(self) -> None:
        """Authenticate WebSocket session with MEXC credentials."""
        req_time = str(int(time.time() * 1000))
        sign_str = f"{self.api_key}{req_time}"
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        login_msg = {
            "method": "login",
            "param": {
                "apiKey": self.api_key,
                "reqTime": req_time,
                "signature": signature
            }
        }
        await self._send(login_msg)
        logger.info("Sent WebSocket login message")

    async def _resubscribe(self) -> None:
        """Resubscribe to public and private channels."""
        # Subscribe to private account channels if credentials exist
        if self.api_key and self.secret_key:
            await self._send({"method": "sub.personal.order", "param": {}})
            await self._send({"method": "sub.personal.position", "param": {}})
            await self._send({"method": "sub.personal.asset", "param": {}})

        # Resubscribe public tickers
        for sym in list(self._subscribed_ticker_symbols):
            await self._send({"method": "sub.ticker", "param": {"symbol": sym}})

        for sym in list(self._subscribed_depth_symbols):
            await self._send({"method": "sub.depth", "param": {"symbol": sym}})

    async def subscribe_ticker(self, symbol: str) -> None:
        """Subscribe to ticker updates for symbol."""
        sym = symbol.upper()
        self._subscribed_ticker_symbols.add(sym)
        if self.is_connected:
            await self._send({"method": "sub.ticker", "param": {"symbol": sym}})

    async def subscribe_depth(self, symbol: str) -> None:
        """Subscribe to depth orderbook updates for symbol."""
        sym = symbol.upper()
        self._subscribed_depth_symbols.add(sym)
        if self.is_connected:
            await self._send({"method": "sub.depth", "param": {"symbol": sym}})

    async def _send(self, payload: dict) -> None:
        """Send JSON payload over WebSocket."""
        if self._ws and self.is_connected:
            try:
                await self._ws.send(json.dumps(payload))
            except Exception as e:
                logger.error(f"Failed to send WS message: {e}")

    async def _heartbeat_loop(self) -> None:
        """Periodic ping message to keep WS connection alive."""
        try:
            while self._running and self.is_connected:
                await asyncio.sleep(self.heartbeat_interval)
                await self._send({"method": "ping"})
        except asyncio.CancelledError:
            pass

    async def _message_reader(self) -> None:
        """Read and route incoming WebSocket messages."""
        async for msg_str in self._ws:
            try:
                if msg_str == "pong" or msg_str == '{"data":"pong"}':
                    continue
                data = json.loads(msg_str)
                await self._handle_message(data)
            except Exception as e:
                logger.error(f"Error parsing WS message: {e} | Raw: {msg_str[:200]}")

    async def _handle_message(self, msg: dict) -> None:
        """Dispatch incoming WebSocket events."""
        channel = msg.get("channel", "")
        method = msg.get("method", "")
        data = msg.get("data")

        # Login Response
        if channel == "rs.login" or method == "login":
            if msg.get("data") == "success" or msg.get("code") == 0:
                self.is_authenticated = True
                logger.info("MEXC WebSocket successfully authenticated")
            else:
                logger.warning(f"MEXC WebSocket authentication failed: {msg}")

        # Ticker Update
        elif channel.startswith("push.ticker") or method == "push.ticker":
            symbol = msg.get("symbol")
            if not symbol and isinstance(data, dict):
                symbol = data.get("symbol")
            if symbol and data:
                self.tickers[symbol] = data
                for cb in self._ticker_listeners:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            asyncio.create_task(cb(symbol, data))
                        else:
                            cb(symbol, data)
                    except Exception as e:
                        logger.error(f"Error in ticker listener: {e}")

        # Orderbook Depth Update
        elif channel.startswith("push.depth") or method == "push.depth":
            symbol = msg.get("symbol")
            if not symbol and isinstance(data, dict):
                symbol = data.get("symbol")
            if symbol and data:
                self.depths[symbol] = data

        # Private Order Update
        elif channel == "push.personal.order" or method == "push.personal.order":
            logger.info(f"Received order update: {data}")
            for cb in self._order_listeners:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        asyncio.create_task(cb(data))
                    else:
                        cb(data)
                except Exception as e:
                    logger.error(f"Error in order listener: {e}")

        # Private Position Update
        elif channel == "push.personal.position" or method == "push.personal.position":
            if isinstance(data, dict):
                symbol = data.get("symbol")
                if symbol:
                    self.positions[symbol] = data
            elif isinstance(data, list):
                for p in data:
                    sym = p.get("symbol")
                    if sym:
                        self.positions[sym] = p

            for cb in self._position_listeners:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        asyncio.create_task(cb(data))
                    else:
                        cb(data)
                except Exception as e:
                    logger.error(f"Error in position listener: {e}")

        # Private Asset / Balance Update
        elif channel == "push.personal.asset" or method == "push.personal.asset":
            if isinstance(data, dict):
                curr = data.get("currency", "USDT")
                self.assets[curr] = data
            for cb in self._asset_listeners:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        asyncio.create_task(cb(data))
                    else:
                        cb(data)
                except Exception as e:
                    logger.error(f"Error in asset listener: {e}")
