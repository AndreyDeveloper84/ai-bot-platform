"""Утверждение о согласии на расчёт — то, что бот прикладывает к POST (DRF-1658).

Проверяется ВТОРАЯ половина контракта границы каталога (beautygo_backend#324):
бот превращает действующую запись реестра в утверждение той формы, которую
ждёт граница, и не выдаёт утверждения там, где основания нет.

Три отказа проверяются по имени, а не «упало/не упало»: у каждого свой
адрес починки, и тест, различающий только факт отказа, зеленел бы на
коде, который на любую беду отвечает одним и тем же словом.

``consent_type="personal_calculation"`` пишется здесь строкой: член
перечисления вводит #1523, а ``CharField.choices`` на записи не
проверяется — реестр принимает значение уже сегодня. Когда #1523
сольётся, строку заменить на ``ConsentRecord.ConsentType.PERSONAL_CALCULATION``.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.consent import personal_calculation as pc
from apps.consent.models import ConsentRecord
from apps.consent.services import record_global_consent, withdraw
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)

CALC = "personal_calculation"
VERSION = "personal-calculation-v1"


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="pc-attest", name="PC")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="pc-1")


def _grant(bot_user: BotUser, *, version: str) -> ConsentRecord:
    return record_global_consent(
        bot_user,
        consent_type=CALC,
        source="test:pc_attestation",
        document_version=version,
    )


class TestAttestationMirrorsTheBoundaryContract:
    def test_granted_with_version_yields_the_exact_form_324_expects(self, bot_user) -> None:
        """Положительная стража — ВПЕРЕДИ отказов.

        Форма сверяется с #324 дословно: ``ConsentAttestationSerializer``
        читает ровно ``type`` и ``document_version``; лишний или
        переименованный ключ граница не примет.
        """
        _grant(bot_user, version=VERSION)

        attestation = pc.current_attestation(bot_user)

        assert attestation.as_payload() == {
            "type": "personal_calculation",
            "document_version": VERSION,
        }

    def test_attach_adds_the_consent_key_and_leaves_the_body_alone(self, bot_user) -> None:
        _grant(bot_user, version=VERSION)
        attestation = pc.current_attestation(bot_user)
        body = {"weight_kg": 62}

        out = pc.attach(body, attestation)

        assert out == {
            "weight_kg": 62,
            "consent": {"type": "personal_calculation", "document_version": VERSION},
        }
        # Исходный словарь не тронут — вызывающий может держать его дальше.
        assert body == {"weight_kg": 62}

    def test_the_newest_active_grant_wins(self, bot_user) -> None:
        """Две действующие записи с разными версиями — берётся свежая.

        ``captured_at`` разводится явно через ``update()``: ``auto_now_add``
        выбросил бы значение из ``create()``, а два ``create()`` подряд
        могут лечь в одну микросекунду — тогда порядок решала бы БД.
        """
        now = timezone.now()
        old = ConsentRecord.all_tenants.create(
            tenant=bot_user.tenant,
            bot_user=bot_user,
            consent_type=CALC,
            granted=True,
            source="test:old",
            document_version="personal-calculation-v1",
        )
        new = ConsentRecord.all_tenants.create(
            tenant=bot_user.tenant,
            bot_user=bot_user,
            consent_type=CALC,
            granted=True,
            source="test:new",
            document_version="personal-calculation-v2",
        )
        ConsentRecord.all_tenants.filter(pk=old.pk).update(captured_at=now - timedelta(days=1))
        ConsentRecord.all_tenants.filter(pk=new.pk).update(captured_at=now)

        assert pc.current_attestation(bot_user).document_version == "personal-calculation-v2"


class TestEveryRefusalIsNamed:
    def test_no_record_at_all_is_not_granted(self, bot_user) -> None:
        with pytest.raises(pc.ConsentAttestationUnavailable) as exc:
            pc.current_attestation(bot_user)
        assert exc.value.reason == pc.NOT_GRANTED

    def test_withdrawn_grant_is_not_granted(self, tenant, bot_user) -> None:
        _grant(bot_user, version=VERSION)
        with tenant_scope(tenant):
            withdraw(bot_user, consent_type=CALC, source="test:withdraw")

        with pytest.raises(pc.ConsentAttestationUnavailable) as exc:
            pc.current_attestation(bot_user)
        assert exc.value.reason == pc.NOT_GRANTED

    def test_declined_row_is_not_granted(self, bot_user) -> None:
        """``granted=False`` — строка явного отказа, не основание."""
        ConsentRecord.all_tenants.create(
            tenant=bot_user.tenant,
            bot_user=bot_user,
            consent_type=CALC,
            granted=False,
            source="test:declined",
            document_version=VERSION,
        )
        with pytest.raises(pc.ConsentAttestationUnavailable) as exc:
            pc.current_attestation(bot_user)
        assert exc.value.reason == pc.NOT_GRANTED

    def test_another_type_does_not_count(self, bot_user) -> None:
        """Согласие на медданные — не согласие на расчёт (§92: типы раздельны)."""
        record_global_consent(
            bot_user,
            consent_type=ConsentRecord.ConsentType.HEALTH.value,
            source="test:health",
            document_version="health-data-v1",
        )
        with pytest.raises(pc.ConsentAttestationUnavailable) as exc:
            pc.current_attestation(bot_user)
        assert exc.value.reason == pc.NOT_GRANTED

    @pytest.mark.parametrize("version", ["", "   "])
    def test_grant_without_a_document_version_is_its_own_refusal(self, bot_user, version) -> None:
        """Запись есть, версии нет — это НЕ ``not_granted``.

        Граница #324 такое утверждение отвергнет (``document_version``
        обязателен и непуст), и чинится это не согласием человека, а
        текстом, под которым его взяли. Слить два отказа в один значило
        бы послать чинить не то.
        """
        _grant(bot_user, version=version)

        with pytest.raises(pc.ConsentAttestationUnavailable) as exc:
            pc.current_attestation(bot_user)
        assert exc.value.reason == pc.NO_DOCUMENT_VERSION

    def test_registry_failure_closes_rather_than_opens(self, bot_user) -> None:
        """Fail-closed: реестр не ответил — утверждения нет, и это названо."""
        with patch.object(pc, "_active_record", side_effect=RuntimeError("db down")):
            with pytest.raises(pc.ConsentAttestationUnavailable) as exc:
                pc.current_attestation(bot_user)
        assert exc.value.reason == pc.LOOKUP_FAILED


class TestTheThreeReasonsAreDistinct:
    def test_reason_names_do_not_collide(self) -> None:
        """Три имени — три разных строки. Иначе лог не различит адрес починки."""
        reasons = {pc.NOT_GRANTED, pc.NO_DOCUMENT_VERSION, pc.LOOKUP_FAILED}
        assert len(reasons) == 3
