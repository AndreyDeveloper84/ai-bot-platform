# C6 — Cross-service E2E smoke: scope (Gamma prep)

**Status:** scoping only, no test code. Block C owns implementation,
after Alpha's C1 + the HTTP outbox publisher land.
**Author:** Stream Gamma (stabilization sprint).
**Inputs:** the A10 canonical fixtures in `tests/fixtures/contracts/`.

---

## 1. What the smoke proves

One happy-path round trip across the Ayla → bot-platform event seam:

```
Ayla: Appointment committed
  → OutboxEvent row written (transactional outbox)
  → HTTP publisher POSTs the envelope to
       bot-platform  POST /api/v1/internal/events/ingest
       (HMAC-signed, per event-contract.md §6)
  → bot: HMAC + timestamp verified
       → parse_envelope() accepts the envelope
       → dispatch → consumer runs → IngestDedupe row written
  → Ayla: OutboxEvent.bot_delivered_at is set
       (per memory outbox_dual_delivery_fields)
```

Assertion surface (minimum):
1. Publisher POST returns 2xx.
2. A bot `IngestDedupe` row exists keyed by the envelope `event_id`.
3. The consumer side-effect happened (e.g. `RemoteBookingProxy` row for
   `booking.created`).
4. Ayla `OutboxEvent.bot_delivered_at` is non-null.
5. Replay of the same `event_id` is a no-op (idempotency, §5).

---

## 2. Two flavours — keep them separate

### 2a. Fixture-driven smoke (owned here, runs in CI)
- Bot side loads `tests/fixtures/contracts/booking.created.v1.json` and
  POSTs it to the ingest view with a valid HMAC header. No Ayla process.
- Deterministic, hermetic, fast. Lives in `apps/eventbus/tests/`.
- Proves the **bot** half: HMAC → parse → dispatch → dedupe → side-effect.
- This is the CI gate. It is **not** a true integration test — it never
  exercises Ayla's outbox/publisher.

### 2b. Live integration smoke (Block C, staging only)
- Real Ayla emits via real HTTP publisher to a real bot instance.
- Proves wiring CI can't: publisher retry/backoff, HMAC secret parity
  across deploys, network/TLS, `bot_delivered_at` write-back.
- Runs against staging on a schedule or pre-pilot gate, NOT per-PR
  (needs both services up + shared secret).

Rule of thumb: **CI = fixture-driven (2a); pre-pilot = live (2b).**
Same fixtures feed both, so a 2a pass and a 2b pass mean the same bytes.

---

## 3. Hard dependencies (smoke is BLOCKED until these land)

1. **Alpha C1** + HTTP outbox publisher (Alpha) — no publisher, no 2b.
2. **B4 vocab fixes (Block B, Alpha joint).** The smoke CANNOT pass live
   today — Ayla emits `payment.confirmed` / `data.booking_id`, bot
   accepts `payment.captured` / `data.appointment_id`. See the B4
   mismatch issues. The fixture-driven half (2a) works now because the
   fixtures are already canonical; the live half (2b) will DLQ until the
   emitter is migrated.
3. HMAC shared-secret provisioning in staging (`event-contract.md §6`).

---

## 4. Suggested test layout (for Block C)

| Flavour | Path | Trigger |
|---------|------|---------|
| 2a fixture-driven | `apps/eventbus/tests/test_e2e_ingest_smoke.py` | every PR (CI) |
| 2b live | `tests/integration/test_cross_service_smoke.py` (marker `@pytest.mark.integration`) | staging schedule / pre-pilot |

Start 2a with `booking.created`; extend to `payment.captured` once B4
lands so the payment path is covered end to end.

---

## 5. Handoff back

- 2a (fixture-driven) is small and unblocked — recommend Gamma picks it
  up in Block C once the ingest view's HMAC test helper is confirmed.
- 2b (live) waits on Alpha C1 + publisher + B4 vocab migration.
