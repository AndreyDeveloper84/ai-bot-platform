"""DRF-2542 §7 — сброс аккаунта с красной строкой под ролью, на которую действует RLS.

Тестовая база, как и стенд, ходит суперпользователем: политика
``memory_entry_non_red_visible`` на него не действует, и узлы сброса зеленеют,
ничего не говоря о дне, когда приложение перейдёт на обычную роль (ADR-0011
§16). Поэтому здесь роль обычная — создаётся внутри транзакции узла и уходит с
её откатом.

Что было (замер на ``d62e07bd``): каскад от ``UserPersonalContext`` красную
строку не видел, ``verify`` рапортовал «чисто», а внешний ключ падал при
фиксации — сброс откатывался целиком, с успешным отчётом на руках.

* r0 — стража: роль действительно не супер, и красная строка ей без GUC не
  видна (иначе остальные узлы мерили бы суперпользователя);
* r1 — сброс снимает красную и зелёную строки, отложенные ключи целы;
* r2 — на красную строку остаётся строка журнала ``purge``, на зелёную — нет;
* r3 — после сброса GUC снят: красные строки других людей не открылись.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import connection
from django.utils import timezone

from apps.identity.models import BotUser, MemoryEntry, RedZoneAccessLog, UserPersonalContext
from apps.identity.services import account_reset as reset
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

ACCOUNT = "max:999000542"
RED = MemoryEntry.SENSITIVITY_RED
GREEN = MemoryEntry.SENSITIVITY_GREEN


def _memory(user_id: uuid.UUID, upc: UserPersonalContext, zone: str, tenant: Tenant) -> MemoryEntry:
    return MemoryEntry.objects.create(
        user_id=user_id,
        personal_context=upc,
        sensitivity_zone=zone,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        consent_at=timezone.now(),
        content={"key": "probe", "value": zone},
        source_tenant_id=tenant.id,
    )


@pytest.fixture
def account(settings) -> dict[str, uuid.UUID]:
    """Аккаунт из allowlist с красной и зелёной строкой + чужая красная строка."""
    settings.ACCOUNT_RESET_ALLOWLIST = [ACCOUNT]
    tenant = Tenant.objects.create(slug="reset-rls-2542", name="Reset RLS")
    user_id = uuid.uuid4()
    upc = UserPersonalContext.objects.create(user_id=user_id)
    channel, channel_user_id = reset.parse_account(ACCOUNT)
    BotUser.all_tenants.create(
        tenant=tenant,
        channel=channel,
        channel_user_id=channel_user_id,
        display_name="Тест",
        ayla_user_id=user_id,
    )
    red = _memory(user_id, upc, RED, tenant)
    green = _memory(user_id, upc, GREEN, tenant)

    other_id = uuid.uuid4()
    other_upc = UserPersonalContext.objects.create(user_id=other_id)
    other_red = _memory(other_id, other_upc, RED, tenant)
    return {"user": user_id, "red": red.id, "green": green.id, "other_red": other_red.id}


@pytest.fixture
def plain_role(account) -> str:
    """Обычная роль на время узла: не супер, RLS не обходит, права на таблицы есть."""
    role = f"reset_rls_{uuid.uuid4().hex[:10]}"
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS")
        cursor.execute(f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {role}")
        cursor.execute(f"GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {role}")
        cursor.execute(f"SET LOCAL ROLE {role}")
    return role


def _as_superuser_count(entry_ids: list[uuid.UUID]) -> int:
    """Сколько из строк осталось в базе — глазами суперпользователя, мимо RLS."""
    with connection.cursor() as cursor:
        cursor.execute("RESET ROLE")
        cursor.execute(
            "SELECT count(*) FROM identity_memoryentry WHERE id = ANY(%s)",
            [entry_ids],
        )
        return int(cursor.fetchone()[0])


def _check_deferred_constraints() -> None:
    """То, что произошло бы при фиксации: отложенные внешние ключи проверяются сейчас."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_r0_the_role_is_plain_and_does_not_see_the_red_row(account, plain_role) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        assert cursor.fetchone() == (False, False)

    visible = set(MemoryEntry.objects.filter(user_id=account["user"]).values_list("id", flat=True))

    assert visible == {account["green"]}


def test_r1_the_reset_removes_the_red_row_and_the_keys_hold(account, plain_role) -> None:
    report = reset.apply(ACCOUNT, "client-onboarding")
    _check_deferred_constraints()

    assert report.leftovers == []
    assert report.removed["identity.MemoryEntry"] == 2
    assert not UserPersonalContext.objects.filter(user_id=account["user"]).exists()
    assert _as_superuser_count([account["red"], account["green"]]) == 0


def test_r2_the_red_row_leaves_a_purge_log_and_the_green_one_does_not(account, plain_role) -> None:
    reset.apply(ACCOUNT, "client-onboarding")

    logs = list(RedZoneAccessLog.objects.filter(user_id=account["user"]))
    assert [(log.memory_entry_id, log.access_type, log.accessor_role) for log in logs] == [
        (account["red"], "purge", "system_job")
    ]
    assert logs[0].accessor_principal.startswith("account_reset:")


def test_r3_the_reset_does_not_leave_the_red_zone_open(account, plain_role) -> None:
    reset.apply(ACCOUNT, "client-onboarding")

    assert not MemoryEntry.objects.filter(id=account["other_red"]).exists()
    assert _as_superuser_count([account["other_red"]]) == 1
