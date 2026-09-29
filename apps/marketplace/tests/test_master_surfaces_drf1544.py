"""Клиентские поверхности спрашивают гейт продажи, а не свою копию (DRF-1544).

Решение владельца 06.09.2026 (реестр открытых решений, §32, пункт 1;
живёт вне этого репозитория): мастер без
канонической связи с Ayla не становится видимым и доступным для записи.
DRF-1540 (#1413) исполнила его в одном месте — ``AVAILABLE`` из
``apps.catalog.master_state``. Два читателя этого модуля гейт не
спрашивали: они набирали ``is_active`` + ``invite_status`` руками и
потому не знали ни про ``archived_at``, ни про ``ayla_user_id``.

Оба места клиентские, и то, что они показывают, разное:

* ``_bookable_qs`` — подбор мастеров клиенту между салонами, самая
  клиентская поверхность контура;
* ``_known_cities`` — список городов, где есть кого предложить.

Второй проверяется отдельно и считается **мастерами, а не городами**.
Выдача там — список городов, и город, у которого остался один
непродаваемый мастер, уходит целиком: посчитав города, увидишь «было 2,
стало 1» и не узнаешь, сколько человек за этим стоит. Считать надо тех,
за кого город отвечает.

Замер ниже (``TestNoNarrowingOnThePilotShape``) — не иллюстрация, а
условие выкладки: на пилоте 06.09.2026 бронируемых 31 и ни одного без
``ayla_user_id``, значит перевод обязан быть тождественным. Уход хотя бы
одного человека с витрины накануне подключения людей — это решение
владельца, а не побочный эффект уборки.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from apps.catalog.models import CatalogMaster
from apps.marketplace.discovery import _known_cities, discover_masters
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _ts() -> datetime:
    return datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


#: Ручной набор, который эти два места держали до DRF-1544.
#:
#: Живёт в тесте, а не в коде: замер «до и после» должен сравнивать две
#: РАЗНЫЕ выборки на одних данных, и держать прежний вариант рядом —
#: единственный способ показать, что новая ничего не потеряла. Четвёртой
#: копией предиката он не становится: продакшен-путь его не читает.
_HAND_ROLLED = {
    "is_active": True,
    "invite_status": CatalogMaster.InviteStatus.ACCEPTED,
}


def _master(
    tenant: Tenant,
    name: str,
    *,
    linked: bool = True,
    archived: bool = False,
    is_active: bool = True,
    invite_status: str = CatalogMaster.InviteStatus.ACCEPTED,
) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        name=name,
        specialization="",
        is_active=is_active,
        invite_status=invite_status,
        archived_at=_ts() if archived else None,
        ayla_user_id=uuid4() if linked else None,
    )


@pytest.fixture
def penza() -> Tenant:
    return Tenant.objects.create(slug="salon-penza", name="Salon Penza", city="Пенза")


@pytest.fixture
def moscow() -> Tenant:
    return Tenant.objects.create(slug="salon-msk", name="Salon Moscow", city="Москва")


class TestPickingAMasterForTheClient:
    """``_bookable_qs`` — подбор мастеров клиенту между салонами."""

    def test_the_ordinary_bookable_master_is_still_offered(self, penza: Tenant) -> None:
        """Положительная стража: перевод на гейт никого не убрал.

        Стоит первой намеренно. Три отрицания ниже проверяют, что кого-то
        НЕ видно, и на пустой выдаче они были бы верны все три.
        """
        _master(penza, "Сазонова Инна")

        names = [card.name for card in discover_masters()]

        assert names == ["Сазонова Инна"]

    def test_a_master_without_an_ayla_link_is_not_offered(self, penza: Tenant) -> None:
        """§32 пункт 1: до неё не дошло бы уведомление о записи.

        Мост ``master_user_id`` идёт через ``ayla_user_id``; строка без
        ключа продавалась бы, а человек о записи не узнавал. Владелец
        выбрал видимый отказ вместо молчаливого.
        """
        _master(penza, "Сазонова Инна")
        _master(penza, "Без связи с Ayla", linked=False)

        names = [card.name for card in discover_masters()]

        assert "Сазонова Инна" in names  # присутствие: выдача не пуста
        assert "Без связи с Ayla" not in names

    def test_an_archived_master_is_not_offered(self, penza: Tenant) -> None:
        """Латентный пропуск ручного набора: ``archived_at`` он не спрашивал.

        Сегодня не течёт — единственный писатель
        (``apps/admin_api/services/master_deactivation.py``) ставит
        ``archived_at`` вместе с ``is_active=False``, и фильтр активности
        отсекал архивных попутно. Тест держит границу на случай пути,
        который проставит столбец отдельно.
        """
        _master(penza, "Сазонова Инна")
        _master(penza, "В архиве", archived=True)

        names = [card.name for card in discover_masters()]

        assert "Сазонова Инна" in names  # присутствие: выдача не пуста
        assert "В архиве" not in names

    def test_a_pending_invite_is_still_not_offered(self, penza: Tenant) -> None:
        """Прежнее поведение не потеряно: непринятое приглашение не продаётся."""
        _master(penza, "Сазонова Инна")
        _master(penza, "Не ответила", invite_status=CatalogMaster.InviteStatus.PENDING)

        names = [card.name for card in discover_masters()]

        assert "Сазонова Инна" in names  # присутствие: выдача не пуста
        assert "Не ответила" not in names


class TestTheCitiesWeCanServe:
    """``_known_cities`` — считаем МАСТЕРОВ, а не строки выдачи."""

    def _masters_behind(self, cities: list[str]) -> set[str]:
        """Кто стоит за списком городов — то, что здесь и надо мерить.

        Город — это не единица ответа: за одним стоит сколько угодно
        людей, и «городов было 1, стало 1» не сообщает ничего о том, что
        произошло с людьми.
        """
        return set(
            CatalogMaster.all_tenants.filter(tenant__city__in=cities).values_list("name", flat=True)
        )

    def test_a_city_with_a_bookable_master_is_recognised(self, penza: Tenant) -> None:
        """Положительная стража к трём отрицаниям ниже."""
        _master(penza, "Сазонова Инна")

        assert _known_cities() == ["Пенза"]

    def test_a_city_whose_only_master_is_unlinked_is_not_recognised(
        self, penza: Tenant, moscow: Tenant
    ) -> None:
        """Город, где предложить некого, перестаёт быть узнаваемым.

        Иначе «Москва» уводит запрос в город, который отвечает пустотой,
        и человек читает это как «мастеров нет вообще».
        """
        _master(penza, "Сазонова Инна")
        _master(moscow, "Без связи с Ayla", linked=False)

        cities = _known_cities()

        assert "Пенза" in cities  # присутствие: список не пуст
        assert "Москва" not in cities
        assert self._masters_behind(cities) == {"Сазонова Инна"}

    def test_a_city_whose_only_master_is_archived_is_not_recognised(
        self, penza: Tenant, moscow: Tenant
    ) -> None:
        _master(penza, "Сазонова Инна")
        _master(moscow, "В архиве", archived=True)

        cities = _known_cities()

        assert "Пенза" in cities  # присутствие: список не пуст
        assert "Москва" not in cities
        assert self._masters_behind(cities) == {"Сазонова Инна"}

    def test_an_unlinked_master_does_not_close_a_city_that_still_has_someone(
        self, penza: Tenant, moscow: Tenant
    ) -> None:
        """Обратная сторона и причина считать людей, а не города.

        Узкий фильтр не имеет права уронить город, в котором есть кого
        предложить: одна непроданная строка рядом с проданной — это минус
        один человек, а не минус город.
        """
        _master(penza, "Сазонова Инна")
        _master(moscow, "Архипкин Денис")
        _master(moscow, "Без связи с Ayla", linked=False)

        cities = _known_cities()

        assert set(cities) == {"Пенза", "Москва"}
        assert self._masters_behind(cities) == {
            "Сазонова Инна",
            "Архипкин Денис",
            "Без связи с Ayla",
        }
        assert {card.name for card in discover_masters()} == {
            "Сазонова Инна",
            "Архипкин Денис",
        }


class TestNoNarrowingOnThePilotShape:
    """Замер: на форме данных пилота перевод обязан быть тождественным.

    Боевой контур 06.09.2026 — 31 бронируемый мастер, ни одного без
    ``ayla_user_id``, архивных среди бронируемых нет по построению. Это
    и воспроизводится: если бы новый предикат снял здесь хоть одного, к
    подключению людей вышла бы витрина короче прежней.
    """

    PILOT_BOOKABLE = 31

    @pytest.fixture
    def pilot(self, penza: Tenant) -> Tenant:
        for index in range(self.PILOT_BOOKABLE):
            _master(penza, f"Мастер {index:02d}")
        return penza

    def test_the_client_facing_pick_loses_nobody(self, pilot: Tenant) -> None:
        before = set(
            CatalogMaster.all_tenants.filter(**_HAND_ROLLED).values_list("name", flat=True)
        )
        after = {card.name for card in discover_masters(limit=self.PILOT_BOOKABLE)}

        assert len(before) == self.PILOT_BOOKABLE
        assert after == before

    def test_the_cities_stand_for_the_same_people(self, pilot: Tenant) -> None:
        before_cities = set(
            CatalogMaster.all_tenants.filter(**_HAND_ROLLED)
            .values_list("tenant__city", flat=True)
            .distinct()
        )
        after_cities = set(_known_cities())

        assert before_cities == {"Пенза"}
        assert after_cities == before_cities

        # И то же самое людьми — единственный замер, который тут что-то
        # значит: город один и до, и после, а человек за ним может уйти.
        behind_before = set(
            CatalogMaster.all_tenants.filter(
                tenant__city__in=before_cities, **_HAND_ROLLED
            ).values_list("name", flat=True)
        )
        behind_after = {card.name for card in discover_masters(limit=self.PILOT_BOOKABLE)}

        assert len(behind_before) == self.PILOT_BOOKABLE
        assert behind_after == behind_before
