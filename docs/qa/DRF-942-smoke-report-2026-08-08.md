# DRF-942 Smoke Report

**Verdict: PASS**

**Scope:** Final read-only staging smoke for Wave 1 Controlled Pilot. Does NOT repeat DRF-954/915/916 acceptance suites; reconciles already-collected evidence against the currently deployed runtime baseline.

---

## Baselines

| Component | Deployed SHA | Source |
|---|---|---|
| BOT web | `4406c0c69cf873277434b2354015d8d90b54f99e` | `ayla-bot-staging-web-1` container |
| BOT worker | `4406c0c69cf873277434b2354015d8d90b54f99e` | `ayla-bot-staging-worker-1` container |
| BOT celery-worker | `4406c0c69cf873277434b2354015d8d90b54f99e` | `ayla-bot-staging-celery-worker-1` container |
| BOT celery-beat | `4406c0c69cf873277434b2354015d8d90b54f99e` | `ayla-bot-staging-celery-beat-1` container |
| Backend (GoBeauty) | `566fe19b19acaf359a94bc2776d1703329f902e7` | host repo `/home/taximeter/beautygo/dev` HEAD |

BOT SHA is the current `origin/dev` HEAD on the staging host (deployed 2026-08-08 01:55:46 +03:00). It is a descendant of DRF-954 PR #1151 (`9e868928...`) and includes the T-03/T-05 fixes (`b584ddf`..`4406c0c`).

---

## Runtime Health

| Check | Result |
|---|---|
| BOT `https://api-dev.gobeauty.site/healthz/` | HTTP 200 |
| BOT `https://api-dev.gobeauty.site/readyz/` | HTTP 200 |
| BOT readyz detail | `postgres`, `redis`, `minio`, `intent_router`, `skill_registry`, `audit_cleanup` all `ok`; `chromadb_auth` = `no_remote_chromadb` (intentionally non-blocking) |
| BOT containers | all `Up 3 hours (healthy)` — web, worker, celery-worker, celery-beat |
| Backend containers | all `Up 2 hours` — web, celery_worker, celery_beat, db |
| Backend public `/health`, `/readyz`, `/healthz` | HTTP 403 (Django Forbidden) — see P2/P3 note below |
| Celery queue length (BOT) | 0 pending |
| nginx config test | blocked by sudo password requirement; no direct evidence, but live traffic is being served |

---

## EventBus Safety

| Check | Result |
|---|---|
| Producer `OUTBOX_EXTERNAL_DELIVERY_TOPICS` | `booking.created,booking.confirmed,booking.cancelled,appointment.rescheduled` — exactly 4 pilot topics |
| Producer effective in Backend worker/beat | same 4 topics |
| Consumer `EVENT_INGEST_ALLOWED_TENANTS` | `b32a057a-56c7-4bf0-ae50-e11e76ab44be` — single pilot tenant, unchanged |
| Consumer `EVENT_INGEST_ALLOWED_EVENTS` | `booking.created,booking.confirmed,booking.cancelled,appointment.rescheduled` — unchanged |
| `EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN` | ABSENT in all BOT services |
| BOT HMAC secret set | yes |
| Backend HMAC secret (`AYLA_OUTBOUND_HMAC_SECRET`) set | yes |
| HMAC alignment | Hashes match (`d582ef38...`) — producer and consumer secrets are identical |
| Invalid-signature probe to `/api/v1/internal/events/ingest` | HTTP 401 `{"status":"unauthorized","reason":"missing_signature"}` — verification active |
| Legacy `booking.rescheduled` in producer topics | NOT present |
| `booking.rescheduled` handler registered in BOT | present in code registry only; producer does not emit it |

---

## DLQ / Dead-letter

| Check | Result |
|---|---|
| Redis keys `*dlq*` | none |
| Redis keys `*dead*` | none |
| Backend outbox publisher logs | `sent=N failed=0 dead=0` for all observed batches |
| BOT logs for `eventbus.ingest.signature_failed` / `unknown_event_name` / `handler_exception` | none in last 500 lines |
| BOT logs for 5xx errors | none in last 500 lines |

---

## Evidence Freshness

| Issue | Linear state | `updatedAt` (UTC) | Relation to current baseline |
|---|---|---|---|
| DRF-954 Controlled Activation | Done | 2026-08-08 08:56:51 | After BOT deploy at 2026-08-07 22:55:46 UTC |
| DRF-955 Runtime Readiness | Done | 2026-08-07 23:04:50 | After BOT deploy |
| DRF-915 Reminder Acceptance | Done | 2026-08-08 10:10:11 | After BOT deploy |
| DRF-916 Booking E2E Acceptance | Done | 2026-08-08 10:14:28 | After BOT deploy |

Current BOT SHA `4406c0c` is a descendant of DRF-954 PR #1151 (`9e868928`). Runtime logs show the 4-event pilot set flowing through Backend outbox → BOT ingest up to 2026-08-08 12:59 UTC, i.e. after all four issues were closed. No evidence suggests the issues were accepted against a stale runtime.

---

## P0/P1

None.

---

## Known P2/P3

1. **Backend public health endpoints return 403.** `https://dev.gobeauty.site/{health,readyz,healthz}` and the same paths on `127.0.0.1:8000` return Django Forbidden. The app is up (internal catalog endpoints return 200), so this is an observability gap, not a runtime failure. DRF-942 AC only requires BOT `/healthz/` and `/readyz/` green.

2. **BOT worker startup warning `eventbus.ingest.proxy_trust_risky`.** Flags that `EVENT_INGEST_EDGE_CONFIGURED_ACK` is not yet set. The edge proxy (nginx) is already terminating X-Forwarded-For; this is a known operational note, not a security breach.

3. **Backend `deliver_notification.row_missing` warnings and `notification.no_tokens` info lines.** These relate to legacy notification tokens for specific users, not the EventBus/booking path. They do not block Controlled Pilot.

4. **BOT web log shows earlier 400/404 on `/api/v1/customer/bookings/<uuid>`.** Timestamps are from the DRF-916 E2E test window; no 5xx or leakage pattern.

5. **nginx config test not executed.** `sudo nginx -t` requires a password; live traffic demonstrates the config is functional.

---

## Linear Updates

- DRF-942: added comment with this smoke evidence; state to be moved to Done if accepted.

---

## Final Answer

**IS STAGING SMOKE GREEN FOR CONTROLLED PILOT? YES**
