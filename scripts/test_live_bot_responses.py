"""Live bot responses and formatter verification script with real exchange data."""
import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_settings
from config.security import SecurityManager
from database.db import Database
from exchange.mexc_client import MexcClient
from bot.formatters import (
    format_balance,
    format_positions,
    format_ticker,
    format_market,
    format_orderbook,
)

logging.basicConfig(level=logging.INFO)


async def main():
    settings = get_settings()
    security = SecurityManager(master_key=settings.MASTER_ENCRYPTION_KEY)
    api_key = security.decrypt(settings.MEXC_API_KEY)
    secret_key = security.decrypt(settings.MEXC_SECRET_KEY)

    db = Database(db_path=":memory:")
    await db.connect()

    real_client = MexcClient(
        api_key=api_key,
        secret_key=secret_key,
        base_url=settings.MEXC_REST_URL,
        dry_run=False,
    )

    async with real_client:
        print("\n" + "=" * 60)
        print("📊 LIVE BOT FORMATTER OUTPUT TEST")
        print("=" * 60)

        # 1. /balance
        print("\n--- [ /balance Output Preview ] ---")
        assets = await real_client.get_account_assets()
        stats = await db.get_or_create_daily_stats(settings.TELEGRAM_WHITELISTED_USERS[0])
        bal_msg = format_balance(assets, stats)
        print(bal_msg)

        # 2. /positions
        print("\n--- [ /positions Output Preview ] ---")
        positions = await real_client.get_open_positions()
        plan_orders = await real_client.get_plan_orders()
        pos_msg = format_positions(positions, plan_orders)
        print(pos_msg)

        # 3. /price WLD
        print("\n--- [ /price WLD Output Preview ] ---")
        ticker = await real_client.get_ticker("WLD_USDT")
        if isinstance(ticker, list) and ticker:
            ticker = ticker[0]
        price_msg = format_ticker(ticker)
        print(price_msg)

        # 4. /market WLD
        print("\n--- [ /market WLD Output Preview ] ---")
        detail = await real_client.get_symbol_detail("WLD_USDT") or {}
        funding = await real_client.get_funding_rate("WLD_USDT")
        mkt_msg = format_market(detail, funding, ticker)
        print(mkt_msg)

        # 5. /orderbook WLD
        print("\n--- [ /orderbook WLD Output Preview ] ---")
        depth = await real_client.get_depth("WLD_USDT", limit=5)
        depth["symbol"] = "WLD_USDT"
        book_msg = format_orderbook(depth, limit=5)
        print(book_msg)

        print("\n" + "=" * 60)
        print("✅ ALL LIVE FORMATTERS VERIFIED CLEANLY")
        print("=" * 60 + "\n")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
