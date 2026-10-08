# ruff: noqa: F811 — фикстуры соседнего набора импортируются и принимаются параметрами
"""DRF-2885 — план из чата Mini App сквозь настоящий ход.

Узлы карточки (``apps/orchestrator/tests/test_plan_engine_card_2885.py``)
держат её саму, но тройку им подаёт заглушка. Здесь ход настоящий: ручка чата
Mini App → глобальный ход → гейт → карточка. Проверяется проводка, которую те
узлы не видят: что глобальный ход передаёт пути плана тройку ЭТОГО хода, и
что на двух ходах подряд ревизия растёт.

Каталог подменён; состояние разговора — настоящее, в подменённом Redis.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client

from apps.integrations.ayla import plan_engine_client as client_mod
from apps.miniapp_api.tests.test_customer_assistant_2799 import (  # noqa: F401 — fixtures
    _ask,
    _bot_token,
    _concierge,
    _global_threads,
    _no_ayla_link,
    _no_intent_llm,
    _person_shell,
    _redis,
    tenant,
    wire,
)
from apps.orchestrator import plan_engine_card as card
from apps.orchestrator.decision_readiness import state as state_mod
from apps.orchestrator.decision_readiness.tests.fakes import FakeRedis
from apps.orchestrator.tests.test_plan_engine_card_2885 import TOKEN, FakeCatalog

pytestmark = pytest.mark.django_db

#: Свой аккаунт: лимит запросов чата считается по человеку, и общий с
#: соседним набором аккаунт выбирал бы его лимит.
PERSON = "2885100"


@pytest.fixture(autouse=True)
def catalog(monkeypatch: pytest.MonkeyPatch) -> FakeCatalog:
    fake = FakeCatalog()
    monkeypatch.setattr(client_mod, "PlanEngineHttpClient", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _state_store(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeRedis()
    monkeypatch.setattr(state_mod, "_redis_client", lambda: store)


@pytest.fixture(autouse=True)
def _on(settings) -> None:
    settings.PLAN_ENGINE_ENABLED = True
    settings.SYNTHETIC_TEST_TRIGGER_ACCOUNTS = (f"max:{PERSON}",)
    settings.DRE_SHADOW_ENABLED = False  # ревизию хода открывает сам путь плана


def test_e1_the_command_in_the_miniapp_chat_composes_with_this_turns_verdict(
    client: Client, tenant, wire, catalog: FakeCatalog
) -> None:
    _person_shell(tenant, PERSON)

    answer = _ask(client, card.TRIGGER, as_user=PERSON)

    assert answer.status_code == 200, answer.content[:300]
    body: dict[str, Any] = answer.json()
    assert body["answer"].endswith(card.QUESTION_SAVE)
    assert body["buttons"] == [
        {"label": "Сохранить", "payload": f"cb:plan:save:{TOKEN}"},
        {"label": "Изменить", "payload": f"cb:plan:edit:{TOKEN}"},
    ]
    assert catalog.composed[0]["safety_state"] == "NORMAL"
    assert catalog.composed[0]["safety_policy_version"].startswith("pre_check-")


def test_e2_the_tap_saves_with_the_confirming_turns_own_revision(
    client: Client, tenant, wire, catalog: FakeCatalog
) -> None:
    _person_shell(tenant, PERSON)
    _ask(client, card.TRIGGER, as_user=PERSON)
    conversation_id = str(_global_threads(PERSON).get().id)
    composed_at = state_mod.peek_revision(conversation_id)
    assert composed_at is not None  # ход сборки открыл ревизию — без теневого контура

    answer = _ask(client, f"cb:plan:save:{TOKEN}", as_user=PERSON)

    assert answer.status_code == 200, answer.content[:300]
    assert answer.json()["answer"] == "PLAN_SAVED · тест"
    command = catalog.saved[0]
    revision = command["evaluated_at_revision"]
    assert isinstance(revision, int) and not isinstance(revision, bool)
    # Подтверждение опознаётся ревизией хода, где предложение ПОКАЗАНО, —
    # она одна на все нажатия; вердикт несёт ревизию хода НАЖАТИЯ.
    assert command["confirmation"]["state_revision"] == composed_at
    assert command["safety_state"] == "NORMAL"
    # Ревизия хода ПОДТВЕРЖДЕНИЯ: выше ревизии хода сборки, а не её повтор.
    assert revision > composed_at
    assert revision == state_mod.peek_revision(conversation_id)


def test_e3_an_unlisted_account_gets_no_card_and_the_catalog_is_not_asked(
    client: Client, tenant, wire, catalog: FakeCatalog, settings
) -> None:
    settings.SYNTHETIC_TEST_TRIGGER_ACCOUNTS = ("max:1",)
    _person_shell(tenant, PERSON)

    answer = _ask(client, card.TRIGGER, as_user=PERSON)

    assert answer.status_code == 200
    # Ход не пропал: человеку ответил обычный разговор (подменённый консьерж)…
    assert answer.json()["answer"] == "Какая услуга интересует?"
    # …а карточки плана в ответе нет, и каталог не спрашивали.
    assert card.QUESTION_SAVE not in answer.json()["answer"]
    assert catalog.composed == []


def test_e4_a_second_tap_through_the_real_turn_keeps_the_confirmation(
    client: Client, tenant, wire, catalog: FakeCatalog
) -> None:
    _person_shell(tenant, PERSON)
    _ask(client, card.TRIGGER, as_user=PERSON)

    _ask(client, f"cb:plan:save:{TOKEN}", as_user=PERSON)
    _ask(client, f"cb:plan:save:{TOKEN}", as_user=PERSON)

    one, two = catalog.saved
    assert one["confirmation"] == two["confirmation"]
    assert one["decision_id"] == two["decision_id"]
    # Ходы разные — и это видно по ревизии вердикта, а не подтверждения.
    assert two["evaluated_at_revision"] > one["evaluated_at_revision"]


def test_e5_removing_a_step_through_the_real_turn_and_saving_what_is_left(
    client: Client, tenant, wire, catalog: FakeCatalog
) -> None:
    """Шаг 3: тап по шагу → новая карточка → «Сохранить» — сквозь настоящий ход:
    пересборка и сохранение идут каждая с тройкой своего хода.

    Три запроса, не четыре: у ручки чата лимит на человека в минуту; меню
    «Изменить» держат узлы карточки.
    """
    _person_shell(tenant, PERSON)
    _ask(client, card.TRIGGER, as_user=PERSON)

    changed = _ask(client, f"cb:plan:drop:{TOKEN}:0", as_user=PERSON)
    assert changed.status_code == 200, changed.content[:300]
    body = changed.json()
    assert body["answer"].startswith("• Вечерняя прогулка")
    assert catalog.composed[1]["excluded_capability_refs"] == ["cap.sleep_routine"]

    saved = _ask(client, body["buttons"][0]["payload"], as_user=PERSON)
    assert saved.status_code == 200, saved.content[:300]
    assert saved.json()["answer"] == "PLAN_SAVED · тест"
    command = catalog.saved[0]
    assert [s["capability_ref"] for s in command["decision"]["steps"]] == ["cap.evening_walk"]
    # Подтверждение опознаётся ревизией показа НОВОЙ карточки; вердикт — своей.
    assert command["evaluated_at_revision"] > command["confirmation"]["state_revision"]
