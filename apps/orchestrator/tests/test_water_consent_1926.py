"""DRF-1926 — стакан воды без согласия на обработку личных данных не записывается.

Еда в чате закрыта воротами ``personal_records_consent_open``
(``apps/skills/food_clarify/text_entry.py``), вода — нет: «стакан воды» →
консьерж → ``log_water`` → ``WaterSkill.add_water`` без проверки согласия.
Правило то же, что у еды, и ответ человеку тот же — ``CONSENT_TEXT`` еды байт
в байт (главное окно 15.09).

Ход прогоняется через канал целиком (``handle_global_max_event``), подменены
только модель и клиент сервиса питания. Проверяется то, что ушло в MAX, и то,
дошёл ли вызов до записи. Обе половины на одних данных: без согласия записи
нет, с согласием — есть. Отказ без положительной стражи неотличим от «навык
не запускался».
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from apps.channels.max import handler as max_handler
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.integrations.ayla import WaterEntryResponse
from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge
from apps.orchestrator.memory import short_term
from apps.skills.food_clarify.text_entry import CONSENT_TEXT

# ``transaction=True``: ход консьержа пишет в БД из другого потока
# (``asyncio.run`` внутри ``generate_concierge_reply``).
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _onboarding_on(settings):
    settings.GLOBAL_BOT_ONBOARDING = True


@pytest.fixture(autouse=True)
def _no_chat_actions(monkeypatch):
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action",
        lambda **kwargs: {"ok": True},
    )


@pytest.fixture(autouse=True)
def _no_second_model_calls(monkeypatch):
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock())
    monkeypatch.setattr(max_handler, "maybe_weave_question", lambda _c, _b, reply: reply)


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def sent(monkeypatch):
    calls: list[str] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append(text)
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def water_writes(monkeypatch):
    """Клиент сервиса питания: каждый вызов записи — строка списка."""
    writes: list[dict] = []

    async def _add_water(**kwargs):
        writes.append(kwargs)
        return WaterEntryResponse(
            entry_id="e-1926",
            ml=250,
            water_ml=250,
            kcal=0,
            milestone_text=None,
            today_total_ml=250,
            today_norm_ml=None,
            alcohol_recovery_hint=False,
            raw={},
        )

    client = Mock()
    client.add_water = _add_water
    monkeypatch.setattr("apps.skills.water.skill.get_nutrition_client", lambda: client)
    return writes


def _model_calls_log_water(monkeypatch) -> None:
    provider = AsyncMock()
    provider.complete.return_value = CompletionResult(
        text="",
        tool_calls=[ToolCall(id="c1", name="log_water", arguments={"drink_text": "стакан воды"})],
        prompt_tokens=30,
        completion_tokens=6,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="tool_calls",
    )
    router = Mock()
    router.get_provider.return_value = provider
    monkeypatch.setattr(concierge, "get_router", lambda: router)


def _turn(user_id: int, *, consent: bool) -> None:
    from django.utils import timezone

    from apps.consent.services import record_global_consent

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), chat_id=str(user_id)
    )
    bot_user.welcomed_at = timezone.now()
    bot_user.save(update_fields=["welcomed_at"])
    if consent:
        record_global_consent(
            bot_user,
            consent_type="personal_data",
            source="test:drf1926",
            document_version="welcome-s2-v1",
        )
    max_handler.handle_global_max_event(
        {
            "update_type": "message_created",
            "timestamp": 1731320000000,
            "message": {
                "sender": {"user_id": user_id, "name": "Ирина"},
                "recipient": {"chat_id": user_id, "chat_type": "dialog"},
                "body": {"mid": f"m{user_id}", "seq": 1, "text": "стакан воды", "attachments": []},
            },
        }
    )


class TestWaterNeedsPersonalDataConsent:
    def test_without_consent_nothing_is_written_and_the_food_sentence_is_said(
        self, monkeypatch, sent, water_writes
    ):
        _model_calls_log_water(monkeypatch)

        _turn(71926, consent=False)

        assert water_writes == [], "запись воды ушла в сервис питания без согласия"
        assert sent, "человек не получил ответа"
        assert sent[-1] == CONSENT_TEXT

    def test_with_consent_the_glass_is_written_as_before(self, monkeypatch, sent, water_writes):
        _model_calls_log_water(monkeypatch)

        _turn(71927, consent=True)

        assert len(water_writes) == 1
        assert water_writes[0]["ml"] == 250
        assert sent and sent[-1].startswith("Записала 250 мл")
