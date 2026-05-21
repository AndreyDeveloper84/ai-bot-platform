# CLAUDE.md — agent operating notes for ai-bot-platform

> Read this before opening files. It captures the project rules that are NOT obvious from the code or git history.
> If something here contradicts an ADR or the active sprint plan, follow the more recent source — and update this doc.

---

## Architecture decision: ADR-0009 — Ayla split-domain architecture (Variant A)

**Status:** Locked 2026-05-20. Active.
**Source of truth:** [`docs/adr/ADR-0009-ayla-split-domain-architecture.md`](docs/adr/ADR-0009-ayla-split-domain-architecture.md)

ai-bot-platform is **one half of a two-repo backend.** Ayla djangoproject owns the canonical state of transactional domains (booking lifecycle, master schedule, services catalog, reviews, payments via YooKassa hold→capture→refund, user profile). ai-bot-platform owns the AI/observability/multi-tenant runtime — conversational memory, reminders, RFM/sentiment, catalog mirror, audit, analytics.

The two backends communicate via:

- **Synchronous:** REST. bot-platform reads Ayla via REST; if bot-platform needs to mutate Ayla state (e.g. cancel a booking on a user's instruction), it calls Ayla's REST API. bot-platform never writes Ayla's tables directly.
- **Asynchronous:** Ayla publishes domain events to bot-platform per the [event contract](docs/architecture/event-contract.md). 12 events at `event_version: 1` for MVP. HMAC-signed POST to `/api/v1/internal/events/ingest`. See `event-contract.md` for envelope, taxonomy, versioning, idempotency, delivery contract, PII rules, and failure modes.

### Hard rules (from ADR-0009, non-negotiable)

1. **No duplicate canonical state.** If Ayla owns it, bot-platform may cache or mirror — never own. Reverse also true.
2. **No direct cross-repo DB access.** Both backends only talk REST + events. No shared tables, no cross-repo `psycopg2`.
3. **No new MVP features merge until Phase 0 close criteria are met.** Allowed during freeze: bug fixes, infra migration, rebrand, event contract code, ADR/sprint docs, Sprint 1 EPICs (Track A). The Phase 0 sprint plan is at `docs/plans/2026-05-20-phase-0-sprint-plan.md`. If your work doesn't fit one of those allow-list categories, stop and confirm with tech lead before merging.
4. **bot-platform does NOT grow new transactional domains.** Any new transactional state belongs to Ayla.
5. **Transactional tools in bot-platform skills are REST wrappers.** bot-platform never DB-writes booking, payment, or catalog. If you're tempted to add such a write, you're about to violate the architecture — stop and confirm with tech lead.
6. **JWT `tenant_id` claim = `active_tenant_id`.** Verify the claimed relationship via `TenantUserRelationship`. For global AI memory requests (memory layer queries), `tenant_id` MAY be null — means «global user scope, not tenant-scoped».
7. **Every cross-service event has `event_version`. Consumers MUST be idempotent.** See `docs/architecture/event-contract.md` §4 + §5.

### Where to put new code

- **AI / conversational logic, skills, orchestration, memory, analytics, reminders** → here, in `apps/*` per existing twenty-app layout.
- **Booking, schedule, catalog, payment, review, user profile lifecycle** → Ayla djangoproject. If a ticket suggests adding such code here, re-route to Ayla. Confirm with tech lead if scope is ambiguous.

---

## Three-repo context

| Repo | Role |
|---|---|
| `ai-bot-platform/` (this repo) | AI / observability / multi-tenant runtime. Consumer of Ayla domain events. |
| Ayla djangoproject | Canonical SoR for transactional domains. Publisher of domain events. |
| `ayla-ai-core/` | Shared AI library — v0.8.1 → v1.0 freeze per ADR-0009. Pinned via `git+ssh@vX.Y.Z` in both consumers. Unchanged for Phase 0. |

ADR-0009 lives in all three repos for cross-team visibility (#412 + #413 + #414).

---

## Active sprint context

Phase 0 is running with parallel agent streams (Alpha/Beta/Gamma) per `docs/plans/2026-05-20-phase-0-parallel-agent-runbook.md`. Each stream has anti-touch lists; editing files outside your stream's roots is forbidden without explicit tech-lead authorization.

**Workflow regulation:** every ticket follows the 10-phase regimen in `docs/plans/2026-05-21-developer-agent-workflow.md` — Phase A (re-orient) through Phase J (closure). Skip a phase only with tech-lead approval recorded in your stream window.

---

## Reading order for a new agent

1. This file (you're already here).
2. `docs/adr/ADR-0009-ayla-split-domain-architecture.md` — the architecture decision that everything else depends on.
3. `docs/architecture/event-contract.md` — the cross-service event taxonomy + envelope (spec for 11 implementation tickets in Bucket 7).
4. `docs/plans/2026-05-20-phase-0-sprint-plan.md` — current sprint plan with bucket-by-bucket scope.
5. `docs/plans/2026-05-20-phase-0-parallel-agent-runbook.md` — your stream's anti-touch list + sync handshakes.
6. `docs/plans/2026-05-21-developer-agent-workflow.md` — the universal 10-phase regimen.
7. `docs/architecture.md` — the older Phase 0 design doc (still authoritative for in-repo architecture; read §2 in light of ADR-0009 refinement).

---

## Conventions you'll encounter

- **Branches:** `phase0/<stream>/<NN>-<slug>` for Phase 0 work; `feat/`, `fix/`, `docs/`, `chore/` for non-Phase-0.
- **PRs:** target `dev`, never `main`. Body includes acceptance checklist mirrored from the GH issue, test plan, out-of-scope notes, and `Closes #NNN.`
- **Commits:** Conventional Commits (`feat`, `fix`, `docs`, `refactor`, `chore`, `test`, `build`, `ci`). Phase 0 work prefixes the type with `[phase0/<stream>]`.
- **Selective staging:** never `git add .` or `git add -A` in this repo — parallel agents have WIP in the working tree. Name files explicitly.
- **Pre-commit hooks:** never bypass with `--no-verify` unless tech lead explicitly authorizes.
- **Detached-HEAD push pattern** (for parallel-agent safety): `git push origin HEAD:refs/heads/<branch>` works even when local branch state is messy.
- **Code Reviewer agent** is mandatory on every PR diff (memory `feedback_pr_workflow_code_reviewer`).

---

## When in doubt

Ask the tech lead before assuming. The architecture has tight rules and the cost of an unauthorized boundary crossing (e.g. writing a booking row in bot-platform) is higher than the cost of a clarifying message.

---

## Last verified

2026-05-21 — created as part of #412 (ADR-0009 docs in ai-bot-platform).
