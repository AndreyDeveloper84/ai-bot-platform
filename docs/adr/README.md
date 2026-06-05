# Architecture Decision Records

ADRs capture *why* the platform is built the way it is. They are immutable: once accepted, a decision is amended by a *new* ADR (with `Supersedes ADR-XXXX`) rather than edited in place.

Read these before changing anything that contradicts them.

## Index (chronological)

| # | Title | Status | Date | Slug |
|---|---|---|---|---|
| 0001 | Multi-tenant-ready architecture from day one | Accepted | 2026-05-07 | [`ADR-0001-multi-tenant-ready.md`](ADR-0001-multi-tenant-ready.md) |
| 0002 | Three-repo split — `mysite/`, `ayla-ai-core/`, `ai-bot-platform/` | Accepted | 2026-05-07 | [`ADR-0002-three-repo-split.md`](ADR-0002-three-repo-split.md) |
| 0003 | tenant_id propagation via TenantContext (ContextVar) | Accepted | 2026-05-07 | [`ADR-0003-tenant-context-via-contextvar.md`](ADR-0003-tenant-context-via-contextvar.md) |
| 0004 | Stack — PostgreSQL + Redis + chromadb + S3 | Accepted | 2026-05-07 | [`ADR-0004-stack-postgres-redis-chromadb-s3.md`](ADR-0004-stack-postgres-redis-chromadb-s3.md) |
| 0005 | Multi-LLM provider routing from Sprint 6 | Accepted | 2026-05-07 | [`ADR-0005-multi-llm-provider-routing.md`](ADR-0005-multi-llm-provider-routing.md) |
| 0006 | Field-level encryption via django-cryptography-django5 | Accepted | 2026-05-09 | [`ADR-0006-field-level-encryption.md`](ADR-0006-field-level-encryption.md) |
| 0007 | Conversation State enum — minimal-first | Accepted | 2026-05-11 | [`ADR-0007-conversation-state-enum.md`](ADR-0007-conversation-state-enum.md) |
| 0008 | Role detection foundation — TenantStaff + CatalogMaster split | Accepted | 2026-05-19 | [`ADR-0008-role-detection-and-staff-model.md`](ADR-0008-role-detection-and-staff-model.md) |

## Source

ADRs 0001–0006 originate from `mysite/docs/arch/PHASE0_DESIGN.md` v2 §10. Each was lifted out and polished into its own file for diff trackability. Two ADRs (0002, 0006) include Sprint 0 corrections relative to the source draft — see the relevant ADR for details.

## Format

Every ADR follows the same template:

```markdown
# ADR-NNNN: Title
**Status:** Accepted | Superseded by ADR-XXXX | Deprecated — YYYY-MM-DD
**Context.** Why we needed to decide.
**Decision.** What we chose.
**Consequences.** Easier / Harder / Acceptable.
**Alternatives considered.** What we rejected and why.
```

Each ADR is ≤ 400 words by design — if a decision needs more, it probably contains two decisions and should be split.
