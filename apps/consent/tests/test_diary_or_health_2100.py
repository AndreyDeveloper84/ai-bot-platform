"""One nutrition basis: diary ``food-diary-v1`` OR a legacy ``HEALTH`` row (DRF-2100).

Owner ruling 18.09 (§48 п.8б): stop issuing HEALTH, keep the old rows valid.
The predicate is :func:`apps.consent.nutrition.diary_or_health_granted`; the
six readers that used to ask for HEALTH by name now ask it. Each reader is
exercised through ITS OWN entry point with real registry rows — no patching
of the predicate — so the census of readers is a census of behaviour.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from apps.consent import nutrition
from apps.consent.models import ConsentRecord
from apps.consent.services import (
    record_global_consent,
    record_person_consent,
    withdraw_person_consent,
)
from apps.consent.tests.legacy_health import seed_legacy_health
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="doh-2100", name="DOH", timezone="Europe/Moscow")


@pytest.fixture
def person(tenant: Tenant) -> BotUser:
    user = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="2100-1")
    record_global_consent(user, source="test:welcome")  # PERSONAL_DATA baseline
    return user


def _grant_v1(person: BotUser) -> None:
    nutrition.grant_diary(person, document_version=nutrition.FOOD_DIARY_CONSENT_DOCUMENT_VERSION)


# --- the predicate -------------------------------------------------------------


class TestDiaryOrHealthGranted:
    def test_legacy_health_without_v1_opens(self, person: BotUser) -> None:
        seed_legacy_health(person)
        assert nutrition.diary_is_granted(person) is False
        assert nutrition.diary_or_health_granted(person) is True

    def test_v1_without_health_opens(self, person: BotUser) -> None:
        _grant_v1(person)
        assert ConsentRecord.all_tenants.filter(bot_user=person, consent_type="health").count() == 0
        assert nutrition.diary_or_health_granted(person) is True

    def test_neither_is_closed(self, person: BotUser) -> None:
        assert nutrition.diary_or_health_granted(person) is False

    def test_withdrawn_legacy_health_is_closed(self, person: BotUser) -> None:
        seed_legacy_health(person)
        withdraw_person_consent(person, consent_type="health", source="test:withdraw")
        assert nutrition.diary_or_health_granted(person) is False

    def test_legacy_health_counts_under_any_document_version(self, person: BotUser) -> None:
        """Old rows are not re-checked against a text nobody shows any more."""

        seed_legacy_health(person, document_version="health-data-v0")
        assert nutrition.diary_or_health_granted(person) is True

    def test_a_stale_diary_version_without_health_stays_closed(self, person: BotUser) -> None:
        """The v1 rule of diary_is_granted is not loosened by the OR."""

        record_person_consent(
            person, consent_type=nutrition.DIARY, source="test:v0", document_version="food-diary-v0"
        )
        assert nutrition.diary_or_health_granted(person) is False

    def test_fails_closed_when_the_registry_read_raises(self, person: BotUser) -> None:
        seed_legacy_health(person)
        with patch("apps.consent.services.has_global_consent", side_effect=RuntimeError("db")):
            assert nutrition.diary_or_health_granted(person) is False

    def test_current_record_prefers_the_diary_row(self, person: BotUser) -> None:
        seed_legacy_health(person)
        legacy = nutrition.diary_or_health_current_record(person)
        assert legacy is not None and legacy.consent_type == "health"
        _grant_v1(person)
        current = nutrition.diary_or_health_current_record(person)
        assert current is not None and current.consent_type == nutrition.DIARY


# --- the readers, each through its own door -------------------------------------


@pytest.mark.parametrize(
    ("seed", "expected"),
    [("legacy", True), ("v1", True), ("none", False)],
    ids=["legacy-health", "diary-v1", "neither"],
)
class TestReaders:
    @staticmethod
    def _seed(person: BotUser, seed: str) -> None:
        if seed == "legacy":
            seed_legacy_health(person)
        elif seed == "v1":
            _grant_v1(person)

    def test_food_history_and_nutrition_context(
        self, person: BotUser, seed: str, expected: bool
    ) -> None:
        from apps.orchestrator.food_history import read_consent_open

        self._seed(person, seed)
        assert read_consent_open(person) is expected

    def test_nutrition_proactive_coach(self, person: BotUser, seed: str, expected: bool) -> None:
        from apps.nutrition_proactive.coach import _health_basis

        self._seed(person, seed)
        assert _health_basis(person) is expected

    def test_coach_observation(self, person: BotUser, seed: str, expected: bool) -> None:
        from apps.orchestrator.coach_observation import _health_open

        self._seed(person, seed)
        assert _health_open(person) is expected

    def test_marketplace_menu(self, person: BotUser, seed: str, expected: bool) -> None:
        from apps.skills.menu.marketplace import health_granted

        self._seed(person, seed)
        assert health_granted(person) is expected


class TestWellnessProactiveBlocker:
    """The blocker cannot say «or»; the task asks it for PERSONAL_DATA and then asks the predicate.
    The tick itself is exercised in ``apps/wellness_proactive/tests/test_tasks.py``."""

    def test_required_consents_no_longer_name_health_and_the_slug_survives(self) -> None:
        from apps.notifications.proactive import BLOCK_REASONS
        from apps.wellness_proactive.tasks import NO_NUTRITION_BASIS, REQUIRED_CONSENTS

        assert REQUIRED_CONSENTS == (ConsentRecord.ConsentType.PERSONAL_DATA.value,)
        assert NO_NUTRITION_BASIS == "no_health_consent"
        assert NO_NUTRITION_BASIS in BLOCK_REASONS
