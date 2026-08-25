"""Tests for MEXC Futures REST API Client."""
import hashlib
import hmac
import pytest
from exchange.mexc_client import MexcClient, MexcAPIError


def test_normalize_symbol():
    assert MexcClient.normalize_symbol("btc") == "BTC_USDT"
    assert MexcClient.normalize_symbol("BTCUSDT") == "BTC_USDT"
    assert MexcClient.normalize_symbol("ETH_USDT") == "ETH_USDT"
    assert MexcClient.normalize_symbol("SOL-USDT") == "SOL_USDT"
    assert MexcClient.normalize_symbol("DOGE/USDT") == "DOGE_USDT"


def test_signature_generation():
    client = MexcClient(
        api_key="my_test_api_key",
        secret_key="my_test_secret_key",
        dry_run=True,
    )
    req_time = "1672531199000"
    param_str = "symbol=BTC_USDT"

    sig = client._generate_signature(req_time, param_str)

    expected = hmac.new(
        b"my_test_secret_key",
        b"my_test_api_key1672531199000symbol=BTC_USDT",
        hashlib.sha256
    ).hexdigest()

    assert sig == expected


@pytest.mark.asyncio
async def test_dry_run_account_assets(mexc_client: MexcClient):
    assets = await mexc_client.get_account_assets()
    assert isinstance(assets, list)
    assert len(assets) > 0
    usdt = assets[0]
    assert usdt["currency"] == "USDT"
    assert usdt["equity"] == 1000.0
    assert usdt["availableBalance"] == 850.0


@pytest.mark.asyncio
async def test_dry_run_order_lifecycle(mexc_client: MexcClient):
    # Submit Order
    order_res = await mexc_client.submit_order(
        symbol="BTC_USDT",
        side=1,  # Open Long
        vol=0.01,
        leverage=10,
        order_type=5,  # Market
        price=0.0,
    )
    assert order_res["is_dry_run"] is True
    assert "sim_ord_" in order_res["orderId"]
    assert order_res["symbol"] == "BTC_USDT"

    # Cancel Order
    cancel_res = await mexc_client.cancel_order(order_res["orderId"])
    assert cancel_res["status"] == "CANCELED"

    # Plan Order
    plan_res = await mexc_client.place_plan_order(
        symbol="BTC_USDT",
        side=4,  # Close Long
        trigger_price=64000.0,
        trend=2,
        vol=0.01,
    )
    assert plan_res["is_dry_run"] is True
    assert "sim_plan_" in plan_res["orderId"]
