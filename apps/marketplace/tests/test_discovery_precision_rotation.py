"""Точность совпадения (DRF-1530) и устойчивая ротация (DRF-1532).

Две задачи в одном модуле потому же, почему они в одном PR: обе меняют одно
место — срез выборки и порядок внутри него, — и разнесённые по разным файлам
тесты одного среза расходятся при первой же правке.

### Что здесь измеряется

`TestTierMeasurement` — воспроизведение замера из `DRF-1526`
(`docs/ANALYSIS_SERVICE_MATCHING.md` §3). Живой контур с этой машины
недоступен, поэтому каталог **реконструирован**: колонка «до» подобрана так,
чтобы совпасть с опубликованным замером пилота 06.09.2026 (8/6/5/5/3/3), а
колонка «после» считается по тем же строкам той же формулой, что стоит в
проде. Это доказательство того, что ярусы сжимаются, а не публикация чисел
живого контура — числа контура перемеряет тот, у кого есть к нему доступ.

Старый счётчик воспроизводится здесь на Python (`_legacy_score`). Он удалён
из кода, и другого способа показать «до» на тех же данных нет; формула взята
дословно из докстринга удалённого `_match_score`: сколько основ запроса
совпало с названием ОДНОЙ лучшей услуги мастера.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from django.conf import settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.marketplace.discovery import (
    _bookable_qs,
    _parse_query,
    discover_masters,
    discover_masters_window,
    name_word_count,
)
from apps.tenancy.models import Tenant

# Postgres-only for the whole module, same reason test_discovery_by_service
# gives: matching is ILIKE and Cyrillic case folding is the database's job.
# On SQLite nothing Cyrillic matches at all, so the ordering assertions would
# not fail — they would compare two empty lists.
pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        "postgresql" not in str(settings.DATABASES["default"]["ENGINE"]),
        reason="Cyrillic ILIKE folding requires Postgres; on SQLite the ordering "
        "assertions would compare empty lists.",
    ),
]


def _ts() -> datetime:
    return datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def penza() -> Tenant:
    return Tenant.objects.create(slug="salon-penza", name="Salon Penza", city="Пенза")


def _master(tenant: Tenant, name: str, *, specialization: str = "") -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        name=name,
        specialization=specialization,
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        # DRF-1540/1544 — синхронизированная строка несёт канонический ключ.
        # Без него мастер не продаётся, и пустая выдача читалась бы как
        # поломка подбора, а не как отсутствие связи с Ayla.
        ayla_user_id=uuid4(),
    )


def _offers(tenant: Tenant, master_name: str, *service_names: str) -> CatalogMaster:
    """A bookable master who performs exactly ``service_names``."""
    master = _master(tenant, master_name)
    for index, service_name in enumerate(service_names):
        service = CatalogService.all_tenants.create(
            tenant=tenant,
            slug=f"{master_name}-{index}".lower().replace(" ", "-"),
            name=service_name,
            is_active=True,
            external_updated_at=_ts(),
        )
        MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    return master


# ─── the reconstructed contour ─────────────────────────────────────────────
#
# One massage service per master, so «best row» is unambiguous and the table
# below can be read off the names. Word counts are what the second factor
# divides by: «Классический массаж» is 2, «Массаж ног — глубокое расслабление
# и лимфодренаж» is 7 (the em-dash is a word — see ``name_word_count``).

_CONTOUR: tuple[tuple[str, str], ...] = (
    ("Мастер 01", "Спортивный массаж"),
    ("Мастер 02", "Спортивный массаж глубоких тканей"),
    ("Мастер 03", "Спортивный восстановительный массаж"),
    ("Мастер 04", "Классический массаж"),
    ("Мастер 05", "Лимфодренажный массаж"),
    ("Мастер 06", "Массаж головы"),
    ("Мастер 07", "Массаж ног — глубокое расслабление и лимфодренаж"),
    ("Мастер 08", "Тайский массаж для двоих в четыре руки"),
    ("Мастер 09", "Аппаратный лимфодренаж LPG"),
    ("Мастер 10", "Классический маникюр"),
    ("Мастер 11", "Аппаратный маникюр"),
    ("Мастер 12", "Комбинированный маникюр"),
    ("Мастер 13", "Маникюр с покрытием"),
    ("Мастер 14", "Маникюр с гель-лаком и дизайном"),
    ("Мастер 15", "Маникюр экспресс без покрытия"),
    ("Мастер 16", "Женская стрижка"),
    ("Мастер 17", "Мужская стрижка"),
    ("Мастер 18", "Стрижка чёлки"),
    ("Мастер 19", "Стрижка горячими ножницами"),
    ("Мастер 20", "Детская стрижка до 10 лет"),
    ("Мастер 21", "Эпиляция воском"),
    ("Мастер 22", "Лазерная эпиляция"),
    ("Мастер 23", "Эпиляция зоны бикини"),
    ("Мастер 24", "Эпиляция ног полностью"),
    ("Мастер 25", "Шугаринг — сахарная эпиляция всего тела"),
)

_QUERIES = ("массаж", "маникюр", "стрижка", "эпиляция", "лимфодренаж", "спортивный массаж")

#: The published pilot measurement this fixture reproduces — found count and
#: top-tier size under the OLD stem counter (DRF-1526 §3).
_PILOT_BEFORE = {
    "массаж": (8, 8),
    "маникюр": (6, 6),
    "стрижка": (5, 5),
    "эпиляция": (5, 5),
    "лимфодренаж": (3, 3),
    "спортивный массаж": (8, 3),
}


@pytest.fixture
def contour(penza: Tenant) -> Tenant:
    for master_name, service_name in _CONTOUR:
        _offers(penza, master_name, service_name)
    return penza


def _legacy_score(service_names: list[str], stems: list[str]) -> int:
    """The stem counter DRF-1530 removed, on one master's rows.

    ``MAX`` over rows of «how many stems this name contains» — the removed
    ``_match_score`` verbatim, kept here so «до» is measured on the same rows
    as «после» rather than quoted from a ticket.
    """
    return max(
        (sum(1 for stem in stems if stem in name.casefold()) for name in service_names),
        default=0,
    )


def _tiers(query: str) -> tuple[int, int, int]:
    """``(found, legacy top tier, precision top tier)`` for one query."""
    rows = list(_bookable_qs(specialization=query))
    stems = _parse_query(query).stems
    # ``all_tenants``: this reads across tenants exactly as discovery does,
    # and the tenant-scoped default manager would hand back an EMPTY list
    # outside a tenant context — which would score every master 0 and make
    # the «до» column read «all tied» for every query by accident.
    names: dict[object, list[str]] = {}
    for master_id, service_name in MasterService.all_tenants.filter(
        master_id__in=[master.id for master in rows]
    ).values_list("master_id", "service__name"):
        names.setdefault(master_id, []).append(service_name or "")
    legacy = [_legacy_score(names.get(master.id, []), stems) for master in rows]
    precise = [round(float(getattr(master, "match_score", 0.0) or 0.0), 9) for master in rows]
    return (
        len(rows),
        legacy.count(max(legacy)) if legacy else 0,
        precise.count(max(precise)) if precise else 0,
    )


class TestNameWordCount:
    """The denominator of the second factor, and why 7 is 7."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Классический массаж", 2),
            ("Массаж ног — глубокое расслабление и лимфодренаж", 7),
            ("Массаж", 1),
            ("", 1),
            ("   ", 1),
        ],
    )
    def test_counts_words(self, name: str, expected: int) -> None:
        assert name_word_count(name) == expected


class TestPrecisionOrdersWithinTheTier:
    """DRF-1530 — «Классический массаж» против длинного рекламного названия."""

    def test_short_exact_name_beats_long_one(self, penza: Tenant) -> None:
        """The example the ticket is written around.

        Both masters match «массаж» and both used to score exactly 1.
        """
        _offers(penza, "Ясная", "Классический массаж")
        _offers(penza, "Абрикосова", "Массаж ног — глубокое расслабление и лимфодренаж")

        names = [card.name for card in discover_masters(specialization="массаж")]

        # Positive first: BOTH are still found. The ordering claim below is
        # about which comes first, not about dropping anyone (DRF-1411).
        assert set(names) == {"Ясная", "Абрикосова"}
        # And the alphabetically-first name is no longer the winner: before
        # this ticket «Абрикосова» led on surname alone.
        assert names[0] == "Ясная"

    def test_all_stems_covered_beats_partial(self, penza: Tenant) -> None:
        _offers(penza, "Первый", "Спортивный массаж")
        _offers(penza, "Второй", "Классический массаж")

        names = [card.name for card in discover_masters(specialization="спортивный массаж")]

        assert set(names) == {"Первый", "Второй"}
        assert names[0] == "Первый"

    def test_specialization_only_master_is_not_dropped(self, penza: Tenant) -> None:
        """Positive guard for the ``COALESCE`` below (DRF-1411).

        A master matched only through the legacy free-text ``specialization``
        has no joined service row at all.
        """
        _offers(penza, "Со связью", "Классический массаж")
        _master(penza, "Без связи", specialization="массаж на дому")

        names = [card.name for card in discover_masters(specialization="массаж")]

        # Present — the OR fallback still finds them.
        assert set(names) == {"Со связью", "Без связи"}
        # And not on top: MAX over zero rows is NULL, and Postgres sorts NULLs
        # FIRST under DESC. Without the COALESCE this assertion fails.
        assert names[0] == "Со связью"

    def test_master_with_no_service_rows_does_not_float_up(self, penza: Tenant) -> None:
        """The same COALESCE guard, stated as the ranking claim it protects."""
        _offers(penza, "Реальная услуга", "Массаж головы")
        _master(penza, "Аноним", specialization="массаж")

        cards = discover_masters(specialization="массаж")

        assert [card.name for card in cards] == ["Реальная услуга", "Аноним"]


class TestTierMeasurement:
    """Замер до и после на реконструированном каталоге — DRF-1530."""

    def test_before_column_reproduces_the_pilot_measurement(self, contour: Tenant) -> None:
        """The fixture is calibrated: «до» must equal the published numbers.

        Without this the «после» column would measure an arbitrary catalog.
        """
        measured = {query: _tiers(query)[:2] for query in _QUERIES}

        assert measured == _PILOT_BEFORE

    def test_top_tier_shrinks_on_every_query(self, contour: Tenant) -> None:
        table = {query: _tiers(query) for query in _QUERIES}
        print("\n| запрос | найдено | ярус ДО | ярус ПОСЛЕ |")
        print("| -- | -- | -- | -- |")
        for query, (found, before, after) in table.items():
            print(f"| {query} | {found} | {before} | {after} |")

        # Everyone found stays found — the factor reorders, it does not filter.
        assert all(found == _PILOT_BEFORE[query][0] for query, (found, _b, _a) in table.items())
        # And every tier is strictly smaller than it was.
        assert all(after < before for _query, (_f, before, after) in table.items())


class TestRotationOnFullEquality:
    """DRF-1532 — сид разговора, а не алфавит."""

    @staticmethod
    def _eight_tied(tenant: Tenant) -> list[str]:
        names = [f"Мастер {index:02d}" for index in range(1, 9)]
        for name in names:
            _offers(tenant, name, "Классический массаж")
        return names

    def test_same_conversation_gives_the_same_order(self, penza: Tenant) -> None:
        self._eight_tied(penza)

        first = [c.name for c in discover_masters(specialization="массаж", rotation_seed="conv-1")]
        second = [c.name for c in discover_masters(specialization="массаж", rotation_seed="conv-1")]

        assert first == second
        assert len(first) == 8

    def test_different_conversations_share_the_first_position(self, penza: Tenant) -> None:
        """Замер по ротации: сколько РАЗНЫХ мастеров бывают первыми."""
        expected = set(self._eight_tied(penza))

        leaders: dict[str, int] = {}
        for index in range(200):
            cards = discover_masters(
                specialization="массаж", limit=1, rotation_seed=f"conv-{index}"
            )
            leaders[cards[0].name] = leaders.get(cards[0].name, 0) + 1

        print(f"\nразных мастеров на первой позиции за 200 разговоров: {len(leaders)}")
        print(f"распределение: {sorted(leaders.items())}")
        # All eight reach first place — the whole point of §29.6.
        assert set(leaders) == expected
        # And roughly evenly: nobody is systematically first, nobody is
        # systematically last. 200/8 = 25 expected; the band is generous
        # because this asserts «not degenerate», not «uniform to three nines».
        assert all(8 <= count <= 60 for count in leaders.values())

    def test_without_a_seed_the_order_stays_deterministic(self, penza: Tenant) -> None:
        """``rotation_seed=None`` changes nothing — the readers without a
        conversation (the HTTP directory) keep the order they had."""
        self._eight_tied(penza)

        first = [card.name for card in discover_masters(specialization="массаж")]
        second = [card.name for card in discover_masters(specialization="массаж")]

        assert first == second
        assert first == sorted(first)

    def test_rotation_never_reorders_distinguishable_candidates(self, penza: Tenant) -> None:
        """Парная положительная стража (DRF-1411).

        Ротация — ответ на «кандидаты неразличимы», а не переранжирование.
        """
        _offers(penza, "Ясная", "Массаж")
        self._eight_tied(penza)

        winners = set()
        for index in range(50):
            cards = discover_masters(specialization="массаж", rotation_seed=f"conv-{index}")
            # Present: the tied eight are all still in the result.
            assert len(cards) == 9
            winners.add(cards[0].name)

        # The one master the precision factor CAN tell apart stays first in
        # every conversation — rotation only ever moved the tie.
        assert winners == {"Ясная"}


class TestPaginationReachesEverybody:
    """DRF-1532 — «Показать ещё» доводит до всех найденных."""

    @staticmethod
    def _eight(tenant: Tenant) -> set[str]:
        names = {f"Мастер {index:02d}" for index in range(1, 9)}
        for name in names:
            _offers(tenant, name, "Классический массаж")
        return names

    def test_two_pages_cover_all_eight_without_repeats(self, penza: Tenant) -> None:
        expected = self._eight(penza)

        page1, total = discover_masters_window(
            specialization="массаж", limit=5, offset=0, rotation_seed="conv-7"
        )
        page2, _total = discover_masters_window(
            specialization="массаж", limit=5, offset=5, rotation_seed="conv-7"
        )

        first = [card.name for card in page1]
        second = [card.name for card in page2]
        assert total == 8
        assert len(first) == 5
        assert len(second) == 3
        # Nobody repeats between the pages…
        assert not set(first) & set(second)
        # …and nobody falls between them.
        assert set(first) | set(second) == expected

    def test_the_sql_slice_no_longer_hides_anybody(self, penza: Tenant) -> None:
        """Before DRF-1532 the sixth candidate was never fetched at all."""
        expected = self._eight(penza)

        page, total = discover_masters_window(specialization="массаж", limit=5)

        assert len(page) == 5
        # The count is what makes «Показать ещё» possible: five shown, eight
        # known to exist. Without it a caller cannot tell «that is everybody»
        # from «three people are unreachable».
        assert total == len(expected) == 8
