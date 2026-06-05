# Shared contract fixtures (Gamma A10)

Canonical, byte-stable payloads for the events and requests that cross
the Ayla ⇄ ai-bot-platform boundary. **This directory is the single
source of truth.** Both repos load these exact bytes so their tests
can't quietly disagree about what a `payment.captured` looks like
(codex audit P0-1: bot consumer tests said `payment.confirmed`, the
contract said `payment.captured` — they drifted because each side
hand-rolled its own dict).

JSON has no comments, so ownership/source notes live here, not inline.

## Fixtures

| File | Kind | Source of truth | Owner |
|------|------|-----------------|-------|
| `booking.created.v1.json` | event envelope | `docs/architecture/event-contract.md` §3.1 | Ayla emits, bot consumes |
| `payment.captured.v1.json` | event envelope | event-contract.md §3.6 | Ayla emits, bot consumes |
| `payment.failed.v1.json` | event envelope | event-contract.md §3.7 | Ayla emits, bot consumes |
| `recommendations.request.json` | REST request body | Ayla `RecommendationsRequestSerializer` (`users/catalog_recommendations_api.py`) | bot sends, Ayla receives |

`recommendations.request.json` is the body of
`POST /api/v1/internal/me/catalog/recommendations/` — fields `lat`,
`lon`, `goal`, all optional. It is **not** an event: no envelope, no
`event_version`.

The three event fixtures use the canonical IDs from the contract
examples (tenant `9c3a7e1b…`, user `f1a2b3c4…`, appointment `b8d3e4f5…`)
so they line up with the existing `apps/eventbus/tests` constants.

## Drift guard

`MANIFEST.sha256` pins each fixture's sha256.
`apps/eventbus/tests/test_contract_fixtures.py::TestManifestIntegrity`
recomputes and compares. After an **intentional** fixture change,
regenerate it:

```bash
python -m tests.fixtures.contracts --write-manifest
```

A change without a regenerated manifest fails CI.

## Cross-repo mirror (Ayla side — Alpha)

ai-bot-platform owns the canonical copy. Ayla mirrors it (ayla-ai-core
is frozen for Phase 0, so the shared-library route is unavailable until
a founder waiver). Procedure for Alpha:

1. Copy `tests/fixtures/contracts/*.json` **and** `MANIFEST.sha256`
   into the Ayla repo (suggested: `tests/fixtures/contracts/`).
2. Add an Ayla **emit** test that loads `booking.created.v1.json` /
   `payment.captured.v1.json` / `payment.failed.v1.json` and asserts
   the outbox publisher produces these exact envelopes.
3. Add the same manifest assertion on the Ayla side, so a local edit
   to Ayla's copy fails Ayla CI.
4. On any intentional contract change: update here first, regenerate
   the manifest, then re-sync to Ayla in the same change set.

Until step 2 lands, the Ayla-emit half of A10's acceptance is tracked
as an Alpha handoff (see the A10 PR body).

## Usage

```python
from tests.fixtures.contracts import load_contract

env = load_contract("booking.created.v1.json")   # -> dict
```
