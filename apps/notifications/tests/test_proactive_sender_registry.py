"""DRF-1731 — реестр проактивных отправителей и класс каждого (38-ФЗ ст. 18).

Замер 12.09.2026 (``Ayla/docs/REPORT_SUB_MARKETING_CONSENT_SENDERS.md``):
десять отправителей клиенту, ``MARKETING`` не читал ни один. Реестр
``PROACTIVE_SENDERS`` называет класс каждого; здесь стережётся, что
реестр ссылается на живые символы (переименованный отправитель ломает
тест, а не выпадает из списка) и что у PROMO-класса требуемый набор
согласий содержит рекламное. Поведение «отозвано → 0 отправок» доказано
у самих отправителей: ``apps/bookings/tests/test_followups.py``
(``TestConsentGate``) и ``apps/nutrition_proactive/tests/test_coach.py``
(``TestMarketingBasis``).
"""

from __future__ import annotations

import importlib

import pytest

from apps.notifications.proactive import (
    BLOCK_REASONS,
    PROACTIVE_SENDERS,
    PROMO_REQUIRED_CONSENTS,
    SENDER_CLASS_PROMO,
    SENDER_CLASS_SERVICE,
    SENDER_CLASS_UNCLEAR,
    consent_blocker,
    marketing_blocker,
)


class TestRegistryNamesLiveSenders:
    @pytest.mark.parametrize("key", sorted(PROACTIVE_SENDERS))
    def test_every_key_resolves_to_a_callable(self, key: str) -> None:
        module_path, _, attr = key.partition(":")
        assert attr, key
        module = importlib.import_module(module_path)
        assert callable(getattr(module, attr)), key

    def test_every_class_is_one_of_three(self) -> None:
        allowed = {SENDER_CLASS_SERVICE, SENDER_CLASS_PROMO, SENDER_CLASS_UNCLEAR}
        assert set(PROACTIVE_SENDERS.values()) <= allowed
        # POSITIVE: реестр непуст и в нём есть каждый класс — иначе квантор
        # «для всех» выше пуст.
        assert len(PROACTIVE_SENDERS) >= 10
        assert set(PROACTIVE_SENDERS.values()) == allowed

    def test_promo_senders_are_the_two_from_the_measurement(self) -> None:
        promo = sorted(k for k, v in PROACTIVE_SENDERS.items() if v == SENDER_CLASS_PROMO)
        assert promo == [
            "apps.bookings.followups:send_post_visit_followups",
            "apps.nutrition_proactive.coach:plan_coach_hints",
        ]


class TestPromoGround:
    def test_promo_required_consents_include_marketing_after_the_baseline(self) -> None:
        assert PROMO_REQUIRED_CONSENTS == ("personal_data", "marketing")
        assert "no_marketing_consent" in BLOCK_REASONS

    @pytest.mark.django_db
    def test_shared_gate_names_the_missing_marketing_consent(self) -> None:
        from apps.consent.models import ConsentRecord
        from apps.identity.models import BotUser
        from apps.tenancy.models import Tenant
        from django.utils import timezone

        tenant = Tenant.objects.create(slug="t-mkt", name="T")
        user = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="u-mkt", consent_at=timezone.now()
        )
        ConsentRecord.all_tenants.create(
            tenant=tenant,
            bot_user=user,
            consent_type=ConsentRecord.ConsentType.PERSONAL_DATA.value,
            granted=True,
            source="test",
        )
        assert consent_blocker(user) is None  # baseline-only caller: unchanged
        assert consent_blocker(user, PROMO_REQUIRED_CONSENTS) == "no_marketing_consent"
        assert marketing_blocker(user) == "no_marketing_consent"

        record = ConsentRecord.all_tenants.create(
            tenant=tenant,
            bot_user=user,
            consent_type=ConsentRecord.ConsentType.MARKETING.value,
            granted=True,
            source="test",
        )
        assert consent_blocker(user, PROMO_REQUIRED_CONSENTS) is None  # POSITIVE
        assert marketing_blocker(user) is None

        record.withdrawn_at = timezone.now()
        record.save(update_fields=["withdrawn_at"])
        assert marketing_blocker(user) == "no_marketing_consent"  # по записи, не по колонке
