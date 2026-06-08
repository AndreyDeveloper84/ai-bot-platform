# Ayla ↔ bot-platform booking REST contract

> **Status: LOCKED — S2 sign-off complete (2026-06-06).** The S2 internal
> Bearer surface shipped and merged to `dev` in **#193** (commit `f2dde60`,
> squashed as `d386df8`). The endpoints, auth, `appointment_id` type, and
> idempotency header below are now **confirmed against the live implementation**
> — see §3/§4/§5 (the two former MUST-lock items in §8 are resolved). S1 may
> now replace the `apps/integrations/ayla/booking_client.py` skeleton (which
> raises `NotImplementedError`) with the real HTTP client and, in a separate
> gated change, flip `BOOKING_VIA_AYLA_REST` ON.
>
> This is a *joint* S1 (bot-platform consumer) + S2 (Ayla canonical backend)
> document.

- **Owner (this doc):** S1 + S2 jointly.
- **Tickets:** #1016 (this bridge), #1014 (catalog/slot reads), #925 / #968
  (the local-mirror divergence this is the proper fix for).
- **Governing decision:** ADR-0009 — Ayla owns canonical booking state;
  bot-platform reads it over REST and drives the booking lifecycle *through*
  Ayla, keeping only a local mirror. bot-platform never writes booking rows
  directly.

---

## 1. Why

Today the booking skill calls YClients directly and writes a local
`BookingRequest` as if it were canonical. Under ADR-0009 that is backwards:
Ayla is the system of record for the booking lifecycle. This contract defines
the REST surface bot-platform consumes so the booking skill can be re-pointed
(behind the `BOOKING_VIA_AYLA_REST` flag) at Ayla, with the local row demoted
to a mirror. Locking the shape *before* writing the HTTP client avoids
freezing the wrong wire format.

## 2. Auth model (#1016 ground-truth)

`Authorization: Bearer {AYLA_INTERNAL_API_TOKEN}` on **every** call.

| Call class | Endpoints | Ayla permission | Extra header |
|---|---|---|---|
| Reads | catalog, slots | `IsInternalBearer` | — |
| Writes | create / cancel / reschedule | `IsBotServiceWithVerifiedClient` | `X-External-User-ID: bot:{channel}:{id}` |

- `X-External-User-ID` is produced by `apps.integrations.ayla.user_proxy.external_user_id_for`
  (`bot:{channel}:{channel_user_id}`). It lets Ayla bind the action to the
  consenting end-user and **verify the `TenantUserRelationship` server-side**
  (ADR-0009 rule 6) — bot-platform never asserts tenant binding unilaterally.
- This is **not** the nutrition client's `X-Service-Token` shared-secret model.
  The two integrations differ today; convergence onto a single s2s-auth scheme
  (RS256 consent-bound tokens) is tracked separately and is a **TODO** on both
  sides. Until that ADR lands, Bearer-per-#1016 is authoritative for booking.

## 3. Endpoints (LOCKED — confirmed against merged S2 surface, #193)

> Paths below are the **real** routes shipped in #193 (mounted under
> `/api/v1/internal/` in `djangoProject/urls.py`). Note: there is **no
> `booking/` path segment** — the earlier draft proposal used one; it was
> dropped at sign-off. Field names in §4 are confirmed against the S2
> serializers; the bot adapter still absorbs any DTO renames so the eight
> booking tools are unaffected.

### 3.1 Reads (`IsInternalBearer`)

| Method | Path | Purpose | Returns |
|---|---|---|---|
| GET | `/api/v1/internal/specialists/` | list specialists (filter `?service_id=`) | `[Specialist]` |
| GET | `/api/v1/internal/specialists/{id}/` | one specialist | `Specialist` |
| GET | `/api/v1/internal/specialists/{id}/slots/` | bookable slots (`?service_ids=&from=&to=`) | `[Slot]` (or grouped by date) |
| GET | `/api/v1/internal/specialists/{id}/services/` | services of a specialist | `[Service]` |
| GET | `/api/v1/internal/services/` | service catalog | `[Service]` |
| GET | `/api/v1/internal/services/categories/` | service categories | `[Category]` |

### 3.2 Writes (`IsBotServiceWithVerifiedClient` = Bearer + `X-External-User-ID`)

| Method | Path | Purpose | Idempotent |
|---|---|---|---|
| POST | `/api/v1/internal/appointments/` | create appointment | yes (`X-Idempotency-Key` honoured) |
| POST | `/api/v1/internal/appointments/{uuid}/cancel/` | cancel | yes |
| POST | `/api/v1/internal/appointments/{uuid}/reschedule/` | move to a new slot | yes |
| GET | `/api/v1/internal/me/bookings/` | the resolved user's appointments | n/a |

## 4. Payload shapes (DRAFT)

Mapped onto the bot-side DTOs in `booking_client.py` (`AylaService`,
`AylaMaster`, `AylaSlot`, `AylaBookingRecord`, `AylaUserRecord`). Field names
TBD with S2; the bot adapter absorbs renames so the eight booking tools are
unaffected.

**Specialist**
```json
{ "id": 11, "name": "Ольга", "specialization": "Массаж", "rating": 4.5, "position": "master" }
```

**Service**
```json
{ "id": 10, "title": "Массаж спины", "price_min": 1500, "price_max": 2500,
  "duration_s": 3600, "category_id": null }
```

**Slot**
```json
{ "time": "14:00", "datetime": "2026-06-10T14:00:00+03:00", "duration_s": 3600 }
```

**Create appointment — request**
```json
{ "specialist_id": 11, "service_ids": [10], "datetime": "2026-06-10T14:00:00",
  "client": { "name": "Anna", "phone": "79991234567" }, "comment": "..." }
```

**Appointment — response** (create / reschedule / list item)
```json
{ "appointment_id": "3f1c2e9a-4b7d-4c2a-9e1f-8a2b6c0d1e34",
  "specialist": { "...": "..." }, "services": [ { "...": "..." } ],
  "datetime": "2026-06-10T14:00:00+03:00", "duration_s": 3600,
  "status": "confirmed" }
```

`appointment_id` is Ayla's canonical id and becomes the bot-side mirror key.

> **✅ LOCKED (item #1, §8): `appointment_id` is a UUID string.**
> Confirmed against the merged S2 surface — `Appointment.id` is a
> `UUIDField` and the write routes are `…/appointments/<uuid:booking_id>/…`.
> The current bot adapter's numeric-id mapping (`BookingRecord.record_id`,
> `UserRecord.id`, mirror marker `yclients_record_id=<id>`) is an **interim
> placeholder**: the real `booking_client.py` must accept a UUID/string
> `appointment_id` (the local mirror keeps its own int PK, but the Ayla-id
> column stores the UUID). This is now a cutover prerequisite **satisfied on
> the S2 side**; the bot side implements it with the real client.

## 5. Idempotency

Writes carry an idempotency key so a retried bot turn cannot double-book.

- **✅ LOCKED (item #2, §8): header name is `X-Idempotency-Key`.** Confirmed
  against the merged S2 surface (`appointments/infrastructure/idempotency.py`,
  Stripe-style semantics; lookup tuple `(user, operation_name, key, target_id)`
  — so the same key reused across different appointments does not cross-replay).
  The bot client passes `idempotency_key` through as `X-Idempotency-Key`.
- Key is required on `POST …/appointments/` and recommended on cancel /
  reschedule. Ayla returns the original result on a duplicate key (no new row).

## 6. Errors

| HTTP | Bot client raises | Meaning |
|---|---|---|
| 2xx | — | success |
| 400 / 409 / 422 | `BookingBadRequestError` | validation, slot gone, consent missing — surface to user |
| 401 / 403 | `BookingBadRequestError` | auth / tenant-binding rejected (not retried) |
| 404 | depends — cancel/reschedule of a missing appt → `False` / `BookingBadRequestError` | already gone |
| 5xx, timeout, network, circuit-open | `BookingUnavailableError` | outage — caller shows fallback; trips the breaker |

Error body shape (code for mapping) — **confirm with S2**:
```json
{ "error": { "code": "SLOT_TAKEN", "message": "..." } }
```

## 7. Resilience (bot side)

- Per-call timeout (default 10s), inline circuit breaker (5 failures / 60s →
  30s cooldown), matching the nutrition client. Only `BookingUnavailableError`
  trips the breaker — `BookingBadRequestError` is user input, not an outage.
- bot fires once per turn; no caller-side retries beyond the idempotent write.

## 8. Sign-off status (owners)

**🔒 Former MUST-lock items — both RESOLVED at S2 sign-off (#193 merged):**

1. [x] **`appointment_id` = UUID** (§4). Confirmed: `Appointment.id` is a
       `UUIDField`; write routes are `…/appointments/<uuid:booking_id>/…`.
       The bot side accepts a UUID/string id in the real client.
2. [x] **Idempotency header name** (§5) — **`X-Idempotency-Key`** (confirmed
       in `appointments/infrastructure/idempotency.py`).

**Other items:**

- [x] **S2:** §3 paths confirmed (no `booking/` segment); reads `IsInternalBearer`,
      writes `IsBotServiceWithVerifiedClient`. §4/§6 shapes track the S2 serializers
      (the bot adapter absorbs renames).
- [x] **S2:** endpoints stood up in the Ayla canonical backend (#193, in `dev`).
- [ ] **Both (TODO, not a blocker):** s2s-auth convergence ADR (RS256) —
      supersedes §2's interim Bearer. Interim Bearer is authoritative until then.
- [ ] **S1 (follow-up, now unblocked):** replace the `booking_client.py` skeleton
      with the real `requests`/httpx implementation against the §3 routes; then a
      separate gated change flips `BOOKING_VIA_AYLA_REST` ON and retires the
      direct-YClients path + local-canonical write.
