"""Mini App goal gate — the [OD-BOT §164] G4 question on `goal_text`,
anketa `answer.text` and `safety_answer`, over HTTP.

The Mini App reads and writes the SAME persisted state as the chat surfaces
(``Conversation.skill_state`` resolved from the bot_user). While the question
is open nothing is forwarded to Ayla: an answer is routed by ``route_g4_reply``
— a positive / recent-resolved sign → the emergency stop; «нет», «не знаю»,
anything else → the same single question again. A plain «нет» never becomes
clearance. The crisis / block gate still runs first on the answer.

Technical checks only. Implementation of registered owner policy does not
constitute CLINICAL APPROVED, PHYSICIAN PASS, or SAFE FOR PILOT.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from unittest.mock import Mock, patch

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.conversations.services import close_conversation, resolve_conversation_for_bot_user
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.integrations.ayla.goals_client import reset_goals_circuit
from apps.miniapp_api.health_gate import (
    KIND_CLARIFY,
    KIND_CRISIS,
    KIND_RED_FLAG,
    KIND_STATE_UNAVAILABLE,
    SAFETY_ANSWER_FIELD,
    STATE_UNAVAILABLE_TEXT,
)
from apps.orchestrator.safety.gate import CRISIS_REPLY_TEXT
from apps.orchestrator.safety.medical_emergency import MEDICAL_EMERGENCY_TEXT_V2
from apps.orchestrator.safety.s1_restriction import restriction
from apps.skills.base import SkillContext
from apps.skills.health_screening.g4_question import G4_ROUTING_QUESTION, g4_pending
from apps.skills.health_screening.skill import HealthScreeningSkill
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)

BOT_TOKEN = "123456:test-bot-token"  # noqa: S105  # pragma: allowlist secret
_DOC: dict[str, object] = {
    "goal": None,
    "questions": [],
    "suggestions": [],
    "intents": [],
    "next": None,
}

AMBIGUOUS = "немеет рука иногда"
G4_FRAME = {"kind": KIND_CLARIFY, "questions": [G4_ROUTING_QUESTION]}


def _sign(params: dict[str, str]) -> str:
    from urllib.parse import urlencode

    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest})


def _auth(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Ольга"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.AYLA_INTERNAL_API_TOKEN = "test-service-token"  # noqa: S105  # pragma: allowlist secret
    reset_goals_circuit()
    cache.clear()
    yield
    cache.clear()
    reset_goals_circuit()


@pytest.fixture
def bot_user(db, settings) -> BotUser:
    tenant = Tenant.objects.create(slug="gate-g4q", name="Gate G4", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = tenant.slug
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="16400", display_name="Ольга"
    )


def _post(client: Client, bot_user: BotUser, body: dict) -> tuple[int, dict, list[dict]]:
    forwarded: list[dict] = []

    def _fake(*, external_user_id: str, payload: dict) -> dict:
        forwarded.append(payload)
        return _DOC

    with patch("apps.integrations.ayla.goals_client.post_goal_select", side_effect=_fake):
        resp = client.post(
            reverse("miniapp_api:customer_goal_select"),
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id),
        )
    return resp.status_code, resp.json(), forwarded


def _goal(text: str, **extra: object) -> dict:
    return {"goal_text": text, "source_channel": "miniapp", **extra}


def _pending(bot_user: BotUser) -> bool:
    """The durable restriction on the identity (re-read from the DB)."""

    fresh = BotUser.all_tenants.get(pk=bot_user.pk)
    return restriction(fresh) is not None


def _question_open(bot_user: BotUser) -> bool:
    conversation = resolve_conversation_for_bot_user(bot_user)
    return conversation is not None and g4_pending(conversation)


class TestTheQuestionIsAsked:
    def test_ambiguous_goal_text_asks_the_one_question_and_forwards_nothing(
        self, client: Client, bot_user: BotUser
    ) -> None:
        status, out, forwarded = _post(client, bot_user, _goal(AMBIGUOUS))
        assert status == 200
        assert out["safety"] == G4_FRAME
        assert forwarded == []
        assert _pending(bot_user) is True

    def test_ambiguous_anketa_answer_text_asks_too(self, client: Client, bot_user: BotUser) -> None:
        body = {"answer": {"key": "free", "text": AMBIGUOUS}, "source_channel": "miniapp"}
        status, out, forwarded = _post(client, bot_user, body)
        assert status == 200
        assert out["safety"] == G4_FRAME
        assert forwarded == []
        assert _pending(bot_user) is True

    def test_explicit_g4_goal_text_is_stop_without_a_question(
        self, client: Client, bot_user: BotUser
    ) -> None:
        status, out, forwarded = _post(client, bot_user, _goal("Внезапно перекосило лицо"))
        assert out["safety"] == {"kind": KIND_RED_FLAG, "text": MEDICAL_EMERGENCY_TEXT_V2}
        assert forwarded == []
        assert _pending(bot_user) is False


class TestTheAnswerIsRoutedNotTrustedAsClearance:
    @pytest.mark.parametrize("answer", ("нет", "не знаю", "хочу просто массаж спины"))
    def test_no_unknown_or_anything_else_keeps_the_question_and_forwards_nothing(
        self, client: Client, bot_user: BotUser, answer: str
    ) -> None:
        _post(client, bot_user, _goal(AMBIGUOUS))

        status, out, forwarded = _post(
            client, bot_user, _goal(AMBIGUOUS, **{SAFETY_ANSWER_FIELD: answer})
        )
        assert status == 200
        assert out["safety"] == G4_FRAME
        assert forwarded == []  # a «нет» is not clearance: nothing continues
        assert _pending(bot_user) is True

    def test_answer_text_while_the_question_is_open_is_not_clearance_either(
        self, client: Client, bot_user: BotUser
    ) -> None:
        _post(client, bot_user, _goal(AMBIGUOUS))
        body = {
            "answer": {"key": "free", "text": "нет, всё нормально"},
            "source_channel": "miniapp",
        }
        status, out, forwarded = _post(client, bot_user, body)
        assert out["safety"] == G4_FRAME
        assert forwarded == []
        assert _pending(bot_user) is True

    @pytest.mark.parametrize(
        "answer", ("да, внезапно онемела правая сторона", "речь нарушилась, но уже прошло")
    )
    def test_positive_or_recent_resolved_answer_is_the_emergency_stop(
        self, client: Client, bot_user: BotUser, answer: str
    ) -> None:
        _post(client, bot_user, _goal(AMBIGUOUS))

        status, out, forwarded = _post(
            client, bot_user, _goal(AMBIGUOUS, **{SAFETY_ANSWER_FIELD: answer})
        )
        assert out["safety"] == {"kind": KIND_RED_FLAG, "text": MEDICAL_EMERGENCY_TEXT_V2}
        assert forwarded == []
        # the question is answered; the restriction stays, now as a durable STOP
        assert _question_open(bot_user) is False
        rec = restriction(BotUser.all_tenants.get(pk=bot_user.pk))
        assert rec is not None and rec.status == "stop" and rec.group == "G4"

    def test_g6_answer_is_the_emergency_stop(self, client: Client, bot_user: BotUser) -> None:
        _post(client, bot_user, _goal(AMBIGUOUS))
        status, out, forwarded = _post(
            client, bot_user, _goal(AMBIGUOUS, **{SAFETY_ANSWER_FIELD: "после крема опухли губы"})
        )
        assert out["safety"] == {"kind": KIND_RED_FLAG, "text": MEDICAL_EMERGENCY_TEXT_V2}
        assert forwarded == []

    def test_crisis_answer_takes_the_crisis_route(self, client: Client, bot_user: BotUser) -> None:
        _post(client, bot_user, _goal(AMBIGUOUS))
        status, out, forwarded = _post(
            client, bot_user, _goal(AMBIGUOUS, **{SAFETY_ANSWER_FIELD: "я думаю о суициде"})
        )
        assert out["safety"] == {"kind": KIND_CRISIS, "text": CRISIS_REPLY_TEXT}
        assert forwarded == []


class TestOneStateAcrossSurfaces:
    def test_the_chat_skill_sees_the_question_the_mini_app_asked(
        self, client: Client, bot_user: BotUser
    ) -> None:
        _post(client, bot_user, _goal(AMBIGUOUS))
        conversation = resolve_conversation_for_bot_user(bot_user)
        assert conversation is not None and g4_pending(conversation)

        skill = HealthScreeningSkill()
        context = SkillContext(conversation=conversation, bot_user=bot_user, message_text="нет")
        assert skill.matches(context) is True
        assert skill.handle(context).meta["reply_kind"] == "health_restriction_persists"

    def test_the_mini_app_sees_the_question_the_chat_asked(
        self, client: Client, bot_user: BotUser
    ) -> None:
        conversation = resolve_conversation_for_bot_user(bot_user, create_if_missing=True)
        assert conversation is not None
        HealthScreeningSkill().handle(
            SkillContext(conversation=conversation, bot_user=bot_user, message_text=AMBIGUOUS)
        )

        status, out, forwarded = _post(client, bot_user, _goal("хочу массаж спины"))
        assert out["safety"] == G4_FRAME
        assert forwarded == []

    def test_a_mock_bot_user_without_a_carrier_never_500s(self) -> None:
        """Unit callers pass Mocks; the gate must degrade to «no state», not raise."""

        from apps.miniapp_api.health_gate import screen_goal_body

        stop, forward = screen_goal_body(Mock(pk=1, tenant=None), {"goal_text": "болит спина"})
        assert stop is not None and stop.kind == KIND_CLARIFY
        assert SAFETY_ANSWER_FIELD not in forward


class TestFailClosedOnStateFailure:
    """The safety state cannot be read, created or written → the request is
    blocked with the `safety_state_unavailable` frame; nothing is forwarded;
    the next request after the failure is screened again from scratch."""

    def test_lookup_failure_blocks_and_forwards_nothing(
        self, client: Client, bot_user: BotUser
    ) -> None:
        with patch(
            "apps.conversations.services.resolve_conversation_for_bot_user",
            side_effect=RuntimeError("db down"),
        ):
            status, out, forwarded = _post(client, bot_user, _goal("хочу массаж спины"))
        assert status == 200
        assert out["safety"] == {"kind": KIND_STATE_UNAVAILABLE, "text": STATE_UNAVAILABLE_TEXT}
        assert forwarded == []

    def test_lookup_failure_never_weakens_an_explicit_stop(
        self, client: Client, bot_user: BotUser
    ) -> None:
        with patch(
            "apps.conversations.services.resolve_conversation_for_bot_user",
            side_effect=RuntimeError("db down"),
        ):
            status, out, forwarded = _post(client, bot_user, _goal("Внезапно перекосило лицо"))
        assert out["safety"] == {"kind": KIND_RED_FLAG, "text": MEDICAL_EMERGENCY_TEXT_V2}
        assert forwarded == []

    def test_lookup_failure_never_weakens_the_crisis_route(
        self, client: Client, bot_user: BotUser
    ) -> None:
        with patch(
            "apps.conversations.services.resolve_conversation_for_bot_user",
            side_effect=RuntimeError("db down"),
        ):
            status, out, forwarded = _post(client, bot_user, _goal("я думаю о суициде"))
        assert out["safety"] == {"kind": KIND_CRISIS, "text": CRISIS_REPLY_TEXT}
        assert forwarded == []

    def test_creation_failure_on_an_ambiguous_goal_blocks(
        self, client: Client, bot_user: BotUser
    ) -> None:
        with patch(
            "apps.conversations.services.resolve_active_conversation",
            side_effect=RuntimeError("cannot create"),
        ):
            status, out, forwarded = _post(client, bot_user, _goal(AMBIGUOUS))
        assert out["safety"] == {"kind": KIND_STATE_UNAVAILABLE, "text": STATE_UNAVAILABLE_TEXT}
        assert forwarded == []
        assert _pending(bot_user) is False

    def test_question_write_failure_on_an_ambiguous_goal_blocks(
        self, client: Client, bot_user: BotUser
    ) -> None:
        with patch(
            "apps.conversations.services.write_skill_state",
            side_effect=RuntimeError("write failed"),
        ):
            status, out, forwarded = _post(client, bot_user, _goal(AMBIGUOUS))
        assert out["safety"] == {"kind": KIND_STATE_UNAVAILABLE, "text": STATE_UNAVAILABLE_TEXT}
        assert forwarded == []
        assert _question_open(bot_user) is False
        # The durable restriction landed before the question failed: blocked
        # either way, and the next request asks again (fail-closed, not lost).
        assert _pending(bot_user) is True

    def test_restriction_write_failure_on_an_ambiguous_goal_blocks(
        self, client: Client, bot_user: BotUser
    ) -> None:
        """The identity row could not be written (the writer swallows and logs;
        the re-read finds nothing) → blocked, nothing forwarded, nothing persisted."""

        with patch("apps.orchestrator.safety.s1_restriction._write_row", new=lambda *a, **k: None):
            status, out, forwarded = _post(client, bot_user, _goal(AMBIGUOUS))
        assert out["safety"] == {"kind": KIND_STATE_UNAVAILABLE, "text": STATE_UNAVAILABLE_TEXT}
        assert forwarded == []
        assert _pending(bot_user) is False

    def test_the_request_after_a_failure_is_screened_again(
        self, client: Client, bot_user: BotUser
    ) -> None:
        with patch("apps.orchestrator.safety.s1_restriction._write_row", new=lambda *a, **k: None):
            _post(client, bot_user, _goal(AMBIGUOUS))
        assert _pending(bot_user) is False
        # Storage is back: the same ambiguous goal asks and persists; a benign
        # goal after that is still bound to the question.
        status, out, forwarded = _post(client, bot_user, _goal(AMBIGUOUS))
        assert out["safety"] == G4_FRAME
        assert forwarded == []
        assert _pending(bot_user) is True
        status, out, forwarded = _post(client, bot_user, _goal("хочу массаж спины"))
        assert out["safety"] == G4_FRAME
        assert forwarded == []

    def test_a_benign_goal_after_a_lookup_failure_without_any_state_forwards(
        self, client: Client, bot_user: BotUser
    ) -> None:
        """Fail-closed is for the failing request; once the carrier answers
        «no state», an unrestricted goal proceeds as before."""

        with patch(
            "apps.conversations.services.resolve_conversation_for_bot_user",
            side_effect=RuntimeError("db down"),
        ):
            _, out, forwarded = _post(client, bot_user, _goal("хочу массаж спины"))
        assert out["safety"]["kind"] == KIND_STATE_UNAVAILABLE and forwarded == []
        _, out, forwarded = _post(client, bot_user, _goal("хочу массаж спины"))
        assert "safety" not in out or out.get("safety") is None
        assert len(forwarded) == 1


class TestDurableStopOnTheMiniApp:
    def test_a_positive_answer_stops_and_every_later_request_is_stopped(
        self, client: Client, bot_user: BotUser
    ) -> None:
        _post(client, bot_user, _goal(AMBIGUOUS))
        _, out, _ = _post(
            client, bot_user, _goal(AMBIGUOUS, **{SAFETY_ANSWER_FIELD: "да, перекосило лицо"})
        )
        assert out["safety"] == {"kind": KIND_RED_FLAG, "text": MEDICAL_EMERGENCY_TEXT_V2}
        for text in ("хочу массаж спины", "мне уже лучше", "всё прошло"):
            _, out, forwarded = _post(client, bot_user, _goal(text))
            assert out["safety"] == {"kind": KIND_RED_FLAG, "text": MEDICAL_EMERGENCY_TEXT_V2}
            assert forwarded == []


class TestNewSessionOnTheMiniApp:
    def test_a_closed_conversation_and_a_fresh_one_keep_the_restriction(
        self, client: Client, bot_user: BotUser
    ) -> None:
        """[OD-BOT §162] «новая сессия — не clearance» over HTTP: the runtime's own
        close, a fresh row, and the goal is still blocked with the question."""

        _post(client, bot_user, _goal(AMBIGUOUS))
        first = resolve_conversation_for_bot_user(bot_user)
        assert first is not None
        close_conversation(first, outcome=Conversation.Outcome.values[0])

        _, out, forwarded = _post(client, bot_user, _goal("хочу массаж спины"))
        assert out["safety"] == G4_FRAME
        assert forwarded == []
        second = resolve_conversation_for_bot_user(bot_user)
        assert second is not None and second.pk != first.pk
        assert g4_pending(second) is True
