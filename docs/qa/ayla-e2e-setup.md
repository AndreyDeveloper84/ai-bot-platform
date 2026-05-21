# Ayla E2E setup (Sprint 9 / Q3)

How to run the live E2E suite at `tests/e2e/test_ayla_integration.py`
against `dev.ayla.app`.

## Default behaviour: skipped

CI does NOT run these tests. The module-level `pytest.mark.skipif` checks
for two env vars; without them the suite skips entirely. This keeps the
default `pytest` invocation deterministic and offline.

## Manual run

```bash
# Token is in 1Password — "Ayla / staging / AYLA_SERVICE_TOKEN"
export AYLA_BASE_URL=https://dev.ayla.app
export AYLA_SERVICE_TOKEN=<token>
uv run pytest tests/e2e/test_ayla_integration.py -v
```

The tests provision a fresh `bot:test:e2e-<uuid>` external user per
test — staging accumulates a small amount of test data but no PII.

## Breaker test (optional)

`TestBreakerOpenClose` needs an additional env pointing at a host that
returns 5xx (so we can drive the breaker open). Two options:

* **Local stub** — `pkill -f tests/_fixtures/always_500.py` running on
  `:8081`; export `AYLA_BASE_URL_BREAKER=http://localhost:8081`.
* **Skip** — the unit-level breaker tests in
  `apps/integrations/ayla/tests/test_nutrition_client.py` exercise the
  state machine without network. The E2E breaker test is mostly a
  sanity check that the breaker actually trips when Ayla is down.

## Nightly schedule (Phase 1)

Once tokens are wired to GitHub Actions secrets (`AYLA_BASE_URL` +
`AYLA_SERVICE_TOKEN` repository secrets), wire a nightly workflow:

```yaml
name: ayla-nightly-e2e
on:
  schedule:
    - cron: '0 3 * * *'  # 03:00 UTC daily
jobs:
  e2e:
    runs-on: ubuntu-latest
    env:
      AYLA_BASE_URL: ${{ secrets.AYLA_STAGING_URL }}
      AYLA_SERVICE_TOKEN: ${{ secrets.AYLA_STAGING_TOKEN }}
    steps:
      - uses: actions/checkout@v4
      - run: uv run pytest tests/e2e/test_ayla_integration.py -v
```

Phase 1 picks this up when the on-call runbook ships. Until then, run
manually before each merge that touches `apps/integrations/ayla/`.

## Failure triage

* `NutritionUnavailableError` from every test → Ayla staging is down,
  not a bot bug. Check the Ayla `#status` channel.
* `KeyError` on `norms` → schema drift; surface in `nutrition_client._parse_profile_response`.
  Bump the I1 fixture, file a ticket against Ayla backend.
* `AuthenticationError` → token rotated. Pull fresh from 1Password.
