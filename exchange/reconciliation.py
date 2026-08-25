"""State reconciliation service to synchronize REST state with WebSocket cache."""
import asyncio
import logging
from typing import Optional

from exchange.mexc_client import MexcClient
from exchange.mexc_ws import MexcWebSocketClient
from database.db import Database

logger = logging.getLogger(__name__)


class StateReconciler:
    """Synchronizes exchange state via REST when WS disconnects/reconnects or on schedule."""

    def __init__(
        self,
        mexc_client: MexcClient,
        ws_client: MexcWebSocketClient,
        db: Database,
        sync_interval: int = 60,
    ):
        self.client = mexc_client
        self.ws = ws_client
        self.db = db
        self.sync_interval = sync_interval
        self._running: bool = False
        self._sync_task: Optional[asyncio.Task] = None

        # Register WS reconnect hook
        self.ws.add_reconnect_listener(self.reconcile_state)

    async def start(self) -> None:
        """Start periodic state reconciliation."""
        if self._running:
            return
        self._running = True
        self._sync_task = asyncio.create_task(self._periodic_sync_loop())
        logger.info("State reconciler service started")

    async def stop(self) -> None:
        """Stop periodic state reconciliation."""
        self._running = False
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
        logger.info("State reconciler service stopped")

    async def _periodic_sync_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.sync_interval)
                await self.reconcile_state()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error during periodic state reconciliation: {e}")

    async def reconcile_state(self) -> None:
        """Fetch REST snapshot and refresh WS in-memory state."""
        try:
            logger.info("Starting exchange state reconciliation via REST...")

            # 1. Fetch Assets
            assets = await self.client.get_account_assets()
            if isinstance(assets, list):
                for asset in assets:
                    curr = asset.get("currency", "USDT")
                    self.ws.assets[curr] = asset

            # 2. Fetch Open Positions
            positions = await self.client.get_open_positions()
            current_symbols = set()
            if isinstance(positions, list):
                for pos in positions:
                    sym = pos.get("symbol")
                    if sym:
                        current_symbols.add(sym)
                        self.ws.positions[sym] = pos

                # Clear positions in cache that are no longer open
                cached_symbols = list(self.ws.positions.keys())
                for sym in cached_symbols:
                    if sym not in current_symbols:
                        self.ws.positions.pop(sym, None)

            logger.info(f"State reconciliation complete. Active positions: {len(self.ws.positions)}")
        except Exception as e:
            logger.error(f"Failed to reconcile exchange state: {e}")
