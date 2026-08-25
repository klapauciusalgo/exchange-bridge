"""Data transfer and domain models for database storage."""
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLogEntry(BaseModel):
    id: Optional[int] = None
    timestamp: str = Field(default_factory=utc_now_iso)
    telegram_user_id: int
    command: str
    payload: str = "{}"
    status: str
    risk_verdict: Optional[str] = None
    latency_ms: float = 0.0
    details: Optional[str] = None


class OrderLogEntry(BaseModel):
    id: Optional[int] = None
    mexc_order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    symbol: str
    side: str
    order_type: str
    price: float = 0.0
    volume: float = 0.0
    leverage: int = 1
    status: str = "NEW"
    filled_price: Optional[float] = None
    fee: float = 0.0
    is_dry_run: bool = False
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class UserRiskConfig(BaseModel):
    user_id: int
    max_leverage: int = 20
    max_position_pct: float = 30.0
    daily_loss_limit_usdt: float = 100.0
    max_daily_loss_pct: float = 10.0
    dry_run: bool = False
    require_pin: bool = False
    pin_salt: Optional[str] = None
    pin_hash: Optional[str] = None
    updated_at: str = Field(default_factory=utc_now_iso)


class WatchlistAlert(BaseModel):
    id: Optional[int] = None
    user_id: int
    symbol: str
    condition: str  # "ABOVE" or "BELOW"
    target_price: float
    is_active: bool = True
    created_at: str = Field(default_factory=utc_now_iso)
    triggered_at: Optional[str] = None


class DailyTradingStats(BaseModel):
    date_str: str  # "YYYY-MM-DD"
    user_id: int
    starting_equity: float = 0.0
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    is_limit_exceeded: bool = False
    updated_at: str = Field(default_factory=utc_now_iso)
