# Plan — Sprint 8: Observability + Shadow mode (F0.15 + F0.16 + IM-2 + IM-3)

> Theme: instrument the pipeline so production failures are visible, then run
> the platform alongside `mysite/maxbot/` in shadow against real traffic so
> we can measure agreement *before* cutover.
> Reference: `mysite/docs/arch/PHASE0_DESIGN.md` §2.3 Sprint 8 (lines 385-393), §6, §10.
> Baseline: `main @ 9ea0bc9` — Sprint 7 closed (41/44 in-repo; FAQ KB-driven
> flow live, ChromaDB Bearer-auth, Anthropic provider + L7 cost-cap).
> Linear epic: **TBD** — `[Sprint 8] Observability + Shadow mode (week 17-18)`.

## Context

Sprint 7 closed the FAQ skill end-to-end against KB chunks; the platform now
serves a real KB-grounded reply for `gpt-4o-mini`/`claude-sonnet-4-6`-routed
turns. Sprint 8 takes the platform from "feature-complete-on-paper" to
"safe-to-canary":

1. **Observability** — without trace_id propagation + Sentry + structured
   logs we cannot debug a production turn. Today a failure looks like an
   anonymous 500 in nginx logs.
2. **Shadow mode** — without running both stacks against the same traffic
   we'd canary blind. Shadow lets us measure intent agreement, action_type
   agreement, latency delta, error delta — and roll back at the edge with
   one nginx config change.
3. **Strict scope flip** — IM-2 in PHASE0_DESIGN. STRICT_TENANT_SCOPE has
   been `strict` in tests + staging since Sprint 2; production stayed in
   `audit` to surface drift without crashing. Sprint 8 flips prod to
   `strict` once shadow mode is 7-day clean.

In parallel: Sprint 7 carry-over **M1/M2/M3** (mysite catalog viewsets +
service-token middleware + webhook) lands as `[FROZEN-EXEMPT]` PRs early
in the sprint. Without M1-M3 the C-track catalog sync runs against a
fixture; folding them in here means shadow mode sees catalog drift on
real data.

## Scope from design doc — Sprint 8

- **F0.15 Observability**
  - OpenTelemetry instrumentation across pipeline (trace_id propagation
    from inbound webhook → every downstream call → audit + ReplayTrace).
  - Sentry SDK for error tracking. DSN per-environment; PII-scrubber
    matches Sprint 5 redactor.
  - Structured JSON logs with `tenant_id` + `trace_id` + `pipeline_step`
    on every line.
  - `/readyz/` aggregator (Sprint 6 / G3) flushed out: chromadb auth
    probe, breaker state, beat liveness.
- **F0.16 Shadow mode**
  - Edge nginx tee: MAX webhook POST mirrored to `mysite/maxbot/` (primary)
    and `ai-bot-platform/` (shadow). Primary's response goes to user;
    shadow's response is **dropped** at the boundary (no outbound, no
    follow-up writes).
  - Shadow Conversation/Message persistence is opt-in via tenant-level
    `shadow_mode=True` flag.
  - Daily delta dashboard: per-day intent agreement, action_type
    agreement, latency p50/p95 delta, error rate delta. Telegram digest
    at 09:00 МСК.
- **IM-2 Strict scope flip**
  - Last-day-of-sprint task once delta dashboard shows 7 consecutive
    clean days. `STRICT_TENANT_SCOPE=strict` in production .env; web +
    worker rolled. Failure rolls back via one-line env revert.
- **IM-3 carry** — Replay sampling stays at 100% in prod. Sprint 9 will
  tune down once cutover hits 50%.
- **M1/M2/M3 carry-over from Sprint 7** — `[FROZEN-EXEMPT]` PRs against
  `mysite/services_app/`. Catalog viewsets + service-token middleware +
  delta webhook. Lead approval required per CLAUDE.md.

## Exit gate

7 consecutive days of shadow mode producing:
- **≥95% intent agreement** between platform IntentDecision and the
  mysite-derived intent label (computed daily from the shadow Message
  rows + mysite Telegram-export ground truth).
- **Zero `strict_tenant_scope` violations** in production audit log
  during the 7-day window.
- **`/readyz/` green** for 6 of the 7 days (one allowed dip for routine
  ops, must clear within 15 min).
- **Sentry P0 = 0**; P1 ≤ 3 with remediation tickets filed.

## Decomposition — 38 sub-tasks across 10 tracks

### Track N — nginx tee + ingress (5 tasks)

- **N1** — `infra/nginx/maxbot-tee.conf`: configure edge to mirror
  webhook POST to both upstream pools (`mysite_maxbot` primary,
  `ai_bot_platform_shadow` secondary). `mirror /shadow` directive with
  `mirror_request_body on`. Header `X-Shadow: 1` injected on shadow
  copy.
- **N2** — `apps/channels/max/inbound.py`: detect `X-Shadow: 1` header
  → set `ChannelMessage.is_shadow=True`. Pipeline step 19 skips outbound
  when this is set.
- **N3** — `apps/conversations/models.py`: add `is_shadow` boolean on
  `Conversation` (default False). Migration. Shadow turns route to a
  separate row even if a primary Conversation exists for the same
  bot_user — so we never mutate primary state from shadow.
- **N4** — Edge dry-run procedure: nginx tee deployed in `staging` first,
  smoke against 1 synthetic webhook; only then flipped on prod with
  `mirror_request_body off` as the rollback switch.
- **N5** — `tests/integration/test_shadow_ingress.py` — POST a webhook
  with `X-Shadow: 1` → assert Conversation `is_shadow=True`, no
  `send_message` call to MAX API.

### Track T — OpenTelemetry trace propagation (5 tasks)

- **T1** — `apps/observability/otel.py`: configure OTel SDK (resource =
  service.name=ai-bot-platform, deployment.environment=<env>). Exporter
  OTLP/gRPC → `OTEL_EXPORTER_OTLP_ENDPOINT`. Local dev defaults to
  no-op exporter.
- **T2** — `apps/orchestrator/pipeline.py`: wrap each of the 19 steps
  with `tracer.start_as_current_span(f"step.{name}")`. Set attributes
  `tenant.id`, `bot_user.id`, `conversation.id`, `intent`. trace_id +
  span_id available via `trace.get_current_span().get_span_context()`.
- **T3** — `apps/audit/services.py::write_audit`: pull current
  trace_id/span_id off `trace.get_current_span()` and persist into
  AuditLog.metadata. Replay redactor allow-lists these fields.
- **T4** — `apps/replay/recorder.py::capture`: thread trace_id through
  to `ReplayTrace.trace_id`. Sprint 5 already had `trace_id` field;
  this just ensures it matches OTel's value rather than a separately
  generated UUID.
- **T5** — `apps/orchestrator/tests/test_otel.py`: drive `turn()`
  under `InMemorySpanExporter`; assert 19 spans emitted with correct
  parent chain + attributes.

### Track E — Sentry (3 tasks)

- **E1** — `apps/observability/sentry.py`: init SDK with
  `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE=0.05`.
  PII scrubber wires `apps.replay.redactor.Redactor.redact_event`
  as a `before_send` hook — reuses Sprint 5 redaction allow-list.
- **E2** — Integration: capture orchestrator `pipeline_error` events
  via `sentry_sdk.capture_exception`. Tag with `tenant_id`,
  `trace_id`, `pipeline_step`. Worker tasks tagged separately.
- **E3** — `tests/integration/test_sentry_redaction.py` — fire a
  fake `LLMError` carrying a phone number → assert Sentry payload
  has `[PHONE]` not raw digits.

### Track J — Structured JSON logs (3 tasks)

- **J1** — `apps/observability/logging.py`: `JsonFormatter` emitting
  `{ts, level, logger, msg, tenant_id, trace_id, span_id,
  pipeline_step, ...extra}`. Configure via `LOGGING` in settings/base.
- **J2** — Tenant + trace context: `tenancy.context.current_tenant_id()`
  + OTel current span injected into every record via a logging
  filter. Zero-touch for existing `logger.info(...)` call sites.
- **J3** — `tests/integration/test_log_shape.py` — capture log lines
  during a `turn()` run, parse JSON, assert required keys present
  on every record.

### Track S — Shadow Conversation/Message + delta (5 tasks)

- **S1** — `apps/tenancy/models.py::Tenant`: add `shadow_mode` boolean
  (default False). Migration. Admin checkbox.
- **S2** — `apps/orchestrator/pipeline.py::turn`: when
  `ChannelMessage.is_shadow=True` OR `tenant.shadow_mode=True`,
  short-circuit step 19 (outbound to user). Persist shadow
  Conversation/Message rows with `is_shadow=True`.
- **S3** — `apps/observability/delta.py::compute_daily_delta(date,
  tenant)` — joins shadow Message rows with mysite Telegram-export
  ground truth (CSV ingest from `data/mysite_export/<date>.csv`) on
  `(bot_user_id, text, ts±60s)`. Returns `DeltaSummary(intent_agreement,
  action_type_agreement, latency_p50_delta_ms, latency_p95_delta_ms,
  error_delta_pct)`.
- **S4** — Celery beat `compute_shadow_delta` daily 08:00 МСК →
  persists `ShadowDeltaSnapshot` (new model: date, tenant FK, JSON
  payload, agreement floats). Telegram digest at 09:00 МСК via
  `notifications/telegram.py`.
- **S5** — `apps/observability/tests/test_delta.py` — synthetic shadow
  Messages + ground-truth CSV → asserts agreement math + edge cases
  (timestamp drift, missing ground truth row, intent renamed).

### Track D — Delta dashboard (3 tasks)

- **D1** — `apps/observability/views.py::shadow_dashboard` — read-only
  HTML view (Django admin-style) showing last-14-days
  `ShadowDeltaSnapshot` rows: intent agreement bar, latency delta line,
  error rate, click-through to underlying turn samples.
- **D2** — `/admin/observability/shadow/` URL + nav entry under the
  Observability app's admin section.
- **D3** — `tests/e2e/test_shadow_dashboard.py` — GET the page as
  staff user; assert it renders rows for seeded snapshots.

### Track M — mysite carry-over (3 tasks, cross-repo)

> **Cross-repo**: lands on `github.com/AndreyDeveloper84/formula_tela`
> (not this repo). PRs tagged `[FROZEN-EXEMPT]`. Lead approval
> required per CLAUDE.md `mysite/maxbot/` freeze policy.

- **M1** — `mysite/services_app/api/v1/catalog/` — 4 DRF
  ReadOnlyModelViewSets (Service / Master / FAQ / HelpArticle) +
  `?since=` filter + cursor pagination. (DRF-592 carry-over.)
- **M2** — `mysite/services_app/api/v1/catalog/middleware.py` —
  service-token auth via header `X-Service-Token`. Token sourced
  from `os.environ.get("AI_BOT_PLATFORM_TOKEN")`. (DRF-593
  carry-over.)
- **M3** — `mysite/services_app/api/v1/catalog/webhooks/` — POST
  endpoint accepts platform-side webhooks for delta push (`event in
  {created, updated, deleted}`, signature header). (DRF-594
  carry-over.)

### Track R — Runbooks (3 tasks)

- **R1** — `docs/runbooks/rollback-procedure.md` filled out: traffic
  routing controls (nginx upstream switch), rollback decision tree,
  last-known-good commit SHA + image tag procedure. Tested via a
  game-day in staging.
- **R2** — `docs/runbooks/shadow-mode-launch.md` — first-time launch
  checklist (nginx tee config, smoke procedure, agreement threshold
  monitoring) + emergency-disable (`mirror_request_body off`).
- **R3** — `docs/runbooks/strict-scope-flip.md` — IM-2 procedure:
  pre-flip checks (7-day clean delta, zero violations in audit log),
  flip command, rollback (single env var revert + worker rolling
  restart). Tested in staging.

### Track F — STRICT_TENANT_SCOPE flip (2 tasks)

- **F1** — Production env flip: `STRICT_TENANT_SCOPE=strict` set in
  `/etc/ai-bot-platform/.env`; `docker compose up -d --force-recreate
  web worker` rolling restart. Single-line revert path documented in
  R3.
- **F2** — Post-flip verification: `apps/audit/models.AuditLog` query
  for any `tenant_scope_violation` action in the 24h post-flip →
  must be zero. Telegram alert if a violation lands.

### Track G — Gates (6 tasks)

- **G1** — `tests/e2e/test_observability_stack.py` — full pipeline turn
  with OTel + Sentry + JSON logs all wired; assert trace_id propagates
  from webhook headers → audit → ReplayTrace; assert Sentry event has
  trace_id tag; assert log line carries tenant_id + trace_id.
- **G2** — `tests/integration/test_shadow_no_outbound.py` —
  `tenant.shadow_mode=True` turn → assert `apps.channels.max.outbound.
  send_message` was NEVER called; shadow Conversation persisted.
- **G3** — Replay diff threshold test:
  `tests/e2e/test_replay_diff_vs_mysite.py` — replay 20 golden traces
  through platform; assert ≥95% match against captured mysite ground
  truth on `intent + action_type`.
- **G4** — `/readyz/` aggregator coverage: extend
  `apps/orchestrator/health.py` to probe chromadb auth (HEAD
  `/api/v2/heartbeat` with token), Celery beat liveness via Redis
  heartbeat key, audit cleanup last-success ≤ 25h.
- **G5** — `tests/smoke/test_otel_export.py` — local `InMemorySpanExporter`
  smoke; CI green light for the OTel pipeline shape even when no real
  collector is reachable.
- **G6** — Sprint 8 epic close-out: Linear status flips, roll-up
  comment with sub-issue IDs, Sprint 7 carry-over receipt (M1/M2/M3
  landed status).

**Total: 38 sub-tasks** (5 N + 5 T + 3 E + 3 J + 5 S + 3 D + 3 M + 3 R + 2 F + 6 G).

## Decisions baked

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Shadow mode delivery | nginx `mirror` directive (real-traffic tee) | Closest to canary behavior; rollback is one `mirror_request_body off`. Replay-based shadow misses race-y timing |
| 2 | OTel exporter | OTLP/gRPC to a self-hosted collector (Jaeger or Tempo) | Vendor-neutral; collector picks the backend. Honeycomb/Lightstep are Phase 1 calls |
| 3 | Sentry sampling | `traces_sample_rate=0.05` | 5% trace sampling is plenty at Phase 0 traffic (single tenant); errors always 100% |
| 4 | Shadow agreement metric | mysite Telegram-export CSV as ground truth | Avoids parsing mysite Postgres tables; CSV is already produced for incident review |
| 5 | Shadow opt-in scope | Per-tenant `shadow_mode` boolean | Catalog cohort flips first; production tenant flips when 95% threshold validates |
| 6 | Strict-scope flip gate | 7-day clean delta + zero audit violations | Per PHASE0_DESIGN §2.3 Sprint 8 / IM-2; matches exit-gate criteria |
| 7 | M1-M3 fold-in | Fold into Sprint 8 early | Catalog sync against fixture is shallow; real mysite catalog needed to measure drift in shadow |
| 8 | trace_id source of truth | OTel-generated, propagated through Replay + Audit | One ID across the stack; eliminates ID-mapping table. Sprint 5 ReplayTrace already had a `trace_id` field — wire it to OTel here |
| 9 | Structured logs library | stdlib `logging` + custom JSON formatter | Avoid pulling in `structlog`; the formatter is ~40 LOC; OTel already provides trace_id |
| 10 | Delta dashboard rendering | Django admin-style HTML (no SPA) | Sprint 8 is observability-first; no need for React. Phase 1 may rebuild on Grafana |

## Critical files

### New
- `apps/observability/__init__.py`, `otel.py`, `sentry.py`, `logging.py`, `delta.py`, `views.py`, `admin.py`, `urls.py`
- `apps/observability/models.py::ShadowDeltaSnapshot`
- `apps/observability/tasks.py::compute_shadow_delta`
- `apps/observability/migrations/0001_initial.py`
- `apps/observability/tests/test_otel.py`, `test_delta.py`, `test_sentry_redaction.py`, `test_logging.py`
- `apps/orchestrator/health.py` (extend Sprint 6 stub)
- `apps/conversations/migrations/0XXX_conversation_is_shadow.py`
- `apps/tenancy/migrations/0XXX_tenant_shadow_mode.py`
- `infra/nginx/maxbot-tee.conf`
- `docs/runbooks/rollback-procedure.md` (flesh out the Sprint 0 skeleton)
- `docs/runbooks/shadow-mode-launch.md`
- `docs/runbooks/strict-scope-flip.md`
- `tests/integration/test_shadow_ingress.py`, `test_shadow_no_outbound.py`, `test_log_shape.py`, `test_sentry_redaction.py`
- `tests/e2e/test_observability_stack.py`, `test_shadow_dashboard.py`, `test_replay_diff_vs_mysite.py`
- `tests/smoke/test_otel_export.py`

### Modified
- `config/settings/base.py` — OTel + Sentry + JSON logging config; `SHADOW_GROUND_TRUTH_PATH`
- `config/settings/production.py` — fail-fast on missing `SENTRY_DSN`, `OTEL_EXPORTER_OTLP_ENDPOINT`; `STRICT_TENANT_SCOPE=strict` toggle at end-of-sprint
- `apps/orchestrator/pipeline.py` — OTel spans around each step; shadow-mode short-circuit at step 19
- `apps/channels/max/inbound.py` — read `X-Shadow` header → `ChannelMessage.is_shadow`
- `apps/conversations/models.py::Conversation` — `is_shadow` field
- `apps/tenancy/models.py::Tenant` — `shadow_mode` field
- `apps/audit/services.py::write_audit` — inject OTel trace_id
- `apps/replay/recorder.py::capture` — accept OTel-supplied trace_id
- `apps/orchestrator/urls.py` — `/readyz/` aggregator
- `.env.example` — `SENTRY_DSN=`, `OTEL_EXPORTER_OTLP_ENDPOINT=`, `SHADOW_GROUND_TRUTH_PATH=`
- `docker-compose.yml` — sidecar `otel-collector` (optional dev profile)

### Cross-repo (mysite/services_app/, FROZEN-EXEMPT)
- `mysite/services_app/api/v1/catalog/views.py` — 4 ReadOnlyModelViewSets
- `mysite/services_app/api/v1/catalog/serializers.py`
- `mysite/services_app/api/v1/catalog/middleware.py` — `X-Service-Token` auth
- `mysite/services_app/api/v1/catalog/webhooks/views.py` — delta-push endpoint
- `mysite/services_app/api/v1/catalog/urls.py`
- `mysite/mysite/urls.py` — wire `/api/v1/catalog/`

## Risks

1. **nginx-tee in production** is the riskiest infrastructure change of
   Phase 0. Mitigation: deploy in staging first (N4); `mirror_request_body
   off` is a one-line emergency switch; shadow Conversation/Message rows
   are isolated (`is_shadow=True`) so even a faulty platform can't
   corrupt primary state.
2. **mysite Telegram-export CSV** as ground truth — the format may shift
   without notice. Mitigation: S5 covers schema drift; if export breaks,
   shadow agreement falls back to platform-vs-platform replay diff (G3)
   for the day until format is fixed.
3. **STRICT_TENANT_SCOPE flip** may surface a long-tail violation that
   wasn't caught in tests. Mitigation: F2 audit query for 24h post-flip;
   single-env-var rollback in R3; the flip is the last move of the
   sprint, not the first.
4. **Cross-repo M1/M2/M3** review latency — Lead approval is the gating
   constraint. Mitigation: open PRs Day 1; the M-track is independent
   of N/T/E/J tracks so we work in parallel.
5. **OTel collector availability** — if the collector is down, OTel
   should never crash the pipeline. Mitigation: T1 defaults to no-op
   exporter when `OTEL_EXPORTER_OTLP_ENDPOINT` is unset; gRPC exporter
   uses lossy batched mode.

## Scope warning

38 tasks at Sprint 5-7 AI-driven velocity (~3-4/day) ≈ 10 working days.
That matches a clean 2-week sprint, with **no contingency** for the
cross-repo M-track review window. Options if it slips:

- Defer **D-track dashboard** (3 tasks) to Sprint 9 — JSON snapshot
  + Telegram digest are enough to gate the strict-scope flip. The HTML
  dashboard is convenience, not load-bearing.
- Defer **R2 + R3 runbooks** to Sprint 9 (write at canary time when
  the operator perspective is fresh).
- Defer **G3 replay-diff threshold test** if mysite export ground truth
  takes longer than expected — replace with G2 + S5 unit math for
  shadow exit-gate.

## Outputs

- **Observability stack**: OpenTelemetry traces visible end-to-end; Sentry
  catching production errors with PII-scrubbed payloads; structured JSON
  logs queryable by tenant_id + trace_id.
- **Shadow mode**: edge nginx tee splits webhook traffic; platform writes
  shadow Conversation/Message rows; daily delta dashboard surfaces
  agreement metrics + latency delta.
- **Production hardening**: STRICT_TENANT_SCOPE=strict flipped after 7
  clean shadow days; rollback procedure runbook tested in staging.
- **Cross-repo M-track closed**: mysite/services_app exposes
  `/api/v1/catalog/*` + service-token middleware + delta webhook,
  unblocking real catalog drift detection in C-track sync.
- **Canary-ready exit gate met**: ≥95% intent agreement for 7 consecutive
  days, zero strict-scope violations post-flip, /readyz/ green, Sentry
  P0 = 0. Sprint 9 starts with full confidence in 10% canary cutover.
