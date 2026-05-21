# Phase 0 + Sprint 1 Track A — Active agent layout

> **Purpose:** Coordinates 6 parallel agent streams across Phase 0 (architecture stabilization) + Sprint 1 Track A (product foundation).
> **Date:** 2026-05-20, revised 2026-05-21 (6-stream layout finalized after reading 4 active-agent retros).
> **Status:** Active
> **Source of truth memory:** `project_ayla_active_streams.md` (linked in MEMORY.md).

## 6-stream layout (current)

| Stream | Agent window | Focus | Working dir / repo | Status |
|---|---|---|---|---|
| **Delta — Master Mini App** | W1 (continuing) | Sprint 1 Track A EPIC #222 — master/admin/internal-chat product. MM5 deactivation, M3 schedule self-edit, M5 conversations, Pro Mini App shell (#253). | `ai-bot-platform/apps/{admin_api,master_api,internal_chat,miniapp/master}/` | Continue with momentum |
| **Epsilon — Channel + Live Ops** | W2 (continuing) | `apps/channels/*` ownership + live ops. F1-F9 cleanup. **Takes Beta-DNS (#417) + Beta-secrets (#419)** + sync 2 YooKassa dashboard flip shepherd. | `ai-bot-platform/apps/channels/*`, infra DNS, webhook lifecycle | Continue + absorb Beta-infra |
| **Zeta — Security Backstop** | W3 (continuing) | Cross-module retro continuation + **second-pass Code Reviewer** on NEW Gamma's high-risk PRs (#432, #442-#447, #435, #434). | Cross-module, especially `apps/eventbus/`, `apps/tenancy/`, `apps/llm/`, `apps/audit/` | Continue retro + Phase 0 backstop |
| **Sprint 1 coordinator + Phase 0 Beta-docs** | W4 (continuing) | Sprint 1 backlog refinement + #246 dry-run shepherd + Sprint 2/3/4 lock scan. **Takes Beta-docs:** #412/#413/#414 ADR copies, #418 READMEs, #437 ADR-0011 privacy, **#441 event-contract.md (CRITICAL Week 1 BLOCKER for Bucket 7)**. | All 3 repos (docs only) + GH issues | Continue + take Beta-policy work |
| **Phase 0 Alpha** | **NEW agent 5** | Ayla djangoproject backend infra: db.sqlite3 cleanup, Postgres+Redis+Celery, Payment refactor, outbox publishers, JWT issuance side. | `C:\Users\user\PycharmProjects\Ayla\djangoproject\` | **START NOW** with [`stream-alpha.md`](2026-05-20-phase-0-prompt-stream-alpha.md) |
| **Phase 0 Gamma** | **NEW agent 6** | ai-bot-platform contracts + events ingest + consumers + apps/orders refactor + API gateway + JWT verify. | `ai-bot-platform/apps/{eventbus,orders}/`, `config/urls.py`, `apps/tenancy/middleware.py`, infra/nginx | **START NOW** with [`stream-gamma.md`](2026-05-20-phase-0-prompt-stream-gamma.md). Wait for W4 #441 before completing #432. |

## Phase 0 Beta — distributed, no dedicated agent

The original Beta stream (cross-repo docs + rebrand + DNS) has been **split across existing windows** to avoid a 7th parallel agent:

- **W4 takes Beta-docs (6 tickets):** #412, #413, #414 (ADR-0009 copies), #418 (README rebrand), #437 (ADR-0011 privacy), **#441 (event-contract.md — Week 1 BLOCKER)**.
- **W2 takes Beta-infra (2 tickets):** #417 (DNS env URL flip), #419 (.mcp.json secrets vault).
- **Deferred Beta-frontend (3 tickets, no owner yet):** #415 (frontAyla `@beautygo` → `@ayla` namespace), #416 (Bundle IDs `ru.ayla.*`), #436 (ADR-0010 LLM benchmark). These are cosmetic or Phase 1 prerequisites; can be picked up Week 2-3 by W1 (has frontend skill) or new agent later.

The `2026-05-20-phase-0-prompt-stream-beta.md` file remains as historical reference for the consolidated Beta scope. **Do not start a fresh agent against it** — work is distributed per above.

## How to start NEW agents 5 and 6

For each NEW agent:

1. Open a new Claude Code (or Codex) window.
2. Open the corresponding stream file (`stream-alpha.md` for agent 5, `stream-gamma.md` for agent 6).
3. Copy the contents of the fenced code block in that file.
4. Paste into the new agent window as the first message.
5. The agent will read the referenced docs, confirm its stream, then begin Week 1 tickets in order.

W1-W4 keep their current context — no re-bootstrap needed. Tech lead in main window propagates Sync handshakes between all 6 streams per the runbook's `§Tech lead's watch protocol`.

## Reading order (for the supervising tech lead)

Before launching agents, re-read:

1. `docs/adr/ADR-0009-ayla-split-domain-architecture.md` — the architecture decision.
2. `docs/plans/2026-05-20-phase-0-sprint-plan.md` — the 35-issue execution plan with 9 close criteria.
3. `docs/plans/2026-05-20-phase-0-parallel-agent-runbook.md` — stream boundaries, sync points, week-by-week, tech-lead watch protocol.
4. `docs/plans/2026-05-21-developer-agent-workflow.md` — universal 10-phase developer workflow regulation that every agent (Alpha/Beta/Gamma + future) follows on every ticket.

For background:

- `docs/plans/2026-05-20-ayla-consolidated-architecture.md` — full audit and rationale (marked SUPERSEDED for active decisions).

## Sync handshakes (cross-stream announcements)

When a stream completes a critical deliverable, it MUST announce in its window, then the tech lead in the main window propagates the announcement to dependent streams. Critical handshakes (updated for 6-stream layout):

| Sync | Driver | Listeners | Week |
|---|---|---|---|
| **4 — Event contract doc (#441)** | W4 (Beta-docs owner) writes `docs/architecture/event-contract.md` | NEW Alpha unblocks #429–#431 outbox publishers; NEW Gamma unblocks #432 completion + #442–#447 consumers; W2 validates channel-side; W3 reviews defence-in-depth patterns | 1 |
| **3 — JWT contract doc** | W4 writes `docs/architecture/jwt-contract.md` | NEW Alpha (issuance side) + NEW Gamma (verification side) | 2 |
| **1 — Env URL flip (#417)** | W2 (Beta-infra owner) | NEW Alpha + NEW Gamma update env vars | 3 |
| **2 — YooKassa webhook switch (#428)** | NEW Alpha implements receiver in Ayla; W2 shepherds dashboard flip + 24h log watch; NEW Gamma removes old bot-platform endpoint | All three coordinate | 3 |
| **5 — API Gateway routes (#434)** | NEW Gamma writes Nginx routing config | W2 confirms path stability; mobile builds switch to `api.ayla.app` | 3 |
| **#246 User-tenant decoupling dry-run** | W4 shepherd | NEW Alpha executes migration; W3 W900/W901 system check validates new TenantUserRelationship surface | 2-3 |
| **Phase 0 Gamma high-risk PR review** | NEW Gamma opens PR | W3 second-pass Code Reviewer on every security-adjacent diff (#432 HMAC ingest, #435 JWT verify, #428 webhook removal, #447 idempotency contract) | Continuous |

## Phase 0 close (when all 9 criteria green)

Tech lead opens "Phase 0 close — unblock Phase 1 MVP" PR. That PR:

- Updates memory `freeze-mvp-until-boundaries-locked` → "RESOLVED — Phase 0 closed YYYY-MM-DD".
- Lifts the Sprint 2/3/4 freeze.
- Announces Phase 1 kickoff.

See `2026-05-20-phase-0-sprint-plan.md §Definition of Phase 0 close`.

## If an agent goes off-script

The runbook's `§What to do if an agent goes off-script` section is canonical. Short version:

1. Tech lead halts the agent (close window or `/stop`).
2. Reset its branch locally if it edited out-of-stream files.
3. Re-issue a narrower ticket from the stream's own table.
4. Repeat behavior → narrow further next time.

## Follow-up polish (non-blocking)

GH issue #451 captures 11 polish findings from the Code Reviewer pass on PR #449. These can land in a single follow-up PR during Phase 0 Week 4 buffer.

## Explicit ticket ownership (clarifies under-named tickets)

Round-5 review caught tickets not explicitly named in the stream rows above. Resolved:

- **#420** (db.sqlite3 cleanup + CI guard) — NEW Alpha, Week 1 first ticket.
- **#427** (apps/orders → display-only) — NEW Gamma, Week 1 independent (no Sync 4 dependency).
- **#433** (consumers umbrella) — NEW Gamma; closes when #442-#446 close.
- **#438** (E2E test mobile→event→memory→Telegram) — **co-owned NEW Alpha + NEW Gamma + W3 review**. Automated portion in CI; manual smoke in `docs/qa/`. Run after first consumer ships.
- **Sprint 1 Track A EPICs #219, #220, #221, #223** (Ayla persona, memory framework, emergency tiers, anonymous OAuth) — NOT abandoned. They sit in W4's coordinator backlog awaiting pickup by Delta (W1 currently focused on #222) or new agents in Week 2-3. Listed in `project_ayla_active_streams.md` for traceability.

## Cognitive overhead warning (6 streams)

6 parallel agents is **above the previously documented 5-sustainable threshold**. Tech lead in main window must:

- Branch watch every **60 min** (not 30 — unrealistic for one human across 6 windows): `git fetch --all && git branch -r | grep -E 'phase0/|phase0-track-a/'` across all 3 repos. Hourly cadence matches the existing runbook.
- Code Reviewer dispatch on EVERY PR (no exceptions per `pr-workflow-code-reviewer` memory).
- W3 doubles as second-pass reviewer on NEW Gamma's high-risk PRs — reduces tech lead load.
- Sync handshake propagation (manually re-post announcements to dependent windows when sync completes).
- Halt protocol if any agent edits files outside its stream's anti-touch list.

**Escape valves (ordered by likelihood):**

1. **If W4 cannot land #441 by EOW1** (highest schedule risk — W4 has Sprint 1 coordinator load + 6 Beta-docs tickets + Sync 3 + Sync 4 writing): halt all 6 streams and **reassign #441 to W3** (security backstop has writing capacity during retro pauses; they already cite ADR-0009 §Mandatory event contract correctly per their retro report).
2. **If Sync 4 (#441) slips beyond Week 1:** pause NEW Gamma entirely until #441 lands. NEW Alpha continues on infra (#420-#426) independent of Sync 4 — they unblock themselves without the contract doc.
3. **If general cognitive overhead breaks:** pause NEW Gamma (slowest critical path due to Sync 4 dependency) and absorb its work into W3. Reduces to 5 streams.

See `2026-05-20-phase-0-parallel-agent-runbook.md` §Tech lead's watch protocol for full procedure.
