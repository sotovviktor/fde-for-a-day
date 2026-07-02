"""Process-global async rate limiting for outbound LLM calls.

All three task endpoints (/triage, /extract, /orchestrate) share a single Azure
OpenAI deployment, and therefore a single RPM quota. To avoid ``429
rate_limit_exceeded`` responses, every LLM request start is paced through one
shared :class:`AsyncRateLimiter` plus a concurrency :class:`asyncio.Semaphore`.

The limiter is a token bucket: tokens refill continuously at ``rate`` tokens per
second and are capped at ``capacity`` (the burst budget). ``acquire`` consumes
one token, sleeping just long enough when the bucket is empty. Token accounting
runs under an :class:`asyncio.Lock`, so it is correct under high concurrency and
never busy-waits (a single ``sleep`` covers the exact deficit).
"""

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from time import monotonic

# Tolerance for token comparisons. Floating-point rounding can leave a refilled
# bucket at, e.g., 0.9999999998 tokens; without this slack the acquire loop would
# spin forever (the sub-epsilon time delta needed to "finish" refilling underflows).
_TOKEN_EPSILON = 1e-9


class AsyncRateLimiter:
    """Async token-bucket limiter that paces request starts to a target rate.

    Args:
        rate_per_second: Sustained token refill rate (requests per second).
        capacity: Maximum burst of tokens that can accumulate while idle. Defaults
            to one second's worth of tokens (at least 1), which lets a short idle
            period absorb a small burst without exceeding the average rate.
        time_func: Monotonic clock source, injectable for deterministic tests.
        sleep_func: Async sleep, injectable for deterministic tests.
    """

    def __init__(
        self,
        *,
        rate_per_second: float,
        capacity: float | None = None,
        time_func: Callable[[], float] = monotonic,
        sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self._rate = rate_per_second
        self._capacity = capacity if capacity is not None else max(1.0, rate_per_second)
        if self._capacity <= 0:
            raise ValueError("capacity must be positive")
        self._time = time_func
        self._sleep = sleep_func
        self._tokens = self._capacity
        self._updated_at = self._time()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until a token is available, then consume it."""
        async with self._lock:
            while True:
                now = self._time()
                elapsed = now - self._updated_at
                self._updated_at = now
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                if self._tokens >= 1.0 - _TOKEN_EPSILON:
                    self._tokens -= 1.0
                    return
                # Sleep exactly long enough to refill the missing fraction of a token.
                deficit = 1.0 - self._tokens
                await self._sleep(deficit / self._rate)
