# Scalability — Code Review Agent

How this system holds up at 100×, 1000×, and 10000× current load. Be honest
where it breaks.

## Current capacity (single replica, default config)

- **Throughput**: ~50 req/min (LLM-bound on Claude p95 ~3-5s)
- **Per-request cost**: roughly $0.005-0.040 depending on the project
- **Memory**: < 500 MB without local ML models; ~1.5 GB with sentence-transformers
- **CPU**: irrelevant — bottleneck is network I/O to Claude/Voyage/Cohere

## 100× users (5K req/min)

What works:
- FastAPI + uvicorn handle this fine with `--workers 4` on a 4-core box.
- Postgres+pgvector at 50K rows + IVFFLAT index handles >1000 QPS without tuning.
- Per-session state is small (~10 KB) — Redis fits this with default config.

What needs attention:
- Claude rate limits become the bottleneck (Anthropic default is ~5 req/s per
  account). Solution: contact Anthropic for tier increase OR add a request
  queue with prioritization.
- Synthesis tokens dominate cost. Consider per-tenant budgets to prevent
  runaway burn.

## 1000× users (50K req/min)

What needs to change:
- **Separate read replica for Postgres**. The IVFFLAT index becomes a hot
  spot. Add a HNSW index for higher recall at higher QPS.
- **Embedding cache by query hash** in Redis. The same query embedded twice
  is wasted spend on Voyage.
- **Pre-warm Qdrant** to fit hot-collection in memory.
- **Move synthesis off the request path** for non-interactive use cases —
  stream events instead.

## 10000× users (500K req/min)

This is the territory where a single backend doesn't work anymore:
- Horizontal shard by tenant or by document corpus.
- Async LLM execution via a queue (Celery + Redis or SQS + Lambda).
- Eval moves out of the API process into a scheduled job.
- Consider self-hosting a smaller fine-tuned LLM for the high-volume,
  low-complexity intent classification step.

## What we'd never auto-scale

The eval harness is single-tenant by design. It runs against a stable
fixture so metrics are comparable across runs. Scaling the eval is solved by
running more *kinds* of evals (adversarial, multi-language, longer-context),
not by parallelism.
