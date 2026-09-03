"""DRF-1453 — нутриционная поверхность на РЕАЛЬНЫХ строках согласия.

Соседний ``test_nutrition_context.py`` проверяет сторож, подменяя
``has_global_consent`` — это правильная проверка формы гейта и никакая
проверка того, что согласие вообще можно выдать. До DRF-1453 выдать
``HEALTH`` было нечем: тип объявлен в модели, присутствует в миграциях и
больше нигде. Отрицательное утверждение «без согласия отказывает» в такой
ситуации ничего не стоит — отказывало бы и с согласием.

Поэтому здесь обе половины на ОДНИХ И ТЕХ ЖЕ данных, без единого мока
согласия (мокается только сеть до Ayla):

1. без ``HEALTH`` — отказ, до Ayla дело не доходит;
2. после :func:`apps.consent.health.grant` — тот же вызов, те же данные,
   ответ по существу;
3. после :func:`apps.consent.health.withdraw` — снова отказ.

Плюс то, ради чего выдача сделана person-level: человек соглашается в
мини-приложении, а читает согласие консьерж — это РАЗНЫЕ ``BotUser``
(разные тенанты, одна пара ``channel``/``channel_user_id``). Грант,
записанный только на спросившую строку, был бы для читающей поверхности
невидим: формально согласие есть, фактически поверхность спит.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.consent import health as health_consent
from apps.consent.models import ConsentRecord
from apps.consent.services import record_global_consent, withdraw_personal_data_for_bot_users
from apps.identity.models import BotUser
from apps.orchestrator import nutrition_context
from apps.orchestrator.nutrition_context import build_nutrition_context_block
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "1453001"


def _deficits() -> SimpleNamespace:
    return SimpleNamespace(
        days_observed=5,
        protein_avg_pct_goal=62.4,
        protein_low_streak_days=4,
        hint="белка стабильно мало",
        fired_keys=["protein_low"],
        raw={},
    )


@pytest.fixture(autouse=True)
def _flag_on(settings):
    settings.CONCIERGE_NUTRITION_CONTEXT_ENABLED = True


@pytest.fixture
def ayla(monkeypatch) -> Mock:
    """Единственный мок в файле — сеть до Ayla. Согласия настоящие."""
    fetch = Mock(return_value=_deficits())
    monkeypatch.setattr(nutrition_context, "_fetch_deficits", fetch)
    return fetch


@pytest.fixture
def miniapp_user(db) -> BotUser:
    """Строка, под которой человека видит мини-приложение."""
    tenant = Tenant.objects.create(slug="hc-salon", name="Салон")
    user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=CHANNEL_USER_ID,
        display_name="Анна",
    )
    # 152-ФЗ база: без неё вторая половина сторожа закрыта независимо от HEALTH.
    record_global_consent(user, source="test:welcome")
    return user


@pytest.fixture
def concierge_user(miniapp_user: BotUser) -> BotUser:
    """Строка того же человека под сентинелом — её читает консьерж."""
    from apps.identity.services import get_global_bot_tenant

    # Сентинел засеян миграцией 0014 — резолвим, а не создаём второй.
    sentinel = get_global_bot_tenant()
    user = BotUser.all_tenants.create(
        tenant=sentinel,
        channel="max",
        channel_user_id=CHANNEL_USER_ID,
        display_name="Анна",
    )
    record_global_consent(user, source="test:welcome")
    return user


def _grant_version() -> str:
    return health_consent.HEALTH_CONSENT_DOCUMENT_VERSION


class TestGrantOpensTheSurface:
    """Отказ, выдача, ответ по существу, отзыв, снова отказ."""

    def test_without_health_consent_the_surface_refuses(self, miniapp_user, ayla) -> None:
        assert build_nutrition_context_block(miniapp_user) == ""
        ayla.assert_not_called()

    def test_after_grant_the_same_call_answers(self, miniapp_user, ayla) -> None:
        assert build_nutrition_context_block(miniapp_user) == ""

        health_consent.grant(miniapp_user, document_version=_grant_version())

        block = build_nutrition_context_block(miniapp_user)
        assert block != ""
        # По существу, а не «непустая строка»: недельная картина доехала.
        assert "белка стабильно мало" in block
        ayla.assert_called()

    def test_withdraw_puts_it_back_to_sleep(self, miniapp_user, ayla) -> None:
        health_consent.grant(miniapp_user, document_version=_grant_version())
        assert build_nutrition_context_block(miniapp_user) != ""

        withdrawn = health_consent.withdraw(miniapp_user)

        assert withdrawn >= 1
        assert build_nutrition_context_block(miniapp_user) == ""

    def test_withdraw_keeps_the_audit_row(self, miniapp_user) -> None:
        """Отзыв не удаляет доказательство — он его датирует (append-only)."""
        health_consent.grant(miniapp_user, document_version=_grant_version())
        health_consent.withdraw(miniapp_user)

        rows = ConsentRecord.all_tenants.filter(
            bot_user=miniapp_user, consent_type=ConsentRecord.ConsentType.HEALTH
        )
        assert rows.count() == 1
        assert rows.get().withdrawn_at is not None

    def test_grant_is_idempotent(self, miniapp_user) -> None:
        for _ in range(3):
            health_consent.grant(miniapp_user, document_version=_grant_version())
        assert (
            ConsentRecord.all_tenants.filter(
                bot_user=miniapp_user,
                consent_type=ConsentRecord.ConsentType.HEALTH,
                withdrawn_at__isnull=True,
            ).count()
            == 1
        )

    def test_withdraw_without_a_grant_is_a_no_op(self, miniapp_user) -> None:
        assert health_consent.withdraw(miniapp_user) == 0


class TestSeparateFromPersonalData:
    """Особая категория не выдаётся заодно с базовым согласием."""

    def test_personal_data_alone_does_not_open_health(self, miniapp_user, ayla) -> None:
        # miniapp_user уже держит personal_data (фикстура) — и этого мало.
        assert health_consent.is_granted(miniapp_user) is False
        assert build_nutrition_context_block(miniapp_user) == ""

    def test_unknown_disclosure_version_is_refused(self, miniapp_user) -> None:
        """Согласие записывается на показанный текст, а не на абстрактное «да»."""
        with pytest.raises(health_consent.UnknownDisclosureVersionError):
            health_consent.grant(miniapp_user, document_version="health-data-v0")
        assert health_consent.is_granted(miniapp_user) is False

    def test_personal_data_withdrawal_cascades_to_health(self, miniapp_user) -> None:
        """Вышел из персонализированного сервиса — особая категория закрылась."""
        health_consent.grant(miniapp_user, document_version=_grant_version())

        withdraw_personal_data_for_bot_users(
            BotUser.all_tenants.filter(id=miniapp_user.id), source="test:erase"
        )

        assert health_consent.is_granted(miniapp_user) is False


class TestGrantReachesTheReadingSurface:
    """Соглашаются в одной строке, читает другая. Обе строки одного человека."""

    def test_grant_from_miniapp_opens_the_concierge_shell(
        self, miniapp_user, concierge_user, ayla
    ) -> None:
        assert build_nutrition_context_block(concierge_user) == ""

        # Тап в мини-приложении — по своей строке.
        health_consent.grant(miniapp_user, document_version=_grant_version())

        # Консьерж читает СВОЮ строку и должен увидеть согласие.
        assert build_nutrition_context_block(concierge_user) != ""

    def test_withdraw_from_miniapp_closes_the_concierge_shell(
        self, miniapp_user, concierge_user, ayla
    ) -> None:
        health_consent.grant(miniapp_user, document_version=_grant_version())
        assert build_nutrition_context_block(concierge_user) != ""

        health_consent.withdraw(miniapp_user)

        assert build_nutrition_context_block(concierge_user) == ""

    def test_a_stranger_on_another_channel_id_is_untouched(
        self, miniapp_user, concierge_user, ayla
    ) -> None:
        """Веер идёт по строкам человека, а не по всем подряд."""
        stranger = BotUser.all_tenants.create(
            tenant=concierge_user.tenant,
            channel="max",
            channel_user_id="9999999",
            display_name="Не она",
        )
        record_global_consent(stranger, source="test:welcome")

        health_consent.grant(miniapp_user, document_version=_grant_version())

        assert health_consent.is_granted(stranger) is False
        assert build_nutrition_context_block(stranger) == ""
