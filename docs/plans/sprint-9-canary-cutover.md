# Plan — Sprint 9: Staging soak + Canary cutover (5% → 50% MAX)

> Theme: take the platform from "feature-complete on `dev`" to "serving real
> MAX traffic" via 7-day shadow soak + STRICT_TENANT_SCOPE flip + canary
> ramp 5% → 25% → 50%.
> Reference: `docs/plans/sprint-8-retro.md` + `mysite/docs/arch/PHASE0_DESIGN.md` §2.3 Sprint 9.
> Baseline: `main @ 254af69` — Sprint 8 closed (33/38 in-repo Done; M-track in flight on mysite; F2 monitor + G3 skeleton merged).
> Linear epic: **TBD** — `[Sprint 9] Staging soak + Canary cutover (week 19-20)`.

## Context

Sprint 8 shipped the observability + shadow-mode infrastructure. The platform now:
- mirrors real MAX webhook traffic via nginx tee → shadow Conversation rows;
- emits 19-step OTel spans + Sentry P0 capture + JSON logs with `tenant_id` + `trace_id`;
- aggregates daily ShadowDeltaSnapshot (intent agreement, action_type agreement, latency p50/p95);
- runs F2 post-flip monitor armed by `STRICT_SCOPE_FLIP_AT` env var;
- has G3 replay-diff skeleton ready to activate on captured fixture.

Sprint 9 turns the dials. **Staging soak** accumulates the 7-day clean delta gate. **M-track integration** wires the platform's catalog/FAQ store against real mysite endpoints (rather than fixture stubs) once the parallel agent's M1..M3 PRs land. **F1 flip** moves `STRICT_TENANT_SCOPE` from `audit` to `strict` in prod. **Canary cutover** routes 5% → 25% → 50% of real MAX traffic to `ai-bot-platform/` (primary), with `mysite/maxbot/` as fallback and per-request rollback via nginx config.

Sprint 10 picks up at 50% → 100% (the freeze lift per CLAUDE.md).

## Scope — what we're carrying

### Carry-overs in motion (not new work — already owned)

- **M1** DRF-724 — mysite catalog viewsets [`FROZEN-EXEMPT`] (parallel agent)
- **M2** DRF-725 — mysite X-Service-Token middleware [`FROZEN-EXEMPT`] (chains on M1)
- **M3** DRF-726 — mysite delta-push webhook [`FROZEN-EXEMPT`] (chains on M2)
- **F1** DRF-730 — prod STRICT flip (gated on 7-day clean shadow)

### New work — Sprint 9 backlog

- **K** — Soak operations (daily delta review, deviation triage, status log)
- **C** — Platform-side integration against M-track (catalog client + sync + webhook receiver)
- **F** — F1 cutover orchestration (staging dry-run + prod arm + 24h watch)
- **N** — Ground-truth fixture capture (G3 activation) + IM-3 replay sampling tune-down
- **A** — Alerting: PagerDuty setup + dual-channel routing
- **R** — Runbook finalization (tenant-onboarding, incident-response, security-incident, on-call)
- **X** — Canary cutover ramp 5% → 25% → 50%
- **G** — Sprint 9 close-out gates

## Decomposition — 32 sub-tasks across 8 tracks

### K-track — Soak operations (4 tasks)

- **K1** — Daily ShadowDeltaSnapshot digest read protocol. 09:00 МСК Telegram → if intent_agreement < 95% OR samples < 30 → flag for triage. Document in `docs/runbooks/shadow-mode-launch.md` section "Daily soak operations".
- **K2** — Deviation triage protocol. If a delta day drops below 95%: capture top-10 disagreement Messages (intent mismatch vs mysite ground truth), open `DRF-` ticket per pattern, classify (skill gap / prompt drift / data gap / canonical-divergence).
- **K3** — Soak day-by-day log. Linear comment thread on Sprint 9 epic, one comment per day with: intent_agreement, sample_count, top-3 deviations, decisions made.
- **K4** — Mid-soak retro (Day 4). Either: continue to F1 flip (if 4 days clean), OR extend soak (if 1+ deviation day), OR roll back shadow infra (if delta < 80% any day).

### C-track — Platform M-track integration (5 tasks)

- **C1** — `apps/catalog/client.py::MysiteCatalogClient` — HTTP client against `mysite /api/v1/catalog/{services,masters,faqs,help-articles}/`. Includes `X-Service-Token` header (env `MYSITE_SERVICE_TOKEN`), `requests.Session` + `Retry` (3 attempts, 502/503/504 backoff), wrapped in CR-3 breaker per-host.
- **C2** — `apps/catalog/sync.py::sync_catalog` Celery task. Daily 03:00 МСК. Reads `last_synced_at` per model, fetches `?since=<iso>`, upserts into platform-local tables. Idempotent on `(tenant_id, source_id)`. Audit row per sync run.
- **C3** — `apps/catalog/webhooks/views.py::CatalogWebhookView` — POST receiver for M3 delta-push. HMAC verify via shared secret (env `MYSITE_WEBHOOK_HMAC_SECRET`). On valid signature → enqueue `apply_delta(event, model, pk)` Celery task. Always 200 (preempt retry storms).
- **C4** — Tenant-scoped catalog tables. Migration adding `Catalog<Service|Master|Faq|HelpArticle>` models with `tenant` FK + default-tenanted manager. Replay redactor allow-lists business-field paths (no PII risk per Sprint 5 audit but document explicitly).
- **C5** — Integration tests + e2e. Mock-mysite stub for C1/C2/C3 unit; e2e gated `@pytest.mark.requires_mysite_dev` activating when `MYSITE_BASE_URL` env set (CI: against dev.gobeauty.site staging).

### F-track — F1 cutover orchestration (3 tasks)

- **F-dry** — F2 monitor staging dry-run. On staging: set `STRICT_SCOPE_FLIP_AT=<now-iso>`, seed `AuditLog(action="tenant_scope_violation")` row, verify Telegram + Sentry alert fire within 15-min tick. Tear down state.
- **F-flip** — prod cutover (gated on K1 7-day clean + zero violations). Edit `/etc/ai-bot-platform/.env` → `STRICT_TENANT_SCOPE=strict` + `STRICT_SCOPE_FLIP_AT=<ISO 8601>`. `docker compose up -d --force-recreate web worker`. Verify F2 monitor armed in worker logs.
- **F-post** — 24h post-flip retro. Read `observability.post_flip.checked` heartbeat rows + violation count. Document outcome in runbook. Clear `STRICT_SCOPE_FLIP_AT` after window closes.

### N-track — Fixture capture + sampling tune-down (3 tasks)

- **N4** — Ground-truth game-day. **Day 1 of cycle.** SSH to mysite staging, send each of 20 golden FAQ fixtures via test harness, capture `(intent, action_type)` from `mysite/maxbot/` chat_rag handlers. Write to `tests/fixtures/mysite_ground_truth/golden_faq_replies.json` per format in `tests/fixtures/mysite_ground_truth/README.md`. Commit + push.
- **N4-verify** — G3 acceptance. Re-run `pytest tests/e2e/test_replay_diff_vs_mysite.py -v` — expect 2 passed (no longer 1 skipped). If < 95% match: capture per-fixture diff, open `DRF-` ticket per gap. Don't merge fixture unless gate green OR diffs documented as known-acceptable.
- **N-im3** — IM-3 replay sampling tune-down. **Last task of sprint.** Gated on canary ≥ 25% stable 3+ days. Lower `REPLAY_SAMPLING_RATE` from 1.0 → 0.25. Update `docs/runbooks/replay-debugging.md`. Verify replay storage volume drops in metrics.

### A-track — PagerDuty + dual-channel alerting (4 tasks)

- **A1** — PagerDuty account + service config. Create "ai-bot-platform" service + on-call schedule (Lead 24/7 with AI/Claude backup pseudo-user for monitoring routing). Document API key in 1Password.
- **A2** — Integration in F2 monitor + Sentry. `apps/observability/alerting.py::page` — dual-channel emit: Telegram (existing) + PagerDuty Events API v2 (`change` for routine, `critical` for STRICT violations). Severity matrix per channel.
- **A3** — On-call response runbook. New `docs/runbooks/on-call.md`. Sections: page-receipt path (PD app + Telegram), 15-min ack SLA, escalation to Lead-direct after 30 min, AI-backup role (diagnostic queries, never destructive actions).
- **A4** — Page routing smoke test. Manual trigger script `python manage.py smoke_alert --severity=critical --message="Sprint 9 A4 smoke"`. Verify both PD + Telegram fire within 60s. Run weekly during soak.

### R-track — Runbook finalization (4 tasks)

- **R-tenant** — `docs/runbooks/tenant-onboarding.md` full. Sections: provisioning (Tenant row + secrets + MAX webhook config); role assignment; smoke (send "/start" → reply lands); rollback (de-provision flow).
- **R-incident** — `docs/runbooks/incident-response.md` full. Sections: prerequisites (PD, status page, war-room channel); declaration flow (sev matrix → IC + Comms + SME role split); stabilization (mitigation runbook chain); communication cadence (30-min on Sev1, 1h on Sev2); resolution + post-mortem within 5 days.
- **R-security** — `docs/runbooks/security-incident.md` full. Sections: data leak protocol (152-ФЗ обязательства); key rotation; customer notification template; regulatory escalation (Roskomnadzor); audit-trail preservation.
- **R-oncall** — `docs/runbooks/on-call.md` (mirror A3 output to standalone runbook). 2-person rotation pattern: Lead + AI/Claude backup. Pager-receive + diagnostic-query + escalation paths. SLA matrix.

### X-track — Canary cutover ramp (6 tasks)

- **X-ready** — Canary readiness review. Gate checklist: 7-day clean delta ✅, all runbooks done ✅, on-call live ✅, PD smoke green ✅, F-dry passed ✅, F-flip merged ✅. Sign-off ticket.
- **X-5pct** — Canary 5% cutover. nginx `split_clients` config: 5% of incoming MAX webhook POSTs route to `ai-bot-platform/` (primary, response returned to user); remaining 95% stays on `mysite/maxbot/`. Duration: 2 days. Monitor: per-hour delta vs shadow baseline. Rollback: single nginx config line + `nginx -s reload`.
- **X-25pct** — Canary 25% cutover (after X-5pct stable 2 days). Same mechanism, 25% split. Duration: 3 days. Same rollback path.
- **X-50pct** — Canary 50% cutover (after X-25pct stable 3 days). Same mechanism, 50% split. **End-of-Sprint-9 state.**
- **X-rollback** — Rollback drill on staging. Before each canary bump exercise the nginx revert path: set 0%, verify all traffic returns to mysite within 60s, restore. Document timing.
- **X-criteria** — Canary stability checkpoint criteria, codified. Bump to next % requires: intent_agreement ≥ 95% × N days, latency p95 within +10% vs mysite, error rate within +0.5pp vs mysite, zero P0 Sentry, zero STRICT violations. Documented in `docs/runbooks/canary-ramp.md` (new).

### G-track — Sprint 9 close-out (3 tasks)

- **G-rollup** — Sprint 9 close-out (DRF-737 pattern). Roll-up comment on Sprint 9 epic. Memory file `project_aibot_sprint9_close.md`. Sprint 9 epic → Done iff X-50pct reached + all gates green; otherwise In Progress with carry-overs into Sprint 10.
- **G-exitgate** — Exit-gate verification record. Sprint 9 metrics summary on epic: 7+ day clean delta, F1 flip outcome, canary stability per step, runbook completion, alerting test history. Becomes Sprint 10 baseline.
- **G-sprint10** — Sprint 10 planning kickoff. Draft `docs/plans/sprint-10-100pct-cutover.md` covering 50% → 100% ramp + freeze lift. Hand-off doc on what gates remain.

## Exit gate

Sprint 9 closes when **all** of:

1. **Shadow soak complete** — 7+ consecutive ShadowDeltaSnapshot days with intent_agreement ≥ 95%.
2. **F1 flipped** — `STRICT_TENANT_SCOPE=strict` in prod, F2 24h watch closed with zero unaccounted violations.
3. **Canary at 50%** — X-50pct active, criteria green for ≥ 1 day.
4. **Runbooks complete** — tenant-onboarding, incident-response, security-incident, on-call all at status "complete" (no "skeleton" / "partial" markers).
5. **On-call live** — PagerDuty service active, 2-person rotation (Lead + AI backup) documented and smoke-tested.
6. **G3 active** — `golden_faq_replies.json` committed; replay-diff CI gate enforcing ≥ 95%.

Soft targets (not blocking):
- IM-3 replay sampling tune-down to 0.25 (last task, executes if 25% canary holds 3+ days).
- Sprint 10 plan draft in repo.

## Decisions baked

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Canary final % at end of Sprint 9 | **50%** | Aggressive ramp lets Sprint 10 do 50 → 100% fast. Risk-priced via X-criteria gates between bumps. |
| 2 | Canary intermediate steps | **5% / 25% / 50%** | Each step roughly doubles the prior. 5% catches first-hour blowups; 25% surfaces traffic-pattern bugs; 50% proves stability under representative load. |
| 3 | On-call rotation | **Lead + AI/Claude backup** | Single human + LLM diagnostic backup. Lead receives PD; AI executes diagnostic queries (read-only) on direction. No second human in Sprint 9 — adds in Sprint 10 if canary holds. |
| 4 | Alerting channels | **PagerDuty + Telegram (dual)** | PD for ack-able critical pages; Telegram for context-rich routine events + warning. Both fire on Sev1; Telegram-only on Sev2/3. |
| 5 | Canary routing mechanism | **nginx `split_clients`** (no app-level routing) | Single-line revert. App-level routing introduces a code path that depends on the very system being canaried. nginx is the contract boundary anyway. |
| 6 | Rollback unit | **One nginx config change + reload** | < 5s revert. Faster than container restart. No persistent state needs unwinding. |
| 7 | Catalog sync strategy | **Daily Celery + delta webhook (M3)** | Catalog rarely changes; daily refresh covers 99% of mutations. M3 webhook handles the long-tail (a master deleted mid-day). |
| 8 | Catalog data ownership | **Mysite is source-of-truth; platform replicates** | Phase 0 freeze keeps mysite canonical. Platform reads its local replica; never writes back. Replication is one-way until Phase 1. |
| 9 | G3 fixture capture timing | **Day 1 of cycle** | Activates the gate throughout the soak — catches drift early, not at the end when fix-time is gone. |
| 10 | IM-3 sampling tune-down timing | **Last task of sprint** | Sampling rate change is irreversible-ish (lost data isn't recoverable). Gate on stable 25%+ canary for confidence. |

## Risks

1. **M-track slip blocks C-track.** If parallel agent doesn't land M1..M3 by Day 3-4 of Sprint 9, C-track e2e blocks. *Mitigation:* C1..C4 (client + sync + webhook receiver + tables) implementable against the dev.gobeauty.site mysite staging where M-track's `dev` branch deploys. Only C5 e2e needs prod-mysite M-track merged.

2. **Shadow delta < 95% on any day** delays F1, which delays canary. *Mitigation:* K2 triage protocol surfaces patterns fast; K4 mid-soak retro gives explicit go/no-go at Day 4 rather than waiting for Day 7 surprise.

3. **STRICT flip surfaces long-tail audit-mode violations.** *Mitigation:* F2 monitor pages on first violation post-flip; single-line env revert documented in R3 strict-scope-flip runbook (already complete from Sprint 8).

4. **Canary 5% surfaces latency tail not visible in shadow.** Shadow drops responses; canary primary serves them. *Mitigation:* X-criteria explicitly checks p95 within +10% vs mysite. Per-hour rollback authority delegated to Lead during canary windows.

5. **PagerDuty setup blocks A2/A3/A4** if account creation has admin friction. *Mitigation:* A1 is Day 1 of cycle. Telegram-only fallback for Sprint 9 if PD slips, with A1 moving to Sprint 10. F-flip can proceed without PD; on-call SLA degrades but F2 monitor still fires via Telegram + Sentry.

6. **Aggressive 5% → 50% ramp** doubles risk per bump vs conservative 5% → 25%. *Mitigation:* X-criteria gate is the contract — bumps don't happen if metrics aren't green. Calendar is target, not commitment.

## Scope warning

32 tasks across 8 tracks. Sprint 8 closed 33 in-repo Done in calendar terms, so 32 is within AI-driven velocity. But Sprint 9 has more **calendar-driven** work (7-day soak can't be compressed) and more **gated** work (canary bumps depend on observed stability). Effective sprint length: 10-12 working days, not 7-8 like Sprint 8 felt.

Contingency picks (if cycle runs hot):
- Defer **N-im3** sampling tune-down → Sprint 10 (lowest cost; replay storage growth is linear).
- Defer **A1** PagerDuty setup → Sprint 10 (Telegram-only is degraded but viable for 5%/25% canary).
- Defer **G-sprint10** Sprint 10 plan draft → start of Sprint 10 cycle.

Hard-gated (cannot defer):
- K1..K4 soak ops — drives the entire F1 gate.
- F-flip — without it, canary can't proceed in audit mode safely.
- X-ready / X-5pct — first real-traffic checkpoint of Phase 0.
- R-incident — required before X-5pct per on-call SOP.

## References

- `docs/plans/sprint-8-retro.md` — what we learned, baseline assumptions.
- `docs/plans/sprint-8-observability-shadow.md` — context for shadow mode infrastructure.
- `docs/runbooks/strict-scope-flip.md` — operational steps for F1.
- `docs/runbooks/shadow-mode-launch.md` — daily soak operations (extended in K1).
- `docs/runbooks/rollback-procedure.md` — env-revert path for F1 + X-rollback.
- `mysite/docs/arch/PHASE0_DESIGN.md` §2.3 Sprint 9 + §6 STRICT scope + §10 canary cutover (read separately; not in this repo).
- `CLAUDE.md` Migration Freeze policy — Sprint 10 / week 21-22 = 100% MAX traffic + freeze lift.

## Sprint 9 → Sprint 10 hand-off (what stays open)

By design, the following move to Sprint 10:
- **Canary 50% → 100%** (X-track continues).
- **Second human in on-call rotation** (after canary holds at 50%).
- **Freeze lift** — `mysite/maxbot/.FROZEN` policy lifts; new feature work resumes on platform only.
- **mysite catalog migration** — at 100% cutover, mysite stops accepting `/api/maxbot/webhook/` traffic; catalog tables migrate to platform-canonical.
