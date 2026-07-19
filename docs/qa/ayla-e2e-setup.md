# Ayla E2E setup (Sprint 9 / Q3; extended S0-B #978/#1048)

How to run the live E2E suite at `tests/e2e/test_ayla_integration.py`
against `dev.gobeauty.site`.

## Default behaviour: skipped

CI does NOT run these tests. The module-level `pytest.mark.skipif` requires
`AYLA_BASE_URL` **plus at least one** Ayla credential; without them the suite
skips entirely. This keeps the default `pytest` invocation deterministic and
offline.

Each client surface authenticates differently and gates on its own secret, so
you only run what you have a token for:

| Surface | Test classes | Secret (env var) |
|---|---|---|
| nutrition (`X-Service-Token`) | `TestNutritionClient`, `TestBreakerOpenClose` | `NUTRITION_SERVICE_TOKEN` (legacy fallback: `AYLA_SERVICE_TOKEN`) |
| profile + recommendations (`Authorization: Bearer`) | `TestProfileClient`, `TestRecommendationsClient` | `AYLA_INTERNAL_API_TOKEN` |

## Manual run

```bash
# Tokens are in 1Password — "Ayla / staging / *"
export AYLA_BASE_URL=https://dev.gobeauty.site
export NUTRITION_SERVICE_TOKEN=<token>       # nutrition surface
export AYLA_INTERNAL_API_TOKEN=<token>       # profile + recommendations (S0-B)
# Optional: exercise the full profile 200 body-shape round-trip against a
# real staging user (otherwise the profile test only asserts Bearer-accepted
# + clean 404 for an unknown user).
export AYLA_E2E_PROFILE_USER_ID=<real staging user UUID>
uv run pytest tests/e2e/test_ayla_integration.py -v
```

Set only the token(s) you have — the classes whose secret is missing skip
cleanly. The tests provision a fresh `bot:test:e2e-<uuid>` external user per
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

Once tokens are wired to GitHub Actions secrets, wire a nightly workflow.
Provide both credentials so the nutrition AND profile/recs surfaces run:

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
      NUTRITION_SERVICE_TOKEN: ${{ secrets.AYLA_STAGING_NUTRITION_TOKEN }}
      AYLA_INTERNAL_API_TOKEN: ${{ secrets.AYLA_STAGING_INTERNAL_TOKEN }}
      # Optional: a seeded staging user for the profile 200 body-shape check.
      AYLA_E2E_PROFILE_USER_ID: ${{ secrets.AYLA_STAGING_PROFILE_USER_ID }}
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
