"""End-to-end smoke against the Ayla staging backend.

Sprint 9 / Q3 (DRF-830); extended S0-B (#978/#1048) with profile + catalog
recommendations coverage. Activated when ``AYLA_BASE_URL`` is set (typically
``https://dev.gobeauty.site``) plus **at least one** Ayla credential — each
client surface authenticates differently and gates on its own secret:

* nutrition — ``X-Service-Token`` from ``AYLA_SERVICE_TOKEN``.
* profile + recommendations — ``Authorization: Bearer`` from
  ``AYLA_INTERNAL_API_TOKEN`` (the S0-B-unified s2s Bearer).

Load the token(s) from 1Password before running. Without any of them the whole
module is skipped — CI default. Run locally with::

    AYLA_BASE_URL=https://dev.gobeauty.site \\
    AYLA_SERVICE_TOKEN=<from 1password> \\
    AYLA_INTERNAL_API_TOKEN=<from 1password> \\
    uv run pytest tests/e2e/test_ayla_integration.py -v

Optional: set ``AYLA_E2E_PROFILE_USER_ID`` to a real staging user UUID to
exercise the full profile 200-body-shape round-trip (otherwise the profile
test only asserts the Bearer is accepted + the route resolves to a clean 404
for an unknown user).

The tests hit a **disposable test user** (``bot:test:e2e-${RUN_ID}``) so
each run's writes are isolated to a fresh ProxyUser. Rows are NOT reaped
afterward (no cleanup API is called) — acceptable on a disposable-id staging
box; do not point this suite at an environment where accreted test rows
matter.

See ``docs/qa/ayla-e2e-setup.md`` for the operator-side procedure
(token rotation, nightly schedule wiring).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest

from apps.integrations.ayla import (
    FoodNotRecognizedError,
    NutritionUnavailableError,
    get_nutrition_client,
    reset_nutrition_client,
)


_BASE_URL = os.environ.get("AYLA_BASE_URL")
# Nutrition's ``X-Service-Token`` secret is ``NUTRITION_SERVICE_TOKEN`` after
# S0-B (#1050); ``AYLA_SERVICE_TOKEN`` is the deprecated fallback name (mirrors
# the settings fallback in ``config/settings/base.py``). Gate on the canonical
# name first so an operator who sets only ``NUTRITION_SERVICE_TOKEN`` still runs
# the nutrition surface.
_SERVICE_TOKEN = os.environ.get("NUTRITION_SERVICE_TOKEN") or os.environ.get("AYLA_SERVICE_TOKEN")
_INTERNAL_TOKEN = os.environ.get("AYLA_INTERNAL_API_TOKEN")
_PROFILE_USER_ID = os.environ.get("AYLA_E2E_PROFILE_USER_ID")

# Module-level gate: skip everything unless a base URL + at least one Ayla
# credential is present. Each test class below adds a skipif for the specific
# secret its surface authenticates with, so a Bearer-only or Service-Token-only
# run exercises exactly the surfaces it can reach. CI default = skip.
pytestmark = pytest.mark.skipif(
    not (_BASE_URL and (_SERVICE_TOKEN or _INTERNAL_TOKEN)),
    reason=(
        "Ayla E2E suite needs AYLA_BASE_URL + at least one of "
        "NUTRITION_SERVICE_TOKEN (nutrition) / AYLA_INTERNAL_API_TOKEN "
        "(profile+recs). See docs/qa/ayla-e2e-setup.md."
    ),
)

# Per-surface credential gates.
_needs_service_token = pytest.mark.skipif(
    not _SERVICE_TOKEN,
    reason="Needs NUTRITION_SERVICE_TOKEN (nutrition X-Service-Token).",
)
_needs_internal_token = pytest.mark.skipif(
    not _INTERNAL_TOKEN,
    reason="Needs AYLA_INTERNAL_API_TOKEN (Bearer for profile + recommendations).",
)


@pytest.fixture(autouse=True)
def _fresh_singleton() -> Generator[None, None, None]:
    """Reset per-process client state between tests: the nutrition singleton
    and the module-level profile/recommendations circuit breakers.

    Settings are read once at import (``django.conf.settings``), so this does
    NOT re-read env vars mid-run — env is fixed before the suite launches. Its
    job is state isolation: a breaker left open by one test must not
    short-circuit the next. Cheap — just clears module-globals.
    """
    from apps.integrations.ayla import profile_client
    from apps.integrations.ayla.recommendations_client import reset_recommendations_circuit

    reset_nutrition_client()
    profile_client._circuit.record_success()
    reset_recommendations_circuit()
    yield
    reset_nutrition_client()
    profile_client._circuit.record_success()
    reset_recommendations_circuit()


@pytest.fixture
def external_user_id() -> str:
    """Unique per-test-run user id. Ayla auto-creates a ProxyUser on
    first use; the unique suffix prevents test cross-talk."""
    return f"bot:test:e2e-{uuid.uuid4().hex[:8]}"


# ─── nutrition_client ────────────────────────────────────────────────────


@_needs_service_token
class TestNutritionClient:
    """I1 (DRF-825) — direct client smokes."""

    @pytest.mark.asyncio
    async def test_get_profile_returns_none_for_fresh_user(self, external_user_id: str) -> None:
        """Fresh user has no profile yet — Ayla returns 404 OR 200 with
        ``exists=false``; client maps both to None."""
        client = get_nutrition_client()
        profile = await client.get_profile(external_user_id=external_user_id)
        assert profile is None

    @pytest.mark.asyncio
    async def test_upsert_profile_then_read_back(self, external_user_id: str) -> None:
        """Round-trip: write a profile, fetch it, assert the norms
        envelope shape (DRF-270 ``data.norms.*``)."""
        client = get_nutrition_client()
        await client.upsert_profile(
            external_user_id=external_user_id,
            data={
                "gender": "female",
                "age": 30,
                "height_cm": 168,
                "weight_kg": 62,
                "goal": "maintain",
                "activity_coefficient": 1.4,
            },
        )

        profile = await client.get_profile(external_user_id=external_user_id)
        assert profile is not None
        # Norms envelope unwrapped correctly.
        assert profile.daily_kcal > 0
        assert profile.protein_g > 0
        assert profile.water_ml > 0
        assert profile.bmr > 0

    @pytest.mark.asyncio
    async def test_water_log_round_trip(self, external_user_id: str) -> None:
        client = get_nutrition_client()
        entry = await client.add_water(
            external_user_id=external_user_id,
            ml=250,
            beverage_slug="voda",
        )
        assert entry.entry_id
        # Voda has water_coefficient=1.0 → water_ml == ml.
        assert entry.water_ml == 250

        today = await client.get_water_today(external_user_id=external_user_id)
        assert today.total_ml >= 250  # at least the entry we just added

    @pytest.mark.asyncio
    async def test_scan_photo_with_invalid_bytes_raises_food_not_recognized(
        self, external_user_id: str
    ) -> None:
        """A 1-byte payload triggers Ayla's FOOD_NOT_RECOGNIZED path."""
        client = get_nutrition_client()
        with pytest.raises(FoodNotRecognizedError):
            await client.scan_photo(
                external_user_id=external_user_id,
                image_bytes=b"\x00",
                filename="invalid.jpg",
            )


# ─── breaker (I3) ────────────────────────────────────────────────────────


@_needs_service_token
class TestBreakerOpenClose:
    """I3 (DRF-827) — circuit breaker against a forced-failure host.

    Skip when ``AYLA_BASE_URL_BREAKER`` env not set — the default Ayla
    staging won't reliably return 5xx for us. Operator points this at
    a sandbox that always 500s (or runs the test via mock + production
    Ayla mix).
    """

    @pytest.mark.skipif(
        not os.environ.get("AYLA_BASE_URL_BREAKER"),
        reason=(
            "Breaker test needs AYLA_BASE_URL_BREAKER pointing at a "
            "host that returns 5xx — see docs/qa/ayla-e2e-setup.md."
        ),
    )
    @pytest.mark.asyncio
    async def test_5_failures_open_breaker(self, external_user_id: str) -> None:
        # Build a separate client against the breaker host so we don't
        # poison the singleton used by other tests.
        from apps.integrations.ayla import NutritionClient

        client = NutritionClient(
            base_url=os.environ["AYLA_BASE_URL_BREAKER"],
            service_token=_SERVICE_TOKEN or "test",
        )
        # 5 failures → open.
        for _ in range(5):
            with pytest.raises(NutritionUnavailableError):
                await client.get_water_today(external_user_id=external_user_id)
        # 6th call short-circuits before HTTP — message includes
        # ``circuit_open``.
        with pytest.raises(NutritionUnavailableError, match="circuit_open"):
            await client.get_water_today(external_user_id=external_user_id)


# ─── profile_client (S0-B / #978) ─────────────────────────────────────────


@_needs_internal_token
class TestProfileClient:
    """#978 — ``GET /api/v1/internal/users/{user_id}/`` with
    ``Authorization: Bearer {AYLA_INTERNAL_API_TOKEN}``.

    Guards the exact route + auth wiring the S0-B migration shipped: a wrong
    path would 404 at the URL resolver and a wrong token would 401/403, both of
    which the assertions below distinguish from the healthy 'unknown user' 404.
    """

    def test_unknown_user_is_not_found_not_auth_error(self) -> None:
        """Round-trip with a random UUID: the Bearer is ACCEPTED (not 401/403)
        and the route resolves to a clean 404 'not found', NOT an auth failure.

        Proves the route is present + the token is valid without needing a
        seeded staging user. ``profile_client`` maps 404 → ``not_found`` and
        401/403 → ``auth`` — asserting on the message pins which one Ayla
        actually returned.
        """
        from apps.integrations.ayla.profile_client import (
            ProfileFetchError,
            fetch_profile_fields,
        )

        with pytest.raises(ProfileFetchError) as exc:
            fetch_profile_fields(uuid.uuid4())
        msg = str(exc.value)
        # Assumption: Ayla's internal by-id lookup returns 404 for an unknown
        # user (not a 403 existence-hiding response). If a future Ayla build
        # 403s here instead, this asserts 'auth' and fails loudly — which is
        # itself worth investigating, so the assumption fails safe.
        assert "not_found" in msg, (
            f"expected a 404 'not_found' (route + Bearer OK, user absent); "
            f"got {msg!r} — 'auth' would mean the token was rejected (401/403)."
        )

    @pytest.mark.skipif(
        not _PROFILE_USER_ID,
        reason=(
            "Set AYLA_E2E_PROFILE_USER_ID to a real staging user UUID for the "
            "full 200 body-shape round-trip."
        ),
    )
    def test_known_user_returns_pii_subset(self) -> None:
        """Full 200 round-trip against a seeded user: Ayla's body parses into
        the closed PII shape (``display_name`` + ``avatar_url``, both ``str``)
        the #446 consumer persists — the 'body matches client parsing' check.
        """
        from apps.integrations.ayla.profile_client import fetch_profile_fields

        fields = fetch_profile_fields(uuid.UUID(_PROFILE_USER_ID))
        assert isinstance(fields.display_name, str)
        assert isinstance(fields.avatar_url, str)


# ─── recommendations_client (S0-B / #1048) ────────────────────────────────


@_needs_internal_token
class TestRecommendationsClient:
    """#1048 — ``POST /api/v1/internal/me/catalog/recommendations/`` with
    ``Authorization: Bearer {AYLA_INTERNAL_API_TOKEN}`` +
    ``X-External-User-ID``.
    """

    def test_recommendations_round_trip(self, external_user_id: str) -> None:
        """200 round-trip: Ayla auto-creates a ProxyUser for the disposable
        ``external_user_id`` and returns a JSON object the client passes
        through. Proves route + Bearer + ``X-External-User-ID`` resolution end
        to end, and that the parsed body is the ``dict`` the client contract
        promises.

        A 4xx is tolerated ONLY if it is a business validation (e.g. Ayla
        rejects the sample payload) — an auth rejection (401/403) fails the
        test, because that means the Bearer was not accepted.
        """
        from apps.integrations.ayla.recommendations_client import (
            RecommendationsBadRequest,
            fetch_recommendations,
        )

        try:
            body = fetch_recommendations(
                external_user_id=external_user_id,
                payload={"lat": 55.75, "lon": 37.61, "goal": "relax", "tenant_history": []},
            )
        except RecommendationsBadRequest as exc:
            assert exc.status_code not in (401, 403), (
                f"Bearer rejected: HTTP {exc.status_code} — the s2s token is not "
                f"accepted by Ayla's recommendations endpoint."
            )
            pytest.skip(
                f"Ayla rejected the sample payload (HTTP {exc.status_code}); "
                f"route + Bearer are healthy — tune the payload for a 200 body check."
            )
        else:
            assert isinstance(body, dict)
