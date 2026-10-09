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
FREE_ASKER = "2885106"


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
        {"label": "Обсудить", "payload": f"cb:plan:discuss:{TOKEN}"},
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


def test_e6_a_free_request_composes_with_the_triple_of_the_real_turn(
    client: Client, tenant, wire, catalog: FakeCatalog, _concierge, settings
) -> None:
    """Настоящий вход: модель выбрала инструмент — сборка берёт тройку с
    разговора, который ход передал консьержу. Аккаунт вне отладочного списка.

    Консьерж подменён на месте выбора инструмента; всё до него (ворота,
    ревизия хода, источник тройки на разговоре) — настоящее.
    """
    from apps.orchestrator.discovery import DiscoveryReply

    settings.SYNTHETIC_TEST_TRIGGER_ACCOUNTS = ()

    def _model_picked_the_tool(text: str, *, bot_user: Any, conversation: Any, **kw: Any) -> Any:
        result = card.compose_for_request(
            bot_user=bot_user, conversation=conversation, trace_id="t"
        )
        assert result is not None
        return DiscoveryReply(text=result.reply_text, action_data=result.action_data)

    _concierge.side_effect = _model_picked_the_tool
    # Свой человек: у ручки чата лимит вопросов на человека в минуту.
    _person_shell(tenant, FREE_ASKER)

    answer = _ask(client, "помоги составить план", as_user=FREE_ASKER)

    assert answer.status_code == 200, answer.content[:300]
    assert answer.json()["answer"].startswith("• Режим сна")
    assert len(catalog.composed) == 1
    assert catalog.composed[0]["safety_state"] == "NORMAL"
    assert catalog.composed[0]["safety_policy_version"].startswith("pre_check-")


def _last_turn(person: str) -> Any:
    from apps.conversations.models import Conversation
    from apps.orchestrator.safety.plan_turn import last_turn_safety

    conversation = Conversation.all_tenants.filter(bot_user__channel_user_id=person).latest("id")
    return last_turn_safety(conversation.id)


def test_e7_every_turn_leaves_its_verdict_for_the_plan_screen(
    client: Client, tenant, wire, catalog: FakeCatalog
) -> None:
    """Сохранение с экрана несёт вердикт ПОСЛЕДНЕГО хода: обычный ход (не
    плановый) записан, а оборванный воротами ход после него — «стоп»."""
    person = "2885107"
    _person_shell(tenant, person)

    _ask(client, "привет", as_user=person)
    normal = _last_turn(person)
    assert normal is not None and normal.safety_state == "NORMAL"

    _ask(client, "я не хочу жить", as_user=person)
    stopped = _last_turn(person)

    assert stopped is not None and stopped.safety_state == "STOP"
    assert stopped.evaluated_at_revision > normal.evaluated_at_revision


def test_e8_a_blocked_persons_turn_is_recorded_before_the_turn_ends(
    client: Client, tenant, wire, catalog: FakeCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ход заблокированного человека кончается раньше обычного пути — и всё
    равно несёт вердикт: иначе экран прочёл бы вердикт хода ДО блокировки."""
    from django.utils import timezone

    from apps.channels.max import handler as max_handler

    person = "2885108"
    _person_shell(tenant, person)
    _ask(client, "привет", as_user=person)
    before = _last_turn(person)
    assert before is not None

    monkeypatch.setattr(max_handler, "blocked_since", lambda **kw: timezone.now())
    _ask(client, "привет ещё раз", as_user=person)
    after = _last_turn(person)

    assert after is not None
    assert after.evaluated_at_revision > before.evaluated_at_revision


def test_e9_a_turn_under_an_operator_is_recorded_before_the_turn_ends(
    client: Client, tenant, wire, catalog: FakeCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ход под оператором кончается молчанием бота — и всё равно несёт вердикт
    (сторож перестановки: запись стоит выше этого выхода)."""
    from apps.channels.max import handler as max_handler

    person = "2885109"
    _person_shell(tenant, person)
    _ask(client, "привет", as_user=person)
    before = _last_turn(person)
    assert before is not None

    monkeypatch.setattr(max_handler, "global_handoff_muted", lambda **kw: True)
    monkeypatch.setattr(max_handler, "notify_silence", lambda **kw: None)
    _ask(client, "привет ещё раз", as_user=person)
    after = _last_turn(person)

    assert after is not None
    assert after.evaluated_at_revision > before.evaluated_at_revision


def test_e10_discussing_through_the_real_turn_gives_the_model_the_plan(
    client: Client, tenant, wire, catalog: FakeCatalog, _concierge, settings
) -> None:
    """«Обсудить» → дословная реплика; следующий свободный ход идёт модели, и
    разговор этого хода несёт открытое обсуждение с шагами плана."""
    from apps.orchestrator.discovery import DiscoveryReply

    person = "2885110"
    _person_shell(tenant, person)
    seen: dict[str, Any] = {}

    def _model(text: str, *, bot_user: Any, conversation: Any, **kw: Any) -> Any:
        seen["block"] = card.render_plan_discussion_block(conversation)
        seen["removable"] = card.discussion_allows_removal(conversation)
        return DiscoveryReply(text="ок")

    _concierge.side_effect = _model
    settings.SYNTHETIC_TEST_TRIGGER_ACCOUNTS = (f"max:{person}",)
    _ask(client, card.TRIGGER, as_user=person)

    opened = _ask(client, f"cb:plan:discuss:{TOKEN}", as_user=person)
    assert opened.status_code == 200, opened.content[:300]
    assert opened.json()["answer"] == "Давай обсудим твой план. Что хочешь изменить или уточнить?"

    _ask(client, "а зачем мне прогулка?", as_user=person)

    assert "1. Режим сна" in seen["block"]
    assert seen["removable"] is True


def test_e11_saving_over_an_active_plan_asks_and_replaces_through_the_real_turn(
    client: Client, tenant, wire, catalog: FakeCatalog, settings
) -> None:
    """У цели уже действует план: «Сохранить» задаёт вопрос владельца, «Заменить
    план» шлёт замену с тройкой СВОЕГО хода — сквозь настоящий ход."""
    person = "2885111"
    settings.SYNTHETIC_TEST_TRIGGER_ACCOUNTS = (f"max:{person}",)
    _person_shell(tenant, person)
    catalog.active_plan_id = "5a5a5a5a-1111-4222-8333-999999999999"
    _ask(client, card.TRIGGER, as_user=person)

    asked = _ask(client, f"cb:plan:save:{TOKEN}", as_user=person)
    assert asked.status_code == 200, asked.content[:300]
    body = asked.json()
    assert body["answer"] == "Заменить текущий план новым? Прежний останется в истории"
    assert [b["label"] for b in body["buttons"]] == ["Заменить план", "Оставить текущий"]
    assert catalog.replaced == []

    done = _ask(client, body["buttons"][0]["payload"], as_user=person)
    assert done.json()["answer"] == "PLAN_REPLACED · тест"
    sent = catalog.replaced[0]
    assert sent["replaces_plan_id"] == "5a5a5a5a-1111-4222-8333-999999999999"
    # Замена несёт вердикт хода нажатия «Заменить план», а не хода сохранения.
    assert sent["evaluated_at_revision"] > catalog.saved[0]["evaluated_at_revision"]
