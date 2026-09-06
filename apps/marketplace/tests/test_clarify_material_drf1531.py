"""Материал различающего вопроса — DRF-1531, каталожная половина.

Решение владельца §29.2: когда разрыв недостаточен, Ayla задаёт ОДИН
различающий вопрос **реальными названиями услуг из каталога**. Здесь
проверяется только материал: какой ярус считается неразличимым и какие имена
он даёт. Решение «спрашивать или показывать» живёт в оркестраторе и проверено
рядом (`apps/orchestrator/tests/test_clarifying_question_drf1531.py`).

Каталог реконструирован тем же способом и теми же строками, что в
`test_discovery_precision_rotation.py`: живой контур с этой машины недоступен,
а числа замера пилота 06.09.2026 воспроизводятся на этих строках той же
формулой, что стоит в проде.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from django.conf import settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.marketplace.discovery import (
    _MAX_CLARIFY_OPTIONS,
    clarification_material,
    discover_masters,
)
from apps.tenancy.models import Tenant

# Postgres-only for the whole module, same reason the precision suite gives:
# matching is ILIKE and Cyrillic case folding is the database's job. On SQLite
# nothing Cyrillic matches, so every assertion below would compare empty lists.
pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        "postgresql" not in str(settings.DATABASES["default"]["ENGINE"]),
        reason="Cyrillic ILIKE folding requires Postgres; on SQLite the tier "
        "assertions would compare empty lists.",
    ),
]


def _ts() -> datetime:
    return datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def penza() -> Tenant:
    return Tenant.objects.create(slug="salon-penza", name="Salon Penza", city="Пенза")


def _service(tenant: Tenant, name: str, *, goals: list[dict[str, str]] | None = None):
    return CatalogService.all_tenants.create(
        tenant=tenant,
        slug=f"svc-{hashlib.sha256(name.encode()).hexdigest()[:12]}",
        name=name,
        is_active=True,
        goals=goals or [],
        external_updated_at=_ts(),
    )


def _offers(tenant: Tenant, master_name: str, *services) -> CatalogMaster:
    """A bookable master performing exactly ``services`` (rows or names)."""
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        name=master_name,
        specialization="",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )
    for item in services:
        row = _service(tenant, item) if isinstance(item, str) else item
        MasterService.all_tenants.create(tenant=tenant, master=master, service=row)
    return master


# ─── the reconstructed contour ─────────────────────────────────────────────
#
# One massage service per master. Precision of «массаж» against each name is
# (matched² / stems × words), so the four TWO-word names all score 0.5 and
# everything longer scores less — that 0.5 tier is the tie the question is
# for, and its names are the ticket's own example.

_MASSAGE_CONTOUR: tuple[tuple[str, str], ...] = (
    ("Мастер 01", "Спортивный массаж"),
    ("Мастер 02", "Классический массаж"),
    ("Мастер 03", "Лимфодренажный массаж"),
    ("Мастер 04", "Массаж головы"),
    ("Мастер 05", "Спортивный восстановительный массаж"),
    ("Мастер 06", "Массаж ног — глубокое расслабление и лимфодренаж"),
    ("Мастер 07", "Тайский массаж для двоих в четыре руки"),
    ("Мастер 08", "Спортивный массаж глубоких тканей"),
)

#: The four names of that tier — the chips DRF-1531 is written around.
_TIER_NAMES = {
    "Классический массаж",
    "Лимфодренажный массаж",
    "Массаж головы",
    "Спортивный массаж",
}


@pytest.fixture
def massage(penza: Tenant) -> Tenant:
    for master_name, service_name in _MASSAGE_CONTOUR:
        _offers(penza, master_name, service_name)
    return penza


class TestTheTieTheQuestionIsFor:
    """«массаж» — восемь найдено, ярус из четырёх, четыре имени."""

    def test_the_top_tier_is_four_of_eight(self, massage: Tenant) -> None:
        found = discover_masters(specialization="массаж")
        material = clarification_material(specialization="массаж")

        print(f"\nнайдено: {len(found)}  ярус: {material.tier}  варианты: {material.options}")
        # Everybody is still found — the question is about ORDER being
        # meaningless, not about the search missing people.
        assert len(found) == 8
        assert material.tier == 4

    def test_the_options_are_the_names_of_that_tier(self, massage: Tenant) -> None:
        material = clarification_material(specialization="массаж")

        assert set(material.options) == _TIER_NAMES

    def test_a_name_from_below_the_tier_is_never_offered(self, massage: Tenant) -> None:
        """Ниже яруса точность УЖЕ различила — спрашивать про это нечего."""
        material = clarification_material(specialization="массаж")

        assert "Массаж ног — глубокое расслабление и лимфодренаж" not in material.options
        assert "Тайский массаж для двоих в четыре руки" not in material.options

    def test_a_tier_master_offering_more_names_only_the_tier_row(self, penza: Tenant) -> None:
        """Мастер из яруса, у которого есть и длинное название.

        Имя берётся у строки, которая ПОСТАВИЛА мастера в ярус, а не у всех
        его совпавших строк.
        """
        _offers(penza, "Двойная", "Классический массаж", "Массаж ног — глубокое и долгое")
        _offers(penza, "Одна", "Спортивный массаж")

        options = clarification_material(specialization="массаж").options

        assert "Массаж ног — глубокое и долгое" not in options
        assert set(options) == {"Классический массаж", "Спортивный массаж"}


class TestEveryOptionCanBeAnswered:
    """«На каждый предложенный вариант есть хотя бы один мастер»."""

    def test_each_option_finds_somebody(self, massage: Tenant) -> None:
        options = clarification_material(specialization="массаж").options

        assert options
        for option in options:
            found = discover_masters(specialization=option)
            print(f"{option!r} -> {len(found)} мастер(ов)")
            assert found, f"чип «{option}» ведёт в пустоту"

    def test_never_more_than_five_options(self, penza: Tenant) -> None:
        """§7 — прогрессивное раскрытие. Больше пяти — снова каталог."""
        for index in range(12):
            _offers(penza, f"Мастер {index:02d}", f"Массаж {index:02d}")

        material = clarification_material(specialization="массаж")

        assert material.tier == 12
        assert len(material.options) == _MAX_CLARIFY_OPTIONS == 5

    def test_the_limit_argument_can_only_narrow(self, massage: Tenant) -> None:
        assert len(clarification_material(specialization="массаж", limit=2).options) == 2
        # Above the ceiling it clamps rather than obeys.
        assert len(clarification_material(specialization="массаж", limit=99).options) == 4


class TestWhenThereIsNothingToAsk:
    """Отрицания — и положительная стража рядом с каждым (DRF-1411)."""

    def test_a_distinguishing_query_leaves_a_tier_of_one(self, massage: Tenant) -> None:
        """Парная положительная стража на тех же данных.

        «спортивный массаж» на том же каталоге: восемь по-прежнему находятся,
        но верхний ярус — один, и переспрашивать человека не о чем.
        """
        found = discover_masters(specialization="спортивный массаж")
        material = clarification_material(specialization="спортивный массаж")

        print(f"\nнайдено: {len(found)}  ярус: {material.tier}")
        assert len(found) == 8
        assert material.tier == 1
        assert material.options == ["Спортивный массаж"]

    def test_naming_a_service_exactly_collapses_the_options(self, massage: Tenant) -> None:
        """Человек назвал услугу — ярус состоит из неё одной."""
        material = clarification_material(specialization="Классический массаж")

        assert material.options == ["Классический массаж"]

    def test_a_city_only_query_has_no_material(self, massage: Tenant) -> None:
        """«мастера в пензе» — города вопросом не уточняют.

        Граница проведена сознательно: город — жёсткое условие поиска (§29.1),
        и сегодняшнее поведение такого запроса эта задача не трогает.
        """
        assert clarification_material(specialization="мастера в пензе") == (0, [])

    def test_an_unparseable_query_has_no_material(self, massage: Tenant) -> None:
        assert clarification_material(specialization="я") == (0, [])
        assert clarification_material(specialization="   ") == (0, [])

    def test_nobody_found_means_nothing_to_ask(self, massage: Tenant) -> None:
        assert clarification_material(specialization="брекеты") == (0, [])

    def test_a_specialization_only_tier_names_nothing(self, penza: Tenant) -> None:
        """Ярус из мастеров без строк услуг: считается, но не называется."""
        for index in range(4):
            CatalogMaster.all_tenants.create(
                tenant=penza,
                external_updated_at=_ts(),
                name=f"Аноним {index}",
                specialization="массаж на дому",
                is_active=True,
                invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            )

        material = clarification_material(specialization="массаж")

        assert material.tier == 4
        assert material.options == []


class TestGoalQueryIsTheSameCase:
    """«хочу расслабиться» — ничья размером со всю цель (DRF-1531, вторая половина)."""

    @pytest.fixture
    def relax(self, penza: Tenant) -> Tenant:
        goals = [{"key": "relax", "label": "Расслабиться и снять стресс"}]
        # «Классический массаж» carries the goal AND has two masters — the
        # count is what puts it first, not the alphabet.
        popular = _service(penza, "Классический массаж", goals=goals)
        _offers(penza, "Мастер А", popular)
        _offers(penza, "Мастер Б", popular)
        for name in ("Ароматерапия", "Стоун-терапия", "Обёртывание", "Йога-релакс"):
            _offers(penza, f"Мастер {name}", _service(penza, name, goals=goals))
        return penza

    def test_the_whole_goal_is_one_tier(self, relax: Tenant) -> None:
        """Цель не ранжируется вовсе — значит ничья равна всему набору."""
        found = discover_masters(specialization="хочу расслабиться")
        material = clarification_material(specialization="хочу расслабиться")

        print(f"\nнайдено по цели: {len(found)}  ярус: {material.tier}")
        assert len(found) == 6
        assert material.tier == 6

    def test_it_offers_catalog_names_not_the_whole_goal(self, relax: Tenant) -> None:
        material = clarification_material(specialization="хочу расслабиться")

        assert len(material.options) == 5
        # Best-supported first: two masters perform it, everyone else one.
        assert material.options[0] == "Классический массаж"
        # And the rest are catalog rows, in a stable order.
        assert material.options[1:] == sorted(material.options[1:])

    def test_every_goal_option_finds_somebody(self, relax: Tenant) -> None:
        for option in clarification_material(specialization="хочу расслабиться").options:
            assert discover_masters(specialization=option), option
