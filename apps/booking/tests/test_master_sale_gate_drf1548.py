"""Мастер без канонической связи с Ayla недоступен для записи (DRF-1548).

Решение владельца ``docs/OPEN_DECISIONS.md`` §32 пункт 1. DRF-1544
закрыла половину про «видимым» — три клиентские поверхности. Здесь
вторая половина, «доступным для записи»: две построчные перепроверки
под локом (создание брони и перенос) спрашивают гейт продажи целиком, а
не набирают его столбцы заново.

Что именно проверяется
----------------------
1. Парная положительная стража (DRF-1411) стоит ПЕРВОЙ в каждом классе:
   на сломанном пути все отрицания верны, и без неё зелень ничего не
   значит.
2. Мастер без ``ayla_user_id`` получает **отличимый** слаг — не успех и
   не общий ``master_not_bookable``.
3. Три отказа не схлопнулись: архивный по-прежнему ``master_archived``,
   непринявший приглашение — ``master_not_bookable``.
4. Контракт наружу: новый слаг присутствует в **обеих** таблицах
   статусов ``apps/miniapp_api/views.py``. Смотреть надо в таблицу, а не
   на ответ: обе разбирают слаг через ``.get(slug, ...)`` с умолчанием,
   и забытый слаг ответ не портит заметно — он молча получает
   правдоподобный статус.
5. Таблица ``SALE_BLOCK_SLUG`` полна по ``SaleBlock``: DRF-1521 добавит
   ``profile_incomplete``, и забыть про него будет нельзя.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from apps.booking.models import BookingRequest
from apps.booking.services.create import (
    BookingCreateError,
    CreateBookingInput,
    create_customer_booking,
)
from apps.booking.services.master_gate import (
    ALL_SALE_BLOCKS,
    SALE_BLOCK_SLUG,
    master_sale_refusal,
)
from apps.booking.services.transitions import (
    InvalidBookingTransition,
    commit_reschedule,
    request_reschedule,
)
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.scheduling.models import Weekday, WorkingHours
from apps.tenancy.models import Tenant


MSK = ZoneInfo("Europe/Moscow")

#: Слаг, которым отвечает новая ветка гейта. Вынесен в константу, чтобы
#: тест на контракт и тесты поведения не могли разъехаться по опечатке.
AYLA_UNLINKED_SLUG = "master_ayla_unlinked"


class _Row:
    """Минимальная строка каталога — предикат читает атрибуты."""

    def __init__(self, values: dict) -> None:
        for key, value in values.items():
            setattr(self, key, value)


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="drf1548", name="DRF-1548", timezone="Europe/Moscow")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="1548",
        chat_id="1548",
        display_name="Мария",
    )


def _master(tenant: Tenant, *, ext: int, name: str, **kw) -> CatalogMaster:
    """Мастер боевой формы: связана с Ayla, если не сказано иначе.

    ``ayla_user_id=None`` в ``kw`` — отдельный названный случай, а не
    забытое поле (тот же приём, что в #1413 и #1417).
    """

    defaults = dict(
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ayla_user_id=uuid4(),
    )
    defaults.update(kw)
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=ext,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        name=name,
        **defaults,
    )


@pytest.fixture
def linked_master(tenant: Tenant) -> CatalogMaster:
    return _master(tenant, ext=1, name="Анна")


@pytest.fixture
def service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=42,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        slug="manicure",
        name="Маникюр",
        duration_min=60,
        is_active=True,
    )


def _offer(tenant: Tenant, master: CatalogMaster, service: CatalogService) -> None:
    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    for weekday in Weekday.values:
        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=master,
            day_of_week=weekday,
            is_working=True,
            start_time=time(10, 0),
            end_time=time(20, 0),
        )


@pytest.fixture
def offered(tenant, linked_master, service) -> None:
    _offer(tenant, linked_master, service)


def _far_future_monday(hour: int = 12, *, weeks: int = 0) -> datetime:
    target = date.today() + timedelta(days=30)
    while target.weekday() != 0:
        target += timedelta(days=1)
    return datetime.combine(target + timedelta(weeks=weeks), time(hour, 0), tzinfo=MSK)


def _create(tenant, bot_user, service, master, *, hour: int = 12, weeks: int = 0):
    return create_customer_booking(
        inp=CreateBookingInput(
            tenant=tenant,
            bot_user=bot_user,
            service_id=str(service.id),
            master_id=str(master.id),
            visit_at=_far_future_monday(hour, weeks=weeks),
        )
    )


class TestCreateAsksTheSaleGate:
    """Создание брони: гейт продажи, а не своя копия столбцов."""

    def test_linked_master_is_still_bookable(
        self, tenant, bot_user, linked_master, service, offered
    ) -> None:
        """Парная положительная стража (DRF-1411) — первой в классе.

        Без неё все отрицания ниже проходили бы и на наглухо сломанном
        создании брони.
        """

        booking = _create(tenant, bot_user, service, linked_master)
        assert booking.status == BookingRequest.Status.CONFIRMED
        assert booking.master_id == linked_master.id

    def test_master_without_ayla_link_is_refused(self, tenant, bot_user, service) -> None:
        unlinked = _master(tenant, ext=2, name="Ольга", ayla_user_id=None)
        _offer(tenant, unlinked, service)
        with pytest.raises(BookingCreateError) as exc:
            _create(tenant, bot_user, service, unlinked)
        assert exc.value.slug == AYLA_UNLINKED_SLUG

    def test_the_three_refusals_stay_three(self, tenant, bot_user, service) -> None:
        """Отличимый — значит не такой же, как у двух соседей."""

        archived = _master(tenant, ext=3, name="Ирина", is_active=False)
        pending = _master(
            tenant, ext=4, name="Дарья", invite_status=CatalogMaster.InviteStatus.PENDING
        )
        unlinked = _master(tenant, ext=5, name="Ольга", ayla_user_id=None)
        for master in (archived, pending, unlinked):
            _offer(tenant, master, service)

        slugs = {}
        for key, master in (("archived", archived), ("pending", pending), ("unlinked", unlinked)):
            with pytest.raises(BookingCreateError) as exc:
                _create(tenant, bot_user, service, master)
            slugs[key] = exc.value.slug

        assert slugs["archived"] == "master_archived"
        assert slugs["pending"] == "master_not_bookable"
        assert slugs["unlinked"] == AYLA_UNLINKED_SLUG
        assert len(set(slugs.values())) == 3

    def test_archived_at_alone_is_refused(self, tenant, bot_user, service) -> None:
        """``archived_at`` тоже спрашивается — своя копия его не знала."""

        archived = _master(
            tenant,
            ext=6,
            name="Полина",
            archived_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        _offer(tenant, archived, service)
        with pytest.raises(BookingCreateError) as exc:
            _create(tenant, bot_user, service, archived)
        assert exc.value.slug == "master_archived"


class TestRescheduleAsksTheSaleGate:
    """Перенос брони на другого мастера — тот же гейт, тот же слаг."""

    def _request_move(self, booking, bot_user, service, new_master, *, hour=16, weeks=0):
        request_reschedule(
            booking,
            actor=bot_user,
            new_master_id=str(new_master.id),
            new_service_id=str(service.id),
            new_visit_at=_far_future_monday(hour, weeks=weeks),
        )
        booking.refresh_from_db()
        return booking

    def test_move_to_linked_master_still_works(
        self, tenant, bot_user, linked_master, service, offered
    ) -> None:
        """Парная положительная стража (DRF-1411) — первой в классе."""

        alt = _master(tenant, ext=7, name="Мария")
        _offer(tenant, alt, service)
        booking = _create(tenant, bot_user, service, linked_master)
        booking = self._request_move(booking, bot_user, service, alt)
        old, new = commit_reschedule(booking, actor=bot_user)
        assert old.status == BookingRequest.Status.RESCHEDULED
        assert new.status == BookingRequest.Status.CONFIRMED
        assert new.master_id == alt.id

    def test_move_to_master_without_ayla_link_is_refused(
        self, tenant, bot_user, linked_master, service, offered
    ) -> None:
        unlinked = _master(tenant, ext=8, name="Ольга", ayla_user_id=None)
        _offer(tenant, unlinked, service)
        booking = _create(tenant, bot_user, service, linked_master)
        booking = self._request_move(booking, bot_user, service, unlinked)
        with pytest.raises(InvalidBookingTransition) as exc:
            commit_reschedule(booking, actor=bot_user)
        assert exc.value.slug == AYLA_UNLINKED_SLUG

    def test_the_three_refusals_stay_three(
        self, tenant, bot_user, linked_master, service, offered
    ) -> None:
        archived = _master(tenant, ext=9, name="Ирина", is_active=False)
        pending = _master(
            tenant, ext=10, name="Дарья", invite_status=CatalogMaster.InviteStatus.PENDING
        )
        unlinked = _master(tenant, ext=11, name="Ольга", ayla_user_id=None)
        for master in (archived, pending, unlinked):
            _offer(tenant, master, service)

        slugs = {}
        cases = (("archived", archived), ("pending", pending), ("unlinked", unlinked))
        for week, (key, master) in enumerate(cases):
            # Каждый случай в свою неделю: слот у связанного мастера один
            # и тот же, а предыдущая бронь его занимает.
            booking = _create(tenant, bot_user, service, linked_master, weeks=week)
            booking = self._request_move(booking, bot_user, service, master, weeks=week)
            with pytest.raises(InvalidBookingTransition) as exc:
                commit_reschedule(booking, actor=bot_user)
            slugs[key] = exc.value.slug

        assert slugs["archived"] == "master_archived"
        assert slugs["pending"] == "master_not_bookable"
        assert slugs["unlinked"] == AYLA_UNLINKED_SLUG
        assert len(set(slugs.values())) == 3


class TestTheSlugIsInBothStatusTables:
    """Контракт наружу. Смотрим в таблицу, а не на ответ.

    ``_ERROR_SLUG_TO_STATUS.get(slug, 400)`` и
    ``_TRANSITION_SLUG_TO_STATUS.get(slug, 409)`` не падают на неизвестном
    слаге и не логируют его. Проверка по ответу увидела бы правдоподобный
    статус и промолчала — поэтому смотрим прямо в словарь.
    """

    def test_every_sale_block_has_a_slug(self) -> None:
        """Полнота таблицы перевода. DRF-1521 добавит свою причину."""

        assert set(SALE_BLOCK_SLUG) == set(ALL_SALE_BLOCKS)
        assert len(set(SALE_BLOCK_SLUG.values())) == len(SALE_BLOCK_SLUG)
        assert SALE_BLOCK_SLUG["ayla_unlinked"] == AYLA_UNLINKED_SLUG

    def test_every_sale_block_slug_is_mapped_on_create(self) -> None:
        from apps.miniapp_api.views import _ERROR_SLUG_TO_STATUS

        for slug in SALE_BLOCK_SLUG.values():
            assert slug in _ERROR_SLUG_TO_STATUS, slug
        assert _ERROR_SLUG_TO_STATUS[AYLA_UNLINKED_SLUG] == 404

    def test_every_sale_block_slug_is_mapped_on_transition(self) -> None:
        from apps.miniapp_api.views import _TRANSITION_SLUG_TO_STATUS

        for slug in SALE_BLOCK_SLUG.values():
            assert slug in _TRANSITION_SLUG_TO_STATUS, slug
        assert _TRANSITION_SLUG_TO_STATUS[AYLA_UNLINKED_SLUG] == 409

    def test_the_helper_answers_with_a_reason_not_a_boolean(self) -> None:
        """``master_sale_refusal``: пара «слаг + detail», либо ``None``.

        Присутствие и отсутствие проверяются на одном корне и на одних
        данных — строка отличается ровно одним столбцом.
        """

        linked = {
            "is_active": True,
            "archived_at": None,
            "invite_status": "accepted",
            "ayla_user_id": uuid4(),
        }
        unlinked = {**linked, "ayla_user_id": None}

        refusal_unlinked = master_sale_refusal(_Row(unlinked))
        refusal_linked = master_sale_refusal(_Row(linked))

        assert refusal_unlinked is not None
        slug, detail = refusal_unlinked
        assert slug == AYLA_UNLINKED_SLUG
        assert detail
        assert refusal_linked is None


class TestTheMeasurementOnPilotShapedData:
    """Замер сужения: сколько записываемых мастеров теряется переводом.

    Форма пилотного контура на 06.09.2026 — 31 бронируемый мастер, ни
    одного без ``ayla_user_id`` (замер DRF-1544, воспроизведён здесь на
    той же форме). Ожидание владельца: **ноль** отказов, которых не было
    бы до перевода. Не ноль — задача останавливается и идёт к владельцу,
    а не «чинится» ослаблением гейта.

    Мерим не через HTTP и не через создание 31 брони: вопрос ровно в
    том, кого пропускает старое условие и кого новое, поэтому оба
    предиката выписаны здесь явно и сравниваются на одних строках.
    """

    #: Пилотный контур 06.09.2026.
    PILOT_BOOKABLE_MASTERS = 31

    def _pilot_rows(self) -> list[_Row]:
        return [
            _Row(
                {
                    "is_active": True,
                    "archived_at": None,
                    "invite_status": "accepted",
                    "ayla_user_id": uuid4(),
                }
            )
            for _ in range(self.PILOT_BOOKABLE_MASTERS)
        ]

    def test_the_gate_refuses_nobody_on_the_pilot_shape(self) -> None:
        rows = self._pilot_rows()

        # Условие, которое обе точки набирали руками до DRF-1548.
        before = [r for r in rows if r.is_active and r.invite_status == "accepted"]
        # Условие после перевода — гейт продажи целиком.
        after = [r for r in rows if master_sale_refusal(r) is None]

        assert len(before) == self.PILOT_BOOKABLE_MASTERS
        assert len(after) == self.PILOT_BOOKABLE_MASTERS
        assert len(before) == len(after)

    def test_the_measurement_would_notice_a_single_unlinked_row(self) -> None:
        """Стража самого замера: он обязан уметь показать ненулевое сужение."""

        rows = self._pilot_rows()
        rows[0].ayla_user_id = None

        before = [r for r in rows if r.is_active and r.invite_status == "accepted"]
        after = [r for r in rows if master_sale_refusal(r) is None]

        assert len(before) - len(after) == 1
