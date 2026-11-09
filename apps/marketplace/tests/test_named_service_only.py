"""Названа услуга каталога — показаны те, кто её оказывает, без «похожих».

Пилот, 09.11.2026. Бот спросил «Что именно хочешь выбрать?» и предложил чипы;
человек нажал «Биоэнергетический массаж». Услугу в контуре оказывают трое
мастеров одного салона. Поиск по её названию вернул этих троих — и следом
массажистов других салонов, у которых общего с запросом одно слово «массаж».

Хвост — не ошибка ранжирования, а его устройство: основы запроса соединены
через ИЛИ и упорядочены по точности (DRF-1283, DRF-1530), чтобы «массаж пенза»
не находил никого. Для ОПИСАНИЯ потребности это правильный ответ. Для НАЗВАНИЯ
услуги — неправильный, показанный с той же уверенностью, что и правильный.

Поэтому срез включается флагом ``named_service_only`` и только там, где
запрос — название услуги. Без флага поиск ведёт себя как раньше; это
закреплено здесь же, первым тестом, на тех же данных.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from django.conf import settings as django_settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.marketplace.discovery import (
    discover_masters,
    discover_masters_window,
    name_stems,
    stems_used_in_service_names,
)
from apps.tenancy.models import Tenant

# Только Postgres, по той же причине, что и у соседних модулей: совпадение —
# это ILIKE, а складывать регистр кириллицы умеет база. На SQLite второе слово
# названия не совпало бы вовсе, и проверки сравнивали бы пустые списки.
pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        "postgresql" not in str(django_settings.DATABASES["default"]["ENGINE"]),
        reason="Cyrillic ILIKE folding requires Postgres.",
    ),
]

NAMED = "Биоэнергетический массаж"


def _ts() -> datetime:
    return datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def _offers(tenant: Tenant, master_name: str, *service_names: str) -> CatalogMaster:
    """Продаваемый мастер, который оказывает ровно ``service_names``."""
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        name=master_name,
        specialization="",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ayla_user_id=uuid4(),
    )
    for index, service_name in enumerate(service_names):
        service = CatalogService.all_tenants.create(
            tenant=tenant,
            slug=f"{master_name}-{index}".lower().replace(" ", "-"),
            name=service_name,
            is_active=True,
            ayla_service_id=uuid4(),
            external_updated_at=_ts(),
        )
        MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    return master


#: Кто оказывает названную услугу — и кто только делит с ней слово.
PERFORMS = {"Био один", "Био и классика", "Био лица"}
SHARES_A_WORD = {"Только классика", "Только спорт", "Два чужих слова"}


@pytest.fixture
def contour() -> Tenant:
    """Контур 09.11 в миниатюре.

    «Био и классика» держит две услуги: мастер, который оказывает названную
    услугу НАРЯДУ с другой, обязан остаться. «Био лица» называет услугу
    длиннее запроса: он тоже её оказывает, и срез по точному равенству
    названий его бы потерял. «Два чужих слова» собирает оба слова запроса из
    ДВУХ разных услуг — это не «Биоэнергетический массаж», и остаться он не
    должен.
    """
    tenant = Tenant.objects.create(slug="formula-tela-0911", name="Формула тела", city="Пенза")
    other = Tenant.objects.create(slug="other-salon-0911", name="Другой салон", city="Пенза")
    _offers(tenant, "Био один", NAMED)
    _offers(tenant, "Био и классика", NAMED, "Классический массаж")
    _offers(tenant, "Био лица", "Биоэнергетический массаж лица")
    _offers(other, "Только классика", "Классический массаж")
    _offers(other, "Только спорт", "Спортивный массаж")
    _offers(other, "Два чужих слова", "Биоэнергетический пилинг", "Тайский массаж")
    return tenant


def _names(cards: list) -> set[str]:
    return {card.name for card in cards}


class TestWithoutTheFlagNothingChanges:
    def test_the_ranked_tail_is_still_there(self, contour: Tenant) -> None:
        """Поведение DRF-1283 для всех, кто флага не просил: найдены все шестеро,
        оказывающие услугу стоят выше остальных."""
        cards = discover_masters(specialization=NAMED)

        assert _names(cards) == PERFORMS | SHARES_A_WORD
        assert _names(cards[:2]) == {"Био один", "Био и классика"}


class TestANamedServiceShowsWhoPerformsIt:
    def test_only_the_masters_of_that_service_are_returned(self, contour: Tenant) -> None:
        cards = discover_masters(specialization=NAMED, named_service_only=True)

        assert _names(cards) == PERFORMS

    def test_inflection_and_filler_do_not_bring_the_tail_back(self, contour: Tenant) -> None:
        """«хочу биоэнергетического массажа» — то же название через тот же разбор."""
        cards = discover_masters(
            specialization="хочу биоэнергетического массажа", named_service_only=True
        )

        assert _names(cards) == PERFORMS

    def test_the_count_and_the_pages_are_cut_from_the_same_list(self, contour: Tenant) -> None:
        """«Показать ещё» перезапускает этот же поиск со сдвигом: вторая
        страница обязана быть хвостом того же списка, а счётчик — его длиной."""
        first, total = discover_masters_window(
            specialization=NAMED, limit=2, offset=0, named_service_only=True
        )
        second, total_again = discover_masters_window(
            specialization=NAMED, limit=2, offset=2, named_service_only=True
        )

        assert total == total_again == len(PERFORMS)
        assert len(first) == 2
        assert _names(first) | _names(second) == PERFORMS
        assert _names(first).isdisjoint(_names(second))

    def test_the_card_carries_the_named_service(self, contour: Tenant, settings) -> None:
        """Кнопка «Записаться» ведёт в названную услугу, а не в соседнюю услугу
        того же мастера."""
        settings.BOOKING_VIA_AYLA_REST = True
        cards = discover_masters(
            specialization=NAMED, named_service_only=True, resolve_service=True
        )

        by_name = {card.name: card.service_name for card in cards}
        assert by_name["Био один"] == NAMED
        assert by_name["Био и классика"] == NAMED


class TestANameWithGlueWordsIsStillAName:
    """Название со связкой: «и», «на», тире запрос отбрасывает.

    Точность такого названия, набранного целиком, меньше единицы («Массаж лица
    и шеи» — три слова запроса на четыре слова названия), и срез по точности
    для него не сработал бы. Сравнение идёт по словам, как у стража DRF-968.
    """

    @pytest.mark.parametrize(
        "name",
        ["Массаж лица и шеи", "Массаж на дому", "Массаж спины — глубокая проработка"],
        ids=["conjunction", "preposition", "dash"],
    )
    def test_the_tail_is_cut_for_it_too(self, contour: Tenant, name: str) -> None:
        _offers(contour, "Тот самый", name)

        uncut = discover_masters(specialization=name)
        cut = discover_masters(specialization=name, named_service_only=True)

        assert "Только классика" in _names(uncut)
        assert _names(cut) == {"Тот самый"}

    def test_name_stems_reduce_a_name_the_way_a_query_is_reduced(self) -> None:
        assert name_stems("Массаж лица и шеи") == ["массаж", "лица", "шеи"]
        assert name_stems("Массаж на дому") == ["массаж", "дому"]
        assert name_stems("Лицо/шея/декольте") == ["лицо", "шея", "деколь"]


class TestWhatTheFlagMustNotCut:
    def test_a_one_word_request_keeps_everyone(self, contour: Tenant) -> None:
        """«массаж» — не название, а род услуг. Даже когда один салон назвал
        свою услугу ровно этим словом, остальные массажисты не исчезают."""
        _offers(contour, "Просто массаж", "Массаж")

        cards = discover_masters(specialization="массаж", named_service_only=True)

        assert _names(cards) == PERFORMS | {"Только классика", "Только спорт"} | {
            "Просто массаж",
            "Два чужих слова",
        }

    def test_a_description_that_names_no_service_keeps_its_ranked_list(
        self, contour: Tenant
    ) -> None:
        """«массаж спины» при услуге «Массаж спины и шеи»: названия, равного
        запросу, в каталоге нет, и обычная ранжированная выдача остаётся."""
        _offers(contour, "Спина и шея", "Массаж спины и шеи")

        cards = discover_masters(specialization="массаж спины", named_service_only=True)

        assert "Спина и шея" in _names(cards)
        assert "Только классика" in _names(cards)

    def test_an_enumeration_no_service_is_called_by_is_left_whole(self, contour: Tenant) -> None:
        """«маникюр, педикюр» — две услуги. «Маникюр и педикюр с покрытием»
        несёт оба слова, но называется иначе и мастера одного маникюра не
        скрывает."""
        _offers(contour, "Обе с покрытием", "Маникюр и педикюр с покрытием")
        _offers(contour, "Один маникюр", "Маникюр")

        cards = discover_masters(specialization="маникюр, педикюр", named_service_only=True)

        assert {"Обе с покрытием", "Один маникюр"} <= _names(cards)

    def test_a_request_nobody_matches_in_full_is_left_alone(self, contour: Tenant) -> None:
        """Нет услуги, равной запросу — срезать нечем, выдача прежняя."""
        cards = discover_masters(specialization="лечебный массаж", named_service_only=True)

        assert _names(cards) == PERFORMS | SHARES_A_WORD


class TestServiceNameStems:
    def test_only_stems_a_service_name_is_written_with_survive(self, contour: Tenant) -> None:
        """«пензе» и «завтра» услугу не называют; «биоэне» и «массаж» — называют."""
        assert stems_used_in_service_names(["биоэне", "пензе", "массаж", "завтра"]) == [
            "биоэне",
            "массаж",
        ]

    def test_an_empty_input_reads_nothing(self, django_assert_num_queries) -> None:
        with django_assert_num_queries(0):
            assert stems_used_in_service_names([]) == []
