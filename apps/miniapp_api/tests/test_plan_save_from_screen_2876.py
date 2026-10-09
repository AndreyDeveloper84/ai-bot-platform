# ruff: noqa: F811 — фикстуры берутся по имени из соседнего набора узлов
"""DRF-2876 — сохранить с экрана Mini App предложение, собранное в чате.

    GET  /customer/plan/current   → поле ``draft``
    POST /customer/plan/save      {token}

Задание владельца (§9): «Mini App должен уметь сохранять без искусственного
сообщения „сохрани“ в чат». Предложение у чата и экрана общее — оно лежит в
состоянии разговора; экран его показывает и сохраняет с вердиктом ПОСЛЕДНЕГО
хода разговора.

Здесь всё настоящее, кроме каталога: ход чата Mini App собирает предложение,
экранные ручки его читают и сохраняют.

Показ
* d1 — предложение из чата приходит экрану: опознаватель и слова каталога;
* d2 — ни ключей способностей, ни идентификаторов решения в нём нет;
* d3 — предложения нет — ``draft: null``;
* d4 — под гейтом согласия предложение не отдаётся, а ручка отвечает 200.

Сохранение
* s1 — сохраняется показанное: то же решение, что ушло бы из чата, с
  ревизией показа и вердиктом последнего хода;
* s2 — повторное нажатие шлёт ту же команду (ключ подтверждения тот же);
* s3 — чужой или устаревший опознаватель ничего не сохраняет;
* s4 — нет вердикта последнего хода — каталог не спрошен;
* s5 — под гейтом согласия — отказ до каталога;
* s6 — после «убрать шаг» в чате экран сохраняет НОВОЕ предложение, старый
  опознаватель устарел;
* s7 — опознаватель только из восьми шестнадцатеричных знаков;
* s8 — флаг выключен: 404.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.integrations.ayla import plan_engine_client as client_mod
from apps.miniapp_api import views_plan_engine as views
from apps.miniapp_api.tests.test_customer_assistant_2799 import (  # noqa: F401 — fixtures
    _ask,
    _bot_token,
    _concierge,
    _global_threads,
    _init_data_header,
    _no_ayla_link,
    _no_intent_llm,
    _person_shell,
    _redis,
    tenant,
    wire,
)
from apps.orchestrator import plan_engine_card as card
from apps.orchestrator import plan_gate
from apps.orchestrator.decision_readiness import state as state_mod
from apps.orchestrator.decision_readiness.tests.fakes import FakeRedis
from apps.orchestrator.tests.test_plan_engine_card_2885 import DECISION_ID, TOKEN, FakeCatalog

pytestmark = pytest.mark.django_db

#: Счётчик людей: у ручки чата лимит вопросов на человека в минуту, а кеш
#: лимита живёт дольше узла — каждому узлу свой человек.
_PEOPLE = iter(range(2876201, 2876999))


@pytest.fixture
def person(settings) -> str:
    value = str(next(_PEOPLE))
    settings.SYNTHETIC_TEST_TRIGGER_ACCOUNTS = (f"max:{value}",)
    return value


@pytest.fixture(autouse=True)
def catalog(monkeypatch: pytest.MonkeyPatch) -> FakeCatalog:
    fake = FakeCatalog()
    monkeypatch.setattr(client_mod, "PlanEngineHttpClient", lambda: fake)
    monkeypatch.setattr(views, "PlanEngineHttpClient", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _state_store(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeRedis()
    monkeypatch.setattr(state_mod, "_redis_client", lambda: store)


@pytest.fixture(autouse=True)
def _on(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.PLAN_ENGINE_ENABLED = True
    settings.DRE_SHADOW_ENABLED = False
    # Человек этих узлов — с основанием; сам гейт держит test_plan_basis_gate_2967.
    monkeypatch.setattr(plan_gate, "plan_processing_refusal", lambda bot_user: None)
    monkeypatch.setattr(views, "plan_processing_refusal", lambda bot_user: None)


def _closed(monkeypatch: pytest.MonkeyPatch, name: str = "PLAN_CONSENT_REQUIRED") -> None:
    monkeypatch.setattr(plan_gate, "plan_processing_refusal", lambda bot_user: name)
    monkeypatch.setattr(views, "plan_processing_refusal", lambda bot_user: name)


def _current(client: Client, person: str):
    return client.get(
        reverse("miniapp_api:customer_plan_current"), HTTP_AUTHORIZATION=_init_data_header(person)
    )


def _save(client: Client, token: Any, person: str):
    return client.post(
        reverse("miniapp_api:customer_plan_save"),
        data=json.dumps({"token": token}),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header(person),
    )


def _composed_in_chat(client: Client, tenant, person: str) -> None:
    _person_shell(tenant, person)
    answer = _ask(client, card.TRIGGER, as_user=person)
    assert answer.status_code == 200, answer.content[:300]


# ─── показ ───────────────────────────────────────────────────────────────


def test_d1_the_proposal_composed_in_the_chat_reaches_the_screen(
    client: Client, tenant, wire, person: str, catalog: FakeCatalog
) -> None:
    _composed_in_chat(client, tenant, person)

    resp = _current(client, person)

    assert resp.status_code == 200, resp.content[:300]
    assert resp.json()["draft"] == {
        "token": TOKEN,
        "steps": [
            {"label": "Режим сна", "why": None},
            {"label": "Вечерняя прогулка", "why": None},
        ],
    }


def test_d2_no_capability_keys_or_decision_ids_reach_the_screen(
    client: Client, tenant, wire, person: str, catalog: FakeCatalog
) -> None:
    _composed_in_chat(client, tenant, person)

    body = json.dumps(_current(client, person).json(), ensure_ascii=False)

    assert "Режим сна" in body  # положительный контроль: предложение в ответе есть
    assert "cap." not in body
    assert DECISION_ID not in body


def test_d3_nothing_composed_no_draft(client: Client, tenant, wire, person: str) -> None:
    _person_shell(tenant, person)

    resp = _current(client, person)

    assert resp.status_code == 200
    assert resp.json()["draft"] is None


@pytest.mark.parametrize(
    "refusal", ["PLAN_DELETION_REQUESTED", "PLAN_CONSENT_REQUIRED", "PLAN_BASIS_UNAVAILABLE"]
)
def test_d4_under_the_consent_gate_the_draft_is_withheld_and_reading_stays_open(
    client: Client, tenant, wire, person: str, catalog: FakeCatalog, monkeypatch, refusal: str
) -> None:
    _composed_in_chat(client, tenant, person)
    assert _current(client, person).json()["draft"] is not None  # до отказа оно показывалось
    _closed(monkeypatch, refusal)

    resp = _current(client, person)

    assert resp.status_code == 200
    assert resp.json()["draft"] is None


# ─── сохранение ──────────────────────────────────────────────────────────


def test_s1_the_shown_proposal_is_saved_with_the_last_turns_verdict(
    client: Client, tenant, wire, person: str, catalog: FakeCatalog
) -> None:
    _composed_in_chat(client, tenant, person)
    shown_at = catalog.composed[0]  # ход показа записал вердикт; ревизия — в состоянии

    resp = _save(client, TOKEN, person)

    assert resp.status_code == 200, resp.content[:300]
    assert resp.json() == {"saved": True}
    command = catalog.saved[0]
    assert command["decision_id"] == DECISION_ID
    assert command["safety_state"] == shown_at["safety_state"] == "NORMAL"
    # Нажатие на экране хода не открывает: вердикт и ревизия — последнего
    # хода чата, то есть хода показа.
    assert command["evaluated_at_revision"] == command["confirmation"]["state_revision"]


def test_s2_a_second_tap_sends_the_same_confirmation(
    client: Client, tenant, wire, person: str, catalog: FakeCatalog
) -> None:
    _composed_in_chat(client, tenant, person)

    first = _save(client, TOKEN, person)
    second = _save(client, TOKEN, person)

    assert (first.status_code, second.status_code) == (200, 200)
    assert len(catalog.saved) == 2
    assert catalog.saved[0]["confirmation"] == catalog.saved[1]["confirmation"]
    assert catalog.saved[0]["decision"] == catalog.saved[1]["decision"]


def test_s2_what_is_saved_is_no_longer_offered_for_saving(
    client: Client, tenant, wire, person: str, catalog: FakeCatalog
) -> None:
    """После сохранения экран не предлагает сохранить то же ещё раз — а
    повторное нажатие старой кнопки (чат или экран) по-прежнему не ошибка."""
    _composed_in_chat(client, tenant, person)
    assert _current(client, person).json()["draft"] is not None

    _save(client, TOKEN, person)

    assert _current(client, person).json()["draft"] is None
    again = _save(client, TOKEN, person)
    assert again.status_code == 200


def test_s2_saved_in_the_chat_is_not_offered_on_the_screen(
    client: Client, tenant, wire, person: str, catalog: FakeCatalog
) -> None:
    _composed_in_chat(client, tenant, person)
    assert _current(client, person).json()["draft"] is not None

    _ask(client, f"cb:plan:save:{TOKEN}", as_user=person)

    assert _current(client, person).json()["draft"] is None


def test_s3_a_foreign_or_stale_token_saves_nothing(
    client: Client, tenant, wire, person: str, catalog: FakeCatalog
) -> None:
    _composed_in_chat(client, tenant, person)

    resp = _save(client, "ffffffff", person)

    assert resp.status_code == 409
    assert resp.json()["error"] == "plan_proposal_expired"
    assert catalog.saved == []


def test_s4_without_a_last_turn_verdict_the_catalog_is_not_asked(
    client: Client, tenant, wire, person: str, catalog: FakeCatalog, monkeypatch
) -> None:
    _composed_in_chat(client, tenant, person)
    monkeypatch.setattr(views, "last_turn_safety_for", lambda bot_user: None)

    resp = _save(client, TOKEN, person)

    assert resp.status_code == 409
    assert resp.json()["error"] == "plan_safety_unavailable"
    assert catalog.saved == []


@pytest.mark.parametrize(
    ("refusal", "status"),
    [
        ("PLAN_DELETION_REQUESTED", 423),
        ("PLAN_CONSENT_REQUIRED", 403),
        ("PLAN_BASIS_UNAVAILABLE", 503),
    ],
)
def test_s5_under_the_consent_gate_nothing_is_saved(
    client: Client,
    tenant,
    wire,
    person: str,
    catalog: FakeCatalog,
    monkeypatch,
    refusal: str,
    status: int,
) -> None:
    _composed_in_chat(client, tenant, person)
    _closed(monkeypatch, refusal)

    resp = _save(client, TOKEN, person)

    assert resp.status_code == status
    assert catalog.saved == []


def test_s5_the_core_itself_refuses_before_reading_the_proposal(
    client: Client, tenant, wire, person: str, catalog: FakeCatalog, monkeypatch
) -> None:
    """Гейт в ядре — первым: без основания с ЧУЖИМ опознавателем отказ гейта,
    а не «устарело» (условие автора гейта)."""
    _composed_in_chat(client, tenant, person)
    conversation = views._person_conversation(
        __import__("apps.identity.models", fromlist=["BotUser"]).BotUser.all_tenants.get(
            channel_user_id=person, tenant=tenant
        )
    )
    monkeypatch.setattr(
        plan_gate, "plan_processing_refusal", lambda bot_user: "PLAN_CONSENT_REQUIRED"
    )

    outcome = card.save_pending(
        bot_user=conversation.bot_user,
        conversation=conversation,
        token="ffffffff",
        safety=None,
        trace_id="t",
    )

    assert outcome.name == "PLAN_CONSENT_REQUIRED"
    assert catalog.saved == []


def test_s6_after_a_step_is_removed_in_the_chat_the_screen_saves_the_new_proposal(
    client: Client, tenant, wire, person: str, catalog: FakeCatalog
) -> None:
    _composed_in_chat(client, tenant, person)
    _ask(client, f"cb:plan:drop:{TOKEN}:0", as_user=person)
    draft = _current(client, person).json()["draft"]
    assert [s["label"] for s in draft["steps"]] == ["Вечерняя прогулка"]
    assert draft["token"] != TOKEN

    stale = _save(client, TOKEN, person)
    fresh = _save(client, draft["token"], person)

    assert stale.status_code == 409
    assert fresh.status_code == 200
    assert len(catalog.saved) == 1
    assert [s["capability_ref"] for s in catalog.saved[0]["decision"]["steps"]] == [
        "cap.evening_walk"
    ]


@pytest.mark.parametrize("token", [None, "", "0F3A9C2E", "0f3a9c2", "0f3a9c2e0", 7, "../../etc"])
def test_s7_only_a_card_token_is_accepted(
    client: Client, tenant, wire, person: str, catalog: FakeCatalog, token: Any
) -> None:
    _composed_in_chat(client, tenant, person)

    resp = _save(client, token, person)

    assert resp.status_code == 400
    assert resp.json()["error"] == "malformed"
    assert catalog.saved == []


def test_s8_switched_off_nothing_is_saved(
    client: Client, tenant, wire, person: str, catalog: FakeCatalog, settings
) -> None:
    _composed_in_chat(client, tenant, person)
    settings.PLAN_ENGINE_ENABLED = False

    resp = _save(client, TOKEN, person)

    assert resp.status_code == 404
    assert resp.json()["error"] == "plan_engine_disabled"
    assert catalog.saved == []
