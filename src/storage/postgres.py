"""Optional PostgreSQL persistence of review history using a ``psycopg`` async pool.

The repository is only constructed when ``DATABASE_URL`` is set. ``psycopg_pool``
is imported lazily so the API starts without the driver being importable.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from loguru import logger

from src.api.schemas import ReviewResponse

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS reviews (
    id           BIGSERIAL PRIMARY KEY,
    trace_id     TEXT NOT NULL,
    diff_sha256  TEXT NOT NULL,
    mode         TEXT NOT NULL,
    n_comments   INTEGER NOT NULL,
    cost_usd     NUMERIC(10, 6) NOT NULL DEFAULT 0,
    latency_ms   INTEGER NOT NULL,
    payload      JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS reviews_created_at_idx ON reviews (created_at DESC);
"""

INSERT_SQL = """
INSERT INTO reviews (trace_id, diff_sha256, mode, n_comments, cost_usd, latency_ms, payload)
VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
RETURNING id
"""

RECENT_SQL = """
SELECT trace_id, mode, n_comments, cost_usd, latency_ms, created_at
FROM reviews ORDER BY created_at DESC LIMIT %s
"""


class _Cursor(Protocol):
    async def execute(self, query: str, params: Any = None) -> Any: ...

    async def fetchone(self) -> Any: ...

    async def fetchall(self) -> Any: ...


class _Connection(Protocol):
    def cursor(self) -> Any: ...


class ConnectionPool(Protocol):
    """Subset of ``psycopg_pool.AsyncConnectionPool`` used here (mockable in tests)."""

    def connection(self) -> Any: ...

    async def open(self) -> None: ...

    async def close(self) -> None: ...


class ReviewRepository:
    """Store and list reviews. Construct with :meth:`from_url` or inject a pool."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    @classmethod
    def from_url(cls, database_url: str, min_size: int = 1, max_size: int = 5) -> ReviewRepository:
        """Create a repository backed by an ``AsyncConnectionPool`` (not opened yet)."""
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(database_url, min_size=min_size, max_size=max_size, open=False)
        return cls(pool)

    async def open(self) -> None:
        """Open the pool and create the schema if needed."""
        await self._pool.open()
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(SCHEMA_SQL)
        logger.info("review repository ready")

    async def close(self) -> None:
        """Close the pool."""
        await self._pool.close()

    async def save(self, response: ReviewResponse, diff_sha256: str) -> int:
        """Insert one review and return its row id."""
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    INSERT_SQL,
                    (
                        response.trace_id,
                        diff_sha256,
                        response.mode,
                        len(response.comments),
                        response.cost_usd,
                        response.latency_ms,
                        json.dumps(response.model_dump()),
                    ),
                )
                row = await cur.fetchone()
        return int(row[0])

    async def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the most recent reviews (for the observability dashboard)."""
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(RECENT_SQL, (limit,))
                rows = await cur.fetchall()
        keys = ("trace_id", "mode", "n_comments", "cost_usd", "latency_ms", "created_at")
        return [dict(zip(keys, row, strict=True)) for row in rows]
