# Phase 0 — Stream Gamma startup prompt

> **Purpose:** Copy this entire file into a new Claude Code (or Codex) window to start the Gamma agent.
> **Stream:** Gamma — ai-bot-platform contracts + events + refactors.
> **Repo working dir:** `C:\Users\user\PycharmProjects\ai-bot-platform`
> **Companion docs:** `2026-05-20-phase-0-parallel-agent-runbook.md`, `2026-05-20-phase-0-sprint-plan.md`, `docs/adr/ADR-0009-ayla-split-domain-architecture.md`.

---

```
=== STREAM GAMMA · ai-bot-platform contracts + events + refactors ===

You are Stream Gamma for Phase 0 of the Ayla project. You work in
parallel with Alpha (Ayla djangoproject) and Beta (cross-repo docs +
frontAyla + DNS). Your stream owns ai-bot-platform contract/event work,
the apps/orders refactor, the API gateway, and JWT verification.

# Working directory + repo
- Repo: C:\Users\user\PycharmProjects\ai-bot-platform
- Sub-directories you OWN:
  - `apps/eventbus/` (all of it, including `apps/eventbus/consumers/`)
  - `apps/orders/` (refactor to display-only)
  - `apps/integrations/yookassa/` (remove)
  - `config/urls.py` (selective edits for webhook removal)
  - `apps/tenancy/middleware.py` or `apps/identity/middleware.py`
    (whichever is JWT verification entry point)
  - `tests/contracts/` (new directory you create)
  - infra/Nginx config for API gateway
- You do NOT own: Ayla repo, ayla-ai-core repo, frontAyla, any docs/
  outside inline code-block usage.

# Read these documents first
1. C:\Users\user\PycharmProjects\ai-bot-platform\docs\adr\ADR-0009-ayla-split-domain-architecture.md
   (Especially §Mandatory event contract + §Hard rules #5, #6, #7.)
2. C:\Users\user\PycharmProjects\ai-bot-platform\docs\plans\2026-05-20-phase-0-parallel-agent-runbook.md
   (Stream Gamma section + Sync points 2, 3, 4, 5.)
3. C:\Users\user\PycharmProjects\ai-bot-platform\docs\plans\2026-05-20-phase-0-sprint-plan.md
   (Buckets 6, 7, 8, 9. Bucket 7 has 12 issues — most of your work.)

# Your tickets (12 total — see runbook §Stream Gamma)

# Week 1 order
Most of your work is BLOCKED by Beta's #441 (event-contract.md doc).
Until Beta announces "Sync 4 complete", you work on independent tickets:

1. **#427** `apps/orders` → display-only. Remove YooKassa lifecycle.
   Keep `OrderView`, `PaymentStatusView`, `PaymentLinkMessage`. Skills
   that triggered checkout now call Ayla `POST /api/v1/payments/create`
   and render returned link. INDEPENDENT — start here.
2. **#432 scaffold** (do NOT finish — wait for Beta #441). You may
   create the URL route `POST /api/v1/internal/events/ingest`, write a
   stub handler returning 501 Not Implemented, and add HMAC middleware
   placeholder. Full handler routing waits for #441 spec.

Once Beta announces #441 has merged (Sync 4), proceed Week 2 plan.

# Week 2 + 3 (preview)
- Week 2: finish #432 (now you have the spec), start #442 booking
  consumer.
- Week 3: #443-#446 remaining consumers, #434 API gateway (coordinate
  with Beta DNS via Sync 5), #435 JWT verification (after Beta lands
  jwt-contract.md via Sync 3), #428 YooKassa webhook URL removal in
  config/urls.py (Sync 2 — coordinate with Alpha).
- Week 4: #447 idempotency contract test, #433 umbrella close.

# Branch + commit conventions
- Branch: `phase0/gamma/<gh-number>-<slug>`.
- Commit prefix: `[phase0/gamma] <type>: <subject>` —
  `feat(events)`, `refactor(orders)`, `feat(gateway)`, `feat(auth)`,
  `test(contracts)`, etc.
- PR target: `dev`.
- Reference GH issue + ADR-0009 in PR body.
- Code Reviewer agent on every PR diff.
- Detached HEAD pattern (memory `feedback_parallel_agent_branch_race`).

# Anti-touch list — DO NOT EDIT
- Any directory under `C:\Users\user\PycharmProjects\Ayla\` —
  Alpha or Beta.
- Any directory under `C:\Users\user\PycharmProjects\ayla-ai-core\` —
  Beta.
- `docs/` in ai-bot-platform — Beta owns for Phase 0. You may add
  inline `apps/eventbus/README.md`-style local docs only.
- Sprint 1 Track A files: `apps/identity/UserPersonalContext` (#228),
  `apps/identity/MemoryEntry` (#229), `apps/identity/RedZoneAccessLog`
  (#230), `apps/tenancy/TenantUserRelationship` (#246),
  `apps/persona/*` (DRF-242.x related), `apps/handoff/Emergency*`
  (#238-#244), `apps/identity/AnonymousSession` (#255-#262) — those
  belong to existing Sprint 1 EPIC owners.

Exception: your event consumers MAY READ from `ClientProfile` and update
non-Sprint-1 fields (RFM rollups). If you need NEW fields on
ClientProfile, STOP and discuss — that's Sprint 1 Track A territory.

# Hard rules
- **Phase 0 freeze.**
- **No duplicate canonical state.** Your apps/orders work REMOVES bot
  ownership of payment state — moving it canonically to Ayla. Same
  spirit for all your refactors.
- **Transactional tools are REST wrappers** (ADR-0009 Hard rule #5):
  any skill/tool that touches booking/payment/catalog MUST call Ayla
  REST. NO direct DB writes from bot-platform.
- **JWT `tenant_id` claim is `active_tenant_id`** (Hard rule #6): your
  JWT middleware verifies `TenantUserRelationship(user_id, tenant_id)`
  on every tenant-scoped request. For global memory requests,
  `tenant_id` may be null.
- **Every event consumer MUST be idempotent** (Hard rule #7): dedupe by
  `event_id` at ingest, plus per-handler upsert-shape side-effects.

# Sync points where you depend on others
- **Sync 4 (Beta's #441 event-contract.md):** waits for Beta. Blocks
  #432 completion + #442-#447 consumers + idempotency test.
- **Sync 3 (Beta's jwt-contract.md):** waits for Beta. Blocks #435 JWT
  verification.
- **Sync 2 (YooKassa webhook switch):** Week 3. Alpha builds receiver
  in Ayla; you remove `/api/v1/yookassa/webhook` from config/urls.py +
  handler. Human (Andrey) flips YooKassa dashboard. Coordinate timing.
- **Sync 5 (API gateway routes #434):** coordinate with Beta on DNS for
  `api.ayla.app`. You write Nginx config.

# Communication protocol
End-of-day status post in this window. Plus:
- Wait for Beta's "Sync 4: event-contract.md landed" — acknowledge in
  this window, then resume #432 + start consumer work.
- Wait for Beta's "Sync 3: jwt-contract.md landed" — acknowledge, start
  #435.
- Before #428 webhook removal: post "Ready to remove YooKassa endpoint
  in bot-platform — Alpha receiver confirmed live" and wait for human
  to flip dashboard.

# First action right now
1. `cd C:\Users\user\PycharmProjects\ai-bot-platform`
2. `git fetch origin && git checkout dev && git pull`
3. Read the three docs above.
4. Open `https://github.com/AndreyDeveloper84/ai-bot-platform/issues/427`
   and read its body.
5. Confirm by posting: "Stream Gamma ready. Starting #427 on branch
   phase0/gamma/427-orders-display-only. Will scaffold #432 in parallel,
   blocked on Beta #441 for completion."
6. Begin #427.

Phase 0 freeze allows your apps/orders refactor — it REMOVES bot
ownership of payment state, doesn't add it. That's a positive constraint
move, not a new feature.
```
