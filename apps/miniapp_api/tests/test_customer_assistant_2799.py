# ruff: noqa: F811 — фикстуры набора согласий импортируются и принимаются параметрами
"""DRF-2799 — разговор с Ayla внутри Mini App: тот же глобальный ход, ответ в Mini App.

Решение владельца 06.10.2026: «диалог продолжается там, где начат». Два
жёстких требования главного окна и четыре гейта ручки:

* t1 — ответ приходит телом, а в MAX не уходит ничего (сеть не тронута);
* t2 — близнец: одна и та же фраза безопасности в MAX и в Mini App даёт ОДИН
  и тот же ответ — безопасность буквально та же, не копия;
* t3 — единая нить: ход пишется в глобальную ``Conversation`` человека, ту же,
  что у чата бота; второй нити не появляется; «Последняя тема» его видит;
* t4 — личность только из принципала: чужой id в теле игнорируется, чужой
  разговор не тронут;
* t5 — повтор того же запроса — не второй ход;
* t6 — квота на человека: сверх лимита 429 до хода;
* t7 — перехват адресный: отправка ДРУГОМУ получателю во время хода уходит
  в MAX, как обычно (уведомление оператору не глохнет);
* t8 — перепись исходящего в MAX: все вызовы HTTP к MAX API живут в
  ``outbound.py``, и каждая такая функция сначала спрашивает перехват;
* t9 — история: та же нить, отсечка отзыва согласия (DRF-2700).

Данные синтетические.
"""

from __future__ import annotations

import ast
import json
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.channels.max import handler as max_handler
from apps.channels.max import outbound
from apps.consent.models import ConsentRecord
from apps.conversations.models import Conversation, Message
from apps.identity.models import BotUser
from apps.miniapp_api.tests.test_customer_consents import (  # noqa: F401 — fixtures
    _bot_token,
    _init_data_header,
    _no_ayla_link,
    tenant,
)
from apps.orchestrator.memory import short_term
from apps.orchestrator.safety.gate import BLOCK_REPLY_TEXT, CRISIS_REPLY_TEXT

pytestmark = pytest.mark.django_db

PERSON = "2799100"
STRANGER = "2799200"


@pytest.fixture(autouse=True)
def _redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _no_intent_llm(monkeypatch):
    """Определитель намерения хода ходит в модель — как у узлов каналов
    (``apps/channels/tests/conftest.py``), глушим его. ``send_chat_action``
    НЕ глушим: его перехват — часть предмета."""
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock(return_value=None))


@pytest.fixture(autouse=True)
def _concierge(monkeypatch):
    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Какая услуга интересует?"))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    monkeypatch.setattr(max_handler, "generate_direct_show_masters_reply", spy)
    return spy


@pytest.fixture
def wire(monkeypatch):
    """Всё, что реально ушло бы в MAX API. Пусто = сеть не тронута."""
    sent: list[dict] = []

    def fake_post(url, *, headers=None, params=None, json=None, timeout=None):
        sent.append({"url": url, "params": params or {}, "json": json or {}})
        return SimpleNamespace(
            status_code=200, text="{}", json=lambda: {"message": {"body": {"mid": "m"}}}
        )

    monkeypatch.setattr(outbound.httpx, "post", fake_post)
    monkeypatch.setattr(outbound.httpx, "put", fake_post)
    return sent


def _person_shell(tenant, channel_user_id: str = PERSON) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
        chat_id=f"chat-{channel_user_id}",
        display_name="Анна",
    )


def _ask(client: Client, text: str, *, as_user: str = PERSON, **extra):
    return client.post(
        reverse("miniapp_api:customer_assistant_ask"),
        data=json.dumps({"text": text, **extra}),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header(as_user),
    )


def _max_turn(text: str, *, user_id: str = PERSON, mid: str = "mx-1") -> None:
    payload = {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": int(user_id), "name": "Анна"},
            "recipient": {"chat_id": int(user_id), "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }
    max_handler.handle_global_max_event(payload, trace_id=str(uuid.uuid4()))


def _global_threads(channel_user_id: str = PERSON):
    return Conversation.all_tenants.filter(
        bot_user__channel="max", bot_user__channel_user_id=channel_user_id
    )


# --------------------------------------------------------------------- #
def test_t1_the_answer_comes_back_in_the_body_and_nothing_goes_to_max(
    client: Client, tenant, wire
) -> None:
    _person_shell(tenant)

    response = _ask(client, "я думаю о суициде")

    assert response.status_code == 200, response.content[:200]
    assert response.json()["answer"] == CRISIS_REPLY_TEXT
    assert [s for s in wire if "/messages" in s["url"]] == []


@pytest.mark.parametrize(
    "phrase,expected",
    [("хочу умереть", CRISIS_REPLY_TEXT), ("посоветуйте ибупрофен от боли", BLOCK_REPLY_TEXT)],
)
def test_t2_the_same_phrase_gets_the_same_safety_answer_in_max_and_in_the_app(
    client: Client, tenant, wire, settings, phrase: str, expected: str
) -> None:
    _person_shell(tenant)
    _person_shell(tenant, STRANGER)

    _max_turn(phrase, user_id=STRANGER)
    in_max = [s["json"].get("text") for s in wire if "/messages" in s["url"]]
    assert in_max == [expected]

    in_app = _ask(client, phrase).json()["answer"]

    assert in_app == expected == in_max[0]


def test_t3_one_thread_for_the_bot_and_the_app(client: Client, tenant, wire) -> None:
    _person_shell(tenant)
    _max_turn("привет", mid="mx-t3")
    threads_before = _global_threads().count()
    thread = _global_threads().get()
    assert Message.all_tenants.filter(conversation=thread, role=Message.Role.USER).count() == 1

    _ask(client, "а что посоветуешь?")

    assert _global_threads().count() == threads_before == 1
    users = Message.all_tenants.filter(conversation=thread, role=Message.Role.USER)
    assert [m.content for m in users.order_by("created_at")] == ["привет", "а что посоветуешь?"]
    topic = client.get(
        reverse("miniapp_api:customer_last_topic"), HTTP_AUTHORIZATION=_init_data_header(PERSON)
    ).json()["last_topic"]
    assert topic is not None


def test_t4_the_person_is_the_verified_principal_not_the_body(client: Client, tenant, wire) -> None:
    _person_shell(tenant)
    _person_shell(tenant, STRANGER)
    _max_turn("привет", user_id=STRANGER, mid="mx-t4")
    stranger_before = Message.all_tenants.filter(conversation__in=_global_threads(STRANGER)).count()
    assert stranger_before > 0

    response = _ask(client, "а что посоветуешь?", channel_user_id=STRANGER, user_id=STRANGER)

    assert response.status_code == 200
    assert (
        Message.all_tenants.filter(conversation__in=_global_threads(STRANGER)).count()
        == stranger_before
    )
    assert Message.all_tenants.filter(
        conversation__in=_global_threads(PERSON), content="а что посоветуешь?"
    ).exists()


def test_t5_a_repeated_request_is_not_a_second_turn(client: Client, tenant, wire) -> None:
    _person_shell(tenant)
    request_id = str(uuid.uuid4())

    first = _ask(client, "привет", request_id=request_id)
    second = _ask(client, "привет", request_id=request_id)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"] == "duplicate_request"
    assert (
        Message.all_tenants.filter(
            conversation__in=_global_threads(), role=Message.Role.USER
        ).count()
        == 1
    )


def test_t6_over_the_quota_is_refused_before_the_turn(
    client: Client, tenant, wire, monkeypatch, _concierge
) -> None:
    from apps.miniapp_api import views_customer_assistant

    from django.core.cache import cache

    cache.clear()  # квота живёт в общем кэше; соседние узлы того же человека её тратят
    _person_shell(tenant)
    monkeypatch.setattr(views_customer_assistant, "ASK_PER_MINUTE", 2)
    assert views_customer_assistant.ASK_PER_MINUTE == 2

    codes = [_ask(client, f"вопрос {i}").status_code for i in range(3)]

    assert codes == [200, 200, 429]
    assert (
        Message.all_tenants.filter(
            conversation__in=_global_threads(), role=Message.Role.USER
        ).count()
        == 2
    )


def test_t7_capture_is_addressed_others_still_get_their_message(wire) -> None:
    from apps.channels.max.delivery_capture import capturing

    with capturing(chat_id="chat-a", user_id="111") as delivery:
        outbound.send_message(chat_id="chat-a", text="тебе")
        outbound.send_message(user_id="111", text="тебе тоже")
        outbound.send_message(user_id="999", text="оператору")

    assert [r["text"] for r in delivery.replies] == ["тебе", "тебе тоже"]
    assert [s["json"]["text"] for s in wire] == ["оператору"]


APPS = Path(outbound.__file__).resolve().parents[2]


def test_t8_every_max_api_call_on_the_turn_path_asks_the_capture_first() -> None:
    """Перепись: HTTP к MAX API — только в ``outbound.py``, и каждая такая функция
    спрашивает перехват. Новая прямая отправка мимо этих функций утекла бы в
    MAX живьём во время хода из Mini App — здесь она покраснеет."""
    reaching_max: list[str] = []
    for path in APPS.rglob("*.py"):
        rel = path.relative_to(APPS.parent).as_posix()
        if "/tests/" in rel or "/migrations/" in rel or path.name.startswith("test_"):
            continue
        source = path.read_text(encoding="utf-8")
        if "_api_base(" in source or "MAX_API_BASE" in source:
            reaching_max.append(rel)
    # Вне хода и потому вне перехвата: команда подписки вебхука (ручной запуск)
    # и задача наблюдаемости (по расписанию, не в запросе человека).
    assert sorted(reaching_max) == [
        "apps/channels/management/commands/max_subscribe_webhook.py",
        "apps/channels/max/outbound.py",
        "apps/observability/tasks.py",
    ], reaching_max

    tree = ast.parse(Path(outbound.__file__).read_text(encoding="utf-8"))
    http_functions: dict[str, bool] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        dump = ast.dump(node)
        if "attr='post'" in dump or "attr='put'" in dump:
            http_functions[node.name] = "delivery_capture" in ast.unparse(node)
    assert http_functions == {
        "send_message": True,
        "edit_message": True,
        "send_chat_action": True,
    }, http_functions


def test_t9_history_is_the_same_thread_after_the_consent_cutoff(
    client: Client, tenant, wire
) -> None:
    shell = _person_shell(tenant)
    asked = _ask(client, "первый вопрос")
    assert asked.status_code == 200, asked.content[:300]
    history_url = reverse("miniapp_api:customer_assistant_history")
    response = client.get(history_url, HTTP_AUTHORIZATION=_init_data_header(PERSON))
    assert response.status_code == 200, response.content[:300]
    before = response.json()
    assert "первый вопрос" in [m["content"] for m in before["messages"]], before

    record = ConsentRecord.all_tenants.create(
        tenant=shell.tenant,
        bot_user=shell,
        consent_type=ConsentRecord.ConsentType.PERSONAL_DATA,
        granted=True,
        source="test-2799",
    )
    ConsentRecord.all_tenants.filter(pk=record.pk).update(
        withdrawn_at=timezone.now() + timedelta(seconds=1)
    )

    after = client.get(history_url, HTTP_AUTHORIZATION=_init_data_header(PERSON)).json()
    assert "первый вопрос" not in [m["content"] for m in after["messages"]]
