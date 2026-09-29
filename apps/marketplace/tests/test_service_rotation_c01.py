"""Услуги перестают резаться в SQL по алфавиту — C-01.

Тот же класс дефекта, что вылечили DRF-1530, DRF-1532 и DRF-1539, и
последний его непочиненный читатель. DRF-1529 называла ТРИ точки сортировки;
вылечена была одна.

`discover_services` упорядочивала `("-match_score", "name", "id")` и резала
в `[:limit]` СРЕДСТВАМИ SQL. Значит услуги, чьё имя стоит дальше по
алфавиту, не показывались никогда — и не потому, что проиграли, а потому
что их не запрашивали. Канон §9 запрещает алфавитный fallback именно при
отсечении top-N: отсечение превращает безобидный порядок в систематическое
смещение показов.

Здесь прибито в порядке цены ошибки:

``TestNobodyIsInvisibleForever`` — то, ради чего правило существует. Это
единственный тест, который КРАСНЕЛ бы до правки; остальные описывают
свойства ротации.

``TestStability`` — §29.6: один и тот же человек получает один и тот же
порядок, разные люди — разные. Не часы и не ``random``: первое свойство
человек замечает, второе — нет.

``TestScoreStillWins`` — положительный страж DRF-1411: ротация отвечает на
«кандидаты неразличимы», а не переранжирует. Где точность совпадения их
различает, её вердикт стоит.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from apps.catalog.models import CatalogMaster, CatalogService
from apps.marketplace.discovery import discover_services
from apps.tenancy.models import Tenant

pytestmark = [pytest.mark.django_db]

#: Имена нарочно в алфавитном порядке: так видно, что режет именно алфавит.
_NAMES = [f"Услуга {letter}" for letter in "АБВГДЕЖЗИКЛМ"]

_SHOWN = 3


def _ts() -> datetime:
    return datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def salon() -> Tenant:
    return Tenant.objects.create(slug="salon-c01", name="Salon C01", city="Пенза")


@pytest.fixture
def catalog(salon: Tenant) -> list[CatalogService]:
    """Двенадцать услуг и один продаваемый мастер — форма живого дефекта.

    Мастер нужен один: `discover_services` показывает услуги салонов, у
    которых есть хоть один продаваемый мастер, а не услуги, привязанные к
    мастеру.
    """
    CatalogMaster.all_tenants.create(
        tenant=salon,
        external_updated_at=_ts(),
        name="Мастер",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        # Без канонического ключа мастер не продаётся, и пустая выдача
        # читалась бы как поломка подбора, а не как отсутствие связи.
        ayla_user_id=uuid4(),
    )
    return [
        CatalogService.all_tenants.create(
            tenant=salon,
            slug=f"usluga-{index}",
            name=name,
            is_active=True,
            external_updated_at=_ts(),
        )
        for index, name in enumerate(_NAMES)
    ]


def _names(**kwargs) -> list[str]:
    return [card.name for card in discover_services(limit=_SHOWN, **kwargs)]


class TestNobodyIsInvisibleForever:
    """§9 и §29.6 — «никто не должен исчезать из выдачи навсегда».

    ЕДИНСТВЕННЫЙ тест здесь, который краснел бы до правки: раньше выдача не
    зависела от человека вовсе, и в top-3 попадали ровно «А», «Б», «В» —
    остальные девять услуг не показывались НИКОМУ И НИКОГДА.
    """

    def test_every_service_reaches_the_top_for_somebody(self, catalog) -> None:
        seen: set[str] = set()
        for index in range(200):
            seen.update(_names(rotation_seed=f"conv-{index}"))

        assert seen == set(_NAMES), f"недостижимы: {sorted(set(_NAMES) - seen)}"

    def test_without_a_seed_it_is_still_the_alphabet(self, catalog) -> None:
        """Контроль присутствия: без сида поведение прежнее.

        Без него «все достижимы» выше не отличалось бы от «фикстура и так
        отдаёт всё» — а вызывающий без разговора обязан получить ровно то,
        что получал вчера.
        """
        assert _names() == _NAMES[:_SHOWN]


class TestStability:
    def test_the_same_person_twice_gets_the_same_order(self, catalog) -> None:
        """§29.6 — список не перетасовывается между репликами человека."""
        first = _names(rotation_seed="conv-42")
        second = _names(rotation_seed="conv-42")

        assert first == second

    def test_different_people_get_different_orders(self, catalog) -> None:
        """Иначе смещение никуда не делось — просто стало другим.

        Проверяется РАЗНООБРАЗИЕ первых мест, а не «два сида дали разное»:
        на двух сидах совпадение случайно, на сотне — уже свойство.
        """
        winners = {_names(rotation_seed=f"conv-{i}")[0] for i in range(100)}

        assert len(winners) > 1
        # И первое место не достаётся одному и тому же имени слишком часто:
        # равномерность и есть то, чем ротация лечит алфавит.
        assert len(winners) >= 4, sorted(winners)

    def test_the_window_size_is_unchanged(self, catalog) -> None:
        assert len(_names(rotation_seed="conv-1")) == _SHOWN


class TestScoreStillWins:
    """DRF-1411 — ротация не переранжирует, она разводит РАВНЫХ."""

    def test_a_better_match_is_never_rotated_away(self, salon, catalog) -> None:
        CatalogService.all_tenants.create(
            tenant=salon,
            slug="massazh-lica",
            name="Массаж лица",
            is_active=True,
            external_updated_at=_ts(),
        )
        CatalogService.all_tenants.create(
            tenant=salon,
            slug="massazh-spiny",
            name="Массаж спины",
            is_active=True,
            external_updated_at=_ts(),
        )

        # Контроль присутствия: без сида запрос действительно ранжирует и
        # ставит полное совпадение первым — иначе тест ниже доказывал бы
        # лишь то, что ротация ничего не делает.
        assert _names(query="массаж лица")[0] == "Массаж лица"

        for index in range(30):
            top = _names(query="массаж лица", rotation_seed=f"conv-{index}")[0]
            assert top == "Массаж лица", f"сид conv-{index} переставил лучший ответ"
