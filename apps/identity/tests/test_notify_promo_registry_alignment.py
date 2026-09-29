"""DRF-1520 — миграция 0021 сводит ``notify_promo`` к реестру согласий.

Data-миграция, которая гасит пользовательский тумблер на всех строках без
доказанного согласия, обязана быть проверена: ошибка направления здесь либо
выдумывает согласие за человека, либо молча стирает то, которое он дал.

Функция вызывается напрямую — так тест видит именно её логику, а не
результат прогона всей цепочки миграций, где расхождение легко списать на
соседний шаг. Историческое состояние моделей эмулируется реальным реестром
приложений: миграция читает только те поля, которые с тех пор не менялись.
"""

from __future__ import annotations

import importlib

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser, UserPreferences
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

_MIGRATION = importlib.import_module(
    "apps.identity.migrations.0021_notify_promo_mirrors_consent_registry"
)


def _align() -> None:
    """Прогнать функцию миграции на ИСТОРИЧЕСКОМ реестре моделей.

    Не на живом: у живых ``UserPreferences``/``ConsentRecord`` менеджер
    ``objects`` — ``TenantScopedManager``, и вызов без тенанта в scope не
    увидел бы ни строки. Исторические модели рендерятся с обычным
    ``Manager`` (``use_in_migrations`` в репозитории не выставлен нигде), и
    именно поэтому миграция действительно проходит по всем площадкам.
    Тест обязан гонять то же, что прогонит ``migrate``, — иначе он
    подтверждает поведение, которого в проде не будет.
    """
    executor = MigrationExecutor(connection)
    # Оба узла: состояние строится по графу от перечисленных вершин, и без
    # ветки ``consent`` в нём не окажется ``ConsentRecord`` — ровно тех
    # зависимостей, которые объявлены у миграции 0021.
    state = executor.loader.project_state(
        [
            ("identity", "0020_drop_userpreferences_allergies"),
            ("consent", "0003_backfill_memory_green_consent"),
        ],
    )
    _MIGRATION.align_notify_promo_to_registry(state.apps, None)


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="promo-align", name="Promo align")


def _user(tenant: Tenant, channel_user_id: str, *, notify_promo: bool) -> BotUser:
    user = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id=channel_user_id)
    UserPreferences.all_tenants.create(bot_user=user, tenant=tenant, notify_promo=notify_promo)
    return user


def _grant(user: BotUser, *, withdrawn_at=None) -> ConsentRecord:
    return ConsentRecord.all_tenants.create(
        tenant=user.tenant,
        bot_user=user,
        consent_type=ConsentRecord.ConsentType.MARKETING.value,
        granted=True,
        source="test:seed",
        withdrawn_at=withdrawn_at,
    )


def test_enum_value_the_migration_hardcodes(tenant) -> None:
    """Миграция ищет строку по литералу — он обязан совпасть с enum."""
    assert ConsentRecord.ConsentType.MARKETING.value == "marketing"


def test_toggle_without_a_proven_grant_is_switched_off(tenant) -> None:
    """Недоказуемое согласие гасится, а не дописывается в реестр задним числом."""
    user = _user(tenant, "align-1", notify_promo=True)
    assert UserPreferences.all_tenants.get(bot_user=user).notify_promo is True

    _align()

    assert UserPreferences.all_tenants.get(bot_user=user).notify_promo is False
    # Согласие за человека не проставлено — реестр остался пуст.
    assert ConsentRecord.all_tenants.filter(bot_user=user).count() == 0


def test_toggle_with_a_proven_grant_survives(tenant) -> None:
    """Строку с действующим согласием миграция не трогает.

    Стоит рядом с предыдущим тестом намеренно: без него «выключилось»
    прошло бы и у миграции, которая гасит всё подряд.
    """
    user = _user(tenant, "align-2", notify_promo=True)
    _grant(user)

    _align()

    assert UserPreferences.all_tenants.get(bot_user=user).notify_promo is True


def test_withdrawn_grant_does_not_count_as_proof(tenant) -> None:
    """Отозванное согласие — не согласие; тумблер гасится."""
    user = _user(tenant, "align-3", notify_promo=True)
    from django.utils import timezone

    _grant(user, withdrawn_at=timezone.now())
    assert UserPreferences.all_tenants.get(bot_user=user).notify_promo is True

    _align()

    assert UserPreferences.all_tenants.get(bot_user=user).notify_promo is False


def test_proven_grant_switches_the_mirror_on(tenant) -> None:
    """Симметричная половина: зеркало догоняет реестр и в обратную сторону."""
    user = _user(tenant, "align-4", notify_promo=False)
    _grant(user)
    assert UserPreferences.all_tenants.get(bot_user=user).notify_promo is False

    _align()

    assert UserPreferences.all_tenants.get(bot_user=user).notify_promo is True
