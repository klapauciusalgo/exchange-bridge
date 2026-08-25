"""Database manager using aiosqlite with WAL mode and robust CRUD operations."""
import os
import json
import logging
from datetime import datetime, date, timezone
from typing import List, Optional
import aiosqlite

from database.models import (
    AuditLogEntry,
    OrderLogEntry,
    UserRiskConfig,
    WatchlistAlert,
    DailyTradingStats,
    utc_now_iso,
)

logger = logging.getLogger(__name__)


class Database:
    """Async SQLite Database Manager."""

    def __init__(self, db_path: str = "data/mexc_bridge.db"):
        self.db_path = db_path
        self._connection: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Initialize database connection and create tables."""
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row

        # Performance and integrity pragmas
        await self._connection.execute("PRAGMA journal_mode=WAL;")
        await self._connection.execute("PRAGMA synchronous=NORMAL;")
        await self._connection.execute("PRAGMA busy_timeout=5000;")

        await self._create_tables()
        logger.info(f"Database connected and initialized at {self.db_path}")

    async def close(self) -> None:
        """Close database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("Database connection closed")

    async def _create_tables(self) -> None:
        """Create schema tables if not existing."""
        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                telegram_user_id INTEGER NOT NULL,
                command TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL,
                risk_verdict TEXT,
                latency_ms REAL NOT NULL,
                details TEXT
            );
        """)
        await self._connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_user_time ON audit_logs(telegram_user_id, timestamp);
        """)

        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS orders_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mexc_order_id TEXT,
                client_order_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                price REAL NOT NULL,
                volume REAL NOT NULL,
                leverage INTEGER NOT NULL,
                status TEXT NOT NULL,
                filled_price REAL,
                fee REAL DEFAULT 0.0,
                is_dry_run INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        await self._connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_mexc_id ON orders_log(mexc_order_id);
        """)

        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS risk_config (
                user_id INTEGER PRIMARY KEY,
                max_leverage INTEGER NOT NULL DEFAULT 20,
                max_position_pct REAL NOT NULL DEFAULT 30.0,
                daily_loss_limit_usdt REAL NOT NULL DEFAULT 100.0,
                max_daily_loss_pct REAL NOT NULL DEFAULT 10.0,
                dry_run INTEGER NOT NULL DEFAULT 0,
                require_pin INTEGER NOT NULL DEFAULT 0,
                pin_salt TEXT,
                pin_hash TEXT,
                updated_at TEXT NOT NULL
            );
        """)

        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                condition TEXT NOT NULL,
                target_price REAL NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                triggered_at TEXT
            );
        """)
        await self._connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_watchlist_active ON watchlist(is_active, symbol);
        """)

        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                date_str TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                starting_equity REAL NOT NULL DEFAULT 0.0,
                realized_pnl REAL NOT NULL DEFAULT 0.0,
                fees_paid REAL NOT NULL DEFAULT 0.0,
                total_trades INTEGER NOT NULL DEFAULT 0,
                winning_trades INTEGER NOT NULL DEFAULT 0,
                losing_trades INTEGER NOT NULL DEFAULT 0,
                is_limit_exceeded INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (date_str, user_id)
            );
        """)
        await self._connection.commit()

    # --- Audit Log Operations ---
    async def log_audit(self, entry: AuditLogEntry) -> int:
        query = """
            INSERT INTO audit_logs (timestamp, telegram_user_id, command, payload, status, risk_verdict, latency_ms, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        async with self._connection.execute(query, (
            entry.timestamp,
            entry.telegram_user_id,
            entry.command,
            entry.payload,
            entry.status,
            entry.risk_verdict,
            entry.latency_ms,
            entry.details
        )) as cursor:
            await self._connection.commit()
            return cursor.lastrowid

    async def get_recent_audit_logs(self, user_id: Optional[int] = None, limit: int = 20) -> List[AuditLogEntry]:
        if user_id:
            query = "SELECT * FROM audit_logs WHERE telegram_user_id = ? ORDER BY id DESC LIMIT ?"
            params = (user_id, limit)
        else:
            query = "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?"
            params = (limit,)

        async with self._connection.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [AuditLogEntry(**dict(row)) for row in rows]

    # --- Order Log Operations ---
    async def record_order(self, order: OrderLogEntry) -> int:
        query = """
            INSERT INTO orders_log (
                mexc_order_id, client_order_id, symbol, side, order_type, price,
                volume, leverage, status, filled_price, fee, is_dry_run, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        async with self._connection.execute(query, (
            order.mexc_order_id,
            order.client_order_id,
            order.symbol,
            order.side,
            order.order_type,
            order.price,
            order.volume,
            order.leverage,
            order.status,
            order.filled_price,
            order.fee,
            1 if order.is_dry_run else 0,
            order.created_at,
            order.updated_at
        )) as cursor:
            await self._connection.commit()
            return cursor.lastrowid

    async def update_order_status(
        self,
        mexc_order_id: str,
        status: str,
        filled_price: Optional[float] = None,
        fee: Optional[float] = None
    ) -> bool:
        now = utc_now_iso()
        query = """
            UPDATE orders_log
            SET status = ?,
                filled_price = COALESCE(?, filled_price),
                fee = COALESCE(?, fee),
                updated_at = ?
            WHERE mexc_order_id = ?
        """
        async with self._connection.execute(query, (status, filled_price, fee, now, mexc_order_id)) as cursor:
            await self._connection.commit()
            return cursor.rowcount > 0

    # --- Risk Config Operations ---
    async def get_user_risk_config(self, user_id: int) -> Optional[UserRiskConfig]:
        query = "SELECT * FROM risk_config WHERE user_id = ?"
        async with self._connection.execute(query, (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["dry_run"] = bool(data["dry_run"])
            data["require_pin"] = bool(data["require_pin"])
            return UserRiskConfig(**data)

    async def save_user_risk_config(self, config: UserRiskConfig) -> None:
        query = """
            INSERT INTO risk_config (
                user_id, max_leverage, max_position_pct, daily_loss_limit_usdt,
                max_daily_loss_pct, dry_run, require_pin, pin_salt, pin_hash, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                max_leverage = excluded.max_leverage,
                max_position_pct = excluded.max_position_pct,
                daily_loss_limit_usdt = excluded.daily_loss_limit_usdt,
                max_daily_loss_pct = excluded.max_daily_loss_pct,
                dry_run = excluded.dry_run,
                require_pin = excluded.require_pin,
                pin_salt = excluded.pin_salt,
                pin_hash = excluded.pin_hash,
                updated_at = excluded.updated_at
        """
        await self._connection.execute(query, (
            config.user_id,
            config.max_leverage,
            config.max_position_pct,
            config.daily_loss_limit_usdt,
            config.max_daily_loss_pct,
            1 if config.dry_run else 0,
            1 if config.require_pin else 0,
            config.pin_salt,
            config.pin_hash,
            utc_now_iso()
        ))
        await self._connection.commit()

    # --- Watchlist Operations ---
    async def add_watchlist_alert(self, alert: WatchlistAlert) -> int:
        query = """
            INSERT INTO watchlist (user_id, symbol, condition, target_price, is_active, created_at, triggered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        async with self._connection.execute(query, (
            alert.user_id,
            alert.symbol.upper(),
            alert.condition.upper(),
            alert.target_price,
            1 if alert.is_active else 0,
            alert.created_at,
            alert.triggered_at
        )) as cursor:
            await self._connection.commit()
            return cursor.lastrowid

    async def get_active_watchlist(self, symbol: Optional[str] = None) -> List[WatchlistAlert]:
        if symbol:
            query = "SELECT * FROM watchlist WHERE is_active = 1 AND symbol = ?"
            params = (symbol.upper(),)
        else:
            query = "SELECT * FROM watchlist WHERE is_active = 1"
            params = ()

        async with self._connection.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            alerts = []
            for row in rows:
                data = dict(row)
                data["is_active"] = bool(data["is_active"])
                alerts.append(WatchlistAlert(**data))
            return alerts

    async def get_user_watchlist(self, user_id: int) -> List[WatchlistAlert]:
        query = "SELECT * FROM watchlist WHERE user_id = ? AND is_active = 1 ORDER BY id DESC"
        async with self._connection.execute(query, (user_id,)) as cursor:
            rows = await cursor.fetchall()
            alerts = []
            for row in rows:
                data = dict(row)
                data["is_active"] = bool(data["is_active"])
                alerts.append(WatchlistAlert(**data))
            return alerts

    async def delete_watchlist_alert(self, alert_id: int, user_id: int) -> bool:
        query = "DELETE FROM watchlist WHERE id = ? AND user_id = ?"
        async with self._connection.execute(query, (alert_id, user_id)) as cursor:
            await self._connection.commit()
            return cursor.rowcount > 0

    async def mark_watchlist_triggered(self, alert_id: int) -> None:
        now = utc_now_iso()
        query = "UPDATE watchlist SET is_active = 0, triggered_at = ? WHERE id = ?"
        await self._connection.execute(query, (now, alert_id))
        await self._connection.commit()

    # --- Daily Stats Operations ---
    async def get_or_create_daily_stats(self, user_id: int, current_equity: float = 0.0) -> DailyTradingStats:
        today_str = date.today().isoformat()
        query = "SELECT * FROM daily_stats WHERE date_str = ? AND user_id = ?"
        async with self._connection.execute(query, (today_str, user_id)) as cursor:
            row = await cursor.fetchone()
            if row:
                data = dict(row)
                data["is_limit_exceeded"] = bool(data["is_limit_exceeded"])
                return DailyTradingStats(**data)

        # Create new record for today
        now = utc_now_iso()
        insert_query = """
            INSERT INTO daily_stats (date_str, user_id, starting_equity, realized_pnl, fees_paid, total_trades, winning_trades, losing_trades, is_limit_exceeded, updated_at)
            VALUES (?, ?, ?, 0.0, 0.0, 0, 0, 0, 0, ?)
        """
        await self._connection.execute(insert_query, (today_str, user_id, current_equity, now))
        await self._connection.commit()
        return DailyTradingStats(
            date_str=today_str,
            user_id=user_id,
            starting_equity=current_equity,
            realized_pnl=0.0,
            fees_paid=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            is_limit_exceeded=False,
            updated_at=now
        )

    async def update_daily_stats(self, stats: DailyTradingStats) -> None:
        query = """
            UPDATE daily_stats
            SET starting_equity = ?,
                realized_pnl = ?,
                fees_paid = ?,
                total_trades = ?,
                winning_trades = ?,
                losing_trades = ?,
                is_limit_exceeded = ?,
                updated_at = ?
            WHERE date_str = ? AND user_id = ?
        """
        await self._connection.execute(query, (
            stats.starting_equity,
            stats.realized_pnl,
            stats.fees_paid,
            stats.total_trades,
            stats.winning_trades,
            stats.losing_trades,
            1 if stats.is_limit_exceeded else 0,
            utc_now_iso(),
            stats.date_str,
            stats.user_id
        ))
        await self._connection.commit()
