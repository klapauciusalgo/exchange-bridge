"""Daily PnL and trade loss tracker."""
import logging
from datetime import datetime, date
from typing import Optional

from database.db import Database
from database.models import DailyTradingStats

logger = logging.getLogger(__name__)


class DailyTracker:
    """Tracks daily trading stats, realized PnL, and checks against daily loss limits."""

    def __init__(self, db: Database):
        self.db = db

    async def get_today_stats(self, user_id: int, current_equity: float = 0.0) -> DailyTradingStats:
        """Get or initialize today's trading statistics."""
        return await self.db.get_or_create_daily_stats(user_id, current_equity)

    async def record_trade_result(
        self,
        user_id: int,
        realized_pnl: float,
        fee: float = 0.0,
        current_equity: float = 0.0,
        daily_loss_limit_usdt: float = 100.0,
        max_daily_loss_pct: float = 10.0,
    ) -> DailyTradingStats:
        """Record trade completion, update realized PnL, and check limit breach."""
        stats = await self.db.get_or_create_daily_stats(user_id, current_equity)
        stats.realized_pnl += realized_pnl
        stats.fees_paid += fee
        stats.total_trades += 1
        if realized_pnl > 0:
            stats.winning_trades += 1
        elif realized_pnl < 0:
            stats.losing_trades += 1

        # Check if daily loss limit is breached
        net_daily_pnl = stats.realized_pnl - stats.fees_paid
        if net_daily_pnl < 0 and abs(net_daily_pnl) >= daily_loss_limit_usdt:
            stats.is_limit_exceeded = True
            logger.warning(f"Daily loss limit USDT breached for user {user_id}: net PnL={net_daily_pnl:.2f}, limit={daily_loss_limit_usdt}")

        if stats.starting_equity > 0:
            loss_pct = (abs(net_daily_pnl) / stats.starting_equity) * 100
            if net_daily_pnl < 0 and loss_pct >= max_daily_loss_pct:
                stats.is_limit_exceeded = True
                logger.warning(f"Daily loss limit % breached for user {user_id}: loss={loss_pct:.2f}%, max={max_daily_loss_pct}%")

        await self.db.update_daily_stats(stats)
        return stats

    async def is_daily_loss_limit_reached(
        self,
        user_id: int,
        daily_loss_limit_usdt: float,
        max_daily_loss_pct: float,
        current_equity: float = 0.0,
        unrealized_pnl: float = 0.0,
    ) -> bool:
        """Check if trading is currently blocked due to daily loss limit."""
        stats = await self.db.get_or_create_daily_stats(user_id, current_equity)
        if stats.is_limit_exceeded:
            return True

        # Include current day's realized PnL + fees + current unrealized drawdown
        total_effective_loss = (stats.realized_pnl - stats.fees_paid) + min(0.0, unrealized_pnl)
        if total_effective_loss < 0:
            if abs(total_effective_loss) >= daily_loss_limit_usdt:
                stats.is_limit_exceeded = True
                await self.db.update_daily_stats(stats)
                return True

            if stats.starting_equity > 0:
                loss_pct = (abs(total_effective_loss) / stats.starting_equity) * 100
                if loss_pct >= max_daily_loss_pct:
                    stats.is_limit_exceeded = True
                    await self.db.update_daily_stats(stats)
                    return True

        return False
