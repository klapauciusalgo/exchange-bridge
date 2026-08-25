"""Risk Management Engine for MEXC Telegram Bridge.
Validates all trading operations before execution.
"""
from dataclasses import dataclass
import logging
from typing import Optional, Tuple

from config.settings import Settings
from database.db import Database
from database.models import UserRiskConfig
from exchange.mexc_client import MexcClient
from risk.daily_tracker import DailyTracker

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    is_valid: bool
    reason: Optional[str] = None
    estimated_margin: float = 0.0
    estimated_liq_price: float = 0.0
    margin_ratio_pct: float = 0.0
    risk_reward_ratio: Optional[float] = None


class RiskEngine:
    """Validates order parameters, leverage, margin limits, and SL/TP sanity."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        mexc_client: MexcClient,
        daily_tracker: Optional[DailyTracker] = None,
    ):
        self.settings = settings
        self.db = db
        self.client = mexc_client
        self.tracker = daily_tracker or DailyTracker(db)

    async def get_effective_user_config(self, user_id: int) -> UserRiskConfig:
        """Get user risk settings from DB or fallback to system defaults."""
        config = await self.db.get_user_risk_config(user_id)
        if not config:
            config = UserRiskConfig(
                user_id=user_id,
                max_leverage=self.settings.MAX_LEVERAGE,
                max_position_pct=self.settings.MAX_POSITION_EQUITY_PCT,
                daily_loss_limit_usdt=self.settings.DAILY_LOSS_LIMIT_USDT,
                max_daily_loss_pct=self.settings.MAX_DAILY_LOSS_PCT,
                dry_run=self.settings.DRY_RUN,
                require_pin=self.settings.REQUIRE_PIN,
            )
        return config

    def calculate_estimated_liquidation(
        self,
        side_is_long: bool,
        entry_price: float,
        leverage: int,
        mmr: float = 0.005,  # 0.5% default Maintenance Margin Rate
    ) -> float:
        """Estimate liquidation price for isolated position."""
        if leverage <= 0 or entry_price <= 0:
            return 0.0
        if side_is_long:
            # Long liq = Entry * (1 - (1/Lev) + MMR)
            return max(0.0, entry_price * (1.0 - (1.0 / leverage) + mmr))
        else:
            # Short liq = Entry * (1 + (1/Lev) - MMR)
            return max(0.0, entry_price * (1.0 + (1.0 / leverage) - mmr))

    async def validate_open_order(
        self,
        user_id: int,
        symbol: str,
        side_is_long: bool,
        size_usdt: float,
        leverage: int,
        current_price: float,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
    ) -> ValidationResult:
        """Thoroughly validate a new position opening request."""
        config = await self.get_effective_user_config(user_id)
        sym = self.client.normalize_symbol(symbol)

        # 1. Check Assessment Zone
        try:
            if await self.client.is_assessment_zone(sym):
                return ValidationResult(
                    is_valid=False,
                    reason=f"Trading rejected: Symbol {sym} is in MEXC Assessment Zone (API restricted)."
                )
        except Exception as e:
            logger.warning(f"Could not check assessment zone for {sym}: {e}")

        # 2. Validate Leverage Limit
        if leverage > config.max_leverage:
            return ValidationResult(
                is_valid=False,
                reason=f"Leverage {leverage}x exceeds your configured maximum limit of {config.max_leverage}x."
            )
        if leverage < 1:
            return ValidationResult(
                is_valid=False,
                reason="Leverage must be at least 1x."
            )

        # 3. Validate Size
        if size_usdt <= 0:
            return ValidationResult(
                is_valid=False,
                reason="Position size must be greater than 0 USDT."
            )
        if current_price <= 0:
            return ValidationResult(
                is_valid=False,
                reason="Invalid market price (<= 0)."
            )

        # 4. Fetch Account Balance & Equity
        try:
            assets = await self.client.get_account_assets()
            usdt_asset = next((a for a in assets if a.get("currency") == "USDT"), {})
            available_balance = float(usdt_asset.get("availableBalance", 0.0))
            total_equity = float(usdt_asset.get("equity", available_balance))
            unrealized_pnl = float(usdt_asset.get("unrealized", usdt_asset.get("unRealizedPnl", usdt_asset.get("unrealized(positionPnl)", 0.0))))
        except Exception as e:
            logger.error(f"Failed to fetch assets in risk check: {e}")
            return ValidationResult(
                is_valid=False,
                reason=f"Failed to verify account balance with exchange: {e}"
            )

        # 5. Check Daily Loss Limit
        is_blocked = await self.tracker.is_daily_loss_limit_reached(
            user_id=user_id,
            daily_loss_limit_usdt=config.daily_loss_limit_usdt,
            max_daily_loss_pct=config.max_daily_loss_pct,
            current_equity=total_equity,
            unrealized_pnl=unrealized_pnl,
        )
        if is_blocked:
            return ValidationResult(
                is_valid=False,
                reason=f"Daily loss limit reached (Max loss: ${config.daily_loss_limit_usdt:.2f} or {config.max_daily_loss_pct}%). New trades blocked until 00:00 UTC."
            )

        # 6. Calculate Margin Required
        required_margin = size_usdt / leverage
        if required_margin > available_balance:
            return ValidationResult(
                is_valid=False,
                reason=f"Insufficient available balance. Required margin: ${required_margin:.2f} USDT, Available: ${available_balance:.2f} USDT."
            )

        # 7. Max Position % of Equity Check
        if total_equity > 0:
            max_allowed_margin = total_equity * (config.max_position_pct / 100.0)
            if required_margin > max_allowed_margin:
                return ValidationResult(
                    is_valid=False,
                    reason=f"Position margin (${required_margin:.2f}) exceeds max allowed {config.max_position_pct}% of total equity (${max_allowed_margin:.2f} max)."
                )

        # 8. SL / TP Direction Sanity Check
        if stop_loss_price is not None and stop_loss_price > 0:
            if side_is_long and stop_loss_price >= current_price:
                return ValidationResult(
                    is_valid=False,
                    reason=f"Invalid Stop Loss for LONG: SL price (${stop_loss_price:.4f}) must be BELOW entry/market price (${current_price:.4f})."
                )
            if not side_is_long and stop_loss_price <= current_price:
                return ValidationResult(
                    is_valid=False,
                    reason=f"Invalid Stop Loss for SHORT: SL price (${stop_loss_price:.4f}) must be ABOVE entry/market price (${current_price:.4f})."
                )

        if take_profit_price is not None and take_profit_price > 0:
            if side_is_long and take_profit_price <= current_price:
                return ValidationResult(
                    is_valid=False,
                    reason=f"Invalid Take Profit for LONG: TP price (${take_profit_price:.4f}) must be ABOVE entry/market price (${current_price:.4f})."
                )
            if not side_is_long and take_profit_price >= current_price:
                return ValidationResult(
                    is_valid=False,
                    reason=f"Invalid Take Profit for SHORT: TP price (${take_profit_price:.4f}) must be BELOW entry/market price (${current_price:.4f})."
                )

        # Calculate Risk/Reward Ratio if both SL and TP are provided
        risk_reward: Optional[float] = None
        if stop_loss_price and take_profit_price:
            risk_dist = abs(current_price - stop_loss_price)
            reward_dist = abs(take_profit_price - current_price)
            if risk_dist > 0:
                risk_reward = reward_dist / risk_dist

        estimated_liq = self.calculate_estimated_liquidation(
            side_is_long=side_is_long,
            entry_price=current_price,
            leverage=leverage,
        )

        margin_ratio = (required_margin / total_equity * 100.0) if total_equity > 0 else 0.0

        return ValidationResult(
            is_valid=True,
            estimated_margin=required_margin,
            estimated_liq_price=estimated_liq,
            margin_ratio_pct=margin_ratio,
            risk_reward_ratio=risk_reward,
        )

    def validate_sl_tp_modification(
        self,
        side_is_long: bool,
        current_price: float,
        entry_price: float,
        sl_price: Optional[float] = None,
        tp_price: Optional[float] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Validate standalone SL/TP update on an open position."""
        reference_price = current_price if current_price > 0 else entry_price

        if sl_price is not None and sl_price > 0:
            if side_is_long and sl_price >= reference_price:
                return False, f"Stop Loss for LONG (${sl_price}) must be below current price (${reference_price})."
            if not side_is_long and sl_price <= reference_price:
                return False, f"Stop Loss for SHORT (${sl_price}) must be above current price (${reference_price})."

        if tp_price is not None and tp_price > 0:
            if side_is_long and tp_price <= reference_price:
                return False, f"Take Profit for LONG (${tp_price}) must be above current price (${reference_price})."
            if not side_is_long and tp_price >= reference_price:
                return False, f"Take Profit for SHORT (${tp_price}) must be below current price (${reference_price})."

        return True, None
