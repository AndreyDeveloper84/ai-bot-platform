"""Два согласия питания — §92, решение владельца 10.09.2026.

Проверяется не наличие констант (это тавтология), а единственное
свойство, ради которого владелец разделил согласие надвое:
**их можно отозвать независимо друг от друга.**

Слитое согласие заставило бы человека отдать параметры тела ради права
вести дневник. Разделение имеет смысл ровно до тех пор, пока отзыв
одного не трогает другое — иначе два имени описывают одну кнопку.
"""

from __future__ import annotations

import pytest

from apps.consent.models import ConsentRecord
from apps.consent.services import grant, has_consent, withdraw
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)

DIARY = ConsentRecord.ConsentType.NUTRITION_DIARY.value
CALC = ConsentRecord.ConsentType.PERSONAL_CALCULATION.value


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="nutrition-consents", name="N")


@pytest.fixture
def person(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="nutrition-consents-1"
    )


class TestTheTwoConsentsAreIndependent:
    def test_withdrawing_the_calculation_leaves_the_diary(
        self, tenant: Tenant, person: BotUser
    ) -> None:
        """Правило 2 §92 в самом дешёвом виде: отказ не закрывает дневник.

        Это тот случай, ради которого согласий два. Человек убирает
        параметры тела и продолжает записывать еду.
        """
        with tenant_scope(tenant):
            grant(
                person,
                consent_type=DIARY,
                source="miniapp:consents",
                document_version="nutrition-v1",
            )
            grant(
                person,
                consent_type=CALC,
                source="miniapp:consents",
                document_version="nutrition-v1",
            )

            # Положительная стража прежде отрицания: оба согласия РЕАЛЬНО
            # выданы. Без неё «дневник остался» зеленело бы и в мире, где
            # ни одно согласие не записалось.
            assert has_consent(person, DIARY)
            assert has_consent(person, CALC)

            withdraw(person, consent_type=CALC, source="miniapp:consents")

            assert not has_consent(person, CALC), (
                "отозванное согласие обязано перестать действовать"
            )
            assert has_consent(person, DIARY), (
                "отзыв расчёта не имеет права закрыть дневник — иначе два "
                "согласия описывают одну кнопку"
            )

    def test_withdrawing_the_diary_leaves_the_calculation(
        self, tenant: Tenant, person: BotUser
    ) -> None:
        """Обратная сторона: независимость обязана работать в обе стороны.

        Без этой половины «независимость» доказывалась бы на одном
        направлении, а на другом могла бы оказаться связкой.
        """
        with tenant_scope(tenant):
            grant(
                person,
                consent_type=DIARY,
                source="miniapp:consents",
                document_version="nutrition-v1",
            )
            grant(
                person,
                consent_type=CALC,
                source="miniapp:consents",
                document_version="nutrition-v1",
            )
            assert has_consent(person, DIARY)
            assert has_consent(person, CALC)

            withdraw(person, consent_type=DIARY, source="miniapp:consents")

            assert not has_consent(person, DIARY)
            assert has_consent(person, CALC)

    def test_neither_is_implied_by_the_older_types(self, tenant: Tenant, person: BotUser) -> None:
        """Ни `HEALTH`, ни `PHOTO_BIOMETRIC` не выдают новых согласий.

        Ровно та подмена, которую владелец запретил: тип, выданный под
        другой объём, не может открывать этот. `HEALTH` человек мог дать
        под скрининг боли — расчёта нормы он этим не разрешал.
        """
        with tenant_scope(tenant):
            grant(
                person,
                consent_type=ConsentRecord.ConsentType.HEALTH.value,
                source="miniapp:profile_health_consent",
                document_version="health-v1",
            )
            grant(
                person,
                consent_type=ConsentRecord.ConsentType.PHOTO_BIOMETRIC.value,
                source="miniapp:profile",
                document_version="photo-v1",
            )

            # Положительная стража: старые согласия действительно выданы.
            assert has_consent(person, ConsentRecord.ConsentType.HEALTH.value)
            assert has_consent(person, ConsentRecord.ConsentType.PHOTO_BIOMETRIC.value)

            assert not has_consent(person, CALC)
            assert not has_consent(person, DIARY)


class TestRuleSixIsSatisfiedByTheCarrier:
    def test_version_source_date_and_withdrawal_all_survive(
        self, tenant: Tenant, person: BotUser
    ) -> None:
        """§92 п.6 — версия текста, дата, способ и отзыв сохраняются.

        Проверяется на носителе, а не на памяти автора: именно этого
        не даёт голая колонка `BotUser.food_scanner_consent_at`, где
        отзыв ставит `NULL` и стирает сам факт выдачи.
        """
        with tenant_scope(tenant):
            grant(
                person,
                consent_type=CALC,
                source="miniapp:consents",
                document_version="nutrition-v1",
            )
            withdraw(person, consent_type=CALC, source="miniapp:consents")

            row = ConsentRecord.all_tenants.filter(bot_user=person, consent_type=CALC).get()

            assert row.document_version == "nutrition-v1", "версия текста"
            assert row.source == "miniapp:consents", "способ получения"
            assert row.captured_at is not None, "дата выдачи"
            assert row.withdrawn_at is not None, "отзыв"
            # Выдача не стёрта отзывом — строка помнит оба события.
            assert row.granted is True
