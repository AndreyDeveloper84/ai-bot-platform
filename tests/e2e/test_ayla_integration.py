"""End-to-end smoke against the Ayla staging backend.

Sprint 9 / Q3 (DRF-830). Activated when:

* ``AYLA_BASE_URL`` is set (typically ``https://dev.gobeauty.site``)
* ``AYLA_SERVICE_TOKEN`` is set (load from 1Password before run)

Without those env vars the whole module is skipped — CI default. Run
locally with::

    AYLA_BASE_URL=https://dev.gobeauty.site \\
    AYLA_SERVICE_TOKEN=<from 1password> \\
    uv run pytest tests/e2e/test_ayla_integration.py -v

The tests hit a **disposable test user** (``bot:test:e2e-${RUN_ID}``) so
re-runs do not pile up data on staging. Each test cleans up after
itself where the API supports it.

See ``docs/qa/ayla-e2e-setup.md`` for the operator-side procedure
(token rotation, nightly schedule wiring).
"""

from __future__ import annotations

import os
import uuid

import pytest

from apps.integrations.ayla import (
    FoodNotRecognizedError,
    NutritionUnavailableError,
    get_nutrition_client,
    reset_nutrition_client,
)


# Module-level gate: skip everything when env unset. CI default = skip.
pytestmark = pytest.mark.skipif(
    not (os.environ.get("AYLA_BASE_URL") and os.environ.get("AYLA_SERVICE_TOKEN")),
    reason=(
        "Ayla E2E suite needs AYLA_BASE_URL + AYLA_SERVICE_TOKEN env vars. "
        "See docs/qa/ayla-e2e-setup.md."
    ),
)


@pytest.fixture(autouse=True)
def _fresh_singleton() -> None:
    """Each test rebuilds the singleton so changes to env vars
    between tests are reflected. Cheap — just clears a module-global.
    """
    reset_nutrition_client()
    yield
    reset_nutrition_client()


@pytest.fixture
def external_user_id() -> str:
    """Unique per-test-run user id. Ayla auto-creates a ProxyUser on
    first use; the unique suffix prevents test cross-talk."""
    return f"bot:test:e2e-{uuid.uuid4().hex[:8]}"


# ─── nutrition_client ────────────────────────────────────────────────────


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
            service_token=os.environ.get("AYLA_SERVICE_TOKEN", "test"),
        )
        # 5 failures → open.
        for _ in range(5):
            with pytest.raises(NutritionUnavailableError):
                await client.get_water_today(external_user_id=external_user_id)
        # 6th call short-circuits before HTTP — message includes
        # ``circuit_open``.
        with pytest.raises(NutritionUnavailableError, match="circuit_open"):
            await client.get_water_today(external_user_id=external_user_id)
