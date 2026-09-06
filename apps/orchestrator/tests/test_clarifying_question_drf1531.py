"""Различающий вопрос вместо сортировки неразличимого — DRF-1531, ход разговора.

Каталожная половина (какой ярус неразличим, какие имена он даёт) проверена в
`apps/marketplace/tests/test_clarify_material_drf1531.py`. Здесь — решение
СПРАШИВАТЬ и то, что происходит после ответа.

Живой проход, который ниже воспроизведён целиком:

    человек: массаж
    Ayla:    Уточните, пожалуйста, что именно подойдёт:
             [Классический массаж] [Лимфодренажный массаж]
             [Массаж головы] [Спортивный массаж]
    человек: (тап) Классический массаж
    Ayla:    Вот мастера, которые могут подойти:
             • Мастер 02 · Классический массаж …

и второй:

    человек: хочу расслабиться
    Ayla:    вопрос с пятью названиями услуг, а не 120 услуг алфавитом
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import pytest
from django.conf import settings
from django.test import override_settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.marketplace.discovery import clarification_material, discover_masters
from apps.orchestrator import discovery as orchestrator_discovery
from apps.orchestrator.concierge import generate_direct_show_masters_reply
from apps.orchestrator.discovery import (
    CLARIFY_SERVICE_QUESTION,
    _MAX_CLARIFICATION_OPTIONS,
    clarification_mode_of,
    clarifying_question,
)
from apps.tenancy.models import Tenant

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        "postgresql" not in str(settings.DATABASES["default"]["ENGINE"]),
        reason="Cyrillic ILIKE folding requires Postgres; on SQLite every "
        "search below would return an empty list and no question could fire.",
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
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        name=master_name,
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )
    for item in services:
        row = _service(tenant, item) if isinstance(item, str) else item
        MasterService.all_tenants.create(tenant=tenant, master=master, service=row)
    return master


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


@pytest.fixture
def massage(penza: Tenant) -> Tenant:
    for master_name, service_name in _MASSAGE_CONTOUR:
        _offers(penza, master_name, service_name)
    return penza


def _options(reply: Any) -> list[str]:
    return list((reply.action_data or {}).get("clarification", {}).get("options", []))


def _labels(reply: Any) -> list[str]:
    payload = (reply.action_data or {})["attachments"][0]["payload"]
    return [button["label"] for button in payload["buttons"]]


def _callbacks(reply: Any) -> list[str]:
    payload = (reply.action_data or {})["attachments"][0]["payload"]
    return [button["callback"] for button in payload["buttons"]]


class TestTheQuestionItself:
    """«массаж» → вопрос с чипами из имён каталога."""

    def test_a_one_word_service_request_is_answered_with_a_question(self, massage: Tenant) -> None:
        reply = clarifying_question(specialization="массаж")

        assert reply is not None
        print(f"\n{reply.text}\n{_labels(reply)}")
        assert reply.text == CLARIFY_SERVICE_QUESTION
        assert _options(reply) == [
            "Классический массаж",
            "Лимфодренажный массаж",
            "Массаж головы",
            "Спортивный массаж",
        ]

    def test_the_options_are_catalog_rows_word_for_word(self, massage: Tenant) -> None:
        """Границу §20 это укрепляет: ни одно слово вопроса не сочинено."""
        reply = clarifying_question(specialization="массаж")
        assert reply is not None

        catalog = set(
            CatalogService.all_tenants.filter(is_active=True).values_list("name", flat=True)
        )
        assert set(_options(reply)) <= catalog

    def test_a_tap_is_a_typed_answer(self, massage: Tenant) -> None:
        """Существующий механизм, а не второй: callback == текст варианта."""
        reply = clarifying_question(specialization="массаж")
        assert reply is not None

        assert _callbacks(reply) == _options(reply)
        assert clarification_mode_of(reply.action_data) == "confirm_one"

    def test_never_more_than_five_chips(self, penza: Tenant) -> None:
        for index in range(12):
            _offers(penza, f"Мастер {index:02d}", f"Массаж {index:02d}")

        reply = clarifying_question(specialization="массаж")

        assert reply is not None
        assert len(_options(reply)) == _MAX_CLARIFICATION_OPTIONS == 5

    def test_every_chip_leads_to_at_least_one_master(self, massage: Tenant) -> None:
        """Чип в пустоту хуже отсутствия чипа."""
        reply = clarifying_question(specialization="массаж")
        assert reply is not None

        for option in _options(reply):
            assert discover_masters(specialization=option), option


class TestOneQuestionNotTwo:
    """§7 — после ответа показываем, а не спрашиваем снова."""

    def test_tapping_a_chip_does_not_ask_again(self, massage: Tenant) -> None:
        first = clarifying_question(specialization="массаж")
        assert first is not None

        for option in _options(first):
            assert clarifying_question(specialization=option) is None, option

    def test_the_answer_narrows_the_tier_to_one(self, massage: Tenant) -> None:
        """Ярус из четырёх превращается в ярус из одного — и дальше точность."""
        before = clarification_material(specialization="массаж")
        after = clarification_material(specialization="Классический массаж")

        print(f"\nярус до вопроса: {before.tier}  после ответа: {after.tier}")
        assert before.tier == 4
        assert after.tier == 1

    def test_the_first_card_after_the_answer_is_that_service(self, massage: Tenant) -> None:
        """Живой проход целиком: вопрос → выбор → сузившийся список."""
        question = clarifying_question(specialization="массаж")
        assert question is not None
        chosen = "Классический массаж"
        assert chosen in _options(question)

        cards = discover_masters(specialization=chosen, resolve_service=True)

        print(f"\nпосле «{chosen}»: {[c.name for c in cards][:5]}")
        assert cards[0].name == "Мастер 02"


class TestWhenItStaysSilent:
    """Отрицания и парная положительная стража на тех же данных (DRF-1411)."""

    def test_a_distinguishing_request_is_not_interrupted(self, massage: Tenant) -> None:
        """«спортивный массаж» — человека не переспрашивают там, где ответ есть."""
        assert clarifying_question(specialization="спортивный массаж") is None
        # Positive half, same data: the request IS answerable, and answering
        # it puts the right master first.
        cards = discover_masters(specialization="спортивный массаж")
        assert cards[0].name == "Мастер 01"

    def test_a_city_only_request_is_not_interrupted(self, massage: Tenant) -> None:
        assert clarifying_question(specialization="мастера в пензе") is None

    def test_a_small_tier_is_answered_not_asked_about(self, penza: Tenant) -> None:
        """Порог 4: три различающихся услуги — ещё не повод спрашивать."""
        _offers(penza, "Первый", "Классический массаж")
        _offers(penza, "Второй", "Спортивный массаж")
        _offers(penza, "Третий", "Массаж головы")

        assert clarification_material(specialization="массаж").tier == 3
        assert clarifying_question(specialization="массаж") is None

    def test_an_empty_request_is_not_a_question(self, massage: Tenant) -> None:
        assert clarifying_question(specialization=None) is None
        assert clarifying_question(specialization="   ") is None


class TestTheThresholdIsASetting:
    """Порог назван явно и вынесен в настройку — его заменит измерение §29.2."""

    def test_the_default_is_four(self) -> None:
        assert settings.DISCOVERY_CLARIFY_MIN_TIER == 4

    @override_settings(DISCOVERY_CLARIFY_MIN_TIER=0)
    def test_zero_disables_the_question_entirely(self, massage: Tenant) -> None:
        """Рубильник: поведение до DRF-1531 возвращается без выкладки."""
        assert clarifying_question(specialization="массаж") is None

    @override_settings(DISCOVERY_CLARIFY_MIN_TIER=5)
    def test_raising_it_above_the_tier_stops_the_question(self, massage: Tenant) -> None:
        assert clarification_material(specialization="массаж").tier == 4
        assert clarifying_question(specialization="массаж") is None


class TestGoalRequest:
    """«хочу расслабиться» — вопрос, а не 120 услуг алфавитом."""

    @pytest.fixture
    def relax(self, penza: Tenant) -> Tenant:
        goals = [{"key": "relax", "label": "Расслабиться и снять стресс"}]
        for name in ("Ароматерапия", "Стоун-терапия", "Обёртывание", "Йога-релакс", "Флоатинг"):
            _offers(penza, f"Мастер {name}", _service(penza, name, goals=goals))
        return penza

    def test_a_goal_is_asked_about_not_listed(self, relax: Tenant) -> None:
        listed = discover_masters(specialization="хочу расслабиться")
        reply = clarifying_question(specialization="хочу расслабиться")

        assert reply is not None
        print(f"\nбез вопроса вернулось бы {len(listed)} мастеров подряд")
        print(f"с вопросом: {_options(reply)}")
        assert len(_options(reply)) == 5

    def test_the_goal_answer_does_not_ask_again(self, relax: Tenant) -> None:
        reply = clarifying_question(specialization="хочу расслабиться")
        assert reply is not None

        for option in _options(reply):
            assert clarifying_question(specialization=option) is None, option


class TestAQuestionNeverCostsTheAnswer:
    """Каталожное чтение вопроса — лучшее усилие, а не условие ответа."""

    @staticmethod
    def _explode(**_kwargs: object) -> None:
        raise RuntimeError("Database access not allowed")

    def test_a_broken_catalog_read_degrades_to_no_question(
        self, massage: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Отрицание. Положительная стража — прямо над ним, на тех же данных."""
        # Positive first: with the catalog reachable, this very query asks.
        assert clarifying_question(specialization="массаж") is not None

        monkeypatch.setattr(orchestrator_discovery, "clarification_material", self._explode)

        # And with it unreachable the turn is not lost — it just gets the list.
        assert clarifying_question(specialization="массаж") is None

    def test_the_turn_still_answers_with_cards(
        self, massage: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Форма живого читателя: каталог вопроса недоступен, ответ — есть.

        Ровно то, на чём падали наборы `show_masters` до стражи выше: там
        маркетплейс замокан и базы нет вовсе.
        """
        monkeypatch.setattr(orchestrator_discovery, "clarification_material", self._explode)

        reply = generate_direct_show_masters_reply("хочу массаж", trace_id="t-soft")

        assert reply is not None
        # The list, not the question, and not an exception.
        assert reply.text.startswith("Вот мастера")


class TestTheConciergeFastPathAsksToo:
    """Оба читателя спрашивают одинаково — иначе экраны разойдутся."""

    def test_the_deterministic_branch_returns_the_question(self, massage: Tenant) -> None:
        reply = generate_direct_show_masters_reply("хочу массаж", trace_id="t-1")

        assert reply is not None
        assert reply.text == CLARIFY_SERVICE_QUESTION
        assert len(_options(reply)) == 4

    def test_and_answers_normally_once_the_service_is_named(self, massage: Tenant) -> None:
        reply = generate_direct_show_masters_reply("хочу классический массаж", trace_id="t-2")

        assert reply is not None
        assert reply.text.startswith("Вот мастера")
        assert "Мастер 02" in reply.text
