"""Салонное приветствие пишет согласие в реестр, а не только в столбец (DRF-2016).

Салонный путь S2 («Да, продолжим» при ``current_tenant()``) ставил
``bot_user.consent_at`` и всё. Читатели же — ``diary_write_refusal`` →
``personal_records_consent_open`` → ``has_global_consent(bot_user,
PERSONAL_DATA)`` — спрашивают СТРОКУ реестра у этого ``BotUser``, столбец не
читают. Человеку, пришедшему через салонный бот, отказ ``consent_required``
повторялся после «Дать согласие». После #1839 / #1843 реестр — единственное
основание, так что дефект живой.

Починка — салонный путь пишет ту же строку тем же person-level примитивом,
что и глобальный (``record_person_consent`` → ``record_global_consent``):
строка на каждую оболочку человека, ``consent_at`` штампуется атомарно со
строкой, повторный тап — без дублей.

* c1 — салонный тап → активная строка ``personal_data`` у ``bot_user``
  (source / version названы), ``consent_at == captured_at``; via_s2a — та
  же строка; второй тап — по-прежнему одна;
* c2 — после салонного согласия дневник не отказывает по
  ``consent_required`` (открытый реестр дневника → ``None``; закрытый →
  ``FOOD_DIARY_CONSENT_REQUIRED``, а не ``CONSENT_REQUIRED``);
* c3 — person-level: вторая оболочка того же человека (тот же канал и
  channel_user_id в другом тенанте) получает свою строку;
* c4 — глобальный путь не изменился: без тенанта в скоупе — строк 0,
  ``consent_at`` None (журнал пишет global_onboarding после рендера);
* c5 — салонный возврат из отказа (кнопка DRF-1968 при салонном скоупе) —
  та же строка;
* c6 — сбой реестра: S5 отдан, ``consent_at`` None, строк 0 — состояние
  «consent_at без строки» недостижимо и при сбое;
* c7 — версия документа S2 одна на оба пути (константа приветствия равна
  константе глобального онбординга).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apps.consent import diary_gate
from apps.consent.models import ConsentRecord
from apps.consent.services import has_global_consent
from apps.identity.models import BotUser
from apps.skills.base import SkillContext
from apps.skills.welcome import skill as welcome_skill
from apps.skills.welcome.skill import WelcomeSkill
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

PERSONAL_DATA = ConsentRecord.ConsentType.PERSONAL_DATA.value


@pytest.fixture
def salon_user() -> BotUser:
    tenant = Tenant.objects.create(slug="drf2016-salon", name="Salon 2016")
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="drf2016-person",
        chat_id="max-2016",
        welcomed_at=None,
    )


def _ctx(text: str, bot_user: BotUser) -> SkillContext:
    return SkillContext(conversation=MagicMock(), bot_user=bot_user, message_text=text)


def _rows(bot_user: BotUser):
    return ConsentRecord.all_tenants.filter(bot_user=bot_user, consent_type=PERSONAL_DATA)


def _tap(text: str, bot_user: BotUser):
    with tenant_scope(bot_user.tenant):
        return WelcomeSkill().handle(_ctx(text, bot_user))


class TestC1TheSalonTapWritesTheRegistryRow:
    def test_direct_tap(self, salon_user: BotUser) -> None:
        result = _tap("cb:welcome:consent_yes", salon_user)
        assert result.meta["reply_kind"] == "welcome_s5_first_action"

        rows = list(_rows(salon_user))
        assert len(rows) == 1
        row = rows[0]
        assert row.granted is True and row.withdrawn_at is None
        assert row.source == welcome_skill.S2_CONSENT_SOURCE_TENANT
        assert row.document_version == welcome_skill.S2_CONSENT_DOCUMENT_VERSION
        assert row.tenant_id == salon_user.tenant_id
        salon_user.refresh_from_db()
        assert salon_user.consent_at == row.captured_at  # атомарно со строкой
        assert has_global_consent(salon_user, PERSONAL_DATA)

    def test_via_s2a_and_a_repeat_tap_keep_one_row(self, salon_user: BotUser) -> None:
        _tap("cb:welcome:consent_yes_via_s2a", salon_user)
        _tap("cb:welcome:consent_yes", salon_user)
        assert _rows(salon_user).count() == 1


class TestC2TheDiaryOpensAfterTheSalonConsent:
    def test_refusal_is_no_longer_consent_required(self, salon_user: BotUser, settings) -> None:
        settings.NUTRITION_ENABLED = True
        # До согласия — отказ по PERSONAL_DATA (положительная стража предмету).
        assert diary_gate.diary_write_refusal(salon_user) == diary_gate.CONSENT_REQUIRED

        _tap("cb:welcome:consent_yes", salon_user)

        with patch("apps.consent.nutrition.diary_is_granted", return_value=True):
            assert diary_gate.diary_write_refusal(salon_user) is None
        with patch("apps.consent.nutrition.diary_is_granted", return_value=False):
            assert (
                diary_gate.diary_write_refusal(salon_user) == diary_gate.FOOD_DIARY_CONSENT_REQUIRED
            )


class TestC3TheRowLandsOnEveryShellOfThePerson:
    def test_channel_sibling_in_another_tenant_gets_its_row(self, salon_user: BotUser) -> None:
        other = Tenant.objects.create(slug="drf2016-other", name="Other 2016")
        sibling = BotUser.all_tenants.create(
            tenant=other,
            channel=salon_user.channel,
            channel_user_id=salon_user.channel_user_id,
            chat_id="max-2016-b",
        )
        assert sibling.pk != salon_user.pk  # положительно: это другая строка того же человека
        before = has_global_consent(sibling, PERSONAL_DATA)
        assert before is False  # empty-assert-ok: same predicate turns True below

        _tap("cb:welcome:consent_yes", salon_user)

        assert has_global_consent(sibling, PERSONAL_DATA)
        assert _rows(sibling).get().tenant_id == other.id  # строка несёт тенант своей оболочки


class TestC4TheGlobalPathIsUntouched:
    def test_no_tenant_in_scope_writes_nothing(self, salon_user: BotUser) -> None:
        result = WelcomeSkill().handle(_ctx("cb:welcome:consent_yes", salon_user))
        assert result.meta["reply_kind"] == "welcome_s5_first_action"
        salon_user.refresh_from_db()
        assert salon_user.consent_at is None
        assert _rows(salon_user).count() == 0


class TestC5TheRecoveryTapOnTheSalonPathWritesTheSameRow:
    def test_recovery_grant_funnels_into_the_registry(self, salon_user: BotUser) -> None:
        origin = welcome_skill.CONSENT_RECOVERY_ORIGINS[0]
        result = _tap(f"cb:welcome:consent_yes_{origin}", salon_user)
        assert result.meta["reply_kind"] == "welcome_s5_first_action"  # салонный путь, не «готово»
        assert _rows(salon_user).count() == 1
        salon_user.refresh_from_db()
        assert salon_user.consent_at is not None


class TestC6ARegistryFailureLeavesNoHalfState:
    def test_no_stamp_without_a_row(self, salon_user: BotUser, caplog) -> None:
        with patch.object(
            welcome_skill, "record_person_consent", side_effect=RuntimeError("registry down")
        ):
            result = _tap("cb:welcome:consent_yes", salon_user)
        assert result.meta["reply_kind"] == "welcome_s5_first_action"  # экран отдан
        salon_user.refresh_from_db()
        assert salon_user.consent_at is None
        assert _rows(salon_user).count() == 0
        assert any("welcome.consent_record_failed" in r.getMessage() for r in caplog.records)


class TestC7OneDocumentVersionForBothPaths:
    def test_constants_agree(self) -> None:
        from apps.channels.max.global_onboarding import CONSENT_DOCUMENT_VERSION

        assert welcome_skill.S2_CONSENT_DOCUMENT_VERSION == CONSENT_DOCUMENT_VERSION
