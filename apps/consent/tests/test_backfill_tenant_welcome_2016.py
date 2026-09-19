"""Бэкфилл строки ``personal_data`` для салонных согласий до DRF-2016.

До этого листа салонное приветствие ставило ``BotUser.consent_at`` и не
писало ``ConsentRecord``; читатели спрашивают строку. Миграция
``0006_backfill_tenant_welcome_personal_data`` доводит старые тапы до
формы, которую новый код пишет сам:

* c7a — ``consent_at`` есть, строк ``personal_data`` нет → одна строка:
  granted, ``captured_at = consent_at`` (когда согласились, а не когда
  выложили), source бэкфилла, version "" (версию текста старого тапа код
  не докажет), tenant — тенант оболочки;
* c7b — ОТОЗВАННАЯ строка есть → ничего: отзыв ``consent_at`` не чистит,
  воскрешать согласие миграцией нельзя;
* c7c — активная строка есть (глобальный путь) → ничего; ``consent_at``
  пуст → ничего; повторный прогон → 0 новых;
* c7d — счётчик: миграция печатает, сколько оболочек получат строку —
  главное окно снимает это число на стенде до выкладки и сверяет после.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone as dj_timezone

from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

migration = importlib.import_module(
    "apps.consent.migrations.0006_backfill_tenant_welcome_personal_data"
)
PERSONAL_DATA = ConsentRecord.ConsentType.PERSONAL_DATA.value
CONSENTED_AT = datetime(2026, 7, 14, 10, 15, tzinfo=timezone.utc)


@pytest.fixture
def historical_apps():
    """Реестр приложений, который Django отдаёт RunPython, — не живой.

    Живой ``ConsentRecord.objects`` — tenant-scoped и без тенанта отвечает
    пустотой; историческая модель в миграции получает обычный менеджер.
    """
    state = MigrationExecutor(connection).loader.project_state(
        ("consent", "0006_backfill_tenant_welcome_personal_data")
    )
    return state.apps


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="drf2016-migration", name="M2016")


def _user(tenant: Tenant, suffix: str, *, consent_at) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"2016-{suffix}",
        consent_at=consent_at,
    )


def _rows(bot_user: BotUser):
    return ConsentRecord.all_tenants.filter(bot_user=bot_user, consent_type=PERSONAL_DATA)


def _run(historical_apps) -> int:
    return migration.run_backfill(historical_apps)


class TestC7TheBackfill:
    def test_a_stamped_user_without_rows_gets_one_dated_row(self, historical_apps, tenant) -> None:
        user = _user(tenant, "stamped", consent_at=CONSENTED_AT)
        assert _rows(user).count() == 0  # положительно: предмет — пустой реестр

        created = _run(historical_apps)

        assert created == 1
        row = _rows(user).get()
        assert row.granted is True and row.withdrawn_at is None
        assert row.captured_at == CONSENTED_AT
        assert row.source == migration.BACKFILL_SOURCE
        assert row.document_version == ""
        assert row.tenant_id == tenant.id

    def test_a_withdrawn_row_is_not_resurrected(self, historical_apps, tenant) -> None:
        user = _user(tenant, "withdrawn", consent_at=CONSENTED_AT)
        ConsentRecord.all_tenants.create(
            tenant=tenant,
            bot_user=user,
            consent_type=PERSONAL_DATA,
            granted=True,
            source="test:earlier",
            withdrawn_at=dj_timezone.now() - timedelta(days=1),
        )
        assert _rows(user).count() == 1

        assert _run(historical_apps) == 0
        assert _rows(user).count() == 1
        assert _rows(user).get().withdrawn_at is not None

    def test_active_row_or_no_stamp_means_nothing_and_rerun_is_idempotent(
        self, historical_apps, tenant
    ) -> None:
        active = _user(tenant, "active", consent_at=CONSENTED_AT)
        ConsentRecord.all_tenants.create(
            tenant=tenant,
            bot_user=active,
            consent_type=PERSONAL_DATA,
            granted=True,
            source="global_onboarding:welcome_s2",
        )
        _user(tenant, "unstamped", consent_at=None)
        fresh = _user(tenant, "fresh", consent_at=CONSENTED_AT)

        assert _run(historical_apps) == 1  # только fresh
        assert _rows(active).count() == 1
        assert _rows(fresh).count() == 1
        assert _run(historical_apps) == 0  # повторный прогон
        assert _rows(fresh).count() == 1

    def test_the_counter_is_printed(self, historical_apps, tenant, capsys) -> None:
        _user(tenant, "one", consent_at=CONSENTED_AT)
        _user(tenant, "two", consent_at=CONSENTED_AT)
        _run(historical_apps)
        out = capsys.readouterr().out
        assert "drf2016 backfill: candidates=2 created=2" in out
