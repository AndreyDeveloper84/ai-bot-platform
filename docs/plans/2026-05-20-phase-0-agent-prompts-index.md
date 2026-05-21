# Phase 0 — Agent prompts index

> **Purpose:** Quick reference for the three startup prompts. Each file is a complete copy-paste-ready prompt for one Claude Code or Codex agent.
> **Date:** 2026-05-20
> **Status:** Active

## How to use

1. Open a new Claude Code (or Codex) window.
2. Open the corresponding stream file (see table below) in any text editor.
3. Copy the contents of the fenced code block in that file.
4. Paste into the new agent window as the first message.
5. The agent will read the referenced docs, confirm its stream, then begin Week 1 tickets in order.

You can run all three agents simultaneously in three adjacent windows. Tech lead supervises from the main window per the runbook's `§Tech lead's watch protocol`.

## Streams

| Stream | Focus | Repo + working dir | Prompt file |
|---|---|---|---|
| **Alpha** | Ayla djangoproject backend (Postgres+Celery+Redis, Payment refactor, outbox publishers, booking_source) | `C:\Users\user\PycharmProjects\Ayla\djangoproject\` | [`2026-05-20-phase-0-prompt-stream-alpha.md`](2026-05-20-phase-0-prompt-stream-alpha.md) |
| **Beta** | Cross-repo docs + frontAyla rename + Bundle IDs + DNS + secrets + ADRs. **Owns #441 — Week 1 BLOCKER for Bucket 7.** | All 3 repos (docs only) + `Ayla/frontAyla/` + infra | [`2026-05-20-phase-0-prompt-stream-beta.md`](2026-05-20-phase-0-prompt-stream-beta.md) |
| **Gamma** | ai-bot-platform contracts + events ingest+consumers + apps/orders refactor + API gateway + JWT verify | `C:\Users\user\PycharmProjects\ai-bot-platform\` | [`2026-05-20-phase-0-prompt-stream-gamma.md`](2026-05-20-phase-0-prompt-stream-gamma.md) |

## Reading order (for the supervising tech lead)

Before launching agents, re-read:

1. `docs/adr/ADR-0009-ayla-split-domain-architecture.md` — the architecture decision.
2. `docs/plans/2026-05-20-phase-0-sprint-plan.md` — the 35-issue execution plan with 9 close criteria.
3. `docs/plans/2026-05-20-phase-0-parallel-agent-runbook.md` — stream boundaries, sync points, week-by-week, tech-lead watch protocol.
4. `docs/plans/2026-05-21-developer-agent-workflow.md` — universal 10-phase developer workflow regulation that every agent (Alpha/Beta/Gamma + future) follows on every ticket.

For background:

- `docs/plans/2026-05-20-ayla-consolidated-architecture.md` — full audit and rationale (marked SUPERSEDED for active decisions).

## Sync handshakes (cross-stream announcements)

When a stream completes a critical deliverable, it MUST announce in its window, then the tech lead in the main window propagates the announcement to dependent streams. Critical handshakes:

| Sync | Driver stream | Trigger | Listeners |
|---|---|---|---|
| 1 — Env URL flip | Beta | DNS `dev.ayla.app` + LE certs live; 30-day 301 active | Alpha + Gamma update env vars |
| 2 — YooKassa webhook switch | Alpha + Gamma + human | Alpha receiver live → human flips dashboard → 24h watch → Gamma removes old endpoint | Both close their parts of #428 |
| 3 — JWT contract doc | Beta | `docs/architecture/jwt-contract.md` PR merged | Alpha + Gamma may start #435 (issuance + verify) |
| 4 — Event contract doc (#441) | Beta | `docs/architecture/event-contract.md` PR merged | Alpha unblocks #429–#431; Gamma unblocks #432 completion + #442–#447 |
| 5 — API gateway routes | Gamma | Nginx config tested; curl matrix in `docs/qa/phase-0-gateway-routing.md` | Mobile builds switch to `api.ayla.app` |

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
