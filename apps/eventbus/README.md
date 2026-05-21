# apps/eventbus

Postgres-outbox-backed domain event bus (dot.notation taxonomy).
Pairs with ``apps/events/`` (sync analytics, snake_case) — naming
selects the bus per memory ``two-bus-event-architecture``.

## Internal events ingest channel (Phase 0 scaffold)

`POST /api/v1/internal/events/ingest/` — published Ayla → bot-platform
domain events arrive here. **Stub today**: returns `501 Not Implemented`
until Beta #441 (`docs/architecture/event-contract.md`) finalises the
wire contract (envelope shape, header names, HMAC algorithm, replay
window).

When the contract lands:

* `apps/eventbus/views.py::InternalEventsIngestView` fills in
  per-`event_name` dispatch to consumer functions (#442-#446).
* `apps/eventbus/middleware.py::HMACSignatureMiddleware` fills in
  signature verification and is added to `settings.MIDDLEWARE`
  before the view route.
* `tests/contracts/` covers idempotency end-to-end (#447).

Publishers hitting `501` should HOLD their event (Ayla outbox stays
unflushed) — they MUST NOT retry on `501`, since the channel is
reserved but the contract is unfinalised.

References: ADR-0009 §Mandatory event contract;
`docs/plans/2026-05-20-phase-0-parallel-agent-runbook.md` §Sync 4.
