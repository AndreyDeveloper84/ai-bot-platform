# Runbook — M6 auto-draft suppress tuning + observability

> Status: **draft** — pre-pilot setup; activate week-1 of pilot.
> Last exercised: _never (pre-pilot)_.
> Target completion sprint: pilot week-2 (post-2026-07-22) — promote to **complete** after first tuning cycle.
> Owner: W1 (Delta stream).
> Triggered by: issue [#690](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/690)
> (PRE_PILOT follow-up from PR [#700](https://github.com/AndreyDeveloper84/ai-bot-platform/pull/700) — closes [#659](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/659)).
> Pilot: 2026-07-15 Penza.

## Purpose

The auto-draft suppress logic shipped in PR #700
(`apps/master_api/tasks.py::auto_generate_draft_for_inbound` Step 2b)
skips regeneration when an ACTIVE draft already exists on the
conversation AND is younger than
`IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS` (default `60`) AND its
`trigger_message_id` still equals the latest customer message id.

The default 60s is **asserted-not-measured**. This runbook is the
protocol to validate + tune the window using first-pilot-week data —
dashboard spec, decision matrix, alerts, and a tuning-history append
log.

## Trigger / when to run

- Pilot day 1 (2026-07-15) — initialize panels + alerts.
- Daily during pilot week 1 — eyeball panels 1-4.
- Pilot day 8 (2026-07-22) — first tuning cycle (decision matrix below).
- After any change to `IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS` — record in §Tuning history + verify 24h later.
- On alert (any of the 3 alerts in §Alerts).

## Prerequisites

- SSH access to prod app host (Sentry DSN, log shipping config, `/etc/ai-bot-platform/.env`).
- Sentry project read access (to scrape log breadcrumbs and search by slug).
- Ability to `systemctl restart` the worker pool (env var is read by `getattr(settings, ...)` per-call — see «How to tune» — but a restart is the safest invariant).
- Familiarity with the structured-log shape produced by `apps/observability/logging.py::JsonFormatter` (each line is one JSON object with `message`, `levelname`, `name`, plus context tags injected by `ContextFilter`).

## Observability stack reconnaissance (as-found 2026-05-25)

Concrete answer for what's wired in this repo today — record the truth, not the wish list.

| Capability | Status | Where |
|---|---|---|
| **Sentry SDK** | Wired. `sentry-sdk` in `pyproject.toml`. Initialized via `apps/observability/sentry.py::configure_sentry`. Fail-fast in prod when `SENTRY_DSN` unset (`config/settings/production.py:45-49`). PII scrubber via Sprint 5 redactor at `before_send`. | `apps/observability/sentry.py` |
| **OpenTelemetry traces** | Wired. `OTEL_EXPORTER_OTLP_ENDPOINT` configurable (`config/settings/base.py:838`). Trace + tenant tags propagated to Sentry events via `_attach_context_tags`. | `apps/observability/sentry.py:148-178` |
| **Structured JSON logs** | Wired. `LOGGING` in `config/settings/base.py:1062-1095` — single `console` handler → stdout. `JsonFormatter` (`apps/observability/logging.py`) emits one JSON object per line. `PIIRedactingFilter` + `ContextFilter` run before the formatter. In prod this lands on stdout → journald. | `config/settings/base.py:1062-1095`, `apps/observability/logging.py` |
| **Prometheus / metrics endpoint** | **Not wired.** No `prometheus_client` import, no `/metrics` endpoint, no `Counter()` / `Histogram()` instrumentation in `apps/`. PR #700 explicitly chose log-only telemetry (the slugs documented below); formal metric backbone is tracked separately in issue #698. | n/a |
| **Grafana / Loki / Datadog** | **Not wired in this repo.** No dashboards-as-code committed under `docs/`, `infra/`, `ops/`. The only Grafana mention in the repo is `docs/runbooks/strict-tenant-refuse-flip.md:182` describing `monitor_pel --format json` output as «JSON output … for Prometheus / Grafana ingestion» — i.e. operator-side wiring, no JSON committed here. | n/a |
| **Log aggregator** | **None in-repo.** Production logs flow `stdout → journald`. Anything richer (Loki / CloudWatch / Datadog Logs) is operator-side infra, not configured in this codebase. |  |

**Operational implication:** the panels below are specified against
two realistic targets — (a) Sentry breadcrumb search (works today) and
(b) `journalctl` + `jq` grep (works today, slower). When a real
log-aggregator (Loki/Datadog) gets wired in production, swap the panel
queries to native search syntax; the slug specs themselves are stable.

## Glossary

- **«Tap-to-decide» latency** — wall-clock time from the auto-draft arriving on screen (visible to master via M5 «ПРЕДЛОЖЕН ОТВЕТ» counter, PR #656) to master tapping one of three actions: «Отправить от себя» / «Отредактировать» / «Пусть помощник ответит». Ground-truth signal the suppress window should approximate.
- **Suppress event** — auto-trigger task gate fired with `auto_draft.idle_active_draft_skipped`. Draft was young (`age_seconds < window`) AND still on the latest customer Msg → regeneration was skipped. LLM call saved.
- **Trigger-drift event** — auto-trigger task gate fired with `auto_draft.suppress_skipped_trigger_drift`. Draft was young, but the customer sent a newer Msg (or the draft had no `trigger_message_id` recorded) → suppression bypassed, regeneration proceeded and the stale ACTIVE row transitioned to REPLACED.
- **Normal-generate event** — auto-trigger ran the LLM end-to-end and emitted `auto_draft.generated` (slug already present at `apps/master_api/tasks.py:552`, predates PR #700).
- **Idle-active suppress rate** — `suppress_events / (suppress_events + trigger_drift_events + normal_generate_events)` over a rolling 1h window per tenant. Target during pilot week 1: 10-30% (suppress doing useful work without over-firing).

## Log slug specs

Both slugs are INFO level, emitted from `apps/master_api/tasks.py` by the
`master_api.tasks` logger. The full string «slug» lives in the log
**message body**, not the logger name — `JsonFormatter` will surface
it under the `message` key of each JSON line.

### `auto_draft.idle_active_draft_skipped`

Source: `apps/master_api/tasks.py:368-375`. Verbatim format string:

```
"master_api.tasks.auto_draft.idle_active_draft_skipped "
"conv=%s draft=%s age_seconds=%.1f window=%ds"
```

Structured fields in the message body:

- `conv=<uuid>` — `Conversation` row id.
- `draft=<uuid>` — existing ACTIVE `AiDraft` row id.
- `age_seconds=<float, 1dp>` — `(now - draft.created_at).total_seconds()`.
- `window=<int>d` — current `IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS` env value at firing time, with a literal trailing `d` (`60d` style — the `%ds` format suffix is part of the slug). When parsing, strip the trailing `d`.

Interpretation: suppress fired. LLM call saved. Master is presumed
still looking at the existing draft for this customer msg.

The task return value carries `{"generated": False, "reason": "idle_active_draft_skipped"}`.

### `auto_draft.suppress_skipped_trigger_drift`

Source: `apps/master_api/tasks.py:385-394`. Verbatim format string:

```
"master_api.tasks.auto_draft.suppress_skipped_trigger_drift "
"conv=%s draft=%s draft_trigger=%s latest_user_msg=%s "
"age_seconds=%.1f"
```

Structured fields:

- `conv=<uuid>` — `Conversation` row id.
- `draft=<uuid>` — existing ACTIVE draft id (the one about to be REPLACED).
- `draft_trigger=<uuid|None>` — existing draft's `trigger_message_id`. `None` is rendered as the literal string `None` by `%s`.
- `latest_user_msg=<uuid|None>` — latest customer `Msg` id in the conversation at gate-eval time. `None` rendered the same way.
- `age_seconds=<float, 1dp>` — same definition as above.

Interpretation: suppress could've fired (draft was young) but newer
customer Msg arrived OR draft had NULL trigger. We let `generate` proceed
to REPLACE the stale draft.

Note: this slug includes `age_seconds` but **not** `window` — the
window value is constant across the firing window of any given worker
process; correlate with the `idle_active_draft_skipped` slug from the
same time bucket to pull window.

### Counter-slug (predates PR #700)

`auto_draft.generated` is emitted at `apps/master_api/tasks.py:552` on
every successful end-to-end generate. Format: `"master_api.tasks.auto_draft.generated conv=%s master=%s draft=%s"`. Panel 1 + Panel 4 use this as the «normal generate» counter.

## Pilot week-1 dashboard

Source: structured JSON log scraping. Two realistic backends.

- **Sentry**: search by `message:"auto_draft.idle_active_draft_skipped"` etc. — works today via Sentry breadcrumbs / messages. Coarse-grained (Sentry isn't a log warehouse) — fine for activity panels (1, 2) but lossy for histograms (3, 4).
- **journalctl + jq**: `journalctl -u ai-bot-workers@* -o cat | jq 'select(.message | test("auto_draft.idle_active_draft_skipped"))'` — exact + fast for the volumes expected at pilot scale (1 tenant, <50 masters); slower at scale.

Until a real aggregator is wired, panels are **manual grep**. Document each grep run's findings in §Tuning history.

### Panel 1: Suppress activity over time

- **Query (jq)**: count of JSON lines where `.message` contains `auto_draft.idle_active_draft_skipped`, bucketed by minute.
- **Query (Sentry)**: messages search `"auto_draft.idle_active_draft_skipped"`, time series with 1-min buckets.
- **Visualization**: time series, 1h window per tenant.
- **Healthy**: non-zero but < 10/min per tenant during business hours.
- **Anomaly: zero suppress over 1h with non-zero inbound msg volume.** Check feature flag (`IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS=0` would disable; see §«How to tune»), suppress gate ordering regression (Step 2b might have been re-ordered), or worker restart needed after env change.
- **Anomaly: > 30/min for a single tenant.** Either bursty master cohort, or window too long suppressing legitimate regenerations.

### Panel 2: Trigger-drift rate

- **Query (jq)**: rolling 1h ratio
  `count(.message matches "suppress_skipped_trigger_drift") / (count("suppress_skipped_trigger_drift") + count("idle_active_draft_skipped"))`.
- **Visualization**: percentage gauge or 1h sliding-window time series.
- **Healthy**: 20-50% (some bursty customer conversations cause drift; most don't).
- **Anomaly: > 75%.** Window too long — almost every suppression decision is being overridden by a newer customer Msg before the master taps. Tighten window per §Tuning protocol (likely to 30-40s).
- **Anomaly: < 5%.** Either window too short to ever see drift (also implies suppress isn't really doing useful work — masters always tap before the window expires), OR a bug in `trigger_message_id` propagation. Check that `AiDraft.trigger_message_id` is being set on generate (it's a column populated in PR #540 territory; if a regression NULLs it, ALL trigger-drift events look like the «NULL trigger» branch instead of the «mismatch» branch).

### Panel 3: `age_seconds` distribution

- **Query (jq)**:
  ```
  journalctl -u 'ai-bot-workers@*' -o cat --since "24h ago" \
    | jq -r 'select(.message | test("auto_draft.idle_active_draft_skipped"))
             | .message
             | capture("age_seconds=(?<a>[0-9.]+)")
             | .a'
  ```
  Pipe to a histogramming tool (`datamash hist 0,5,10,...,60`).
- **Visualization**: histogram, 5s buckets from 0 to `window`.
- **Healthy**: distribution centered around 20-40s with a long tail toward 60s (window cap).
- **Anomaly: most events clustered near `age_seconds ≈ window`** (e.g. 55-60s when window=60s). Masters are slow to decide; consider loosening window per §Tuning protocol.
- **Anomaly: bimodal distribution** (cluster < 10s + cluster near window). Two master cohorts with very different decision speeds — consider per-tenant tuning (env var is process-wide in MVP; per-tenant tuning is a future enhancement, not currently supported).

### Panel 4: Tap-to-decide latency (ground truth)

This is the **ground-truth signal** the window should approximate.

- **Source**: time delta between `auto_draft.generated` slug (`apps/master_api/tasks.py:552`) and the master's tap on the corresponding draft.

**Caveat — there is NO INFO log slug today on send-as-me / release-to-ai.** The endpoints `apps/master_api/views.py::conversation_draft_send_as_me` (line 1199) and `conversation_draft_release_to_ai` (line 1251) update `AiDraft.status` in the DB (`SENT_AS_MASTER` / `RELEASED_TO_AI`) but do not emit a structured log line. Three ways to recover tap-to-decide latency at pilot:

1. **DB-derived (canonical for week 1):** join `AiDraft` rows where `status IN (SENT_AS_MASTER, RELEASED_TO_AI)` to themselves on the generate event — compute `acted_at - created_at`. `acted_at` lives in `AiDraft.updated_at` for these terminal statuses. Read-only SQL on prod replica; no code change required.
2. **Nginx / WSGI access log:** the `POST /api/master/conversations/<uuid>/drafts/<uuid>/send-as-me` and `.../release-to-ai` requests are in the access log with timestamps. Pair with the audit row keyed by `draft_id`.
3. **Future enhancement:** emit `auto_draft.acted` INFO slug from both view handlers with `conv=%s draft=%s decision=<send_as_me|edit|release_to_ai> latency_ms=%d`. **Filed as nice-to-have (separate issue, see §Nice-to-have).**

- **Visualization**: histogram of `acted_at - created_at` for ACTIVE-then-terminal drafts.
- **Healthy median**: 30-60s.
- **Use median + p75 to calibrate the window** per the decision matrix below.

## Tuning protocol

### Week 1 — collect baseline

Default window stays at `60`. Watch panels 1-4 daily. Aim for ≥ 1000
suppress events across pilot tenants to have a statistically meaningful
sample. If volume is too low (single tenant, low message flow), extend
the baseline window to 14 days before tuning.

### Week 2 — tune

Decision matrix:

| Tap-to-decide median (panel 4) | Current window (60s) verdict | Action |
|---|---|---|
| < 30s | Too long, suppressing past master action | Tighten to `2 × p75` (e.g. window=45s if p75=22s) |
| 30-60s | Approximately right | Keep at 60s OR fine-tune to `1.5 × median` |
| 60-90s | Slightly short, missing genuine «still viewing» cases | Loosen to `1.5 × median` (e.g. 90s if median=60s) |
| > 90s | Too short for this cohort | Loosen to 120s; review whether pilot mastery / training is the lever instead |

Interlock with trigger-drift rate (panel 2):

- If `trigger_drift_rate > 75%` → **tighten regardless** of tap-to-decide; window is straddling bursty customer messages.
- If `trigger_drift_rate < 5%` AND `tap-to-decide >> window` → loosen.

### How to tune

```bash
# Production env update (operator) — pick the value from the decision matrix.
sudo vi /etc/ai-bot-platform/.env
# IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS=45

# Roll the worker pool so the new value is picked up. The setting is
# loaded via os.environ at settings-module import time; the task does
# `getattr(settings, "IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS", 60)`
# per call which reads the FROZEN module attribute, not the live env.
# This is the same restart-required pattern as STRICT_TENANT_REFUSE
# (see docs/runbooks/strict-tenant-refuse-flip.md §«⚠ Flip requires
# worker restart»).
sudo systemctl restart ai-bot-platform-prod-celery
```

No code deploy needed. Setting `IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS=0` disables the suppress entirely (regression escape hatch — explicitly supported, see `apps/master_api/tasks.py:300-303`).

### Document tuning decision

Append a row to §Tuning history with:

- Date (UTC).
- Old value → new value.
- Rationale (which panel signal triggered it — e.g. «panel 4 median=22s, p75=18s → 2×p75=36s, rounded to 45s»).
- Verification: 24h after change, what changed in panels 1-4? Did the suppress rate move into the 10-30% band? Did trigger-drift drop?

## Alerts (pre-pilot setup)

Three alerts. All wire-up is **operator-side** — this codebase has no
in-repo alerting backend (see §Observability stack reconnaissance).
Wire each in the team's chosen monitoring tool (Sentry alert rules,
Grafana alerting, or a cron + curl + Slack webhook against the
journalctl grep, depending on what's in place at pilot time).

### Alert 1: Suppress gate broken

- **Condition**: `count(message contains "auto_draft.idle_active_draft_skipped") == 0` over 24h, AND `count(message contains "auto_draft.generated") > 50` over the same window.
- **Severity**: P1 — the suppress gate may have regressed; the #659 collision race is re-opened.
- **Action**:
  1. Check `IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS` env var on prod worker host (`systemctl show ai-bot-platform-prod-celery --property=Environment` or `cat /etc/ai-bot-platform/.env`). If `0`, the suppress is intentionally disabled — confirm with §Tuning history.
  2. Check Step 2b ordering in `apps/master_api/tasks.py::auto_generate_draft_for_inbound` (suppress block must run AFTER human-locked check but BEFORE master-involvement lookup).
  3. Check worker restart timestamp vs. the last env-var change — env-var changes require restart.

### Alert 2: Trigger-drift overwhelming

- **Condition**: rolling 4h `trigger_drift_rate > 75%` (per panel 2 query).
- **Severity**: P2 — window too long; masters are being shown stale drafts that the suppress logic refused to refresh.
- **Action**: tighten window per §Tuning protocol decision matrix. Likely target: 30-40s. Restart worker pool. Verify panel 2 returns to the 20-50% healthy band within 4h.

### Alert 3: Cost spike vs baseline

- **Condition**: LLM cost per master per day jumps > 2× pre-suppress baseline (compute baseline from week-of-2026-07-08, pre-pilot dry run).
- **Severity**: P2 — suppress may not be firing; unexpected regen volume.
- **Action**:
  1. Cross-check Panel 1 (suppress activity) — is suppress count near zero? If yes → see Alert 1 troubleshooting.
  2. If suppress is firing normally → cost spike is not suppress-related; investigate Anthropic provider cost, conversation volume, or per-turn token budget separately.

## Tuning history

| Date (UTC) | Old | New | Rationale | Verification |
|---|---|---|---|---|
| 2026-07-15 | — | 60 (default) | Initial pilot launch | Baseline week 1 collection |

(Append a row on each change. Keep the table chronological — oldest first.)

## Verification

After each tuning change:

1. 1h post-restart: confirm panel 1 shows suppress events with the new `window=<N>d` value in the slug.
2. 24h post-change: panels 2 + 3 + 4 against the prior baseline — did the targeted metric move in the predicted direction?
3. Annotate §Tuning history with what was seen.

If the metric moved the **wrong** direction, revert by setting the
previous value and restarting workers. The change is hot-tunable; no
data migration is involved.

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| P0 (cost runaway, customer-visible regression) | Tech lead | Telegram / Slack on-call |
| P1 (suppress gate broken) | W1 Delta stream lead | Same |
| P2 (tuning anomaly, drift overwhelming) | W1 Delta stream | Async during business hours |

## Out of scope

- Real-world tuning data — pilot hasn't started yet; this runbook is
  the protocol, not the result.
- Grafana JSON committed — dashboards-as-code is not currently a
  pattern in this repo; manual grep is the documented fallback.
- Code instrumentation changes — INFO log slugs are already in place
  from PR #700; no new emit added by this PR.
- Code-level metric exporter — already separately tracked in issue
  [#698](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/698).
- Alert wiring in a specific monitoring tool — operator's task,
  out-of-scope for this docs-only PR.

## Nice-to-have (file as separate issues)

- **`auto_draft.acted` INFO slug from `conversation_draft_send_as_me` + `conversation_draft_release_to_ai`** with `decision=<send_as_me|edit|release_to_ai> latency_ms=%d` — would make Panel 4 (tap-to-decide latency) a pure-log derivation instead of requiring a DB join. Today's workaround: SQL on `AiDraft(status, updated_at - created_at)`.
- **Per-tenant `IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS`** — current MVP is a process-wide env var. Bimodal `age_seconds` distribution across tenants (panel 3 anomaly) would motivate this.
- **Histogram metric exporter** for `age_seconds` and `tap-to-decide` — would make panels 3 + 4 first-class instead of jq-derived. Aligns with issue #698.

## Related

- PR #700 — issue #659 (collision race fix) — adds the suppress gate this runbook tunes.
- Issue #690 — this PRE_PILOT follow-up (tuning + dashboard + alerts).
- Issue #698 — code-level metric exporter (separate stream).
- `docs/runbooks/strict-tenant-refuse-flip.md` — sibling runbook covering env-var-flip-requires-worker-restart semantics.
- `apps/master_api/tasks.py::auto_generate_draft_for_inbound` — code under test.
- `apps/master_api/tests/test_auto_draft.py::TestIssue659IdleActiveSuppress` — 6 tests covering the suppress branches; useful when investigating an Alert 1 «suppress broken» symptom.

## Changelog

- 2026-05-25 — initial draft (issue #690, PR follow-up to #700).
