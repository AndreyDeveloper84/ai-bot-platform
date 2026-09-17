"""Согласие дневника/сканера в реестре — DRF-1963 (M1, владелец 15.09, §6).

Держит четыре свойства, ради которых колонка ``BotUser.food_scanner_consent_at``
заменена строкой реестра ``food_diary_processing``:

* выдача записывается на ИЗВЕСТНЫЙ текст — чужая версия отвергается (D3);
* отзыв не стирает факт выдачи — строка остаётся с ``withdrawn_at``;
* отзыв personal_data снимает и это согласие — каскад, а не ручной ``update`` (D6);
* согласие одно на человека — вторая оболочка (салонный бот, M4) видит ту же выдачу.
"""

from __future__ import annotations

import uuid

import pytest

from apps.consent import nutrition
from apps.consent.models import ConsentRecord
from apps.consent.services import record_global_consent, withdraw_personal_data_for_bot_users
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="m1-diary", name="M1 diary")


@pytest.fixture
def person(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="m1-diary-1")


def _rows(bot_user: BotUser):
    return ConsentRecord.all_tenants.filter(bot_user=bot_user, consent_type=nutrition.DIARY)


class TestGrant:
    def test_grant_writes_a_versioned_row_and_opens_the_predicate(self, person) -> None:
        assert nutrition.diary_is_granted(person) is False  # before

        record = nutrition.grant_diary(
            person, document_version=nutrition.FOOD_DIARY_CONSENT_DOCUMENT_VERSION
        )

        assert record is not None
        assert record.consent_type == "food_diary_processing"
        assert record.document_version == "food-diary-v0"
        assert record.source == nutrition.GRANT_SOURCE
        assert nutrition.diary_is_granted(person) is True
        assert nutrition.diary_current_record(person) == record

    def test_an_unknown_version_is_refused_and_writes_nothing(self, person) -> None:
        with pytest.raises(nutrition.UnknownDisclosureVersionError):
            nutrition.grant_diary(person, document_version="food-diary-v999")
        with pytest.raises(nutrition.UnknownDisclosureVersionError):
            nutrition.grant_diary(person, document_version="")
        assert _rows(person).count() == 0
        assert nutrition.diary_is_granted(person) is False

    def test_grant_is_idempotent(self, person) -> None:
        v = nutrition.FOOD_DIARY_CONSENT_DOCUMENT_VERSION
        first = nutrition.grant_diary(person, document_version=v)
        second = nutrition.grant_diary(person, document_version=v)
        assert first == second
        assert _rows(person).filter(withdrawn_at__isnull=True).count() == 1


class TestWithdraw:
    def test_withdrawal_closes_the_predicate_and_keeps_the_grant_on_record(self, person) -> None:
        nutrition.grant_diary(
            person, document_version=nutrition.FOOD_DIARY_CONSENT_DOCUMENT_VERSION
        )
        assert nutrition.diary_is_granted(person) is True  # presence first

        assert nutrition.withdraw_diary(person) == 1

        assert nutrition.diary_is_granted(person) is False
        assert nutrition.diary_current_record(person) is None
        rows = list(_rows(person))
        assert len(rows) == 1  # the grant is still there — the column used to erase it
        assert rows[0].withdrawn_at is not None
        assert rows[0].granted is True

    def test_personal_data_withdrawal_cascades_to_the_diary_consent(self, person) -> None:
        """D6: any withdrawal of personal_data takes this consent with it."""
        record_global_consent(person, source="test:pd")
        nutrition.grant_diary(
            person, document_version=nutrition.FOOD_DIARY_CONSENT_DOCUMENT_VERSION
        )
        assert nutrition.diary_is_granted(person) is True

        withdraw_personal_data_for_bot_users(
            BotUser.all_tenants.filter(pk=person.pk).select_related("tenant"), source="test:cascade"
        )

        assert nutrition.diary_is_granted(person) is False
        assert _rows(person).filter(withdrawn_at__isnull=False).count() == 1


class TestOnePersonOneConsent:
    def test_a_second_shell_of_the_same_person_sees_the_grant(self, tenant, person) -> None:
        """M4: the salon bot does not ask again — the grant is written for the person."""
        person_key = uuid.uuid4()
        BotUser.all_tenants.filter(pk=person.pk).update(ayla_user_id=person_key)
        person.refresh_from_db()
        salon = Tenant.objects.create(slug="m1-salon", name="Salon")
        salon_shell = BotUser.all_tenants.create(
            tenant=salon, channel="max", channel_user_id="m1-diary-salon", ayla_user_id=person_key
        )
        assert nutrition.diary_is_granted(salon_shell) is False  # before

        nutrition.grant_diary(
            person, document_version=nutrition.FOOD_DIARY_CONSENT_DOCUMENT_VERSION
        )

        assert nutrition.diary_is_granted(salon_shell) is True
        salon_row = nutrition.diary_current_record(salon_shell)
        assert salon_row is not None
        assert salon_row.tenant_id == salon.id

        nutrition.withdraw_diary(person)
        assert nutrition.diary_is_granted(salon_shell) is False
