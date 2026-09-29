"""Салон, который клиенту не показывает никого, произносится вслух.

DRF-1540 поставил в гейт продажи ``ayla_user_id IS NOT NULL``, и у этого
решения есть состояние, при котором клиент получает пустую выдачу, а
причину ему не называет никто: мастера приняты, активны, не в архиве —
и ни одна строка не проходит гейт. Построчный ``sale_block`` отвечал про
это честно и по одному; суммы не задавал никто.

Сценарий соло-мастера воспроизводится здесь буквально в той форме, в
которой его создаёт ``apps.identity.services.solo_onboarding``:
``ACCEPTED`` + ``is_active=True`` + пустой ``ayla_user_id``, без
``accepted_at``. Заполнить ключ ей некому — приглашения она не
принимала, синхронизация по её ``_solo_external_id`` не придёт.

**Отрицательное утверждение не живёт без парной положительной стражи на
тех же данных** (negative_assert_guard, DRF-1411): каждое «не кричит»
стоит рядом с «на этих же данных счётчик посмотрел и посчитал». Счётчик,
не увидевший ни одного салона, молчит тоже, и молчание такого рода —
ровно то, что здесь заменяется.
"""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone

from apps.catalog.master_state import sale_block
from apps.catalog.models import CatalogMaster
from apps.catalog.visibility import invisible_tenants, tenant_visibilities
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

ACCEPTED = CatalogMaster.InviteStatus.ACCEPTED


@pytest.fixture
def salon(db) -> Tenant:
    Tenant.objects.all().delete()
    return Tenant.objects.create(slug="formula-tela", name="Формула Тела")


def _master(tenant: Tenant, name: str, **fields) -> CatalogMaster:
    """Строка каталога с явными столбцами гейта и без умолчаний-сюрпризов."""

    defaults = dict(
        invite_status=ACCEPTED,
        is_active=True,
        archived_at=None,
        ayla_user_id=uuid.uuid4(),
        accepted_at=timezone.now(),
        external_updated_at=timezone.now(),
    )
    defaults.update(fields)
    return CatalogMaster.all_tenants.create(tenant=tenant, name=name, **defaults)


def _solo_master(tenant: Tenant, name: str) -> CatalogMaster:
    """Соло-мастер в той форме, в которой её создаёт ``solo_onboarding``.

    Ключевое здесь — ``ayla_user_id=None`` при принятом приглашении и
    ``is_active=True``: для ``is_admitted`` строка в полном порядке, для
    ``sale_block`` — ``ayla_unlinked``.
    """

    return _master(tenant, name, ayla_user_id=None, accepted_at=None)


class TestInvisibleWhole:
    """«Клиент не увидит никого» отличается от «салон ещё не заполнили»."""

    def test_solo_master_without_ayla_key_makes_the_tenant_invisible(self, salon: Tenant) -> None:
        _solo_master(salon, "Анна")

        invisible = invisible_tenants()

        assert [v.slug for v in invisible] == ["formula-tela"]
        assert invisible[0].admitted == 1
        assert invisible[0].available == 0
        assert invisible[0].unlinked == 1

    def test_one_sellable_master_is_enough_to_be_visible(self, salon: Tenant) -> None:
        _solo_master(salon, "Анна")
        _master(salon, "Борис")

        seen = tenant_visibilities()

        # Стража присутствия: счётчик посмотрел на салон и посчитал обе
        # строки, прежде чем промолчать про невидимость.
        assert [v.slug for v in seen] == ["formula-tela"]
        assert (seen[0].admitted, seen[0].available, seen[0].unlinked) == (2, 1, 1)
        assert not seen[0].invisible_whole
        assert invisible_tenants() == []

    def test_empty_tenant_is_not_a_silent_failure(self, salon: Tenant) -> None:
        """Ноль строк — состояние подключения, а не поломка.

        ``available == 0`` в одиночку кричало бы здесь, и краснота была
        бы вечной: пустой салон не «сломался молча», его ещё не
        заполнили. Расхождение зеркала с бэкендом ловит другой сигнал.
        """

        seen = tenant_visibilities()

        # Стража присутствия: салон в отчёте есть, и он именно пуст.
        assert [v.slug for v in seen] == ["formula-tela"]
        assert (seen[0].mirrored, seen[0].admitted, seen[0].available) == (0, 0, 0)
        assert not seen[0].invisible_whole
        assert invisible_tenants() == []

    def test_all_revoked_is_a_decision_and_does_not_cry(self, salon: Tenant) -> None:
        """Отозванные — решение владелицы, у него есть своё слово в ростере.

        Кричать стоит о том, что выглядит рабочим и не продаётся, а не о
        том, что владелица сама сняла с витрины.
        """

        _master(salon, "Анна", archived_at=timezone.now())
        _master(salon, "Борис", archived_at=timezone.now())

        seen = tenant_visibilities()

        assert [v.slug for v in seen] == ["formula-tela"]
        assert (seen[0].mirrored, seen[0].admitted, seen[0].available) == (2, 0, 0)
        assert seen[0].blocks == {"revoked": 2}
        assert not seen[0].invisible_whole


class TestCounterMatchesTheRowGate:
    """Счётчик — сумма построчного гейта, а не второй гейт рядом с ним."""

    def test_every_reason_is_counted_exactly_as_sale_block_answers(self, salon: Tenant) -> None:
        rows = [
            _master(salon, "Продаётся"),
            _solo_master(salon, "Без ключа"),
            _master(salon, "Отозвана", archived_at=timezone.now()),
            _master(salon, "Приглашена", invite_status=CatalogMaster.InviteStatus.PENDING),
        ]

        seen = tenant_visibilities()

        by_row: dict[str, int] = {}
        for row in rows:
            block = sale_block(row)
            if block is not None:
                by_row[block] = by_row.get(block, 0) + 1

        assert [v.slug for v in seen] == ["formula-tela"]
        assert seen[0].blocks == by_row
        assert seen[0].available == sum(1 for row in rows if sale_block(row) is None)
        assert seen[0].mirrored == len(rows)

    def test_deepest_gate_branch_gets_every_column_it_asks_for(self, salon: Tenant) -> None:
        """Забытый столбец в ``.values()`` — ``KeyError``, а не тихий отказ.

        ``sale_block`` читает строку строго, и это замысел: умолчание
        читалось бы как «условие не применилось». Проверяется самая
        глубокая ветка (``profile_incomplete``, DRF-1521) — та, что
        спрашивает вдобавок ``linked_bot_user_id``, ``accepted_at`` и
        ``name``. Если гейт заведёт новый столбец и список в
        ``visibility._GATE_COLUMNS`` отстанет, здесь станет красно, а не
        тихо.
        """

        bot_user = BotUser.all_tenants.create(
            tenant=salon,
            channel="max",
            channel_user_id="max-1",
            display_name="Анна",
            chat_id="max-1",
        )
        _master(
            salon,
            "",
            is_active=False,
            linked_bot_user=bot_user,
            accepted_at=timezone.now(),
        )

        seen = tenant_visibilities()

        assert [v.slug for v in seen] == ["formula-tela"]
        assert seen[0].blocks == {"profile_incomplete": 1}


class TestScope:
    """Кого счётчик считает, а кого не считает намеренно."""

    def test_global_bot_is_excluded(self, salon: Tenant) -> None:
        """У ``global_bot`` нет каталога салона — его вечный ноль не сигнал.

        Тот же довод, по которому его исключает
        ``apps.catalog.staleness.sync_ages``.
        """

        Tenant.objects.create(slug=GLOBAL_BOT_TENANT_SLUG, name="Global")
        _master(salon, "Анна")

        seen = tenant_visibilities()

        assert [v.slug for v in seen] == ["formula-tela"]

    def test_tenants_do_not_borrow_each_others_masters(self, salon: Tenant) -> None:
        """Чтение скоплено по арендатору — сумма одного не течёт в другого."""

        other = Tenant.objects.create(slug="sorok-okon", name="Сорок окон")
        _master(salon, "Анна")
        _solo_master(other, "Борис")

        seen = {v.slug: v for v in tenant_visibilities()}

        assert sorted(seen) == ["formula-tela", "sorok-okon"]
        assert (seen["formula-tela"].admitted, seen["formula-tela"].available) == (1, 1)
        assert (seen["sorok-okon"].admitted, seen["sorok-okon"].available) == (1, 0)
        assert [v.slug for v in invisible_tenants()] == ["sorok-okon"]
