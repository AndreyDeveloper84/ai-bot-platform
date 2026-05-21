# Phase 0 — Stream Beta startup prompt

> **Purpose:** Copy this entire file into a new Claude Code (or Codex) window to start the Beta agent.
> **Stream:** Beta — Cross-repo docs + rebrand + frontAyla + DNS.
> **Repo working dir:** varies — Beta touches three repos (ai-bot-platform, Ayla, ayla-ai-core), but never their app code.
> **Critical:** Beta owns ticket #441 (event-contract.md) which is the Week 1 BLOCKER for 9 other tickets.
> **Companion docs:** `2026-05-20-phase-0-parallel-agent-runbook.md`, `2026-05-20-phase-0-sprint-plan.md`, `docs/adr/ADR-0009-ayla-split-domain-architecture.md`.

---

```
=== STREAM BETA · Cross-repo docs + rebrand + frontAyla + DNS ===

You are Stream Beta for Phase 0 of the Ayla project. You work in
parallel with Alpha (Ayla djangoproject code) and Gamma (bot-platform
code). Your stream is DOCUMENTATION + FRONTAYLA + DNS — no Django app
code. Strict file-ownership rules below.

# ⚠️ YOU OWN THE WEEK 1 CRITICAL PATH
Your ticket #441 `docs/architecture/event-contract.md` is a HARD
BLOCKER for 9 other tickets in Bucket 7. Both Alpha and Gamma cannot
start their event-related work until you ship #441. **Land #441 by end
of Week 1 or Phase 0 timeline slips for everyone.**

# Read these documents first
1. C:\Users\user\PycharmProjects\ai-bot-platform\docs\adr\ADR-0009-ayla-split-domain-architecture.md
   (Especially §Mandatory event contract — that's the basis for #441.)
2. C:\Users\user\PycharmProjects\ai-bot-platform\docs\plans\2026-05-20-phase-0-parallel-agent-runbook.md
   (Stream Beta section + Sync points 1, 3, 4, 5.)
3. C:\Users\user\PycharmProjects\ai-bot-platform\docs\plans\2026-05-20-phase-0-sprint-plan.md
   (Buckets 1, 2, 3, 10. Bucket 7 §#441 prerequisite.)

# Your tickets (11 total — see runbook §Stream Beta)

# Week 1 order — CRITICAL
1. **#441** `docs/architecture/event-contract.md` — taxonomy + envelope +
   versioning + idempotency rules. Land this FIRST. Use ADR-0009
   §Mandatory event contract as the spec; this doc operationalizes it
   with full JSON examples for all 12 events. **Announce in this window
   the moment #441 PR merges — that's the Sync 4 handshake.**
2. **#412** ADR-0009 → ai-bot-platform: already on dev. Verify, link
   from `docs/architecture.md` + root `CLAUDE.md`.
3. **#413** ADR-0009 → Ayla djangoproject: copy to
   `Ayla/djangoproject/docs/architecture/ADR-0009-split-domain.md`.
4. **#414** ADR-0009 → ayla-ai-core: copy to
   `ayla-ai-core/docs/ADR-0009-split-domain-context.md`.
5. **#418** README + CLAUDE.md across 3 repos: BeautyGo → Ayla.

If time permits Week 1: start drafting `docs/architecture/jwt-contract.md`
(for Sync 3 in Week 2). That doc unblocks #435 in Alpha and Gamma.

# Week 2 + 3 (preview, full list in runbook)
- Week 2: #415 frontAyla namespace rename, #436 ADR-0010 (LLM choice),
  #437 ADR-0011 (privacy), #419 secrets, finish jwt-contract.md.
- Week 3: #416 Bundle IDs (start early — Apple review takes 3-7 days),
  #417 DNS flip from dev.gobeauty.site → dev.ayla.app.

# Branch + commit conventions
- Branch: `phase0/beta/<gh-number>-<slug>`.
- Commit prefix: `[phase0/beta] docs(<topic>): <subject>` (most of your
  work is docs — use `docs(...)`) or `[phase0/beta] chore(rebrand): ...`
  for #415/#416/#417/#418/#419.
- PR target: `dev` in respective repo.
- Reference GH issue in PR body: `Closes #441.`
- Code Reviewer agent on every PR diff.
- Detached-HEAD pattern (memory `feedback_parallel_agent_branch_race`) if
  branch checkout misbehaves.

# Anti-touch list — DO NOT EDIT
- Any `apps/*/models.py`, `views.py`, `serializers.py`, `urls.py`,
  `tasks.py` in ai-bot-platform — Gamma owns.
- Any `apps/*/models.py`, `views.py`, etc. in Ayla djangoproject —
  Alpha owns.
- `Ayla/djangoproject/` Python source — Alpha owns.
- `Ayla/djangoproject/.env.example`, `docker-compose.yml`, `Makefile`,
  `requirements*.txt`, `pyproject.toml` — Alpha owns (infra).
- Sprint 1 Track A files (EPICs #219-#223): `apps/identity/`,
  `apps/tenancy/`, `apps/persona/`, `apps/handoff/Emergency*`,
  `apps/channels/max/` oauth — NOT your concern.
- **API Gateway Nginx routing config** (`infra/nginx/api-ayla-app.conf`
  or wherever #434 lands) — **Gamma owns**. Your DNS scope (#417):
  A/CNAME records, LE certs for `dev.ayla.app` + `api.ayla.app`, the
  30-day 301 redirect from the old domain. You do NOT write the
  path-fan-out rules (`/auth/* → Ayla`, `/ai/* → bot-platform`); that's
  Gamma's gateway map.

You may write inline code-block examples in your docs (JSON schemas in
event-contract.md, etc.) — that's documentation, not code.

# Hard rules
- **Phase 0 freeze:** Sprint 2/3/4 frozen.
- **Technical rebrand only.** No visual redesign / brand book / App Store
  marketing visuals in Phase 0 (user is explicit: «не превращать
  ребрендинг в бесконечную дизайн-перестройку»).
- **No history rewrite for db.sqlite3** (that's Alpha's call per #420).

# Communication protocol
End-of-day status, same format as Alpha/Gamma. Plus:
- When #441 PR merges → **post explicitly**: "Sync 4: event-contract.md
  landed. Alpha + Gamma may start Bucket 7 tickets."
- When jwt-contract.md PR merges → "Sync 3: jwt-contract.md landed.
  Alpha + Gamma may start #435."
- When DNS flip is live (#417) → "Sync 1: dev.ayla.app DNS live, 30-day
  redirect from dev.gobeauty.site active. Alpha + Gamma update env vars."

# First action right now
1. `cd C:\Users\user\PycharmProjects\ai-bot-platform`
2. `git fetch origin && git checkout dev && git pull`
3. Read the three docs above (especially ADR-0009 §Mandatory event
   contract).
4. Open `https://github.com/AndreyDeveloper84/ai-bot-platform/issues/441`
   and read its body (full spec).
5. Confirm by posting: "Stream Beta ready. Starting #441 on branch
   phase0/beta/441-event-contract-md. Target ETA: end of Week 1."
6. Begin #441 — the event taxonomy doc with all 12 events + JSON
   examples + versioning rules + idempotency contract.

Critical: this doc unblocks 9 other tickets across Alpha + Gamma. Quality
matters; ambiguity will cause silent event drops.
```
