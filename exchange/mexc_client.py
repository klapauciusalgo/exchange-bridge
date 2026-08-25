"""MEXC Futures REST API Client with HMAC-SHA256 authentication and rate limiting."""
import aiohttp
import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlencode

from exchange.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class MexcAPIError(Exception):
    """Exception raised when MEXC API returns an error."""
    def __init__(self, code: int, message: str, raw_response: Optional[dict] = None):
        self.code = code
        self.message = message
        self.raw_response = raw_response or {}
        super().__init__(f"MEXC API Error [Code {code}]: {message}")


class MexcClient:
    """Async Client for MEXC Futures REST API."""

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        base_url: str = "https://contract.mexc.com",
        rate_limiter: Optional[RateLimiter] = None,
        dry_run: bool = False,
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")
        self.rate_limiter = rate_limiter or RateLimiter(max_requests=10, window_seconds=2.0)
        self.dry_run = dry_run
        self._session: Optional[aiohttp.ClientSession] = None
        self._contract_details_cache: Dict[str, dict] = {}
        self._contract_cache_time: float = 0.0

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        """Convert BTC, BTCUSDT, btc_usdt to MEXC contract format 'BTC_USDT'."""
        s = symbol.strip().upper().replace("/", "_").replace("-", "_")
        if "_" not in s:
            if s.endswith("USDT"):
                base = s[:-4]
                s = f"{base}_USDT"
            else:
                s = f"{s}_USDT"
        return s

    def _generate_signature(self, req_time: str, param_str: str) -> str:
        """Generate HMAC-SHA256 signature for MEXC Futures API."""
        sign_str = f"{self.api_key}{req_time}{param_str}"
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return signature

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
        is_private: bool = False,
    ) -> Dict[str, Any]:
        """Execute HTTP request with rate limiting and signing."""
        await self.rate_limiter.acquire()
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "MEXC-Telegram-Trading-Bridge/1.0",
        }

        param_str = ""
        if method.upper() in ["GET", "DELETE"]:
            if params:
                # Alphabetically sorted query string
                sorted_params = sorted(params.items(), key=lambda x: x[0])
                param_str = urlencode(sorted_params)
        elif method.upper() in ["POST", "PUT"]:
            if data is not None:
                if isinstance(data, (dict, list)):
                    param_str = json.dumps(data, separators=(",", ":"))
                else:
                    param_str = str(data)

        if is_private:
            if not self.api_key or not self.secret_key:
                raise ValueError("API Key and Secret Key are required for private endpoints.")
            req_time = str(int(time.time() * 1000))
            signature = self._generate_signature(req_time, param_str)
            headers["ApiKey"] = self.api_key
            headers["Request-Time"] = req_time
            headers["Signature"] = signature

        try:
            req_kwargs = {"headers": headers}
            if params and method.upper() in ["GET", "DELETE"]:
                req_kwargs["params"] = params
            if data is not None and method.upper() in ["POST", "PUT"]:
                req_kwargs["data"] = param_str

            async with session.request(method, url, **req_kwargs) as response:
                status_code = response.status
                try:
                    resp_json = await response.json()
                except Exception:
                    resp_text = await response.text()
                    raise MexcAPIError(status_code, f"Non-JSON response: {resp_text}")

                # MEXC futures API standard response: {"success": true, "code": 0, "data": ...}
                success = resp_json.get("success", False)
                code = resp_json.get("code", 0)
                if not success or (code != 0 and code != 200):
                    message = resp_json.get("message", resp_json.get("msg", "Unknown MEXC API error"))
                    raise MexcAPIError(code, message, resp_json)

                return resp_json.get("data", resp_json)
        except aiohttp.ClientError as e:
            logger.error(f"Network error calling {url}: {e}")
            raise MexcAPIError(-1, f"Network communication error: {str(e)}")

    # ==========================================
    # Public Market Endpoints
    # ==========================================

    async def get_contract_details(self, force_refresh: bool = False) -> Dict[str, dict]:
        """Fetch all contract specifications and cache them."""
        now = time.time()
        if not force_refresh and self._contract_details_cache and (now - self._contract_cache_time < 300):
            return self._contract_details_cache

        data = await self._request("GET", "/api/v1/contract/detail")
        cache = {}
        if isinstance(data, list):
            for item in data:
                symbol = item.get("symbol")
                if symbol:
                    cache[symbol] = item
        self._contract_details_cache = cache
        self._contract_cache_time = now
        return cache

    async def get_symbol_detail(self, symbol: str) -> Optional[dict]:
        sym = self.normalize_symbol(symbol)
        details = await self.get_contract_details()
        return details.get(sym)

    async def is_assessment_zone(self, symbol: str) -> bool:
        """Check if symbol is currently in Assessment Zone (restricted API trading)."""
        detail = await self.get_symbol_detail(symbol)
        if not detail:
            return False
        # In MEXC contract details, state or zone flags indicate restricted trading
        is_restricted = detail.get("state") in [1, 2] or detail.get("isAssessment", False)
        return is_restricted

    async def get_ticker(self, symbol: Optional[str] = None) -> Union[dict, List[dict]]:
        """Get latest ticker for a symbol or all tickers."""
        params = {}
        if symbol:
            params["symbol"] = self.normalize_symbol(symbol)
        return await self._request("GET", "/api/v1/contract/ticker", params=params)

    async def get_depth(self, symbol: str, limit: int = 10) -> dict:
        """Get orderbook bids & asks depth."""
        sym = self.normalize_symbol(symbol)
        return await self._request("GET", f"/api/v1/contract/depth/{sym}", params={"limit": limit})

    async def get_funding_rate(self, symbol: str) -> dict:
        """Get funding rate information for symbol."""
        sym = self.normalize_symbol(symbol)
        return await self._request("GET", f"/api/v1/contract/funding_rate/{sym}")

    async def get_index_price(self, symbol: str) -> dict:
        """Get index and mark price."""
        sym = self.normalize_symbol(symbol)
        return await self._request("GET", f"/api/v1/contract/index_price/{sym}")

    @staticmethod
    def normalize_interval(interval: str) -> str:
        """Map user timeframe string (e.g. 1m, 5m, 15m, 1h, 4h, 1d) to MEXC format."""
        inv = interval.strip().lower()
        mapping = {
            "1m": "Min1",
            "5m": "Min5",
            "15m": "Min15",
            "30m": "Min30",
            "1h": "Min60",
            "60m": "Min60",
            "4h": "Hour4",
            "8h": "Hour8",
            "1d": "Day1",
            "d": "Day1",
            "1w": "Week1",
            "w": "Week1",
            "1mth": "Month1",
            "1mo": "Month1",
        }
        return mapping.get(inv, "Min15")

    async def get_kline(
        self,
        symbol: str,
        interval: str = "Min15",
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> dict:
        """Get K-line / Candlestick data for a symbol."""
        sym = self.normalize_symbol(symbol)
        mexc_interval = self.normalize_interval(interval) if not interval.startswith(("Min", "Hour", "Day", "Week", "Month")) else interval
        params = {"interval": mexc_interval}
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end

        return await self._request("GET", f"/api/v1/contract/kline/{sym}", params=params)

    # ==========================================
    # Private Account & Trading Endpoints
    # ==========================================

    async def get_account_assets(self) -> List[dict]:
        """Fetch user futures account assets / margin balances."""
        if self.dry_run:
            return [{
                "currency": "USDT",
                "equity": 1000.0,
                "availableBalance": 850.0,
                "frozenBalance": 150.0,
                "positionMargin": 150.0,
                "unrealized(positionPnl)": 25.5,
                "bonus": 0.0
            }]
        return await self._request("GET", "/api/v1/private/account/assets", is_private=True)

    async def get_open_positions(self, symbol: Optional[str] = None) -> List[dict]:
        """Fetch active open futures positions."""
        if self.dry_run:
            # Return simulated position if in dry run
            return []
        params = {}
        if symbol:
            params["symbol"] = self.normalize_symbol(symbol)
        data = await self._request("GET", "/api/v1/private/position/open_positions", params=params, is_private=True)
        return data if isinstance(data, list) else []

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[dict]:
        """Fetch open unfilled limit orders."""
        if self.dry_run:
            return []
        params = {}
        if symbol:
            params["symbol"] = self.normalize_symbol(symbol)
        data = await self._request("GET", "/api/v1/private/order/list/open_orders", params=params, is_private=True)
        return data if isinstance(data, list) else []

    async def get_order_history(self, symbol: Optional[str] = None, page_num: int = 1, page_size: int = 20) -> List[dict]:
        """Fetch historical orders."""
        if self.dry_run:
            return []
        params = {"page_num": page_num, "page_size": page_size}
        if symbol:
            params["symbol"] = self.normalize_symbol(symbol)
        data = await self._request("GET", "/api/v1/private/order/list/history_orders", params=params, is_private=True)
        return data if isinstance(data, list) else []

    async def set_leverage(self, symbol: str, leverage: int, position_type: int = 1) -> dict:
        """Change leverage for a symbol (position_type: 1=Long, 2=Short)."""
        sym = self.normalize_symbol(symbol)
        if self.dry_run:
            return {"symbol": sym, "leverage": leverage, "positionType": position_type}
        payload = {
            "symbol": sym,
            "leverage": leverage,
            "positionType": position_type
        }
        return await self._request("POST", "/api/v1/private/position/change_leverage", data=payload, is_private=True)

    async def submit_order(
        self,
        symbol: str,
        side: int,  # 1: Open Long, 2: Close Short, 3: Open Short, 4: Close Long
        vol: float,
        leverage: int,
        order_type: int = 5,  # 1: Limit, 5: Market
        price: float = 0.0,
        open_type: int = 1,  # 1: Isolated, 2: Cross
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
    ) -> dict:
        """Submit a new futures order with exact exchange precision formatting."""
        sym = self.normalize_symbol(symbol)

        # Get contract precision details
        try:
            details = await self.get_contract_details()
            detail = details.get(sym, {})
            vol_scale = int(detail.get("volScale", 0))
            price_scale = int(detail.get("priceScale", 4))
            min_vol = float(detail.get("minVol", 1.0))
        except Exception:
            vol_scale = 0
            price_scale = 4
            min_vol = 1.0

        if vol_scale == 0:
            formatted_vol = max(int(min_vol), int(round(vol)))
        else:
            formatted_vol = max(min_vol, round(vol, vol_scale))

        payload = {
            "symbol": sym,
            "side": side,
            "vol": formatted_vol,
            "leverage": leverage,
            "type": order_type,
            "openType": open_type,
        }
        if order_type == 1 and price > 0:
            payload["price"] = round(price, price_scale)
        if stop_loss_price is not None and stop_loss_price > 0:
            payload["stopLossPrice"] = round(stop_loss_price, price_scale)
        if take_profit_price is not None and take_profit_price > 0:
            payload["takeProfitPrice"] = round(take_profit_price, price_scale)

        if self.dry_run:
            simulated_order_id = f"sim_ord_{int(time.time() * 1000)}"
            logger.info(f"[DRY_RUN] Simulated order submitted: {payload}")
            return {
                "orderId": simulated_order_id,
                "symbol": sym,
                "side": side,
                "vol": formatted_vol,
                "price": price,
                "is_dry_run": True,
            }

        res = await self._request("POST", "/api/v1/private/order/submit", data=payload, is_private=True)
        if isinstance(res, dict):
            return res
        return {"orderId": str(res), "raw": res}

    async def cancel_order(self, order_id: str) -> dict:
        """Cancel a specific open order."""
        if self.dry_run:
            logger.info(f"[DRY_RUN] Cancel order {order_id}")
            return {"orderId": order_id, "status": "CANCELED"}
        # MEXC accepts array of orderIds: [orderId]
        payload = [order_id]
        return await self._request("POST", "/api/v1/private/order/cancel", data=payload, is_private=True)

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> dict:
        """Cancel all open limit orders, optionally filtered by symbol."""
        if self.dry_run:
            logger.info(f"[DRY_RUN] Cancel all orders for {symbol}")
            return {"status": "SUCCESS"}
        payload = {}
        if symbol:
            payload["symbol"] = self.normalize_symbol(symbol)
        return await self._request("POST", "/api/v1/private/order/cancel_all", data=payload, is_private=True)

    # ==========================================
    # Plan Orders (Stop Loss & Take Profit Triggers)
    # ==========================================

    async def place_plan_order(
        self,
        symbol: str,
        side: int,  # 2: Close Short, 4: Close Long
        trigger_price: float,
        trend: int,  # 1: Latest >= triggerPrice (for TP long / SL short), 2: Latest <= triggerPrice (for SL long / TP short)
        vol: float,
        leverage: int = 1,
        trigger_type: int = 1,  # 1: Mark Price, 3: Latest Price
        order_type: int = 2,  # 2: Market Plan Order
        open_type: int = 1,
    ) -> dict:
        """Place a Stop Loss or Take Profit trigger plan order."""
        sym = self.normalize_symbol(symbol)
        payload = {
            "symbol": sym,
            "side": side,
            "triggerPrice": trigger_price,
            "trend": trend,
            "vol": vol,
            "triggerType": trigger_type,
            "orderType": order_type,
            "openType": open_type,
            "executeCycle": 1,  # 24 Hours
        }
        if leverage > 1:
            payload["leverage"] = leverage

        if self.dry_run:
            sim_id = f"sim_plan_{int(time.time() * 1000)}"
            logger.info(f"[DRY_RUN] Simulated plan order placed: {payload}")
            return {"orderId": sim_id, "symbol": sym, "triggerPrice": trigger_price, "is_dry_run": True}

        res = await self._request("POST", "/api/v1/private/planorder/place", data=payload, is_private=True)
        if isinstance(res, dict):
            return res
        return {"orderId": str(res), "raw": res}

    async def place_stop_order(
        self,
        symbol: str,
        position_id: Optional[Any] = None,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        loss_trend: int = 1,
        profit_trend: int = 1,
        auto_replace: bool = True,
    ) -> dict:
        """Set or update Stop Loss / Take Profit on an open position."""
        sym = self.normalize_symbol(symbol)
        try:
            details = await self.get_contract_details()
            detail = details.get(sym, {})
            price_scale = int(detail.get("priceScale", 4))
        except Exception:
            price_scale = 4

        payload = {
            "symbol": sym,
            "volType": 2,  # 2: Full Position
        }
        if position_id:
            try:
                payload["positionId"] = int(position_id)
            except Exception:
                payload["positionId"] = position_id
        if stop_loss_price is not None and stop_loss_price > 0:
            payload["stopLossPrice"] = round(stop_loss_price, price_scale)
            payload["lossTrend"] = loss_trend
        if take_profit_price is not None and take_profit_price > 0:
            payload["takeProfitPrice"] = round(take_profit_price, price_scale)
            payload["profitTrend"] = profit_trend

        if self.dry_run:
            sim_id = f"sim_stop_{int(time.time() * 1000)}"
            logger.info(f"[DRY_RUN] Simulated stop order placed: {payload}")
            return {
                "orderId": sim_id,
                "symbol": sym,
                "stopLossPrice": stop_loss_price,
                "takeProfitPrice": take_profit_price,
                "is_dry_run": True,
            }

        # Clear existing stop order for this symbol first to prevent Code 5005 duplicate error
        if auto_replace:
            try:
                await self._request("POST", "/api/v1/private/stoporder/cancel_all", data={"symbol": sym}, is_private=True)
            except Exception as e:
                logger.debug(f"Could not clear existing stop orders for {sym}: {e}")

        res = await self._request("POST", "/api/v1/private/stoporder/place", data=payload, is_private=True)
        if isinstance(res, dict):
            return res
        return {"orderId": str(res), "raw": res}

    async def get_plan_orders(self, symbol: Optional[str] = None) -> List[dict]:
        """Fetch active trigger plan orders and stop orders (SL/TP)."""
        if self.dry_run:
            return self._dry_run_plan_orders
        params = {}
        if symbol:
            params["symbol"] = self.normalize_symbol(symbol)

        all_orders = []
        # 1. Fetch active stop orders (SL / TP attached to positions)
        try:
            stop_orders = await self._request("GET", "/api/v1/private/stoporder/open_orders", params=params, is_private=True)
            if isinstance(stop_orders, list):
                all_orders.extend(stop_orders)
        except Exception as e:
            logger.debug(f"Could not fetch stop orders: {e}")

        # 2. Fetch standard trigger plan orders
        try:
            plan_orders = await self._request("GET", "/api/v1/private/planorder/list/orders", params=params, is_private=True)
            if isinstance(plan_orders, list):
                all_orders.extend(plan_orders)
        except Exception as e:
            logger.debug(f"Could not fetch plan orders: {e}")

        return all_orders

    async def cancel_plan_order(self, order_id: str) -> dict:
        """Cancel a trigger plan order or stop order."""
        if self.dry_run:
            logger.info(f"[DRY_RUN] Cancel plan order {order_id}")
            return {"orderId": order_id, "status": "CANCELED"}
        # Try canceling via stoporder endpoint first, then planorder
        try:
            return await self._request("POST", "/api/v1/private/stoporder/cancel", data=[order_id], is_private=True)
        except Exception:
            payload = [{"orderId": order_id}]
            return await self._request("POST", "/api/v1/private/planorder/cancel", data=payload, is_private=True)

    async def cancel_all_plan_orders(self, symbol: Optional[str] = None) -> dict:
        """Cancel all trigger plan orders and stop orders."""
        if self.dry_run:
            logger.info(f"[DRY_RUN] Cancel all plan orders for {symbol}")
            return {"status": "SUCCESS"}
        try:
            await self._request("POST", "/api/v1/private/stoporder/cancel_all", is_private=True)
        except Exception as e:
            logger.debug(f"Could not cancel all stop orders: {e}")

        payload = {}
        if symbol:
            payload["symbol"] = self.normalize_symbol(symbol)
        return await self._request("POST", "/api/v1/private/planorder/cancel_all", data=payload, is_private=True)
