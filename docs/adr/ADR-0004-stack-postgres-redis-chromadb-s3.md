# ADR-0004: Stack — PostgreSQL + Redis + chromadb + S3-compatible storage

**Status:** Accepted — 2026-05-07

## Context

The platform needs four storage primitives:

1. **OLTP** for transactional facts (tenants, conversations, messages, bookings).
2. **Fast session/state** for sticky bucketing, idempotency keys, live prompt reload.
3. **Vector search** for RAG over the FAQ knowledge base.
4. **Binary blob storage** for food images, voice snippets, replay artefacts.

Team is 2–3 people; ops capacity is limited. Each new piece of infrastructure costs ongoing time, so we biased hard towards "few moving parts that ops already knows".

## Decision

- **PostgreSQL 16** for OLTP. JSONB columns for flexible payloads. Already used in `mysite/`; ops familiarity is high.
- **Redis 7** for sessions, Streams (the worker job bus), pub/sub (live prompt reload via `apps.promptreg`), and caching.
- **chromadb 0.5.x** for vectors. Per-tenant collections. Embeddings via OpenAI `text-embedding-3-small`.
- **S3-compatible storage** (MinIO in dev, Yandex Object Storage or Selectel in prod) for replay artefacts and food images.

The full stack runs locally via `docker-compose.yml` (DRF-404). Healthchecks bring it up in <60s.

## Consequences

- **Easier:** `make up` on a fresh laptop = full stack in a minute.
- **Easier:** ops already knows Postgres, Redis, MinIO from `mysite/`. chromadb is the only new tool.
- **Acceptable:** chromadb is single-node — adequate at <10M chunks (5–10 years at current FAQ growth). Reconsider in Phase 2.
- **Acceptable:** S3-compatible means we can swap MinIO ↔ Yandex ↔ Selectel without code change; only the bucket URL and credentials move.
- **Harder:** four backing services means four `docker compose ps` rows that must all stay healthy. Mitigated by the `[depends_on: condition: service_healthy]` chain in compose.

## Alternatives considered

- **pgvector inside Postgres.** Rejected. Yields one less moving part but couples vector search latency to Postgres CPU, and search quality is meaningfully behind chromadb's HNSW today. The existing `services/formulatela_mcp/` already uses chromadb — no migration cost.
- **Pinecone / Weaviate (cloud).** Rejected. RU geographic / data-residency constraints + DPA effort + cost.
- **Qdrant.** Considered. chromadb wins on simpler Python API and operational footprint already validated in `mysite/`.
- **Native filesystem for replay artefacts.** Rejected. Doesn't scale across worker hosts and complicates retention/lifecycle policies. S3-compatible is the standard answer.
