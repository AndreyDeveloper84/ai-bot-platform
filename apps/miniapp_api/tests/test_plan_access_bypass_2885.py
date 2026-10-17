# ruff: noqa: F811 — фикстуры берутся по имени из соседних наборов узлов
"""DRF-2885 — замок приёмки Плана нельзя обойти ни одним путём человека.

Требование владельца 10.10 (дословно): регрессионные тесты на обход — чат,
Mini App, прямые API, действия с шагами.

Здесь всё настоящее: ход чата Mini App, согласие человека, гейт согласия,
замок (``PLAN_ACCEPTANCE_ACCOUNTS`` — настоящей настройкой, без подмен).
Подменены каталог плана, каталог записи и связка человека с каталогом —
шпионами, которые считают ЛЮБОЙ вызов.

Узлы построены так, чтобы «закрыто» нельзя было получить случайно: человек
сначала назван в списке и проходит весь путь (предложение собрано, услуга
выбрана, времена показаны) — у него есть и сохранённый план, и несохранённое
предложение, и состояние шага, и вердикт хода. Затем его убирают из списка —
и каждое действие перестаёт доходить до каталога.

* x1 — чат: «мой план», кнопка «Составить план», кнопки предложения и замены;
* x2 — чат: действия с шагами (шаг, услуга, день, время);
* x3 — экран и прямые ручки: все шесть маршрутов плана отвечают «выключено»;
* x4 — экран: действия с шагами (услуги, выбор, день, запись);
* x5 — модели инструмент сборки не предлагается, а вызов мимо предложения
  ничего не собирает;
* x6 — закрыто при пустом списке, при списке не того вида и для другого
  человека, когда список называет соседа;
* x7 — обратно: назвали снова — путь снова работает (замок, а не поломка);
* x8 — четыре внутренних читателя закрыты и при прямом вызове.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.integrations.ayla import booking_client as booking_mod
from apps.integrations.ayla import plan_engine_client as client_mod
from apps.miniapp_api import views_customer_assistant as chat_views
from apps.miniapp_api import views_plan_engine as views
from apps.miniapp_api.tests.test_customer_assistant_2799 import (  # noqa: F401 — fixtures
    _ask,
    _bot_token,
    _concierge,
    _global_threads,
    _init_data_header,
    _no_ayla_link,
    _no_intent_llm,
    _redis,
    tenant,
    wire,
)
from apps.miniapp_api.tests.test_plan_from_chat_e2e_2885 import _person_shell
from apps.miniapp_api.tests.test_plan_step_booking_e2e_2885 import (
    AYLA_USER,
    PLAN8,
    PLAN_ID,
    SEARCH8,
    Booking,
    StepCatalog,
)
from apps.orchestrator import plan_step_card as step
from apps.orchestrator.decision_readiness import state as state_mod
from apps.orchestrator.decision_readiness.tests.fakes import FakeRedis
from apps.orchestrator.tests.test_plan_engine_card_2885 import TOKEN

pytestmark = pytest.mark.django_db

_PEOPLE = iter(range(2885601, 2885999))
NEIGHBOUR = "max:2885600"


class Spy:
    """Обёртка, которая записывает имя КАЖДОГО вызванного метода."""

    def __init__(self, inner: Any, calls: list[str], prefix: str) -> None:
        self._inner = inner
        self._calls = calls
        self._prefix = prefix

    def __getattr__(self, name: str) -> Any:
        found = getattr(self._inner, name)
        if not callable(found):
            return found

        def recorded(*args: Any, **kwargs: Any) -> Any:
            self._calls.append(f"{self._prefix}.{name}")
            return found(*args, **kwargs)

        return recorded


@pytest.fixture(autouse=True)
def calls(monkeypatch: pytest.MonkeyPatch, settings) -> list[str]:
    """Все обращения наружу по пути Плана: каталог плана, запись, связка."""
    from apps.identity.services import ayla_link

    seen: list[str] = []
    catalog = Spy(StepCatalog(), seen, "plan")
    booking = Spy(Booking(), seen, "booking")
    monkeypatch.setattr(client_mod, "PlanEngineHttpClient", lambda: catalog)
    monkeypatch.setattr(views, "PlanEngineHttpClient", lambda: catalog)
    monkeypatch.setattr(booking_mod, "get_ayla_booking_client", lambda: booking)

    def link(bot_user: Any, trigger: str = "") -> str:
        seen.append("link.ensure_ayla_link")
        return AYLA_USER

    monkeypatch.setattr(ayla_link, "ensure_ayla_link", link)
    store = FakeRedis()
    monkeypatch.setattr(state_mod, "_redis_client", lambda: store)
    monkeypatch.setattr(step, "_today", lambda: date(2026, 10, 10))
    # Узел шлёт больше ходов, чем человек успел бы за минуту.
    monkeypatch.setattr(chat_views, "ASK_PER_MINUTE", 1000)
    settings.PLAN_ENGINE_ENABLED = True
    settings.DRE_SHADOW_ENABLED = False
    return seen


@pytest.fixture
def person(tenant, settings) -> str:
    value = str(next(_PEOPLE))
    _person_shell(tenant, value)
    settings.PLAN_ACCEPTANCE_ACCOUNTS = (f"max:{value}",)
    return value


def _post(client: Client, person: str, route: str, body: dict[str, Any]):
    return client.post(
        reverse(f"miniapp_api:{route}"),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header(person),
    )


def _current(client: Client, person: str):
    return client.get(
        reverse("miniapp_api:customer_plan_current"), HTTP_AUTHORIZATION=_init_data_header(person)
    )


def _walk_while_listed(client: Client, person: str, calls: list[str]) -> None:
    """Названный человек проходит путь: после этого у него есть всё, чем
    можно было бы воспользоваться в обход."""
    plan = _ask(client, "мой план", as_user=person)
    assert "Режим сна" in [b["label"] for b in plan.json()["buttons"]], plan.content[:300]
    composed = _ask(client, "cb:plan:compose", as_user=person)
    assert composed.status_code == 200, composed.content[:300]
    offers = _ask(client, f"cb:plan:step:{PLAN8}:0", as_user=person)
    assert offers.json()["answer"].endswith("PLAN_STEP_OFFERS · тест"), offers.content[:300]
    slots = _ask(client, f"cb:plan:offer:{SEARCH8}:0", as_user=person)
    assert slots.json()["answer"].endswith("PLAN_STEP_SLOTS · тест"), slots.content[:300]
    seen = _current(client, person)
    assert seen.status_code == 200 and seen.json()["plan"]["plan_id"] == PLAN_ID
    # Положительный контроль шпионов: путь действительно ходил наружу.
    for expected in ("plan.compose_decision", "plan.step_candidates", "plan.resolve_step"):
        assert expected in calls, (expected, calls)
    assert "booking.get_available_times" in calls


CHAT_PLAN_TAPS = [
    "мой план",
    "cb:plan:compose",
    f"cb:plan:save:{TOKEN}",
    f"cb:plan:edit:{TOKEN}",
    f"cb:plan:drop:{TOKEN}:0",
    f"cb:plan:discuss:{TOKEN}",
    "cb:plan:discuss:saved",
    f"cb:plan:replace:{PLAN8}",
    f"cb:plan:keep:{PLAN8}",
]
CHAT_STEP_TAPS = [
    f"cb:plan:step:{PLAN8}:0",
    f"cb:plan:offer:{SEARCH8}:0",
    f"cb:plan:day:{SEARCH8}:0",
    f"cb:plan:slot:{SEARCH8}:0",
]
SCREEN_ROUTES: list[tuple[str, dict[str, Any]]] = [
    ("customer_plan_decision", {}),
    ("customer_plan_save", {"token": TOKEN}),
    ("customer_plan_replace", {"plan_id": PLAN_ID, "replaces_plan_id": PLAN_ID}),
    ("customer_plan_keep", {"plan_id": PLAN_ID}),
    ("customer_plan_step", {"action": "offers", "token": PLAN8, "index": 0}),
]
SCREEN_STEP_ACTIONS = [
    {"action": "offers", "token": PLAN8, "index": 0},
    {"action": "choose", "token": SEARCH8, "index": 0},
    {"action": "day", "token": SEARCH8, "index": 0},
    {"action": "book", "token": SEARCH8, "index": 0},
]


def _unlist(settings, accounts: Any = (NEIGHBOUR,)) -> None:
    settings.PLAN_ACCEPTANCE_ACCOUNTS = accounts


def _assert_no_plan_words(answer: Any) -> None:
    """Ответ вне списка не несёт ни карточки плана, ни исхода механизма."""
    body = answer.json()
    text = str(body.get("answer") or "")
    assert "· тест" not in text, text
    labels = [b.get("label") for b in body.get("buttons") or []]
    assert "Режим сна" not in labels and "Обсудить" not in labels, labels


def test_x1_chat_no_plan_entry_reaches_the_catalog_once_unlisted(
    client: Client, tenant, wire, person: str, calls: list[str], settings
) -> None:
    _walk_while_listed(client, person, calls)
    _unlist(settings)
    before = list(calls)

    for tap in CHAT_PLAN_TAPS:
        answer = _ask(client, tap, as_user=person)
        assert answer.status_code == 200, (tap, answer.content[:200])
        _assert_no_plan_words(answer)
        assert calls == before, (tap, calls[len(before) :])


def test_x2_chat_no_step_action_reaches_the_catalog_once_unlisted(
    client: Client, tenant, wire, person: str, calls: list[str], settings
) -> None:
    _walk_while_listed(client, person, calls)
    _unlist(settings)
    before = list(calls)

    for tap in CHAT_STEP_TAPS:
        answer = _ask(client, tap, as_user=person)
        assert answer.status_code == 200, (tap, answer.content[:200])
        _assert_no_plan_words(answer)
        assert calls == before, (tap, calls[len(before) :])


def test_x3_screen_every_plan_route_answers_disabled_once_unlisted(
    client: Client, tenant, wire, person: str, calls: list[str], settings
) -> None:
    _walk_while_listed(client, person, calls)
    _unlist(settings)
    before = list(calls)

    read = _current(client, person)
    assert (read.status_code, read.json()["error"]) == (404, "plan_engine_disabled")
    for route, body in SCREEN_ROUTES:
        resp = _post(client, person, route, body)
        assert (resp.status_code, resp.json()["error"]) == (404, "plan_engine_disabled"), route
        assert calls == before, (route, calls[len(before) :])


def test_x3_the_census_of_screen_routes_matches_the_plan_routes_of_the_url_table() -> None:
    """Список маршрутов узла — не от руки: он сверен с таблицей маршрутов."""
    from apps.miniapp_api import urls

    in_table = {
        pattern.name
        for pattern in urls.urlpatterns
        if getattr(pattern.callback, "__module__", "") == views.__name__
    }
    assert in_table == {"customer_plan_current", *(route for route, _ in SCREEN_ROUTES)}


def test_x4_screen_no_step_action_reaches_the_catalog_once_unlisted(
    client: Client, tenant, wire, person: str, calls: list[str], settings
) -> None:
    _walk_while_listed(client, person, calls)
    _unlist(settings)
    before = list(calls)

    for body in SCREEN_STEP_ACTIONS:
        resp = _post(client, person, "customer_plan_step", body)
        assert (resp.status_code, resp.json()["error"]) == (404, "plan_engine_disabled"), body
        assert calls == before, (body, calls[len(before) :])


def test_x5_the_model_is_not_offered_the_tool_and_a_call_past_the_offer_composes_nothing(
    client: Client, tenant, wire, person: str, calls: list[str], settings
) -> None:
    from apps.conversations.services import resolve_active_global_conversation
    from apps.identity.models import BotUser
    from apps.orchestrator.concierge import COMPOSE_PLAN_TOOL, _tools_offered
    from apps.orchestrator.plan_engine_card import compose_for_request

    _walk_while_listed(client, person, calls)
    shell = BotUser.all_tenants.filter(channel="max", channel_user_id=person).first()
    assert shell is not None
    conversation = resolve_active_global_conversation(shell, create_if_missing=False)
    assert conversation is not None

    def offered() -> set[str]:
        return {str(spec["name"]) for spec in _tools_offered("составь мне план", conversation)}

    assert COMPOSE_PLAN_TOOL in offered()  # названному человеку инструмент предлагается
    _unlist(settings)
    before = list(calls)

    assert COMPOSE_PLAN_TOOL not in offered()
    # Модель всё же вызвала инструмент (старая подсказка, чужой текст) — ничего.
    assert compose_for_request(bot_user=shell, conversation=conversation, trace_id="t") is None
    assert calls == before, calls[len(before) :]


@pytest.mark.parametrize(
    "accounts",
    [
        (),
        (NEIGHBOUR,),
        "self-as-a-string",  # аккаунт человека строкой вместо списка — подставляется в узле
        ("max:",),
        ("*",),
    ],
)
def test_x6_closed_for_an_empty_list_a_list_of_the_wrong_shape_and_a_neighbours_list(
    client: Client, tenant, wire, person: str, calls: list[str], settings, accounts: Any
) -> None:
    _walk_while_listed(client, person, calls)
    _unlist(settings, f"max:{person}" if accounts == "self-as-a-string" else accounts)
    before = list(calls)

    chat = _ask(client, "мой план", as_user=person)
    tap = _ask(client, f"cb:plan:slot:{SEARCH8}:0", as_user=person)
    read = _current(client, person)
    book = _post(
        client, person, "customer_plan_step", {"action": "book", "token": SEARCH8, "index": 0}
    )

    _assert_no_plan_words(chat)
    _assert_no_plan_words(tap)
    assert (read.status_code, book.status_code) == (404, 404)
    assert calls == before, calls[len(before) :]


def test_x8_the_inner_readers_are_closed_on_their_own_not_only_behind_the_outer_ones(
    client: Client, tenant, wire, person: str, calls: list[str], settings
) -> None:
    """Четыре читателя замка стоят ЗА другими (ручкой экрана, обработчиком
    нажатия). Каждый закрыт и при прямом вызове: сосед, который позовёт их
    мимо внешней проверки, замка не обойдёт."""
    from apps.conversations.services import resolve_active_global_conversation
    from apps.identity.models import BotUser
    from apps.orchestrator.plan_engine_card import (
        discussed_plan,
        pending_proposal_view,
        trigger_visible,
    )
    from apps.orchestrator.plan_step_card import step_action

    _walk_while_listed(client, person, calls)
    settings.SYNTHETIC_TEST_TRIGGER_ACCOUNTS = (f"max:{person}",)
    shell = BotUser.all_tenants.filter(channel="max", channel_user_id=person).first()
    assert shell is not None

    def talk() -> Any:
        found = resolve_active_global_conversation(shell, create_if_missing=False)
        assert found is not None
        return found

    def act() -> str:
        return step_action(
            kind="day",
            token=SEARCH8,
            index=0,
            bot_user=shell,
            conversation=talk(),
            trace_id="t",
            safety=views.last_turn_safety_for(shell),
        ).name

    # Названному человеку все четыре отвечают.
    assert pending_proposal_view(talk()) is not None
    discussing = _ask(client, "cb:plan:discuss:saved", as_user=person)
    assert discussing.status_code == 200, discussing.content[:300]
    assert discussed_plan(talk()) is not None
    assert trigger_visible(shell) is True
    assert act() == "PLAN_STEP_SLOTS"

    _unlist(settings)
    before = list(calls)

    assert pending_proposal_view(talk()) is None
    assert discussed_plan(talk()) is None
    assert trigger_visible(shell) is False
    assert act() == "PLAN_ENGINE_UNAVAILABLE"
    assert calls == before, calls[len(before) :]


def test_x7_named_again_the_path_works_again(
    client: Client, tenant, wire, person: str, calls: list[str], settings
) -> None:
    """Замок, а не поломка: вернули в список — нажатие времени записывает."""
    _walk_while_listed(client, person, calls)
    _unlist(settings)
    closed = _ask(client, f"cb:plan:slot:{SEARCH8}:0", as_user=person)
    _assert_no_plan_words(closed)
    assert "booking.create_appointment" not in calls

    settings.PLAN_ACCEPTANCE_ACCOUNTS = (NEIGHBOUR, f"max:{person}")
    booked = _ask(client, f"cb:plan:slot:{SEARCH8}:0", as_user=person)

    assert booked.json()["answer"].endswith("PLAN_STEP_BOOKED · тест"), booked.content[:300]
    assert calls.count("booking.create_appointment") == 1
