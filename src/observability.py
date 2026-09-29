"""Structured logging and per-request metrics (trace_id, latency, tokens, cost).

Every request carries a ``trace_id`` stored in a :class:`contextvars.ContextVar`
so that log lines emitted by graph nodes are correlated without threading the
identifier through every signature. :class:`RequestMetrics` accumulates LLM usage
and per-node timings; :func:`node_span` logs the entry and exit state of a node.
"""

from __future__ import annotations

import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from loguru import Record

from src.llm.client import LLMResult

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")


def new_trace_id() -> str:
    """Return a fresh 32-hex-char trace identifier."""
    return uuid.uuid4().hex


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    """Configure the process-wide loguru sink.

    Args:
        level: Minimum level (DEBUG, INFO, WARNING, ERROR).
        json_logs: Emit one JSON object per line (for log shippers) instead of text.
    """
    logger.remove()
    logger.configure(patcher=_inject_trace_id)
    fmt = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | trace={extra[trace_id]} | "
        "{name}:{function}:{line} - {message}"
    )
    logger.add(sys.stderr, level=level.upper(), serialize=json_logs, format=fmt)


def _inject_trace_id(record: Record) -> None:
    record["extra"].setdefault("trace_id", trace_id_var.get())


@dataclass
class NodeTiming:
    """Timing and comment counts for one graph node."""

    node: str
    latency_ms: int
    comments_in: int
    comments_out: int


@dataclass
class RequestMetrics:
    """Accumulated cost and latency for one review request."""

    trace_id: str = field(default_factory=new_trace_id)
    started_at: float = field(default_factory=time.perf_counter)
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    node_timings: list[NodeTiming] = field(default_factory=list)

    def add_llm(self, result: LLMResult) -> None:
        """Fold one LLM call into the totals."""
        self.llm_calls += 1
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        self.cost_usd = round(self.cost_usd + result.cost_usd, 6)

    @property
    def latency_ms(self) -> int:
        """Wall-clock milliseconds since the metrics object was created."""
        return int((time.perf_counter() - self.started_at) * 1000)

    def as_dict(self) -> dict[str, Any]:
        """Serialize for logging or API responses."""
        data = asdict(self)
        data.pop("started_at", None)
        data["latency_ms"] = self.latency_ms
        return data

    def log_summary(self) -> None:
        """Emit one INFO line with the request totals."""
        logger.bind(trace_id=self.trace_id).info(
            "request done latency_ms={} llm_calls={} in_tokens={} out_tokens={} cost_usd={}",
            self.latency_ms,
            self.llm_calls,
            self.input_tokens,
            self.output_tokens,
            self.cost_usd,
        )


@contextmanager
def node_span(metrics: RequestMetrics, node: str, comments_in: int) -> Iterator[dict[str, int]]:
    """Log entry/exit of a graph node and record its timing.

    The yielded dict has a ``comments_out`` key the caller sets before leaving the
    block so the exit log and :class:`NodeTiming` carry the output size.
    """
    log = logger.bind(trace_id=metrics.trace_id)
    log.info("node enter name={} comments_in={}", node, comments_in)
    t0 = time.perf_counter()
    state = {"comments_out": 0}
    try:
        yield state
    except Exception as exc:
        log.exception("node failed name={} error={}", node, exc)
        raise
    finally:
        latency = int((time.perf_counter() - t0) * 1000)
        metrics.node_timings.append(
            NodeTiming(
                node=node,
                latency_ms=latency,
                comments_in=comments_in,
                comments_out=state["comments_out"],
            )
        )
        log.info(
            "node exit name={} comments_out={} latency_ms={}",
            node,
            state["comments_out"],
            latency,
        )
