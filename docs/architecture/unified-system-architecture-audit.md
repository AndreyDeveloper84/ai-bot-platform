## Status update — 2026-06-01 (tech-lead reconciliation)

This section overlays current status onto the original audit below. The body of this document is unchanged; treat the statuses here as authoritative where they differ from the original "Status: Open" lines.

### P0 status table

| ID | Finding | Current status | Notes / PRs |
| --- | --- | --- | --- |
| P0-1 | Event-name drift | PARTIAL | Booking events flow Ayla→bot. **Payment vocabulary NOT yet aligned** (Block B). Do not enable per-topic external delivery for payment events until Block B aligns names, otherwise 422/DLQ. |
| P0-2 | Booking ownership split | DEFERRED | Deferred post-pilot per verdict A2 (Block D). Dual-source accepted LATENT with divergence monitoring + G9 import-linter contract. |
| P0-3 | Payment create contract | RESOLVED (decision) | Canonical create = `POST /payments/create {appointment_id, return_url}` (per API Spec v2.0). Bot's `amount_rub`/`kind=certificate` shape is incompatible; verdict B = bot does NOT create payments (retry-only). Vocab alignment tracked in Block B. |
| P0-4 | Outbox not delivered to bot | CLOSED (Ayla side) | Block C shipped — Ayla PRs #170 (OutboxEvent dual-delivery), #177 (HTTP publisher + retry/backoff), #181 (HMAC + timestamp), #178 (replay command), #182 (E2E smoke Ayla half). Gate C unlocked. Bot-side joint smoke half PENDING (Gamma). Per-topic `external_delivery_enabled` opt-in PENDING. |
| P0-5 | ai-core version drift | CLOSED | A9 — bot-platform PR #935 + Ayla PR #176, both pin ayla-ai-core @ `e73a1b4784c150493c300b316d7a62cd423c8377`. |
| P0-6 | Catalog/schedule source-of-truth split | UNCHANGED | Context unchanged; mirror strategy per ADR-0009; catalog ownership in Ayla. |
| P0-7 | YClients ownership | UPDATED | Per E0.4 audit: bot-side YClients webhook is FULL+HARDENED (tenant scoping, audit wrapper, event emit); the `BookingRequest`→`RemoteBookingProxy` shrink is in-flight (latent, tied to A2 / Block D). |

### New artifacts since this audit

- `docs/architecture/contract-matrix.md` — cross-system contract registry, draft v1 (PR #940).
- E0.1 / E0.1-followup / E0.4 / E0.5 / E0.6 legacy-migration coverage audits + maintainability docs committed (PR #940).

---

__BODY_UNIFIED__