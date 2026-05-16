# Technical decisions — Code Review Agent

This document records the non-obvious choices and what alternative was rejected.
A line gets added here every time the system gains a knob a future maintainer
might second-guess.

## Why Pydantic v2 over `attrs` or `dataclasses`

Pydantic validates at the I/O boundary (HTTP / LLM JSON output / DB rows) and
its v2 rewrite is fast enough that we don't pay a runtime cost. Plain
`dataclasses` would force us to write the same validators by hand; `attrs`
matches Pydantic v2 on ergonomics but isn't the default in FastAPI.

## Why LangGraph over LangChain Chains

The agent flow has explicit branches (route after intent classification,
reflection loops with caps, parallel/sequential rounds). LangGraph's
`StateGraph` lets us see those transitions in code; LangChain `Chain`s hide
them inside `__call__` overloads, which makes debugging the wrong path
painful in production.

## Why a free-tier fallback for every paid API

Every external API has been wrapped behind a `Protocol` with a
deterministic-but-realistic offline implementation. Three benefits:
1. Tests run in CI without burning real credits.
2. Demo on a new machine works without 6 signups.
3. When a paid API rate-limits us in prod, the fallback is the failover —
   not a 500 response.

## Why we don't auto-tune anything in production

When the eval drift detector flags a metric drop, the system **proposes** a
parameter change with simulated impact (against the gold set) — it does not
auto-apply. The applier is the human or, in the prod loop, an A/B canary on
10% traffic that auto-rolls-back on regression. Silent auto-tuning at 3am is
how RAG degrades for three weeks before anyone notices.

## Why a 70% coverage floor (not 90%)

90% pushes time into mocking I/O thoroughly enough to write the mock, which
is wasted effort compared to writing more integration-level eval cases. We
front-load eval coverage instead. The 70% floor is enough to catch the
"someone deleted a private helper that secretly was used elsewhere" class of
bug.
