# DRF-945 Pilot Readiness Report

## Verdict
**NEEDS_WORK**

Техническое acceptance Wave 1 (runtime, booking E2E, EventBus, reminders, privacy) — green.
Операционная готовность к запуску 3–5 реальных пользователей **не завершена**: отсутствует операционный pilot roster, не подтверждено согласие провайдера/специалиста и не формализована support-модель. Эти gaps не являются P0/P1, но блокируют PASS по DRF-945.

---

## Accepted Baseline

| Component | Accepted SHA | Verification |
|---|---|---|
| BOT / ai-bot-platform | `4406c0c69cf873277434b2354015d8d90b54f99e` (origin/dev) | `git diff 4406c0c..HEAD` = empty (current checkout `a764275` on branch `fix/drf956-canonicalize-runtime-profile-erasure` has zero tree diff vs accepted baseline) |
| Backend (GoBeauty host repo) | `566fe19b19acaf359a94bc2776d1703329f902e7` | Not present in this repo; accepted per DRF-954/955/916/942 evidence |

**Drift check:** Subsequent commits after accepted baseline are the canonicalized DRF-956 privacy hotfix, already merged into `dev` as `4406c0c`. No functional drift detected. Untracked local artifacts (`docs/qa/DRF-942-smoke-report-2026-08-08.md`, `tests/acceptance/drf915_reminder_acceptance.py`, `scratchpad/WAVE1_T02_CONTRACT_GAPS_DECISION.md`) are evidence/test files and do not affect runtime.

---

## Pilot Tenant

- **Slug:** `formula-tela`
- **UUID:** `b32a057a-56c7-4bf0-ae50-e11e76ab44be`
- **Booking flag:** `BOOKING_VIA_AYLA_REST=true` (accepted staging-wide toggle per DRF-955 owner decision)
- **Service linkage:** 58/58 active services linked (`ayla_service_id` populated) — DRF-955/942 evidence
- **Identity linkage:** one linked pilot user, one unlinked synthetic user, zero identity conflicts — DRF-955 evidence

---

## Pilot User Readiness

**GAP.**

Evidence из закрытых acceptance issues:
- DRF-955: один linked pilot user (MAX channel, id partially redacted) и один synthetic/unlinked user.
- DRF-916 / DRF-915: тесты использовали synthetic identities.

Конкретный operational roster из 3–5 реальных пользователей **не зафиксирован** ни в Linear, ни в репозитории. Без понятного способа выбрать/пригласить участников запуск невозможен. Это не P0/P1, но блокирует PASS.

---

## Provider / Service Readiness

- **Services:** 58/58 active `CatalogService` rows linked to Ayla catalog (`ayla_service_id` non-null).
- **Specialists/providers:** implied by linked services; one pilot user linked.
- **GAP:** нет evidence, что реальный специалист/салон (`formula-tela`) уведомлен о Controlled Pilot и согласен принимать тестовые/пилотные записи. Необходимо подтвердить, что записи не попадут к неподготовленному специалисту.

---

## Runtime

- BOT `/healthz/` → 200, `/readyz/` → 200 (postgres, redis, minio, intent_router, skill_registry, audit_cleanup ok; Chroma intentionally non-blocking).
- All BOT containers stable (web, worker, celery-worker, celery-beat).
- Backend containers stable; health/ready green per DRF-955.
- Celery queue length 0.
- Django `manage.py check` passes locally on current tree.

---

## Booking / EventBus / Reminder Evidence

Evidence reconciliation (не повторялись заново):

| Area | Evidence | Status |
|---|---|---|
| EventBus controlled lifecycle | DRF-954 Done — live activation passed on 4-event set | PASS |
| Booking E2E create/lookup/reschedule/cancel | DRF-916 Done — real channel path, duplicate-safe, stale 409, idempotent cancel | PASS |
| Reminders | DRF-915 Done — no duplicates/backdated, cancel clears pending, reschedule re-pegs, #1148 accepted P2 | PASS |
| Staging smoke | DRF-942 Done — runtime/EventBus/DLQ green | PASS |
| Staging validation | DRF-944 Done — all AC satisfied on current baseline | PASS |
| Privacy runtime | DRF-956 Done — confirmed delete fails closed, PII erased | PASS |

Producer topics (DRF-954/942):
- `booking.created`
- `booking.confirmed`
- `booking.cancelled`
- `appointment.rescheduled`

Consumer allowlists:
- Tenant: `b32a057a-56c7-4bf0-ae50-e11e76ab44be` only
- Events: same 4 events
- `EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN` absent

Legacy `booking.rescheduled` handler registered in code but **not** externally delivered.

---

## Privacy Operational Readiness

- DRF-956 code/runtime acceptance: **PASS**.
- Destructive delete невозможен без confirmation; wrong token → 400 / zero mutation; confirmed erase очищает phone, display_name, client_name, avatar_url, context, preferences.
- Person-level resolution защищает от cross-shell leakage; conflicting sibling `ayla_user_id` fails closed.
- **GAP:** нет dedicated runbook/четкого operational path для support-ответа на `502 partial` / `not_linked` запросы удаления. Для пилота достаточно manual support path (operator выполняет confirmed Mini App delete или использует canonicalized privacy service), но владелец и шаги должны быть назначены и задокументированы.

---

## Support Model

**Current state:** ad-hoc.

- Runtime operations and all acceptance evidence выполнены одним и тем same инженером (Андрей Тихонов — inferred из Linear assignment и комментариев).
- Incident response runbook существует, owner "Lead", formal on-call запланирован на Sprint 10.
- **Missing:** dedicated pilot support owner/operator, канал связи для 3–5 пользователей и ожидаемое время реакции.

**Proposed minimal pilot support model (to be confirmed by Product Owner):**
- Responsible person: Andrey Tikhonov (owner/operator).
- Channel: Telegram admin chat / Linear DRF-946 thread.
- Reaction time: best-effort during pilot window; Sev1 booking/P0 incidents escalate per `incident-response.md`.

---

## Rollback / Recovery

**EventBus producer:** set `OUTBOX_EXTERNAL_DELIVERY_TOPICS=` (empty), restart Backend Celery workers — OFF within minutes. Evidence: DRF-954 performed this safety reset before controlled activation.

**EventBus consumer:** clear `EVENT_INGEST_ALLOWED_TENANTS` and `EVENT_INGEST_ALLOWED_EVENTS`, restart BOT services — returns to fail-closed (coordinated with Ayla on-call, because Ayla will receive 500 → retry → dead-letter).

**Booking path:** `BOOKING_VIA_AYLA_REST` is the staging-wide toggle; flipping to `false` and restarting BOT routes booking back through legacy/YClients path.

**Runtime deploy/rollback:** Docker Compose `ayla-bot-staging` is the factual pilot topology (DRF-955). Legacy systemd units (`ai-bot-platform-dev.service`, `gobeauty-dev.service`, etc.) are inactive+disabled and cannot unexpectedly restart.

**Answer: CAN WE STOP THE PILOT SAFELY WITHIN MINUTES? YES.**

---

## Observability

| Signal | Available? | Notes |
|---|---|---|
| BOT health/readiness | YES | `/healthz/`, `/readyz/` 200 |
| Backend health | PARTIAL | Internal `/healthz/` green; public `/health*/*` returns 403 (P2 gap) |
| Booking failures | YES | Booking lifecycle logs, Mini App API logs, Backend outbox logs |
| EventBus ingest failures | YES | `eventbus.ingest.*` logs, `IngestDLQ`, `HandlerFailureTracker` |
| DLQ/dead-letter | YES | Redis `*dlq*` / `*dead*` keys, Backend outbox `dead=0` logs |
| Reminder failures | YES | `apps.bookings.tasks.send_due_reminders` logs, `BookingReminder` rows |
| Tenant verification errors | YES | `tenant_verify_rejected` audit logs with reason |

**Known warnings from DRF-942 (P2/P3, non-blocking):**
1. Backend public health endpoints 403.
2. BOT startup `eventbus.ingest.proxy_trust_risky` warning (`EVENT_INGEST_EDGE_CONFIGURED_ACK` not set).
3. Backend legacy notification token gaps (`deliver_notification.row_missing`, `notification.no_tokens`).
4. Earlier E2E 4xx artifacts in BOT logs (no 5xx/leakage).

---

## P0/P1 Gate

- **P0:** 0
- **Release-blocking P1:** 0

No new P0/P1 introduced in this validation. All historically closed P0/P1 remain closed.

---

## Accepted P2/P3

| Issue / Finding | Severity | Pilot Impact | Mitigation | Accepted |
|---|---|---|---|---|
| #1148 — SENT reminder not re-armed after future reschedule | P2 | User won't receive a new reminder after a future reschedule; old stale reminder is prevented | Confirmed in DRF-915 tests | YES |
| DRF-916 P3 — cross-tenant lookup probe limited by single-tenant DB | P3 | Cross-tenant lookup not empirically tested on prod DB; same-tenant foreign 404 verified | Tenant isolation enforced by code/allowlists | YES |
| DRF-916 P3 — rejected foreign-tenant event status-code rough edge | P3 | Pre-T0 rejected foreign-tenant event may 500 without DLQ/dedupe row | Only affects historical pre-activation events; allowlists prevent new ones | YES |
| DRF-916 P3 — duplicate create replay returns 201 | P3 | Cosmetic; no duplicate appointment created | Idempotency key guarantees no mutation | YES |
| DRF-942 — Backend public health endpoints 403 | P2 | Observability gap for external health checks | Internal health green; BOT health green | YES |
| DRF-942 — `proxy_trust_risky` warning | P3 | Operational note; edge proxy already terminates X-Forwarded-For | Acknowledged | YES |
| DRF-942 — legacy notification token gaps | P3 | Legacy notification path only; not on EventBus/booking critical path | Acknowledged | YES |
| DRF-945 — operational pilot roster undefined | GAP (not P0/P1) | Launch of 3–5 real users cannot proceed | Owner must define roster and invite method | NO (needs owner action) |
| DRF-945 — provider consent to pilot bookings not confirmed | GAP (not P0/P1) | Risk of test bookings reaching unprepared specialist | Owner must confirm provider consent | NO (needs owner action) |
| DRF-945 — formal pilot support model missing | GAP (not P0/P1) | No clear support channel/SLA for pilot users | Owner must assign support contact and channel | NO (needs owner action) |

---

## Food Scanner Optional Experiment

**Verdict: OPTIONAL PILOT EXPERIMENT — NOT READY**

Evidence:
- Code present in repo; failure path isolated from booking core.
- Master switches `NUTRITION_ENABLED` and `FOOD_PHOTO_SCAN_ENABLED` default to `false`.
- `BotUser.food_scanner_consent_at` field exists.
- Production consent-write path for `food_scanner_consent_at` is **absent**; DRF-957 explicitly allows manual consent provisioning as a temporary pilot measure.

Core Pilot GO does **not** depend on Food Scanner. To enable the experiment:
1. Core Pilot GO achieved independently.
2. Each pilot user explicitly consents.
3. Operator manually sets `food_scanner_consent_at` per user.
4. Flags enabled and smoke scan performed.

**Food Scanner НЕ блокирует core Controlled Pilot.**

---

## Readiness Matrix

| Area | Evidence | Status | Blocker? |
|---|---|---|---|
| Runtime | DRF-942/955 smoke; `manage.py check` green | PASS | No |
| Booking | DRF-916 E2E acceptance | PASS | No |
| EventBus | DRF-954 controlled activation; DRF-942 smoke | PASS | No |
| Reminders | DRF-915 acceptance | PASS | No |
| Privacy code/runtime | DRF-956/959 | PASS | No |
| Tenant provisioning | DRF-955; 58/58 services linked | PASS | No |
| Pilot users | No operational roster of 3–5 real users | **GAP** | Yes (operational) |
| Providers/services | Services linked; provider consent not confirmed | **GAP** | Yes (operational) |
| Support | Ad-hoc owner; no formal channel/SLA | **GAP** | Yes (operational) |
| Rollback | Producer OFF path + allowlist clear + deploy rollback | PASS | No |
| Observability | Health/logs/DLQ; public Backend health 403 (P2) | PASS with accepted P2 | No |
| Known issues | P2/P3 register accepted | PASS | No |
| Food Scanner optional | Flags default OFF; manual consent required | NOT READY (optional) | No |

---

## Linear Updates

- **DRF-945:** moved `Todo → In Progress`; added `PILOT READINESS VALIDATION STARTED` comment.
- **DRF-946:** status unchanged; added comment with NEEDS_WORK finding and owner actions.
- **DRF-945:** after owner actions close gaps, re-run validation and move to `Done` if PASS.

---

## Remaining Owner Actions

Before Controlled Pilot can be considered ready for Product Owner Go/No-Go:

1. **Define operational pilot roster:** 3–5 real users, their roles/scenarios, invite method.
2. **Confirm provider/specialist consent:** ensure salon/specialist knows about pilot and can safely accept test bookings.
3. **Formalize pilot support model:** assign support owner, channel (Telegram/Linear), expected response time.
4. **Document manual privacy-delete support path:** what operator does when a pilot user hits `502 partial` / `not_linked`.
5. **Decide on Food Scanner:** if included, manually provision consent per user and run smoke; otherwise keep flags OFF.
6. **Re-run DRF-945** after closing operational gaps.

---

## Final Answer

**IS CONTROLLED PILOT READY FOR PRODUCT OWNER GO/NO-GO?**

**NO.**

Технический baseline готов к передаче в DRF-946, но запуск 3–5 реальных пользователей требует закрытия операционных gaps: roster пользователей, согласие провайдера и формализацию support-модели.

DRF-946 не запущена; реальные пользователи не приглашены.
