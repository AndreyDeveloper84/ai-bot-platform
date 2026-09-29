"""Писатель согласия на персональный расчёт (§92, N-a3).

До этого модуля `personal_calculation` умел только ЧИТАТЬ реестр —
`current_attestation` — и писателя не было ни на dev, ни в #1523. Значит
после #324 анкета отказывала всем: по названной причине, но всем, и §103
«пересчёт после согласия» не наступал ни у кого.

Форма взята из `apps/consent/health.py` намеренно — два согласия не
должны расходиться в устройстве: версия обязательна и проверяется,
выдача идемпотентна, отзыв не удаляет строк.
"""

from __future__ import annotations

import pytest

from apps.consent import personal_calculation as pc
from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="pc-grant", name="PC")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="pc-g-1")


class TestGrantWritesWhatTheBoundaryReads:
    def test_after_grant_the_attestation_carries_the_version(self, bot_user):
        """Положительная стража, названная в постановке: после согласия
        `current_attestation` возвращает непустую версию.

        Проверяется ЧТЕНИЕМ той же функцией, которой ходит граница, — не
        «мы записали», а «граница увидит».
        """
        assert pc.grant(bot_user, document_version=pc.PERSONAL_CALCULATION_DOCUMENT_VERSION)

        attestation = pc.current_attestation(bot_user)

        assert attestation.type == pc.PERSONAL_CALCULATION
        assert attestation.document_version == pc.PERSONAL_CALCULATION_DOCUMENT_VERSION
        assert attestation.document_version  # непустая — то, что требует #324

    def test_before_grant_there_is_nothing_to_attest(self, bot_user):
        """Отрицательная половина: без выдачи — отказ по имени `not_granted`.

        Без неё положительный тест зеленел бы и на реестре, который
        отвечает «есть» всем.
        """
        assert pc.is_granted(bot_user) is False
        with pytest.raises(pc.ConsentAttestationUnavailable) as exc:
            pc.current_attestation(bot_user)
        assert exc.value.reason == pc.NOT_GRANTED


class TestTheVersionIsAContractNotDecoration:
    def test_an_unknown_version_is_refused_and_nothing_is_written(self, bot_user):
        """Показали одну версию — записать просят другую. Отказ.

        `document_version` — единственное доказательство того, ЧТО
        человеку показали. Принять чужую строку значит записать в
        юридический журнал непроверяемое утверждение.
        """
        with pytest.raises(pc.UnknownDisclosureVersionError):
            pc.grant(bot_user, document_version="personal-calculation-v999")

        assert not ConsentRecord.all_tenants.filter(
            bot_user=bot_user, consent_type=pc.PERSONAL_CALCULATION
        ).exists()

    def test_the_version_is_the_same_string_the_catalog_boundary_gets(self, bot_user):
        """Один конец контракта — одна строка. Сравнивается с тем, что
        реально ляжет в `as_payload()`, а не с константой по имени."""
        pc.grant(bot_user, document_version=pc.PERSONAL_CALCULATION_DOCUMENT_VERSION)

        payload = pc.current_attestation(bot_user).as_payload()

        assert payload == {
            "type": "personal_calculation",
            "document_version": pc.PERSONAL_CALCULATION_DOCUMENT_VERSION,
        }


class TestGrantIsIdempotentAndWithdrawIsReversible:
    def test_a_second_grant_does_not_duplicate(self, bot_user):
        pc.grant(bot_user, document_version=pc.PERSONAL_CALCULATION_DOCUMENT_VERSION)
        pc.grant(bot_user, document_version=pc.PERSONAL_CALCULATION_DOCUMENT_VERSION)

        active = ConsentRecord.all_tenants.filter(
            bot_user=bot_user,
            consent_type=pc.PERSONAL_CALCULATION,
            granted=True,
            withdrawn_at__isnull=True,
        )
        assert active.count() == 1

    def test_withdraw_returns_the_surface_to_refusal_without_deleting(self, bot_user):
        """§92: «версия текста, дата, способ получения и ОТЗЫВ сохраняются».

        Отзыв проставляет `withdrawn_at`, строка остаётся — журнал
        append-only. И читающая сторона снова отказывает.
        """
        pc.grant(bot_user, document_version=pc.PERSONAL_CALCULATION_DOCUMENT_VERSION)

        withdrawn = pc.withdraw(bot_user)

        assert withdrawn >= 1
        assert pc.is_granted(bot_user) is False
        rows = ConsentRecord.all_tenants.filter(
            bot_user=bot_user, consent_type=pc.PERSONAL_CALCULATION
        )
        assert rows.exists(), "отзыв не должен удалять строки"
        assert all(r.withdrawn_at is not None for r in rows)
