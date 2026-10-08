"""DRF-2885 — свободная просьба составить план: инструмент модели консьержа.

Решение владельца 08.10: вход в план — кнопка и свободная просьба, без точной
кодовой фразы. Модель только ВЫБИРАЕТ инструмент ``compose_plan``; план
собирает каталог, а ответ человеку — карточка каталога, без второго прохода
модели.

* t1 — выбранный инструмент отвечает карточкой сборки, одним проходом, и
  сборке передан разговор ЭТОГО хода (с него берётся тройка безопасности);
* t2 — сборка ничего не вернула (механизм выключен) → плана в ответе нет;
* t3 — при выключенном механизме инструмента у модели нет вовсе;
* t4 — при включённом — есть.
"""

from __future__ import annotations

from typing import Any

from unittest.mock import AsyncMock, Mock

import pytest

from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge, plan_engine_card as card
from apps.orchestrator.concierge import generate_concierge_reply
from apps.skills.base import SkillResult

pytestmark = pytest.mark.django_db(transaction=True)


def _bot_user(prefix: str) -> Any:
    from apps.consent.services import record_global_consent
    from apps.identity.services import resolve_or_create_global_bot_user

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=f"{prefix}-uid", chat_id=f"{prefix}-chat"
    )
    record_global_consent(bot_user, source="welcome")
    return bot_user


def _conversation(bot_user: Any) -> Any:
    from apps.conversations.services import resolve_active_global_conversation

    return resolve_active_global_conversation(bot_user)


def _model(monkeypatch: pytest.MonkeyPatch, result: CompletionResult) -> tuple[AsyncMock, dict]:
    captured: dict[str, Any] = {}

    async def _complete(messages: Any, model: str = "", tools: Any = None, **kw: Any) -> Any:
        captured["tools"] = tools
        return result

    provider = AsyncMock()
    provider.complete.side_effect = _complete
    router = Mock()
    router.get_provider.return_value = provider
    monkeypatch.setattr(concierge, "get_router", lambda: router)
    return provider, captured


def _picks_the_tool() -> CompletionResult:
    return CompletionResult(
        text="", tool_calls=[ToolCall(id="t1", name="compose_plan", arguments={})]
    )


def test_t1_the_chosen_tool_answers_with_the_composed_card_in_one_pass(
    monkeypatch: pytest.MonkeyPatch, settings
) -> None:
    settings.PLAN_ENGINE_ENABLED = True
    provider, _ = _model(monkeypatch, _picks_the_tool())
    seen: dict[str, Any] = {}

    def _compose(*, bot_user: Any, conversation: Any, trace_id: str) -> SkillResult:
        seen["conversation"] = conversation
        return SkillResult(
            reply_text="• Режим сна",
            action_type="plan_engine_proposal",
            action_data={"buttons": [{"label": "Сохранить", "callback": "cb:plan:save:0f3a9c2e"}]},
        )

    monkeypatch.setattr(card, "compose_for_request", _compose)
    bot_user = _bot_user("plan-tool-1")
    conversation = _conversation(bot_user)

    reply = generate_concierge_reply(
        "помоги составить план", bot_user=bot_user, conversation=conversation
    )

    assert reply.text == "• Режим сна"
    assert (reply.action_data or {})["buttons"][0]["callback"] == "cb:plan:save:0f3a9c2e"
    assert seen["conversation"] is conversation
    assert provider.complete.await_count == 1


def test_t2_nothing_composed_means_no_plan_in_the_answer(
    monkeypatch: pytest.MonkeyPatch, settings
) -> None:
    settings.PLAN_ENGINE_ENABLED = True
    _model(monkeypatch, _picks_the_tool())
    monkeypatch.setattr(card, "compose_for_request", lambda **kw: None)
    bot_user = _bot_user("plan-tool-2")

    reply = generate_concierge_reply(
        "помоги составить план", bot_user=bot_user, conversation=_conversation(bot_user)
    )

    assert reply.text
    assert not (reply.action_data or {}).get("buttons")


@pytest.mark.parametrize("enabled", [False, True])
def test_t3_t4_the_model_sees_the_tool_only_with_the_engine_on(
    monkeypatch: pytest.MonkeyPatch, settings, enabled: bool
) -> None:
    settings.PLAN_ENGINE_ENABLED = enabled
    _, captured = _model(monkeypatch, CompletionResult(text="ok"))
    bot_user = _bot_user(f"plan-tool-3-{enabled}")

    generate_concierge_reply("привет", bot_user=bot_user, conversation=_conversation(bot_user))

    assert ("compose_plan" in {t["name"] for t in captured["tools"]}) is enabled
