"""Tests for proactive notification worker: fills, liquidation risk, and price alerts."""
from unittest.mock import AsyncMock, MagicMock
import pytest
from services.notification import NotificationService
from database.db import Database
from database.models import WatchlistAlert, OrderLogEntry
from exchange.mexc_client import MexcClient
from exchange.mexc_ws import MexcWebSocketClient


@pytest.mark.asyncio
async def test_order_fill_notification(notification_service: NotificationService, db: Database):
    # Insert order in DB
    order_id = "ord_fill_test_123"
    await db.record_order(OrderLogEntry(
        mexc_order_id=order_id,
        symbol="BTC_USDT",
        side="OPEN_LONG",
        order_type="MARKET",
        price=65000.0,
        volume=0.1,
        leverage=10,
        status="NEW",
    ))

    alerts_sent = []
    notification_service.broadcast_alert = AsyncMock(side_effect=lambda msg, is_critical=False: alerts_sent.append(msg))

    # Trigger WS order update event
    ws_event = {
        "id": order_id,
        "symbol": "BTC_USDT",
        "status": 3,  # FILLED
        "side": 1,  # Open Long
        "dealAvgPrice": 65120.5,
        "dealVol": 0.1,
    }
    await notification_service._handle_ws_order_update(ws_event)

    assert len(alerts_sent) == 1
    assert "ORDER FILLED" in alerts_sent[0]
    assert "BTC_USDT" in alerts_sent[0]
    assert "$65120.5000" in alerts_sent[0]


@pytest.mark.asyncio
async def test_watchlist_price_alert_trigger(notification_service: NotificationService, db: Database):
    user_id = 111222333
    alert = WatchlistAlert(
        user_id=user_id,
        symbol="ETH_USDT",
        condition="ABOVE",
        target_price=3500.0,
    )
    alert_id = await db.add_watchlist_alert(alert)

    alerts_sent = []
    notification_service.broadcast_alert = AsyncMock(side_effect=lambda msg, is_critical=False: alerts_sent.append(msg))

    # Trigger ticker price below threshold -> should not trigger
    await notification_service._handle_ws_ticker_update("ETH_USDT", {"lastPrice": 3480.0})
    assert len(alerts_sent) == 0

    # Trigger ticker price above threshold -> should trigger
    await notification_service._handle_ws_ticker_update("ETH_USDT", {"lastPrice": 3510.0})
    assert len(alerts_sent) == 1
    assert "PRICE ALERT TRIGGERED" in alerts_sent[0]
    assert "ETH_USDT" in alerts_sent[0]

    # Verify alert was deactivated in DB
    active_alerts = await db.get_active_watchlist("ETH_USDT")
    assert len(active_alerts) == 0


@pytest.mark.asyncio
async def test_liquidation_risk_warning(notification_service: NotificationService, ws_client: MexcWebSocketClient):
    alerts_sent = []
    notification_service.broadcast_alert = AsyncMock(side_effect=lambda msg, is_critical=False: alerts_sent.append((msg, is_critical)))

    # Set up dangerous position in WS cache (mark price 51000, liq price 50000 -> distance ~1.96% < 15%)
    ws_client.positions["BTC_USDT"] = {
        "symbol": "BTC_USDT",
        "positionType": 1,
        "holdVol": 1.0,
        "markPrice": 51000.0,
        "liquidPrice": 50000.0,
    }

    await notification_service._check_liquidation_risks()
    assert len(alerts_sent) == 1
    msg, is_critical = alerts_sent[0]
    assert is_critical is True
    assert "LIQUIDATION RISK WARNING" in msg
    assert "BTC_USDT" in msg
    assert "1.96%" in msg


@pytest.mark.asyncio
async def test_auto_positions_broadcast(notification_service: NotificationService, mexc_client: MexcClient):
    alerts_sent = []
    notification_service.broadcast_alert = AsyncMock(side_effect=lambda msg, is_critical=False: alerts_sent.append(msg))

    # Mock open positions and plan orders
    mexc_client.get_open_positions = AsyncMock(return_value=[
        {
            "symbol": "BTC_USDT",
            "positionType": 1,
            "holdVol": 0.5,
            "openAvgPrice": 65000.0,
            "liquidatePrice": 58000.0,
            "unRealizedPnl": 25.0,
            "leverage": 10,
            "im": 325.0,
        }
    ])
    mexc_client.get_plan_orders = AsyncMock(return_value=[])

    await notification_service.broadcast_positions_snapshot()
    assert len(alerts_sent) == 1
    assert "AUTOMATED POSITIONS SNAPSHOT" in alerts_sent[0]
    assert "BTC_USDT" in alerts_sent[0]
    assert "65,000.0000" in alerts_sent[0]

