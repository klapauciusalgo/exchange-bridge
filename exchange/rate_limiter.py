"""Token bucket / sliding window rate limiter to comply with MEXC rate limits."""
import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Sliding window async rate limiter.
    Ensures that within `window_seconds`, no more than `max_requests` are dispatched.
    """

    def __init__(self, max_requests: int = 10, window_seconds: float = 2.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps = []
        self._lock = asyncio.Lock()
        self.total_requests = 0
        self.throttled_count = 0

    async def acquire(self) -> None:
        """Acquire permission to send a request, sleeping asynchronously if needed."""
        async with self._lock:
            now = time.monotonic()
            # Prune timestamps outside the current window
            self._timestamps = [t for t in self._timestamps if now - t < self.window_seconds]

            if len(self._timestamps) >= self.max_requests:
                # Calculate sleep time until the oldest timestamp exits the window
                oldest = self._timestamps[0]
                sleep_duration = self.window_seconds - (now - oldest) + 0.05
                if sleep_duration > 0:
                    self.throttled_count += 1
                    logger.debug(f"Rate limit reached ({len(self._timestamps)}/{self.max_requests}). Throttling for {sleep_duration:.3f}s")
                    await asyncio.sleep(sleep_duration)

                # Prune again after sleeping
                now = time.monotonic()
                self._timestamps = [t for t in self._timestamps if now - t < self.window_seconds]

            self._timestamps.append(time.monotonic())
            self.total_requests += 1

    def get_stats(self) -> dict:
        return {
            "max_requests": self.max_requests,
            "window_seconds": self.window_seconds,
            "current_window_usage": len(self._timestamps),
            "total_requests": self.total_requests,
            "throttled_count": self.throttled_count,
        }
