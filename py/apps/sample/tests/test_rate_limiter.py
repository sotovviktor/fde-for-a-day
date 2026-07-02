"""Deterministic unit tests for AsyncRateLimiter (no network, fake clock)."""

import asyncio

import pytest
from rate_limiter import AsyncRateLimiter


class _FakeClock:
    """A controllable monotonic clock whose ``sleep`` advances time instantly."""

    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_rejects_non_positive_rate() -> None:
    with pytest.raises(ValueError):
        AsyncRateLimiter(rate_per_second=0)


async def test_burst_then_paces_to_rate() -> None:
    """Capacity tokens are served immediately; further acquires pace to the rate."""
    clock = _FakeClock()
    # 10 req/s with a burst capacity of 3 tokens.
    limiter = AsyncRateLimiter(
        rate_per_second=10.0,
        capacity=3.0,
        time_func=clock.time,
        sleep_func=clock.sleep,
    )

    # First 3 acquires drain the initial burst without advancing time.
    for _ in range(3):
        await limiter.acquire()
    assert clock.now == pytest.approx(0.0)

    # The next acquire must wait one refill interval (1 / 10 s).
    await limiter.acquire()
    assert clock.now == pytest.approx(0.1)

    # And the one after that another interval.
    await limiter.acquire()
    assert clock.now == pytest.approx(0.2)


async def test_average_rate_is_bounded_under_concurrency() -> None:
    """Many concurrent acquires are paced so the sustained rate is not exceeded."""
    clock = _FakeClock()
    rate = 5.0  # tokens per second
    limiter = AsyncRateLimiter(
        rate_per_second=rate,
        capacity=1.0,
        time_func=clock.time,
        sleep_func=clock.sleep,
    )

    n = 20
    await asyncio.gather(*(limiter.acquire() for _ in range(n)))

    # With capacity 1, the first token is free and the remaining (n - 1) are paced
    # one interval apart: total elapsed == (n - 1) / rate.
    assert clock.now == pytest.approx((n - 1) / rate)


async def test_idle_refill_is_capped_at_capacity() -> None:
    """A long idle period cannot accumulate more than ``capacity`` tokens."""
    clock = _FakeClock()
    limiter = AsyncRateLimiter(
        rate_per_second=1.0,
        capacity=2.0,
        time_func=clock.time,
        sleep_func=clock.sleep,
    )

    # Sit idle far longer than it takes to refill capacity.
    clock.now += 100.0

    # Only ``capacity`` (2) tokens are available instantly; the 3rd must wait.
    await limiter.acquire()
    await limiter.acquire()
    assert clock.now == pytest.approx(100.0)
    await limiter.acquire()
    assert clock.now == pytest.approx(101.0)
