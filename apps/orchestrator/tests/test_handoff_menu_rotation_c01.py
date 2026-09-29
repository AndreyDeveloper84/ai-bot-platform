"""Меню услуг мастера перестаёт резаться по алфавиту — C-01.

Второй непочиненный читатель того же дефекта. Кнопочный бюджет меню — 10
строк, а прайс мастера длиннее; резал SQL, ничьи разводил алфавит. Значит
услуги за десятой буквой не показывались никогда, и кто именно выпал,
решала первая буква названия.

Ротация взята та же, что у списка мастеров
(:func:`apps.marketplace.discovery.rotate_ties`), а не написана вторая:
второй ключ означал бы второй контракт «стабильно внутри человека,
равномерно между людьми», и разойтись им — вопрос времени.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.catalog.models import CatalogService
from apps.orchestrator.handoff import _ASK_SERVICE_BUTTON_LIMIT, _menu_rows
from apps.tenancy.models import Tenant

pytestmark = [pytest.mark.django_db]

#: Заметно больше кнопочного бюджета — иначе «хвост недостижим» нечем
#: показать: без хвоста алфавитный срез ничего и не отрезает.
_TOTAL = 30


def _ts() -> datetime:
    return datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def roster() -> list[str]:
    tenant = Tenant.objects.create(slug="salon-menu-c01", name="Salon Menu", city="Пенза")
    names = [f"Услуга {index:02d}" for index in range(_TOTAL)]
    for index, name in enumerate(names):
        CatalogService.all_tenants.create(
            tenant=tenant,
            slug=f"menu-{index}",
            name=name,
            is_active=True,
            external_updated_at=_ts(),
        )
    return names


def _qs():
    return CatalogService.all_tenants.all().order_by("name")


def _shown(seed: str | None) -> list[str]:
    return [name for _pk, name in _menu_rows(_qs(), seed=seed)]


class TestTheMenuTail:
    def test_without_a_seed_the_menu_is_the_alphabet(self, roster) -> None:
        """Контроль присутствия: вызывающий без разговора не меняет поведения."""
        assert _shown(None) == roster[: _ASK_SERVICE_BUTTON_LIMIT + 1]

    def test_every_service_reaches_the_menu_for_somebody(self, roster) -> None:
        """То, ради чего правило существует. Краснеет до правки.

        Раньше в меню попадали ровно первые одиннадцать по алфавиту, а
        остальные девятнадцать не видел никто и никогда.
        """
        seen: set[str] = set()
        for index in range(300):
            seen.update(_shown(f"conv-{index}"))

        assert seen == set(roster), f"недостижимы: {sorted(set(roster) - seen)}"

    def test_the_same_person_twice_gets_the_same_menu(self, roster) -> None:
        assert _shown("conv-7") == _shown("conv-7")

    def test_different_people_get_different_menus(self, roster) -> None:
        firsts = {_shown(f"conv-{i}")[0] for i in range(100)}

        assert len(firsts) >= 4, sorted(firsts)

    def test_the_button_budget_is_unchanged(self, roster) -> None:
        """Ротация переставляет, а не расширяет: +1 строка на признак обрезки.

        Тот же ``+1``, что читался и раньше, — он и говорит вызывающему,
        что список не поместился, без второго запроса COUNT.
        """
        assert len(_shown("conv-1")) == _ASK_SERVICE_BUTTON_LIMIT + 1
