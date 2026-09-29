"""Cache and PostgreSQL repository tests (fake pool, no database)."""

from typing import Any

from src.api.schemas import ReviewResponse
from src.storage.cache import ReviewCache, cache_key
from src.storage.postgres import INSERT_SQL, SCHEMA_SQL, ReviewRepository


def _resp(trace: str) -> ReviewResponse:
    return ReviewResponse(trace_id=trace, mode="linter-only")


def test_cache_key_is_stable_and_parameterised() -> None:
    assert cache_key("d", "linter-only", 10) == cache_key("d", "linter-only", 10)
    assert cache_key("d", "linter-only", 10) != cache_key("d", "pipeline", 10)
    assert cache_key("d", "linter-only", 10) != cache_key("d", "linter-only", 5)


def test_cache_lru_eviction_and_counters() -> None:
    cache = ReviewCache(maxsize=2)
    cache.put("a", _resp("a"))
    cache.put("b", _resp("b"))
    assert cache.get("a") is not None  # a becomes most recent
    cache.put("c", _resp("c"))  # evicts b
    assert cache.get("b") is None and cache.get("c") is not None
    assert (cache.hits, cache.misses, len(cache)) == (2, 1, 2)
    cache.clear()
    assert len(cache) == 0 and cache.hits == 0


def test_cache_disabled() -> None:
    cache = ReviewCache(maxsize=0)
    cache.put("a", _resp("a"))
    assert cache.get("a") is None


class _FakeCursor:
    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self.log = log

    async def __aenter__(self) -> "_FakeCursor":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, query: str, params: Any = None) -> None:
        self.log.append((query, params))

    async def fetchone(self) -> tuple[int]:
        return (42,)

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return [("t1", "linter-only", 2, 0.0, 12, "2026-01-01")]


class _FakeConn:
    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self.log = log

    async def __aenter__(self) -> "_FakeConn":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.log)


class FakePool:
    def __init__(self) -> None:
        self.log: list[tuple[str, Any]] = []
        self.opened = False
        self.closed = False

    def connection(self) -> _FakeConn:
        return _FakeConn(self.log)

    async def open(self) -> None:
        self.opened = True

    async def close(self) -> None:
        self.closed = True


async def test_repository_lifecycle_save_and_recent() -> None:
    pool = FakePool()
    repo = ReviewRepository(pool)
    await repo.open()
    assert pool.opened and pool.log[0][0] == SCHEMA_SQL
    row_id = await repo.save(_resp("t1"), "sha")
    assert row_id == 42
    query, params = pool.log[-1]
    assert query == INSERT_SQL and params[0] == "t1" and params[1] == "sha"
    rows = await repo.recent(5)
    assert rows[0]["trace_id"] == "t1" and rows[0]["latency_ms"] == 12
    assert pool.log[-1][1] == (5,)
    await repo.close()
    assert pool.closed


def test_from_url_builds_unopened_pool() -> None:
    repo = ReviewRepository.from_url("postgresql://u:p@localhost:5432/db")
    assert repo is not None
