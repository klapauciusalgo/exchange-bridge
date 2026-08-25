"""Tests for Risk Engine validations, leverage limits, daily loss limits, and SL/TP sanity."""
import pytest
from risk.risk_engine import RiskEngine
from risk.daily_tracker import DailyTracker
from database.db import Database


@pytest.mark.asyncio
async def test_validate_leverage_limit(risk_engine: RiskEngine):
    user_id = 111222333

    # Valid leverage (10x <= 20x)
    res = await risk_engine.validate_open_order(
        user_id=user_id,
        symbol="BTC_USDT",
        side_is_long=True,
        size_usdt=100.0,
        leverage=10,
        current_price=65000.0,
    )
    assert res.is_valid is True

    # Exceeding leverage (25x > 20x)
    res_invalid = await risk_engine.validate_open_order(
        user_id=user_id,
        symbol="BTC_USDT",
        side_is_long=True,
        size_usdt=100.0,
        leverage=25,
        current_price=65000.0,
    )
    assert res_invalid.is_valid is False
    assert "exceeds your configured maximum limit" in res_invalid.reason


@pytest.mark.asyncio
async def test_validate_insufficient_balance(risk_engine: RiskEngine):
    user_id = 111222333
    # Dry run available balance is 850 USDT
    # Size 20,000 USDT with 10x leverage requires 2,000 USDT margin (exceeds balance)
    res = await risk_engine.validate_open_order(
        user_id=user_id,
        symbol="BTC_USDT",
        side_is_long=True,
        size_usdt=20000.0,
        leverage=10,
        current_price=65000.0,
    )
    assert res.is_valid is False
    assert "Insufficient available balance" in res.reason


@pytest.mark.asyncio
async def test_validate_max_position_equity_pct(risk_engine: RiskEngine):
    user_id = 111222333
    # Dry run equity is 1000 USDT. Max position is 30% = 300 USDT margin max.
    # Sizing 4,000 USDT with 10x leverage = 400 USDT margin (exceeds 300 USDT limit)
    res = await risk_engine.validate_open_order(
        user_id=user_id,
        symbol="BTC_USDT",
        side_is_long=True,
        size_usdt=4000.0,
        leverage=10,
        current_price=65000.0,
    )
    assert res.is_valid is False
    assert "exceeds max allowed 30.0% of total equity" in res.reason


@pytest.mark.asyncio
async def test_sl_tp_direction_sanity(risk_engine: RiskEngine):
    user_id = 111222333
    entry_price = 65000.0

    # 1. LONG: SL must be < entry
    res_bad_sl_long = await risk_engine.validate_open_order(
        user_id=user_id,
        symbol="BTC_USDT",
        side_is_long=True,
        size_usdt=100.0,
        leverage=10,
        current_price=entry_price,
        stop_loss_price=66000.0,  # Bad: above entry
    )
    assert res_bad_sl_long.is_valid is False
    assert "Invalid Stop Loss for LONG" in res_bad_sl_long.reason

    # 2. LONG: TP must be > entry
    res_bad_tp_long = await risk_engine.validate_open_order(
        user_id=user_id,
        symbol="BTC_USDT",
        side_is_long=True,
        size_usdt=100.0,
        leverage=10,
        current_price=entry_price,
        take_profit_price=64000.0,  # Bad: below entry
    )
    assert res_bad_tp_long.is_valid is False
    assert "Invalid Take Profit for LONG" in res_bad_tp_long.reason

    # 3. SHORT: SL must be > entry
    res_bad_sl_short = await risk_engine.validate_open_order(
        user_id=user_id,
        symbol="BTC_USDT",
        side_is_long=False,
        size_usdt=100.0,
        leverage=10,
        current_price=entry_price,
        stop_loss_price=64000.0,  # Bad: below entry
    )
    assert res_bad_sl_short.is_valid is False
    assert "Invalid Stop Loss for SHORT" in res_bad_sl_short.reason

    # 4. Valid Long with SL and TP
    res_valid_long = await risk_engine.validate_open_order(
        user_id=user_id,
        symbol="BTC_USDT",
        side_is_long=True,
        size_usdt=100.0,
        leverage=10,
        current_price=entry_price,
        stop_loss_price=64000.0,
        take_profit_price=68000.0,
    )
    assert res_valid_long.is_valid is True
    # Risk = 1000, Reward = 3000 -> RR = 3.0
    assert res_valid_long.risk_reward_ratio == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_daily_loss_limit_blocking(risk_engine: RiskEngine, daily_tracker: DailyTracker):
    user_id = 111222333

    # Initially trading is allowed
    res1 = await risk_engine.validate_open_order(
        user_id=user_id,
        symbol="BTC_USDT",
        side_is_long=True,
        size_usdt=100.0,
        leverage=10,
        current_price=65000.0,
    )
    assert res1.is_valid is True

    # Record a realized loss of 150 USDT (limit is 100 USDT)
    await daily_tracker.record_trade_result(
        user_id=user_id,
        realized_pnl=-150.0,
        fee=2.0,
        current_equity=1000.0,
        daily_loss_limit_usdt=100.0,
        max_daily_loss_pct=10.0,
    )

    # Next trade should be blocked
    res2 = await risk_engine.validate_open_order(
        user_id=user_id,
        symbol="BTC_USDT",
        side_is_long=True,
        size_usdt=100.0,
        leverage=10,
        current_price=65000.0,
    )
    assert res2.is_valid is False
    assert "Daily loss limit reached" in res2.reason


def test_estimated_liquidation(risk_engine: RiskEngine):
    entry = 50000.0
    lev = 10
    mmr = 0.005

    # Long liq = 50000 * (1 - 0.1 + 0.005) = 50000 * 0.905 = 45250
    liq_long = risk_engine.calculate_estimated_liquidation(
        side_is_long=True,
        entry_price=entry,
        leverage=lev,
        mmr=mmr,
    )
    assert liq_long == pytest.approx(45250.0)

    # Short liq = 50000 * (1 + 0.1 - 0.005) = 50000 * 1.095 = 54750
    liq_short = risk_engine.calculate_estimated_liquidation(
        side_is_long=False,
        entry_price=entry,
        leverage=lev,
        mmr=mmr,
    )
    assert liq_short == pytest.approx(54750.0)
