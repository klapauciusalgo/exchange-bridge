"""Tests for async token bucket rate limiter."""
import asyncio
import time
import pytest
from exchange.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_burst():
    limiter = RateLimiter(max_requests=5, window_seconds=0.5)

    start = time.monotonic()
    for _ in range(5):
        await limiter.acquire()
    elapsed = time.monotonic() - start

    # First 5 requests should pass immediately
    assert elapsed < 0.2
    assert limiter.total_requests == 5
    assert limiter.throttled_count == 0


@pytest.mark.asyncio
async def test_rate_limiter_throttling():
    limiter = RateLimiter(max_requests=3, window_seconds=0.3)

    start = time.monotonic()
    # Request 4 times (4th should be delayed)
    for _ in range(4):
        await limiter.acquire()
    elapsed = time.monotonic() - start

    assert elapsed >= 0.25
    assert limiter.total_requests == 4
    assert limiter.throttled_count >= 1
