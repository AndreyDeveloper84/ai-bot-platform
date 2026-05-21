# Phase 0 — Parallel Agent Runbook

> **Status:** Active 2026-05-20
> **Purpose:** Distribute Phase 0 work across **3 parallel Claude/Codex agents** running in adjacent windows. The plan guarantees agents do not collide on file ownership, define explicit sync points, and give the supervising tech lead (Claude in main session) a way to watch progress.
> **Companion docs:** ADR-0009 (architecture), `2026-05-20-phase-0-sprint-plan.md` (full plan), `2026-05-20-ayla-consolidated-architecture.md` (rationale).
> **Phase 0 backlog:** 35 issues (#412–#447) + 4 edits applied 2026-05-20.

## Hard rules

1. **One agent = one stream = one branch.** Never check out a branch from another stream.
2. **File ownership boundary is non-negotiable.** Each stream has its own directory roots. If a ticket forces you to cross the boundary, STOP and ping the tech lead — never edit out-of-stream files even "just one line".
3. **Branch naming:** `phase0/<stream>/<ticket-id>-<slug>` — e.g. `phase0/alpha/421-docker-compose`, `phase0/gamma/432-events-ingest`.
4. **Commit prefix:** `[phase0/<stream>] <conventional commit type>: <subject>` — e.g. `[phase0/gamma] feat(events): /api/v1/internal/events/ingest endpoint`.
5. **No merges to main during Phase 0.** All work targets `dev` branch in each repo (memory `feedback_pr_base_branch`). PR title includes `[phase0/<stream>] #<ticket>`.
6. **Code Reviewer agent runs on every PR diff** (memory `feedback_pr_workflow_code_reviewer`) — applies to all three streams.
7. **Sprint 1 Track A (5 EPICs #219-#223) is NOT in this runbook.** Those tickets run independently with their existing owners. Phase 0 agents do not touch Sprint 1 Track A files (memory model, persona, emergency, provider migration, anonymous OAuth) unless explicitly handed off.

## Stream definitions

### Stream Alpha — Ayla djangoproject backend

**Goal:** Stabilize Ayla djangoproject infra + own Ayla side of contracts.

**Repo + roots owned:**
- `C:\Users\user\PycharmProjects\Ayla\djangoproject\` — primary
- Does NOT touch `Ayla\frontAyla\` (that's Beta).
- Does NOT touch `ai-bot-platform\` (that's Gamma).

**Tickets (12):**
| # | Title (short) | Notes |
|---|---|---|
| #420 | db.sqlite3 out of git | Fast (~30 min). Do first. |
| #421 | docker-compose Postgres+Redis+MinIO | Foundation for everything else in Alpha. |
| #422 | settings/dev.py + settings/test.py | After #421. |
| #423 | Celery worker+beat in compose | After #421-422. |
| #424 | SQLite → Postgres data migration | After #421-423. |
| #425 | Outbox worker enabled + integration test | After #424. |
| #426 | Payment refactor: appointments → payments | After #424. Two-step migration; high risk. |
| #428 | YooKassa webhook receive in Ayla (+ switch dashboard) | Coordinate with Gamma — see Sync 2. |
| #429 | Outbox publisher: booking.* | Blocked by #441 (Beta). |
| #430 | Outbox publisher: payment.* | Blocked by #441. |
| #431 | Outbox publisher: service/master/review/user.profile | Blocked by #441. |
| #435 | JWT issuance side (Ayla) | Blocked by jwt-contract.md (Beta). See Sync 3. |
| #439 | booking_source on ProviderProfile | After #424. |

**Branches:** `phase0/alpha/<#>-<slug>` in `Ayla` repo.

**Anti-touch list (do NOT edit these even if tempted):**
- `Ayla/frontAyla/` — Beta owns.
- `Ayla/djangoproject/docs/` — Beta owns (#413).
- `ai-bot-platform/` — Gamma owns.
- Sprint 1 Track A files in bot-platform (`apps/identity/`, `apps/tenancy/`, `apps/persona/`, etc.) — out of Phase 0.

### Stream Beta — Cross-repo docs + rebrand + frontAyla + DNS

**Goal:** Documentation, rebrand, DNS/secrets — work that touches multiple repos but no Django app code.

**Repo + roots owned:**
- `Ayla\frontAyla\` — primary (mobile monorepo)
- `*/docs/` directories in all 3 repos
- `*/README.md`, `*/CLAUDE.md` in all 3 repos
- DNS configuration (Cloudflare/registrar — outside repos)
- Nginx reverse proxy config (likely `infra/` or in deploy scripts)
- `.mcp.json` cleanup

**Tickets (10):**
| # | Title (short) | Notes |
|---|---|---|
| #412 | ADR-0009 → ai-bot-platform docs/adr/ | Already on disk. Just PR + merge. |
| #413 | ADR-0009 → Ayla djangoproject docs/architecture/ | Touch only docs in Ayla. |
| #414 | ADR-0009 → ayla-ai-core docs/ | Touch only docs in ayla-ai-core. |
| #415 | frontAyla `@beautygo/*` → `@ayla/*` | Heavy yarn workspace + import rewrite. |
| #416 | Bundle IDs `ru.ayla.client/pro` | After #415. Apple provisioning slow — start early. |
| #417 | DNS `dev.gobeauty.site` → `dev.ayla.app` | See Sync 1. |
| #418 | README + CLAUDE.md + service titles across 3 repos | Touch only docs. |
| #419 | `.mcp.json` plaintext → vault + .example | Across repos. |
| #436 | ADR-0010: LLM provider choice for Russian | Docs only. |
| #437 | ADR-0011: UserPersonalContext privacy policy | Docs only. Gates Sprint 1 Track A #228-230 merge. |
| #441 | event-contract.md taxonomy doc | **BLOCKER** for #429-433, #442-447. Do early week 1. |

**Branches:** `phase0/beta/<#>-<slug>` in respective repo.

**Anti-touch list:**
- Any `apps/*/models.py`, `views.py`, `serializers.py`, `urls.py`, `tasks.py` — those are Alpha or Gamma.
- `Ayla/djangoproject/` Python source — Alpha owns.
- `ai-bot-platform/apps/` — Gamma owns.

### Stream Gamma — ai-bot-platform contracts + refactors

**Goal:** Own bot-platform's side of split-domain — contracts, events ingest+consumers, orders refactor, gateway, JWT verify.

**Repo + roots owned:**
- `C:\Users\user\PycharmProjects\ai-bot-platform\` — primary, ALL `apps/eventbus/`, `apps/orders/`, `apps/eventbus/consumers/`, `tests/contracts/`, `config/urls.py`.

**Tickets (11):**
| # | Title (short) | Notes |
|---|---|---|
| #427 | apps/orders → display-only | Independent. Do early. |
| #428 | Remove `/api/v1/yookassa/webhook` from bot-platform urls.py | Coordinate with Alpha (Sync 2). |
| #432 | `/api/v1/internal/events/ingest` endpoint + HMAC + dedup | Blocked by #441 (Beta). |
| #433 | Umbrella for consumers | Closes when #442-446 close. |
| #434 | Nginx API gateway | Coordinate with Beta DNS (Sync 1). |
| #435 | JWT verification (bot-platform) | Blocked by jwt-contract.md (Beta) + paired with Alpha #435. See Sync 3. |
| #442 | Consumer: booking.* | After #432. |
| #443 | Consumer: payment.* | After #432 + #428. |
| #444 | Consumer: service.updated (catalog mirror) | After #432. |
| #445 | Consumer: master.schedule.updated + review.created | After #432. |
| #446 | Consumer: user.profile.updated | After #432. |
| #447 | Idempotency contract test | After at least one consumer ships. |

**Branches:** `phase0/gamma/<#>-<slug>` in `ai-bot-platform`.

**Anti-touch list:**
- `Ayla/` repo (anything) — Alpha or Beta.
- `docs/` in bot-platform — Beta owns for Phase 0 doc work; Gamma may write inline comments + edit `apps/eventbus/README.md`-like local docs.
- Sprint 1 Track A apps (`apps/identity/UserPersonalContext`, `apps/tenancy/TenantUserRelationship`, `apps/persona/`, etc.) — out of Phase 0. If a consumer needs to write into `ClientProfile`, that's read+update only on existing fields, not new field migrations.

## Sync points (mandatory coordination)

Sync points are moments where two streams must hand off or align. Tech lead in main window confirms each sync.

### Sync 1 — Env URL flip (`dev.gobeauty.site` → `dev.ayla.app`)
- **Driver:** Beta (#417).
- **Steps:**
  1. Beta sets up DNS + LE certs for `dev.ayla.app`.
  2. Beta installs 30-day 301 redirect from old domain.
  3. Beta announces in tech-lead window: "DNS live + redirect active".
  4. Alpha updates `Ayla/djangoproject/.env.example` + `settings/dev.py` `ALLOWED_HOSTS` + `CORS_ALLOWED_ORIGINS`.
  5. Gamma updates `ai-bot-platform` env files + adds `dev.ayla.app` to `ALLOWED_HOSTS`.
  6. Beta closes #417.
- **Failure mode:** If old domain still has live mobile builds talking to it, the 30-day 301 buys time. Do not turn off old domain before Phase 1.

### Sync 2 — YooKassa webhook switch (#428)
- **Driver:** Alpha owns receiver in Ayla; Gamma removes receiver in bot-platform; human (Andrey) flips YooKassa dashboard.
- **Steps:**
  1. Alpha implements `POST /api/v1/payments/webhook/` in Ayla djangoproject (signature verification, idempotent).
  2. Alpha announces: "Ayla payment webhook ready at staging URL X".
  3. Gamma adds feature flag `YOOKASSA_WEBHOOK_OWNER = "ayla"` in bot-platform and keeps old endpoint accepting (no-op) for safety window.
  4. Human flips YooKassa Personal Cabinet webhook URL to Ayla.
  5. Both teams watch logs for 24h to confirm Ayla receives, bot-platform receives nothing.
  6. Gamma removes `/api/v1/yookassa/webhook` URL + handler code.
  7. Alpha closes #428 (Ayla side); Gamma closes its sibling work.
- **Failure mode:** if both endpoints try to process same webhook, idempotency in both prevents double-charge but creates audit noise. Watch for it.

### Sync 3 — JWT contract (#435 both sides + Beta JWT doc)
- **Driver:** Beta writes `docs/architecture/jwt-contract.md` (separate from #441 event contract).
- **Steps:**
  1. Beta drafts JWT contract doc (issuer = Ayla, claims, verification flow, anonymous shape, TenantUserRelationship verification rule). Lands in `Ayla/djangoproject/docs/architecture/jwt-contract.md` AND mirrored in `ai-bot-platform/docs/architecture/jwt-contract.md`.
  2. Beta announces: "JWT contract doc landed".
  3. Alpha implements issuance side (or wires existing Phase A.7 DRF-242.8 work to new contract).
  4. Gamma implements verification side (middleware in `apps/tenancy/` or `apps/identity/`).
  5. Integration test (#447 family, but JWT-specific): mobile JWT → both backends accept → both verify TenantUserRelationship.
- **Failure mode:** Alpha and Gamma diverge on claim shape → all subsequent tests fail. Run signature interop test first.

### Sync 4 — Event contract (#441 → #429-433 + #442-447)
- **Driver:** Beta writes `docs/architecture/event-contract.md`.
- **Steps:**
  1. Beta writes contract doc (envelope schema, 12 events, versioning, idempotency, delivery, PII rules, failure modes).
  2. Beta announces: "Event contract doc landed".
  3. Alpha + Gamma can start their respective Bucket 7 work in parallel.
  4. Integration test (#447) verifies end-to-end after at least one consumer ships.
- **Failure mode:** doc is ambiguous → publisher and consumer diverge on payload shape → events silently dropped. Mitigate: doc includes JSON examples for every event, and #447 is run early.

### Sync 5 — API Gateway routes (#434) interacts with all three streams
- **Driver:** Gamma writes Nginx config for `api.ayla.app`.
- **Coordination:**
  - Beta makes sure DNS for `api.ayla.app` works in dev (parallel to #417).
  - Alpha confirms its endpoint paths are stable (no path renames during gateway work).
  - Gamma writes routing tests, opens PR.
- **Failure mode:** Path conflicts (e.g. both repos have `/users/me/*`) → gateway picks wrong backend. Mitigate: test curl matrix in `docs/qa/phase-0-gateway-routing.md`.

## Week-by-week per-stream plan

### Week 1 (2026-05-20 → 2026-05-27)
- **Alpha:** #420 (fast), #421, #422, #423 (foundation).
- **Beta:** #412, #413, #414 (ADR docs, fast), **#441 event-contract.md (BLOCKER for Bucket 7 — must land this week)**, #418 README updates.
- **Gamma:** #427 apps/orders display-only (independent), draft #432 ingest endpoint (can scaffold without #441 but cannot finish).
- **Sync:** Beta announces #441 landed by end of Week 1 → unblocks Alpha + Gamma for Week 2.

### Week 2 (2026-05-27 → 2026-06-03)
- **Alpha:** #424 SQLite→Postgres migration, #425 outbox worker, #426 Payment refactor.
- **Beta:** #415 frontAyla namespace, #436 ADR-0010 LLM, #437 ADR-0011 privacy, draft #419 secrets, write jwt-contract.md (Sync 3).
- **Gamma:** #432 ingest endpoint complete (Week 1 scaffold + #441 spec), start #442 booking consumer.
- **Sync:** Beta announces JWT contract doc landed (Sync 3 mid-week).

### Week 3 (2026-06-03 → 2026-06-10)
- **Alpha:** #429, #430, #431 outbox publishers (now unblocked), #439 booking_source, #435 issuance side.
- **Beta:** #416 Bundle IDs (Apple review starts now), #417 DNS flip (Sync 1), #419 secrets.
- **Gamma:** #443, #444, #445, #446 remaining consumers, #434 API gateway (Sync 5), #435 verification side, #428 webhook URL removal (Sync 2).
- **Sync:** Sync 1 (env URL), Sync 2 (YooKassa), Sync 5 (gateway).

### Week 4 (buffer) — Catch-up + #433 umbrella close + #447 idempotency test
- All streams clear spillover. Apple provisioning may still be in review — does not block.
- **#447 idempotency contract test** runs in CI green.
- **#433 umbrella** closes when all consumer issues close.
- Phase 0 close PR — tech lead validates 9 close criteria + merges the closing doc.

## Tech lead's watch protocol (Claude in main session)

Tech lead does NOT execute Phase 0 tickets. Tech lead watches and coordinates:

1. **Branch watch.** Every 30-60 min, run:
   ```
   cd C:\Users\user\PycharmProjects\ai-bot-platform && git fetch --all && git branch -r | grep phase0/
   cd C:\Users\user\PycharmProjects\Ayla && git fetch --all && git branch -r | grep phase0/
   cd C:\Users\user\PycharmProjects\ayla-ai-core && git fetch --all && git branch -r | grep phase0/
   ```
   Verify branch names match stream prefix. If `phase0/alpha/...` shows up in `ai-bot-platform` repo — collision; halt that agent.

2. **PR watch.** When PR opens in any repo:
   ```
   gh pr list --repo <repo> --json number,title,headRefName,labels,author
   ```
   Open PR → Dispatch Code Reviewer agent (memory `feedback_pr_workflow_code_reviewer`).

3. **File overlap watch.** When two streams have open branches in same repo, run:
   ```
   git diff --name-only main...phase0/alpha/<#> > /tmp/alpha-files.txt
   git diff --name-only main...phase0/gamma/<#> > /tmp/gamma-files.txt
   comm -12 <(sort /tmp/alpha-files.txt) <(sort /tmp/gamma-files.txt)
   ```
   Any output = collision. Halt the second agent, route file ownership question.

4. **Sync handshake.** When stream announces a Sync point completion, post the announcement in the supervised conversation. Other streams confirm receipt before proceeding.

5. **Issue status watch.** Every few hours:
   ```
   gh issue list --repo AndreyDeveloper84/ai-bot-platform --milestone "Sprint 1 — Foundation backbone" --state all --json number,title,state,labels --limit 100
   ```
   Detect closed/in-progress drift.

## Branch protection

For `dev` branch in each repo:
- Require PR (already configured per DRF-891 work).
- Require Code Reviewer pass.
- Require label `phase0/<stream>` on PR for traceability.
- Auto-assign reviewers via CODEOWNERS where applicable.

## What to do if an agent goes off-script

If an agent in window N starts editing files outside its stream root:
1. Tech lead stops it (`/stop` or close window).
2. Hard reset its branch locally (`git restore <files>` for unintended edits).
3. Re-issue the agent the correct ticket scope from its stream's table above.
4. If repeat behavior, narrow next ticket to a smaller scope.

## What to do if a sync point fails

If Sync 1 (env URL) fails:
- Old domain stays live; do not depend on `dev.ayla.app` in Alpha/Gamma yet.
- Re-attempt next day; Beta debugs DNS/cert.

If Sync 2 (YooKassa webhook) fails:
- Switch back via YooKassa dashboard immediately (human action).
- Alpha + Gamma both keep their receivers in place until resolved.

If Sync 3 (JWT contract) fails:
- Roll back to existing JWT shape on Ayla side.
- Bot-platform pins to old verification logic.
- Beta rewrites contract doc with concrete examples.

If Sync 4 (event contract) fails post-merge:
- Stop dispatcher in Ayla (`celery control disable_consumer` or feature flag).
- Roll back consumer in bot-platform.
- Fix doc, re-run integration test, re-enable.

If Sync 5 (gateway) fails:
- Roll back Nginx config to direct-pointed routes (single backend per host).
- Mobile builds continue against old direct URLs.
- Gamma debugs offline.

## Daily check-in cadence

End of each working day:
1. Each agent posts in its window: "Today: closed #X, #Y. In progress: #Z. Blocked: #W (waiting on Sync N from Beta)."
2. Tech lead aggregates into single status post.
3. Cross-stream blockers escalated next morning.

## When to declare Phase 0 done

All 9 close criteria green (per `2026-05-20-phase-0-sprint-plan.md` §Definition of Phase 0 close). Tech lead opens "Phase 0 close — unblock Phase 1 MVP" PR that:
- Updates memory `freeze-mvp-until-boundaries-locked` → "RESOLVED — Phase 0 closed YYYY-MM-DD".
- Lifts Sprint 2/3/4 freeze.
- Announces Phase 1 kickoff.
