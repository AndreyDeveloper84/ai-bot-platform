# Ayla ↔ bot-platform booking REST contract

> **Status: DRAFT — pending S2 sign-off.** This is the spec-first artifact for
> #1016. It is a *joint* S1 (bot-platform consumer) + S2 (Ayla canonical
> backend) document. **No bot-platform code may rely on these endpoints as
> live, and the real HTTP client must not be implemented, until S2 confirms
> the paths, payloads, auth, and idempotency header below.** The bot-side
> client (`apps/integrations/ayla/booking_client.py`) is currently a skeleton
> whose methods raise `NotImplementedError` for exactly this reason.

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

## 3. Endpoints (DRAFT — to confirm with S2)

> Paths, query params, and field names below are the bot-side *proposal*
> derived from what the booking skill consumes today (the YClients DTOs in
> `apps/integrations/yclients/client.py`). S2 confirms or amends.

### 3.1 Reads

| Method | Path | Purpose | Returns |
|---|---|---|---|
| GET | `/api/v1/internal/booking/specialists` | list specialists (filter `?service_id=`) | `[Specialist]` |
| GET | `/api/v1/internal/booking/services` | service catalog (filter `?specialist_id=`) | `[Service]` |
| GET | `/api/v1/internal/booking/specialists/{id}/slots` | bookable slots (`?service_ids=&from=&to=`) | `[Slot]` (or grouped by date) |

### 3.2 Writes

| Method | Path | Purpose | Idempotent |
|---|---|---|---|
| POST | `/api/v1/internal/booking/appointments` | create appointment | yes (key required) |
| POST | `/api/v1/internal/booking/appointments/{id}/cancel` | cancel | yes |
| POST | `/api/v1/internal/booking/appointments/{id}/reschedule` | move to a new slot | yes |
| GET | `/api/v1/internal/booking/appointments?external_user_id=` | the user's appointments | n/a |

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
{ "appointment_id": "a1b2c3", "specialist": { "...": "..." },
  "services": [ { "...": "..." } ], "datetime": "2026-06-10T14:00:00+03:00",
  "duration_s": 3600, "status": "confirmed" }
```

`appointment_id` is Ayla's canonical id and becomes the bot-side mirror key.

> **Open tension (see §8):** the bot's booking tools are currently int-keyed
> (`BookingRecord.record_id`, `UserRecord.id`, and the mirror marker
> `yclients_record_id=<id>` are ints). The adapter maps a **numeric**
> `appointment_id` losslessly and **fails loudly** on a non-numeric one rather
> than corrupt the mirror. Lock either a numeric-compatible id here, or commit
> to evolving the tools to a string key, before flipping the flag ON.

## 5. Idempotency

Writes carry an idempotency key so a retried bot turn cannot double-book.

- **Header name: TBD — confirm exact spelling with S2.** bot-platform's
  payment client uses `Idempotence-Key`; the nutrition client uses
  `X-Idempotency-Key`. Pick one and record it here before PR-A merges. The
  bot client passes `idempotency_key` through to whichever header S2 names.
- Key is required on `POST .../appointments` and recommended on cancel /
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

## 8. Open items before lock (owners)

- [ ] **S2:** confirm/amend §3 paths, §4 field names, §6 error codes.
- [ ] **S2:** stand up the endpoints in the Ayla canonical backend.
- [ ] **S1 + S2:** fix the idempotency header name (§5).
- [ ] **S1 + S2:** decide `appointment_id` representation — numeric-compatible
      (so the int-keyed mirror round-trips) or string (and evolve the tools).
      The adapter currently rejects non-numeric ids (§4).
- [ ] **Both:** s2s-auth convergence ADR (RS256) — supersedes §2's interim Bearer.
- [ ] **S1 (follow-up, after lock):** replace the `booking_client.py` skeleton
      with the real `requests`-based implementation; then a separate change
      flips `BOOKING_VIA_AYLA_REST` ON and retires the direct-YClients path +
      local-canonical write.
