"""
Single-flight request coalescer.

Concurrent identical requests (same cache key) share one upstream call
instead of each making their own. Uses asyncio.Task so only one fetch
runs while others await its result.
"""

import asyncio
from typing import Any, Callable, Awaitable


class Coalescer:
    def __init__(self):
        self._in_flight: dict[str, asyncio.Task] = {}

    async def do(self, key: str, fn: Callable[[], Awaitable[Any]]) -> Any:
        """
        If `key` is already in-flight, await the existing task.
        Otherwise, create a new task for `fn()` and share it.
        """
        if key in self._in_flight:
            return await self._in_flight[key]

        task = asyncio.create_task(self._run(key, fn))
        self._in_flight[key] = task
        try:
            return await task
        finally:
            self._in_flight.pop(key, None)

    async def _run(self, key: str, fn: Callable[[], Awaitable[Any]]) -> Any:
        try:
            return await fn()
        except Exception:
            # Remove from in-flight so retries can proceed
            self._in_flight.pop(key, None)
            raise

    @property
    def in_flight_count(self) -> int:
        return len(self._in_flight)
