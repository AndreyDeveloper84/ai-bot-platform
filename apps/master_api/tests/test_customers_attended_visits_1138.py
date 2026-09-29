"""DRF-1138 + DRF-2462 — «клиент приходил» читается из зеркала и гейтится по тому, кто закрыл.

**DRF-1138 (источник).** Список «Клиенты» читал ``BookingRequest`` по
``master_id``. На пилоте ``master_id`` пуст у всех строк — живые визиты лежат в
зеркале ``RemoteBookingProxy`` под ``specialist_id``. Итог: пустой список при
HTTP 200, неотличимый от «клиентов нет».

**DRF-2462 (гейт).** Визитом считался любой проставленный ``completed_at`` —
в том числе автозакрытие по часам на визитах, которые канон отменил. Решение
владельца 30.08: «гейтим последствия по completed_by». Чип «постоянный клиент»
значит «приходил больше одного раза» (DRF-1146), а часы о приходе не знают.

Одно правило — :func:`visit_source.attended_visits` — для списка и для чипа
дня: два правила для одного вопроса уже расходились.

Каждый узел — пара, которая обязана различаться; положительная сторона впереди.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from django.utils import timezone as dj_timezone

from apps.booking.models import BookingRequest, RemoteBookingProxy
from apps.catalog.models import CatalogMaster, CatalogService
from apps.identity.models import BotUser
from apps.master_api.services import customers as cs
from apps.master_api.services import dashboard as ds
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _client(tenant: Tenant, name: str) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"c-{uuid.uuid4().hex[:10]}",
        client_name=name,
    )


def _visit(
    tenant: Tenant,
    specialist_id: uuid.UUID,
    customer: BotUser,
    *,
    days_ago: int,
    status: str = "completed",
    completed_by: str = "master",
    service: str = "Массаж",
) -> RemoteBookingProxy:
    sid = uuid.uuid4()
    CatalogService.all_tenants.create(
        tenant=tenant,
        external_updated_at=dj_timezone.now(),
        name=service,
        slug=f"svc-{sid.hex[:8]}",
        duration_min=60,
        is_active=True,
        ayla_service_id=sid,
    )
    start = NOW - timedelta(days=days_ago)
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=uuid.uuid4(),
        tenant=tenant,
        bot_user=customer,
        start_at=start,
        end_at=start + timedelta(hours=1),
        status=status,
        completed_by=completed_by,
        service_id=sid,
        specialist_id=specialist_id,
    )


def _roster(master: CatalogMaster) -> dict[str, dict]:
    return {row["first_name"]: row for row in cs.list_master_customers(master=master, now=NOW)}


class TestTheRosterReadsTheMirror:
    """DRF-1138: источник — зеркало; ``BookingRequest`` больше не читается."""

    def test_pilot_shape_mirror_visit_is_listed_local_row_is_not(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        anna = _client(tenant, "Анна")
        olga = _client(tenant, "Ольга")
        # Анна — как на пилоте: визит в зеркале, закрыт человеком.
        _visit(tenant, accepted_master.id, anna, days_ago=5, service="Лимфодренаж")
        # Ольга — только старая локальная строка, даже с мастером и штампом.
        # Прежний источник засчитал бы её; зеркало о ней не знает.
        BookingRequest.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            bot_user=olga,
            service_name="Маникюр",
            client_name="Ольга",
            client_phone="+79000000000",
            visit_at=NOW - timedelta(days=4),
            duration_min=60,
            status=BookingRequest.Status.CONFIRMED,
            completed_at=NOW - timedelta(days=4),
        )

        roster = _roster(accepted_master)

        assert set(roster) == {"Анна"}
        assert roster["Анна"]["total_visits"] == 1
        # Имя услуги — из каталога по ``service_id`` зеркала.
        assert roster["Анна"]["last_visit_service_name"] == "Лимфодренаж"

    def test_solo_master_visit_under_the_catalog_id_is_listed(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        """Соло-мастер: зеркало знает его под каталожным id (DRF-1933)."""
        accepted_master.catalog_specialist_id = uuid.uuid4()
        accepted_master.save(update_fields=["catalog_specialist_id"])
        anna = _client(tenant, "Анна")
        _visit(tenant, accepted_master.catalog_specialist_id, anna, days_ago=3)

        assert set(_roster(accepted_master)) == {"Анна"}

    def test_another_masters_visit_is_not_mine(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        anna = _client(tenant, "Анна")
        vera = _client(tenant, "Вера")
        _visit(tenant, accepted_master.id, anna, days_ago=3)
        _visit(tenant, uuid.uuid4(), vera, days_ago=3)

        assert set(_roster(accepted_master)) == {"Анна"}


class TestAVisitIsWhatAHumanClosed:
    """DRF-2462: автозакрытие по часам — не визит."""

    def test_human_closed_counts_clock_closed_does_not(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        anna = _client(tenant, "Анна")
        _visit(tenant, accepted_master.id, anna, days_ago=20, completed_by="master:42")
        _visit(tenant, accepted_master.id, anna, days_ago=10, completed_by="system")
        _visit(tenant, accepted_master.id, anna, days_ago=5, completed_by="admin")

        row = _roster(accepted_master)["Анна"]

        assert row["total_visits"] == 2
        # Последний визит — последний ЗАСЧИТАННЫЙ, а не тот, что закрыли часы.
        assert row["last_visit_at"] == (NOW - timedelta(days=5)).isoformat()

    # "system\n" и "\tsystem" — SQL TRIM их не снимает; решает confirmed_by_human.
    @pytest.mark.parametrize(
        "actor", ["system", " System ", "", "auto_close", "cron", "system\n", "\tsystem"]
    )
    def test_every_machine_actor_is_refused_as_in_confirmed_by_human(
        self, tenant: Tenant, accepted_master: CatalogMaster, actor: str
    ) -> None:
        anna = _client(tenant, "Анна")
        vera = _client(tenant, "Вера")
        _visit(tenant, accepted_master.id, anna, days_ago=5, completed_by="master")
        _visit(tenant, accepted_master.id, vera, days_ago=5, completed_by=actor)

        assert set(_roster(accepted_master)) == {"Анна"}

    def test_a_clock_closed_customer_is_not_at_risk_nor_returning(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        """Три визита по часам 70–90 дней назад: прежний счёт дал бы
        «постоянная» и «давно не была». Людьми закрытые — дают."""
        anna = _client(tenant, "Анна")
        vera = _client(tenant, "Вера")
        for days in (90, 80, 70):
            _visit(tenant, accepted_master.id, anna, days_ago=days, completed_by="master")
            _visit(tenant, accepted_master.id, vera, days_ago=days, completed_by="system")

        roster = _roster(accepted_master)

        # Состав целиком: Анна есть, Веры (закрыта часами) нет — одним
        # равенством, а не голым «нет», которое прошло бы на пустом списке.
        assert set(roster) == {"Анна"}
        assert roster["Анна"]["is_returning"] is True
        assert roster["Анна"]["at_risk"] is True

    def test_cancelled_by_canon_is_not_a_visit_even_if_closed_by_someone(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        anna = _client(tenant, "Анна")
        vera = _client(tenant, "Вера")
        _visit(tenant, accepted_master.id, anna, days_ago=5)
        _visit(tenant, accepted_master.id, vera, days_ago=5, status="cancelled")

        assert set(_roster(accepted_master)) == {"Анна"}


def _booking(tenant: Tenant, master: CatalogMaster, customer: BotUser, start: datetime) -> None:
    RemoteBookingProxy.all_tenants.create(
        appointment_id=uuid.uuid4(),
        tenant=tenant,
        bot_user=customer,
        start_at=start,
        end_at=start + timedelta(hours=1),
        status="confirmed",
        specialist_id=master.id,
    )


class TestTheNextVisitIsTodayInTheSalonZone:
    """Правило продукта, из-за которого узел ниже краснел вечерами: ближайший
    визит — «в пределах сегодняшнего дня в зоне салона» (``get_next_visit``).
    Пара, чтобы следующий не «исправил» границу: визит до полуночи салона
    находится, визит за полуночью салона — нет, и это верное поведение."""

    def test_before_the_salons_midnight_it_is_found_after_it_it_is_not(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        assert str(ds.salon_zone(accepted_master.tenant)) == "Europe/Moscow"
        anna = _client(tenant, "Анна")
        evening = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)  # 21:00 МСК
        _booking(tenant, accepted_master, anna, evening + timedelta(minutes=30))  # 21:30 МСК
        found = ds.get_next_visit(accepted_master, evening)

        late = datetime(2026, 9, 29, 20, 0, tzinfo=timezone.utc)  # 23:00 МСК
        _booking(tenant, accepted_master, anna, late + timedelta(hours=2))  # 01:00 МСК завтра
        tomorrow = ds.get_next_visit(accepted_master, late)

        assert found is not None
        assert tomorrow is None


class TestTheDayChipUsesTheSameRule:
    """Чип «постоянный клиент» на ближайшем визите — то же правило, что список."""

    def _upcoming(self, tenant: Tenant, master: CatalogMaster, customer: BotUser):
        # Часы файла — ``NOW`` (15:00 МСК), как у всех визитов выше. Раньше
        # здесь стояли настоящие часы: ``get_next_visit`` ищет «сегодня в зоне
        # салона», и визит ``now + 2h`` уезжал за полночь МСК каждый вечер с
        # 19:00 до 21:00 UTC — узел краснел два часа в сутки (dev 29.09).
        start = NOW + timedelta(hours=2)
        _booking(tenant, master, customer, start)
        return ds.get_next_visit(master, NOW)

    def test_two_human_closed_visits_light_the_chip(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        anna = _client(tenant, "Анна")
        for days in (20, 10):
            _visit(tenant, accepted_master.id, anna, days_ago=days, completed_by="master")

        nv = self._upcoming(tenant, accepted_master, anna)

        assert nv is not None and nv.is_returning_customer is True

    def test_two_clock_closed_visits_do_not(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        vera = _client(tenant, "Вера")
        for days in (20, 10):
            _visit(tenant, accepted_master.id, vera, days_ago=days, completed_by="system")

        nv = self._upcoming(tenant, accepted_master, vera)

        assert nv is not None and nv.is_returning_customer is False
