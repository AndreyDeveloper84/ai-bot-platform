"""DRF-1765 — сторож границы C02: опция уточнения смысла — не услуга, не
мастер, не салон и не цена.

Макет C02 (DRF-1176): «Не показываем услуги, мастеров, салоны, цены».
Опции формулирует модель; нарушившая границу снимается до рендера,
сняты все — вопрос без клавиатуры (режим ``free``).

Стражи:
- цена ловится по классу (валюта, «руб», «р.», «цена», «стоимость»), число
  без валюты — нет («2 недели»);
- услуга — по совпадению стемов с именем каталога (равенство, не вхождение:
  «свежий вид» проходит, «Классический массаж» — нет);
- мастер/салон — дословно;
- рендер: снятые опции не попадают в клавиатуру, все сняты → без клавиатуры;
- положительная стража: результаты-формулировки проходят;
- сбой каталога — опция остаётся, вопрос живой.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from django.conf import settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.orchestrator import clarify_guard
from apps.orchestrator.clarify_guard import (
    PRICE_RE,
    clarification_option_violation,
    filter_clarification_options,
)
from apps.orchestrator.discovery import _render_ask_clarification
from apps.tenancy.models import Tenant

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        "postgresql" not in str(settings.DATABASES["default"]["ENGINE"]),
        reason="поиск по имени услуги — кириллический ILIKE, только Postgres",
    ),
]


def _ts() -> datetime:
    return datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def salon() -> Tenant:
    return Tenant.objects.create(slug="salon-roza", name="Салон Роза", city="Пенза")


@pytest.fixture
def catalog(salon: Tenant) -> None:
    svc = CatalogService.all_tenants.create(
        tenant=salon,
        slug="svc-" + hashlib.sha256(b"massage").hexdigest()[:12],
        name="Классический массаж",
        is_active=True,
        external_updated_at=_ts(),
    )
    master = CatalogMaster.all_tenants.create(
        tenant=salon,
        external_updated_at=_ts(),
        name="Анна Соколова",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ayla_user_id=uuid4(),
    )
    MasterService.all_tenants.create(tenant=salon, master=master, service=svc)


class TestPricePattern:
    @pytest.mark.parametrize(
        "text",
        [
            "Массаж 1500 ₽",
            "от 1 500 руб",
            "2000 руб.",
            "1500 р.",
            "цена ниже",
            "стоимость по запросу",
        ],
    )
    def test_price_in_any_spelling(self, text: str) -> None:
        assert PRICE_RE.search(text)

    @pytest.mark.parametrize(
        "text", ["2 недели", "в течение 3 дней", "Свежий вид", "Рядом с домом"]
    )
    def test_numbers_without_currency_are_not_prices(self, text: str) -> None:
        # Присутствие рядом с отсутствием: тот же шаблон на цене срабатывает,
        # а на числе без валюты — нет (иначе зеленел бы и сломанный шаблон).
        assert PRICE_RE.search(f"{text} — 1500 ₽")
        assert PRICE_RE.search(text) is None


class TestViolationByRole:
    def test_service_master_salon_price_are_named(self, catalog) -> None:
        assert clarification_option_violation("Классический массаж") == "service"
        assert clarification_option_violation("классический массаж") == "service"
        assert clarification_option_violation("Анна Соколова") == "master"
        assert clarification_option_violation("Салон Роза") == "salon"
        assert clarification_option_violation("Массаж 1500 ₽") == "price"

    def test_outcomes_pass(self, catalog) -> None:
        """Положительная стража: формулировки результата — не нарушение."""
        for option in ["Свежий вид", "Меньше отёчности", "Снять напряжение", "Не знаю"]:
            assert clarification_option_violation(option) is None, option

    def test_one_shared_word_is_not_a_service(self, catalog) -> None:
        """Равенство стемов, не вхождение: «массаж спины» ≠ «Классический массаж»."""
        assert clarification_option_violation("массаж спины") is None


class TestRender:
    """Граница держится на опциях модели (вызов в concierge), не в рендере."""

    def test_violating_options_never_reach_the_keyboard(self, catalog) -> None:
        kept, dropped = filter_clarification_options(
            ["Свежий вид", "Классический массаж", "Анна Соколова", "1500 ₽", "Меньше отёчности"]
        )
        assert kept == ["Свежий вид", "Меньше отёчности"]
        assert dropped == ["service", "master", "price"]
        reply = _render_ask_clarification("Что важнее?", kept)
        assert reply.action_data is not None
        labels = [b["label"] for b in reply.action_data["attachments"][0]["payload"]["buttons"]]
        assert labels == ["Свежий вид", "Меньше отёчности"]

    def test_all_dropped_means_free_question(self, catalog) -> None:
        kept, _ = filter_clarification_options(["Классический массаж", "Анна Соколова"])
        reply = _render_ask_clarification("Что подойдёт?", kept)
        assert reply.action_data is None
        assert reply.text == "Что подойдёт?"

    def test_catalog_material_path_is_not_filtered(self, catalog) -> None:
        """Положительная стража: DRF-1531 (§29.2) нарочно называет услуги —
        рендер по материалу каталога через сторож не идёт."""
        reply = _render_ask_clarification("Что именно подойдёт?", ["Классический массаж"])
        assert reply.action_data is not None
        labels = [b["label"] for b in reply.action_data["attachments"][0]["payload"]["buttons"]]
        assert labels == ["Классический массаж"]

    def test_concierge_wires_the_guard(self) -> None:
        """Сторож стоит именно на пути ask_clarification модели."""
        import inspect

        from apps.orchestrator import concierge

        src = inspect.getsource(concierge)
        idx = src.index("rendered = _render_ask_clarification(")
        assert "filter_clarification_options(" in src[idx - 600 : idx]


class TestFailOpen:
    def test_catalog_failure_keeps_the_option(self, monkeypatch, caplog) -> None:
        def boom(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(clarify_guard, "discover_services", boom)
        kept, dropped = filter_clarification_options(["Свежий вид"])
        assert kept == ["Свежий вид"] and dropped == []
        assert any("clarify_guard.catalog_unreadable" in r.getMessage() for r in caplog.records)
