"""In-memory LRU cache of review responses keyed by the diff's SHA-256."""

from __future__ import annotations

import hashlib
from collections import OrderedDict

from src.api.schemas import ReviewResponse


def cache_key(diff_text: str, mode: str, max_comments: int) -> str:
    """Stable key: hash of the diff plus the parameters that change the output."""
    digest = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    return f"{mode}:{max_comments}:{digest}"


class ReviewCache:
    """Bounded LRU cache; ``maxsize=0`` disables caching."""

    def __init__(self, maxsize: int = 256) -> None:
        self.maxsize = maxsize
        self._data: OrderedDict[str, ReviewResponse] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> ReviewResponse | None:
        """Return the cached response and mark it as recently used."""
        if key in self._data:
            self._data.move_to_end(key)
            self.hits += 1
            return self._data[key]
        self.misses += 1
        return None

    def put(self, key: str, value: ReviewResponse) -> None:
        """Store ``value``, evicting the least recently used entry when full."""
        if self.maxsize <= 0:
            return
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)

    def clear(self) -> None:
        """Drop every entry and reset counters."""
        self._data.clear()
        self.hits = self.misses = 0
