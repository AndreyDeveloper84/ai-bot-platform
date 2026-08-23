"""DRF-1284 — the weekly nutrition picture on the LIVE global path.

``apps/orchestrator/tests/test_nutrition_context.py`` proves the block
builder. This file proves the wiring: that a real
``_handle_global_max_event_inner`` turn carries the block from consent
read to system prompt, and that the three failure shapes cost the
picture rather than the reply.

The LLM itself is stubbed at ``generate_concierge_reply`` — what is under
test is the handler, not the model — except in the prompt-assembly test,
which captures the prompt the real concierge would have rendered.
"""

from __future__ import annotations

import json
import uuid

import pytest

from apps.channels.handlers import GlobalMaxHandler
from apps.channels.max import handler as max_handler
from apps.consent.models import ConsentRecord
from apps.consent.services import record_global_consent
from apps.identity.models import BotUser
from apps.orchestrator.memory import short_term

pytestmark = pytest.mark.django_db

_USER_ID = 424242
_CHAT_ID = 515151


def _raw_entry(text: str) -> dict:
    payload = {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": _USER_ID, "name": "Марина"},
            "recipient": {"chat_id": _CHAT_ID, "chat_type": "dialog"},
            "body": {"mid": f"m-{uuid.uuid4()}", "seq": 1, "text": text, "attachments": []},
        },
    }
    return {
        "data": json.dumps(payload),
        "trace_id": str(uuid.uuid4()),
        "resolved_tenant_id": "",
    }


@pytest.fixture(autouse=True)
def _flag_on(settings):
    """DRF-1284 ships OFF; the wiring below is what an operator turns on."""
    settings.CONCIERGE_NUTRITION_CONTEXT_ENABLED = True


@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        max_handler,
        "send_message",
        lambda *, chat_id, text, attachments=None, timeout=10.0: (
            calls.append({"chat_id": chat_id, "text": text}) or {"ok": True}
        ),
    )
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def captured_turn(monkeypatch):
    """Stub the concierge and record the kwargs the handler handed it."""
    from apps.orchestrator.discovery import DiscoveryReply

    seen: dict = {}

    def _fake(message_text, **kw):
        seen.update(kw)
        seen["message_text"] = message_text
        return DiscoveryReply(text="Поняла, подберём мастера.")

    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", _fake)
    return seen


@pytest.fixture
def no_memory(monkeypatch):
    """Silence the neighbouring memory surface — it is not under test here."""
    monkeypatch.setattr(max_handler, "build_concierge_memory_block", lambda bot_user: "")
    monkeypatch.setattr(max_handler, "render_current_personal_context", lambda uid: "")


def _grant(user_id: int, *types: str) -> BotUser:
    bot_user = BotUser.all_tenants.get(channel="max", channel_user_id=str(user_id))
    for consent_type in types:
        record_global_consent(bot_user, consent_type=consent_type, source="test:drf1284")
    return bot_user


def _stub_ayla(monkeypatch, deficits=None, error: Exception | None = None):
    from types import SimpleNamespace

    async def _call(**kw):
        if error is not None:
            raise error
        return deficits

    monkeypatch.setattr(
        "apps.integrations.ayla.get_nutrition_client",
        lambda: SimpleNamespace(weekly_deficits=_call),
    )
    monkeypatch.setattr(
        "apps.integrations.ayla.external_user_id_for", lambda bot_user: f"max:{_USER_ID}"
    )


def _week(**kw):
    from types import SimpleNamespace

    base = {
        "days_observed": 6,
        "protein_avg_pct_goal": 58.0,
        "protein_low_streak_days": 4,
        "hint": "белка мало шестой день подряд",
        "fired_keys": ["protein_low"],
        "raw": {},
    }
    base.update(kw)
    return SimpleNamespace(**base)


# ─── the three turns ───────────────────────────────────────────────────────


def test_consented_user_with_records_gets_the_week_in_the_prompt(
    settings, monkeypatch, mock_send, fake_redis, captured_turn, no_memory
) -> None:
    """Positive turn: both bases open, Ayla has a week → it reaches the prompt."""
    GlobalMaxHandler()(_raw_entry("Привет"))
    _grant(
        _USER_ID,
        ConsentRecord.ConsentType.PERSONAL_DATA.value,
        ConsentRecord.ConsentType.HEALTH.value,
    )
    _stub_ayla(monkeypatch, deficits=_week())

    GlobalMaxHandler()(_raw_entry("Что мне съесть после тренировки?"))

    block = captured_turn["nutrition_block"]
    assert "белка мало шестой день подряд" in block
    assert "<<<UNTRUSTED_CONTEXT>>>" in block
    # And it survives all the way into the rendered system prompt.
    from apps.orchestrator.concierge import build_concierge_system_prompt

    assert block in build_concierge_system_prompt(nutrition_block=block)
    assert len(mock_send) == 2


def test_consented_user_without_records_gets_a_normal_turn(
    settings, monkeypatch, mock_send, fake_redis, captured_turn, no_memory
) -> None:
    """Negative turn: consent open, empty week → no block, no mention of emptiness."""
    GlobalMaxHandler()(_raw_entry("Привет"))
    _grant(
        _USER_ID,
        ConsentRecord.ConsentType.PERSONAL_DATA.value,
        ConsentRecord.ConsentType.HEALTH.value,
    )
    _stub_ayla(
        monkeypatch,
        deficits=_week(
            days_observed=0, protein_avg_pct_goal=None, protein_low_streak_days=0, hint=""
        ),
    )

    GlobalMaxHandler()(_raw_entry("Посоветуй мастера по маникюру"))

    assert captured_turn["nutrition_block"] == ""
    assert len(mock_send) == 2
    assert mock_send[-1]["text"]  # a real reply, not a refusal


def test_unconsented_user_never_reaches_ayla(
    settings, monkeypatch, mock_send, fake_redis, captured_turn, no_memory
) -> None:
    """Consent turn: no HEALTH basis → no picture, no wire call, turn passes."""
    GlobalMaxHandler()(_raw_entry("Привет"))
    _grant(_USER_ID, ConsentRecord.ConsentType.PERSONAL_DATA.value)

    called = {"n": 0}

    def _client():
        called["n"] += 1
        raise AssertionError("Ayla must not be called without HEALTH consent")

    monkeypatch.setattr("apps.integrations.ayla.get_nutrition_client", _client)

    GlobalMaxHandler()(_raw_entry("Что мне съесть после тренировки?"))

    assert captured_turn["nutrition_block"] == ""
    assert called["n"] == 0
    assert len(mock_send) == 2


# ─── degradation on the live path ──────────────────────────────────────────


def test_ayla_outage_costs_the_picture_not_the_reply(
    settings, monkeypatch, mock_send, fake_redis, captured_turn, no_memory
) -> None:
    from apps.integrations.ayla import NutritionUnavailableError

    GlobalMaxHandler()(_raw_entry("Привет"))
    _grant(
        _USER_ID,
        ConsentRecord.ConsentType.PERSONAL_DATA.value,
        ConsentRecord.ConsentType.HEALTH.value,
    )
    _stub_ayla(monkeypatch, error=NutritionUnavailableError("circuit_open"))

    GlobalMaxHandler()(_raw_entry("Подбери мастера"))

    assert captured_turn["nutrition_block"] == ""
    assert len(mock_send) == 2


def test_builder_exploding_costs_the_picture_not_the_reply(
    settings, monkeypatch, mock_send, fake_redis, captured_turn, no_memory
) -> None:
    """The handler's own net: even a builder that breaks its contract is absorbed.

    This runs after the idempotency key is claimed — a raise here would
    lose the reply on retry, not retry it.
    """
    GlobalMaxHandler()(_raw_entry("Привет"))
    monkeypatch.setattr(
        max_handler,
        "build_nutrition_context_block",
        lambda bot_user: (_ for _ in ()).throw(RuntimeError("contract broken")),
    )

    GlobalMaxHandler()(_raw_entry("Подбери мастера"))

    assert captured_turn["nutrition_block"] == ""
    assert len(mock_send) == 2


def test_rollback_flag_off_leaves_the_turn_untouched(
    settings, monkeypatch, mock_send, fake_redis, captured_turn, no_memory
) -> None:
    settings.CONCIERGE_NUTRITION_CONTEXT_ENABLED = False
    GlobalMaxHandler()(_raw_entry("Привет"))
    _grant(
        _USER_ID,
        ConsentRecord.ConsentType.PERSONAL_DATA.value,
        ConsentRecord.ConsentType.HEALTH.value,
    )
    monkeypatch.setattr(
        "apps.integrations.ayla.get_nutrition_client",
        lambda: (_ for _ in ()).throw(AssertionError("flag off must not call Ayla")),
    )

    GlobalMaxHandler()(_raw_entry("Что мне съесть?"))

    assert captured_turn["nutrition_block"] == ""
    assert len(mock_send) == 2
