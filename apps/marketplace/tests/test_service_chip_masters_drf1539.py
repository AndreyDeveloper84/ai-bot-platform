"""Тап по чипу услуги перестаёт резать мастеров в SQL по алфавиту — DRF-1539.

Тот же класс дефекта, что чинили DRF-1530 и DRF-1532, но другой читатель:
`discover_masters_for_service` резал выборку в SQL по `order_by("name", "id")`,
и после позиции пять мастера не прятались — они не запрашивались.

**Оценка на этом пути вырождена, и это измерено, а не предположено.** Все
кандидаты выполняют ровно ту услугу, по которой тапнули, поэтому точность
совпадения у них одна и та же. Значит §29 отвечает здесь второй своей
половиной: устойчивая ротация плюс «Показать ещё» до всех.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.marketplace.discovery import _rotate_ties, discover_masters_for_service
from apps.tenancy.models import Tenant

pytestmark = [pytest.mark.django_db]


def _ts() -> datetime:
    return datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def penza() -> Tenant:
    return Tenant.objects.create(slug="salon-penza", name="Salon Penza", city="Пенза")


@pytest.fixture
def popular(penza: Tenant) -> CatalogService:
    """Одна услуга и восемь мастеров на ней — форма живого дефекта."""
    service = CatalogService.all_tenants.create(
        tenant=penza,
        slug="klassicheskiy-massazh",
        name="Классический массаж",
        is_active=True,
        external_updated_at=_ts(),
    )
    for index in range(1, 9):
        master = CatalogMaster.all_tenants.create(
            tenant=penza,
            external_updated_at=_ts(),
            name=f"Мастер {index:02d}",
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        MasterService.all_tenants.create(tenant=penza, master=master, service=service)
    return service


_ALL_EIGHT = {f"Мастер {index:02d}" for index in range(1, 9)}


class TestTheSliceStopsBeingFinal:
    """Замер до и после: сколько находится, сколько показывается."""

    def test_five_shown_of_eight_found_and_the_rest_are_reachable(
        self, popular: CatalogService
    ) -> None:
        page1 = discover_masters_for_service(popular.id, limit=5, rotation_seed="conv-7")
        page2 = discover_masters_for_service(popular.id, limit=5, offset=5, rotation_seed="conv-7")
        everyone = discover_masters_for_service(popular.id, limit=200, rotation_seed="conv-7")

        first = [card.name for card in page1]
        second = [card.name for card in page2]
        print(f"\nнайдено: {len(everyone)}  показано: {len(first)}  вторая страница: {len(second)}")
        assert len(everyone) == 8
        assert len(first) == 5
        assert len(second) == 3
        # Nobody repeats between the pages…
        assert not set(first) & set(second)
        # …and nobody falls between them. Before DRF-1539 the three were
        # unreachable, and which three was decided by surname.
        assert set(first) | set(second) == _ALL_EIGHT

    def test_paging_is_stable_by_construction(self, popular: CatalogService) -> None:
        """Вторая страница — хвост ровно той первой, которую видел диалог."""
        whole = [
            c.name for c in discover_masters_for_service(popular.id, limit=200, rotation_seed="c")
        ]
        paged = [
            card.name
            for offset in (0, 5)
            for card in discover_masters_for_service(
                popular.id, limit=5, offset=offset, rotation_seed="c"
            )
        ]

        assert paged == whole

    def test_an_offset_past_the_end_is_empty_not_wrapped(self, popular: CatalogService) -> None:
        """Хвост кончается, а не заворачивается на начало."""
        # Positive first, on the same data: offset 5 DOES reach people, so the
        # emptiness below is the end of the list and not a broken read.
        assert len(discover_masters_for_service(popular.id, limit=5, offset=5)) == 3

        assert discover_masters_for_service(popular.id, limit=5, offset=99) == []

    def test_the_service_stamp_survives_paging(self, popular: CatalogService) -> None:
        """Карточка второй страницы несёт ту же услугу — кнопка не теряет контекст."""
        cards = discover_masters_for_service(popular.id, limit=5, offset=5, rotation_seed="conv-7")

        assert cards
        assert all(card.service_id == popular.id for card in cards)
        assert all(card.service_name == "Классический массаж" for card in cards)


class TestRotationOnThisPath:
    """§29.6 — внутри диалога порядок не меняется, между диалогами делится."""

    def test_two_taps_in_one_conversation_give_the_same_order(
        self, popular: CatalogService
    ) -> None:
        first = [c.name for c in discover_masters_for_service(popular.id, rotation_seed="conv-1")]
        second = [c.name for c in discover_masters_for_service(popular.id, rotation_seed="conv-1")]

        assert first == second
        assert len(first) == 8

    def test_different_conversations_share_the_first_position(
        self, popular: CatalogService
    ) -> None:
        leaders: dict[str, int] = {}
        for index in range(200):
            cards = discover_masters_for_service(popular.id, limit=1, rotation_seed=f"conv-{index}")
            leaders[cards[0].name] = leaders.get(cards[0].name, 0) + 1

        print(f"\nразных мастеров первыми за 200 разговоров: {len(leaders)}")
        print(f"распределение: {sorted(leaders.items())}")
        assert set(leaders) == _ALL_EIGHT
        assert all(8 <= count <= 60 for count in leaders.values())

    def test_without_a_seed_nothing_changes(self, popular: CatalogService) -> None:
        """Читатели без разговора (HTTP-каталог, тесты) сохраняют свой порядок."""
        names = [card.name for card in discover_masters_for_service(popular.id)]

        assert names == sorted(names)
        assert names == [c.name for c in discover_masters_for_service(popular.id)]


class TestScoreStillOutranksRotation:
    """Парная положительная стража (DRF-1411) для общей функции порядка.

    На этом пути кандидаты равны по построению, поэтому утверждение
    проверяется на самой `_rotate_ties` — той функции, которой тап теперь
    пользуется. Если бы ротация умела двигать различённых кандидатов, чинить
    надо было бы её, а не вызывающего.
    """

    def test_a_higher_score_wins_in_every_conversation(self, penza: Tenant) -> None:
        masters = []
        for index in range(8):
            master = CatalogMaster.all_tenants.create(
                tenant=penza,
                external_updated_at=_ts(),
                name=f"Мастер {index:02d}",
                is_active=True,
                invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            )
            master.match_score = 1.0 if index == 3 else 0.5
            masters.append(master)

        winners = {_rotate_ties(masters, f"conv-{i}")[0].name for i in range(50)}

        # Positive first: everybody is still in the list, all fifty times.
        assert all(len(_rotate_ties(masters, f"conv-{i}")) == 8 for i in range(50))
        assert winners == {"Мастер 03"}

    def test_ties_below_the_winner_still_rotate(self, penza: Tenant) -> None:
        masters = []
        for index in range(8):
            master = CatalogMaster.all_tenants.create(
                tenant=penza,
                external_updated_at=_ts(),
                name=f"Мастер {index:02d}",
                is_active=True,
                invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            )
            master.match_score = 1.0 if index == 3 else 0.5
            masters.append(master)

        seconds = {_rotate_ties(masters, f"conv-{i}")[1].name for i in range(80)}

        # The tie under the winner is a tie, and rotation is what orders it —
        # otherwise «Мастер 00» would be second in every conversation forever.
        assert len(seconds) > 1
