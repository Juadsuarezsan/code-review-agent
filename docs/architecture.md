# Architecture — Code Review Agent

## Summary

Unified-diff parsing + 10+ static rules + Claude reviewer (JSON schema) + test-gap analyzer. Posts inline comments to GitHub PRs.

## High-level data flow

```
              HTTP request
                   │
                   ▼
        ┌──────────────────────┐
        │   FastAPI (src/api)  │
        │   Pydantic schemas   │
        └──────────┬───────────┘
                   │
                   ▼
        ┌──────────────────────┐
        │  Core domain logic   │
        │  (src/agents | src/  │
        │   engine | etc.)     │
        └──────────┬───────────┘
                   │
        ┌──────────┴──────────┐
        ▼                     ▼
   ┌──────────┐         ┌──────────────┐
   │ External │         │ Eval harness │
   │   APIs   │         │ (src/eval)   │
   │ (Claude, │         │              │
   │  Voyage, │         │ Synthetic    │
   │  etc.)   │         │ gold set     │
   └──────────┘         └──────────────┘
```

## Boundary separation

The codebase separates four concerns explicitly, mapping one folder per concern:

- **`src/api/`** — HTTP transport. Pydantic schemas, route handlers, exception → HTTP
  mapping. Nothing in here knows about Claude / databases / embeddings; it only
  knows how to call the domain layer.
- **`src/<domain>/`** (agents, retrieval, extractors, etc.) — Pure domain logic.
  Takes typed inputs, returns typed outputs. Has no FastAPI imports.
- **`src/config.py`** — Single source of truth for env-driven configuration.
  Read via `get_settings()` (lru-cached). Tests call `get_settings.cache_clear()`
  when they mutate env vars.
- **`src/eval/`** — Reads its own fixtures from `data/eval/`, runs the domain
  layer in isolation, writes a structured report. Never used by the API at
  request time (except as the `/api/eval/run` convenience endpoint which just
  forwards).

## External dependencies

| Service | Required for | Free fallback |
|---|---|---|
| Anthropic Claude API | Real LLM reasoning | Deterministic heuristic / template |
| Voyage AI embeddings | Production retrieval quality | `sentence-transformers/all-MiniLM-L6-v2` |
| Cohere Rerank v3 | Top-k re-ordering precision | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| Postgres + pgvector | Persistent state + vector search | In-memory store |

When the relevant API key is missing, the project picks the free fallback and
keeps running. No code path raises on missing optional credentials.

## What lives where

- **Production secrets** → `.env` (gitignored)
- **Domain logic** → `src/`
- **Test data + fixtures** → `data/eval/`
- **Generated outputs** → `data/processed/` (gitignored; reproducible from `scripts/`)
- **Specs (source of truth)** → `/docs/portafolio_specs.md` at repo root
