"""Tests for MEXC Futures WebSocket Client."""
import asyncio
import pytest
from exchange.mexc_ws import MexcWebSocketClient


@pytest.mark.asyncio
async def test_ws_event_dispatching(ws_client: MexcWebSocketClient):
    received_tickers = []
    received_orders = []
    received_positions = []
    received_assets = []

    ws_client.add_ticker_listener(lambda sym, data: received_tickers.append((sym, data)))
    ws_client.add_order_listener(lambda data: received_orders.append(data))
    ws_client.add_position_listener(lambda data: received_positions.append(data))
    ws_client.add_asset_listener(lambda data: received_assets.append(data))

    # 1. Simulate Ticker Event
    ticker_msg = {
        "channel": "push.ticker",
        "symbol": "BTC_USDT",
        "data": {
            "symbol": "BTC_USDT",
            "lastPrice": 65400.0,
            "fairPrice": 65390.0,
            "riseFallRate": 0.025,
        }
    }
    await ws_client._handle_message(ticker_msg)
    assert "BTC_USDT" in ws_client.tickers
    assert ws_client.tickers["BTC_USDT"]["lastPrice"] == 65400.0
    assert len(received_tickers) == 1
    assert received_tickers[0][0] == "BTC_USDT"

    # 2. Simulate Order Event
    order_msg = {
        "channel": "push.personal.order",
        "data": {
            "id": "ord_9988",
            "symbol": "ETH_USDT",
            "status": 3,  # Filled
            "side": 1,
            "price": 3500.0,
            "vol": 1.5,
        }
    }
    await ws_client._handle_message(order_msg)
    assert len(received_orders) == 1
    assert received_orders[0]["id"] == "ord_9988"

    # 3. Simulate Position Event
    pos_msg = {
        "channel": "push.personal.position",
        "data": {
            "symbol": "SOL_USDT",
            "positionType": 1,
            "holdVol": 10.0,
            "openPrice": 150.0,
            "markPrice": 155.0,
        }
    }
    await ws_client._handle_message(pos_msg)
    assert "SOL_USDT" in ws_client.positions
    assert ws_client.positions["SOL_USDT"]["holdVol"] == 10.0
    assert len(received_positions) == 1

    # 4. Simulate Asset Event
    asset_msg = {
        "channel": "push.personal.asset",
        "data": {
            "currency": "USDT",
            "equity": 1250.0,
            "availableBalance": 1050.0,
        }
    }
    await ws_client._handle_message(asset_msg)
    assert "USDT" in ws_client.assets
    assert ws_client.assets["USDT"]["equity"] == 1250.0
    assert len(received_assets) == 1
