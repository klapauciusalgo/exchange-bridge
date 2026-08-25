"""Live exchange and bot connection testing script."""
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_settings
from config.security import SecurityManager
from exchange.mexc_client import MexcClient, MexcAPIError
from exchange.mexc_ws import MexcWebSocketClient
import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("live_test")


async def test_telegram_token(bot_token: str) -> Dict[str, Any]:
    """Test Telegram Bot Token via getMe API."""
    if not bot_token:
        return {"status": "SKIPPED", "message": "No TELEGRAM_BOT_TOKEN set."}

    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                data = await resp.json()
                if data.get("ok"):
                    result = data.get("result", {})
                    bot_username = result.get("username", "Unknown")
                    bot_name = result.get("first_name", "Unknown")
                    return {
                        "status": "SUCCESS",
                        "username": f"@{bot_username}",
                        "name": bot_name,
                        "bot_id": result.get("id"),
                    }
                else:
                    return {
                        "status": "FAILED",
                        "error_code": data.get("error_code"),
                        "description": data.get("description"),
                    }
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}


async def test_mexc_public_endpoints(client: MexcClient) -> Dict[str, Any]:
    """Test MEXC Futures REST public endpoints."""
    results = {}
    try:
        # Ticker
        ticker = await client.get_ticker("BTC_USDT")
        if isinstance(ticker, list) and ticker:
            ticker = ticker[0]
        results["ticker"] = {
            "status": "SUCCESS",
            "last_price": ticker.get("lastPrice"),
            "rise_fall_rate": ticker.get("riseFallRate"),
        }
    except Exception as e:
        results["ticker"] = {"status": "FAILED", "error": str(e)}

    try:
        # Funding rate
        funding = await client.get_funding_rate("BTC_USDT")
        results["funding_rate"] = {
            "status": "SUCCESS",
            "rate": funding.get("fundingRate"),
        }
    except Exception as e:
        results["funding_rate"] = {"status": "FAILED", "error": str(e)}

    try:
        # Orderbook
        depth = await client.get_depth("BTC_USDT", limit=5)
        results["orderbook"] = {
            "status": "SUCCESS",
            "bids_count": len(depth.get("bids", [])),
            "asks_count": len(depth.get("asks", [])),
        }
    except Exception as e:
        results["orderbook"] = {"status": "FAILED", "error": str(e)}

    try:
        # Contract detail
        details = await client.get_contract_details()
        btc_detail = details.get("BTC_USDT", {})
        results["contract_details"] = {
            "status": "SUCCESS",
            "total_contracts": len(details),
            "btc_max_leverage": btc_detail.get("maxLeverage"),
        }
    except Exception as e:
        results["contract_details"] = {"status": "FAILED", "error": str(e)}

    return results


async def test_mexc_private_endpoints(client: MexcClient) -> Dict[str, Any]:
    """Test MEXC Futures REST private authenticated endpoints."""
    results = {}
    try:
        # Account assets
        assets = await client.get_account_assets()
        usdt_asset = next((a for a in assets if a.get("currency") == "USDT"), None)
        results["account_assets"] = {
            "status": "SUCCESS",
            "total_assets_count": len(assets) if isinstance(assets, list) else 0,
            "usdt_equity": usdt_asset.get("equity") if usdt_asset else "N/A",
            "usdt_available": usdt_asset.get("availableBalance") if usdt_asset else "N/A",
        }
    except MexcAPIError as me:
        results["account_assets"] = {
            "status": "FAILED",
            "code": me.code,
            "message": me.message,
            "raw": me.raw_response
        }
    except Exception as e:
        results["account_assets"] = {"status": "ERROR", "error": str(e)}

    try:
        # Open positions
        positions = await client.get_open_positions()
        results["open_positions"] = {
            "status": "SUCCESS",
            "count": len(positions) if isinstance(positions, list) else 0,
            "symbols": [p.get("symbol") for p in positions] if isinstance(positions, list) else [],
        }
    except MexcAPIError as me:
        results["open_positions"] = {
            "status": "FAILED",
            "code": me.code,
            "message": me.message,
        }
    except Exception as e:
        results["open_positions"] = {"status": "ERROR", "error": str(e)}

    try:
        # Open orders
        orders = await client.get_open_orders()
        results["open_orders"] = {
            "status": "SUCCESS",
            "count": len(orders) if isinstance(orders, list) else 0,
        }
    except MexcAPIError as me:
        results["open_orders"] = {
            "status": "FAILED",
            "code": me.code,
            "message": me.message,
        }
    except Exception as e:
        results["open_orders"] = {"status": "ERROR", "error": str(e)}

    return results


async def test_mexc_websocket(ws_client: MexcWebSocketClient) -> Dict[str, Any]:
    """Test MEXC WebSocket connection and subscriptions."""
    results = {}
    try:
        await ws_client.start()
        # Wait up to 5 seconds for connection
        start_wait = time.time()
        while not ws_client.is_connected and (time.time() - start_wait < 5):
            await asyncio.sleep(0.2)

        results["connected"] = ws_client.is_connected

        # Subscribe to BTC_USDT ticker
        await ws_client.subscribe_ticker("BTC_USDT")

        # Wait up to 5 seconds to receive a ticker or auth response
        start_wait = time.time()
        while "BTC_USDT" not in ws_client.tickers and (time.time() - start_wait < 5):
            await asyncio.sleep(0.2)

        results["ticker_received"] = "BTC_USDT" in ws_client.tickers
        if "BTC_USDT" in ws_client.tickers:
            results["sample_ws_ticker"] = ws_client.tickers["BTC_USDT"].get("lastPrice")

        results["authenticated"] = ws_client.is_authenticated
    except Exception as e:
        results["error"] = str(e)
    finally:
        await ws_client.stop()

    return results


async def main():
    settings = get_settings()
    security = SecurityManager(master_key=settings.MASTER_ENCRYPTION_KEY)

    api_key = security.decrypt(settings.MEXC_API_KEY)
    secret_key = security.decrypt(settings.MEXC_SECRET_KEY)

    print("\n" + "=" * 60)
    print("🔍 MEXC ↔ TELEGRAM TRADING BRIDGE: LIVE CONNECTIVITY TEST")
    print("=" * 60)

    # 1. Telegram Bot Token Test
    print("\n[1/4] Testing Telegram Bot Connection...")
    tg_res = await test_telegram_token(settings.TELEGRAM_BOT_TOKEN)
    if tg_res.get("status") == "SUCCESS":
        print(f"  ✅ Telegram Bot Valid: {tg_res.get('name')} ({tg_res.get('username')}) [ID: {tg_res.get('bot_id')}]")
    else:
        print(f"  ❌ Telegram Bot Test Failed: {tg_res}")

    print(f"  ℹ️  Configured Whitelist IDs: {settings.TELEGRAM_WHITELISTED_USERS}")

    # 2. MEXC Public REST API Test
    print("\n[2/4] Testing MEXC Futures Public REST API...")
    real_client = MexcClient(
        api_key=api_key,
        secret_key=secret_key,
        base_url=settings.MEXC_REST_URL,
        dry_run=False,
    )
    async with real_client:
        pub_res = await test_mexc_public_endpoints(real_client)
        for name, data in pub_res.items():
            if data.get("status") == "SUCCESS":
                print(f"  ✅ Public endpoint '{name}': SUCCESS {data}")
            else:
                print(f"  ❌ Public endpoint '{name}': FAILED {data}")

        # 3. MEXC Private REST API Test
        print("\n[3/4] Testing MEXC Futures Private (Authenticated) REST API...")
        priv_res = await test_mexc_private_endpoints(real_client)
        for name, data in priv_res.items():
            if data.get("status") == "SUCCESS":
                print(f"  ✅ Private endpoint '{name}': SUCCESS {data}")
            else:
                print(f"  ❌ Private endpoint '{name}': FAILED {data}")

    # 4. MEXC WebSocket Test
    print("\n[4/4] Testing MEXC Futures WebSocket Connection & Channels...")
    ws_client = MexcWebSocketClient(
        ws_url=settings.MEXC_WS_URL,
        api_key=api_key,
        secret_key=secret_key,
    )
    ws_res = await test_mexc_websocket(ws_client)
    print(f"  {'✅' if ws_res.get('connected') else '❌'} WS Connected: {ws_res.get('connected')}")
    print(f"  {'✅' if ws_res.get('ticker_received') else '❌'} WS Public Ticker Received: {ws_res.get('ticker_received')} (BTC: {ws_res.get('sample_ws_ticker')})")
    print(f"  {'✅' if ws_res.get('authenticated') else 'ℹ️'} WS Authenticated: {ws_res.get('authenticated')}")

    print("\n" + "=" * 60)
    print("🏁 LIVE TEST COMPLETED")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
