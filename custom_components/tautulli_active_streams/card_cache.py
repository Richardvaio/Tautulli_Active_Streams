"""Small demand-driven caches for card API data."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any


@dataclass(slots=True)
class _CacheItem:
    value: Any
    stored_at: float


class CardDataCache:
    """TTL/LRU cache that coalesces identical in-flight Tautulli requests."""

    def __init__(self, max_entries: int = 64) -> None:
        self._max_entries = max(1, max_entries)
        self._items: OrderedDict[str, _CacheItem] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_or_fetch(
        self,
        key: str,
        ttl: float,
        fetch: Callable[[], Awaitable[Any]],
    ) -> tuple[Any, bool]:
        """Return data and whether stale cached data was used after a failure."""
        cached = self._items.get(key)
        if cached and monotonic() - cached.stored_at < ttl:
            self._items.move_to_end(key)
            return cached.value, False

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._items.get(key)
            if cached and monotonic() - cached.stored_at < ttl:
                self._items.move_to_end(key)
                return cached.value, False
            try:
                value = await fetch()
            except Exception:
                if cached:
                    self._items.move_to_end(key)
                    return cached.value, True
                raise
            self._items[key] = _CacheItem(value=value, stored_at=monotonic())
            self._items.move_to_end(key)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)
            return value, False
