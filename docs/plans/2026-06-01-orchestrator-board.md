# Orchestrator board — 2026-06-01

> Source of truth for current stream tasks. Pull from `dev`, not chat. Rules: one agent = one branch; PR base = `dev`; Code-Reviewer mandatory; stay inside your task "Scope".

## Block B — payment stabilization (UNBLOCKED after Block C)

### B-1 · Align payment event vocabulary — owner: Gamma (consumer) + Alpha (emit)
- Goal: payment event names match the contract: `payment.authorized` / `payment.captured` / `payment.failed` / `payment.refunded`.
- Scope: Gamma — `apps/eventbus/consumers/` (payment handlers, register `(event_name, version)`); Alpha — emit sites in `payments/` (OutboxEvent names).
- Steps: (1) use the emit-name audit result; (2) Alpha renames emit to canonical; (3) Gamma registers consumers on canonical names + dedupe by `event_id`; (4) shared-fixture round-trip test.
- Do NOT: enable `external_delivery_enabled` for payment until B-1 is green (else 422/DLQ); do not touch booking vocabulary (it is fine).
- Depends on: emit-name audit.
- DoD: a payment event from Ayla is observed in bot IngestDedupe under the canonical name; round-trip test green.

### B-2 · Certificate skill gate — owner: W2 (depends on W4)
- Goal: the deferred certificate flow cannot fire during pilot.
- Scope: `apps/skills/payment_failed/` (or the cert entry skill); reads `CERTIFICATE_PAYMENT_ENABLED`.
- Steps: (1) W4 ships `CERTIFICATE_PAYMENT_ENABLED=False` (B-flag); (2) W2 adds the gate + test "flag off -> cert unavailable".
- Do NOT: implement the cert flow itself (deferred post-pilot); gate only.
- DoD: with flag off, cert path returns "unavailable"; test exists.

### B-flag · CERTIFICATE_PAYMENT_ENABLED — owner: W4
- Scope: `config/settings/*`, default `False`. DoD: flag readable, default off; unblocks B-2.

### B-3 · Bot retry-only enforcement — owner: Gamma/W2
- Goal: bot does NOT create payments; retry-only by idempotency (canonical create = Ayla `POST /payments/create {appointment_id, return_url}`).
- Scope: ensure no create-payment path in bot; retry = re-POST with same `Idempotence-Key`.
- DoD: import-linter/test confirms no create-payment in bot.

## Marching orders (scope-locked)

- Gamma -> NOW: bot-half joint smoke C6. Scope: `apps/eventbus/`. DoD: consumer fires + IngestDedupe assert on shared fixtures green. Then B-1.
- W2 -> pickup: B-2 if B-flag merged, else confirm E0#1 (master-context) / MAX-hardening status (pilot items outrank D3), then D3. Do NOT touch Ayla/frontend.
- W4 -> confirm B-flag status; drive food_scanner skill backend (waiver on `apps/skills/food_scanner`, W2 review, secret `NUTRITION_SERVICE_TOKEN`, contract = live `/nutrition/internal/{scan,profile,summary}`).
- W1 -> Profile tab deferred-scope from `dev`. Do NOT backend.
- W3 -> review PR #940; security columns in `contract-matrix.md` (auth fragmentation = priority); review #925/#927.
- Alpha -> Block C done; next B-1 emit renames; hold payment delivery until B-1.
- Human/ops -> `GH_DEPLOY_TOKEN` (unblocks bot#938 / E0#6 escalation recheck).

## Status references
- PR #939 merged (profile-flow Variant-3 deferral).
- PR #940 open (audits + contract-matrix draft; reviewer W3).
- A9 closed both halves (bot #935 + Ayla #176).
- Block C closed Ayla-side (Gate C); P0-4 closed; bot-half smoke pending Gamma.
