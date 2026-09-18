"""Safety entry on the goal anketa free text (DRF-1763, C02-M7).

The checkpoint from the gap map: «текст с болью не создаёт цель молча».
Every node here asks the proxy the same two questions — did Ayla get a
write, and what did the person get instead — with the safety producers
running for real (no patching of ``pre_check`` / ``classify``: D7 says
the signal set IS theirs, so the test must see their verdicts).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time as time_module
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.integrations.ayla.goals_client import reset_goals_circuit
from apps.miniapp_api import health_gate
from apps.miniapp_api.health_gate import (
    HEALTH_ACKNOWLEDGEMENT_COPY,
    HEALTH_CLARIFY_QUESTIONS,
    KIND_BLOCK,
    KIND_CLARIFY,
    KIND_CRISIS,
    KIND_RED_FLAG,
    SAFETY_ANSWER_FIELD,
)
from apps.orchestrator.safety.gate import BLOCK_REPLY_TEXT, CRISIS_REPLY_TEXT
from apps.skills.health_screening.skill import RED_FLAG_REPLY, SOFT_PAIN_REPLY
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-1763"  # noqa: S105 — test fixture  # pragma: allowlist secret
REPO_ROOT = Path(__file__).resolve().parents[3]

#: The gap-map sentence. Soft pain for the chat classifier, ALLOW for pre_check.
PAIN_GOAL = "болит спина после работы"
#: Vague-medical bucket: pre_check CLARIFY (and soft pain).
VAGUE_MEDICAL = "почему болит голова по утрам"
#: Two red flags the chat classifier knows (DRF-973 widened «онемела»).
RED_FLAG = "онемела рука и температура 38.5"
CRISIS = "я думаю о суициде"
BLOCK = "подберите обезболивающее таблетки"
CLEAN_GOAL = "хочу расслабиться и восстановиться"
CLEAN_ANSWER = "спина, после сидячей работы"

_DOC = {
    "version": 2,
    "known": {"goal": None},
    "missing": [],
    "suggestions": [],
    "intents": [],
    "next": None,
}


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
    tenant = Tenant.objects.create(slug="gate-1763", name="Gate", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = tenant.slug
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="17630", display_name="Ольга"
    )


def _post(client: Client, bot_user: BotUser, body: dict) -> tuple[int, dict, list[dict]]:
    """POST the body through the proxy; return (status, json, bodies Ayla received)."""

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


# --- the checkpoint: a health signal stops the write -------------------------


class TestHealthSignalStopsTheWrite:
    def test_pain_in_goal_text_asks_instead_of_creating_the_goal(
        self, client: Client, bot_user: BotUser
    ) -> None:
        status, body, forwarded = _post(client, bot_user, _goal(PAIN_GOAL))
        assert status == 200
        assert forwarded == [], "Ayla received a write for a sentence about pain"
        assert "data" not in body, "the document must not move while the question is open"
        assert body["safety"] == {
            "kind": KIND_CLARIFY,
            "acknowledgement": HEALTH_ACKNOWLEDGEMENT_COPY,
            "questions": list(HEALTH_CLARIFY_QUESTIONS),
        }

    def test_pain_in_anketa_free_text_answer_is_gated_the_same_way(
        self, client: Client, bot_user: BotUser
    ) -> None:
        body = {"answer": {"step": "what_bothers", "text": PAIN_GOAL}, "source_channel": "miniapp"}
        status, out, forwarded = _post(client, bot_user, body)
        assert status == 200
        assert forwarded == []
        assert out["safety"]["kind"] == KIND_CLARIFY

    def test_pre_check_clarify_bucket_asks_too(self, client: Client, bot_user: BotUser) -> None:
        """«почему болит…» is the bucket the chat gate lets through as allowed=True (gate.py:145).
        D1-B: here it is a question, not clearance."""

        status, out, forwarded = _post(client, bot_user, _goal(VAGUE_MEDICAL))
        assert forwarded == []
        assert out["safety"]["kind"] == KIND_CLARIFY

    def test_red_flag_answers_see_a_doctor_and_creates_nothing(
        self, client: Client, bot_user: BotUser
    ) -> None:
        status, out, forwarded = _post(client, bot_user, _goal(RED_FLAG))
        assert forwarded == []
        assert out["safety"] == {"kind": KIND_RED_FLAG, "text": RED_FLAG_REPLY}

    def test_crisis_phrase_gets_the_founder_approved_crisis_reply(
        self, client: Client, bot_user: BotUser
    ) -> None:
        status, out, forwarded = _post(client, bot_user, _goal(CRISIS))
        assert forwarded == []
        assert out["safety"] == {"kind": KIND_CRISIS, "text": CRISIS_REPLY_TEXT}

    def test_block_phrase_gets_the_block_reply(self, client: Client, bot_user: BotUser) -> None:
        status, out, forwarded = _post(client, bot_user, _goal(BLOCK))
        assert forwarded == []
        assert out["safety"] == {"kind": KIND_BLOCK, "text": BLOCK_REPLY_TEXT}


# --- what still flows ---------------------------------------------------------


class TestCleanTextStillFlows:
    def test_clean_goal_text_is_forwarded_unchanged(
        self, client: Client, bot_user: BotUser
    ) -> None:
        status, out, forwarded = _post(client, bot_user, _goal(CLEAN_GOAL))
        assert status == 200
        assert forwarded == [_goal(CLEAN_GOAL)]
        assert out == {"data": _DOC}

    @pytest.mark.parametrize(
        "body",
        [
            {"goal_key": "relax", "source_channel": "miniapp"},
            {"intent": "start_anketa", "source_channel": "miniapp"},
            {"answer": {"step": "s1", "option_key": "k"}, "source_channel": "miniapp"},
            {"answer": {"step": "s1", "confirm": True}, "source_channel": "miniapp"},
        ],
        ids=["goal_key", "intent", "option_key", "confirm"],
    )
    def test_bodies_without_free_text_are_not_screened(
        self, client: Client, bot_user: BotUser, body: dict
    ) -> None:
        with (
            patch.object(health_gate, "evaluate_inbound") as gate,
            patch.object(health_gate, "classify") as cls,
        ):
            _, out, forwarded = _post(client, bot_user, body)
        assert forwarded == [body]
        assert "safety" not in out
        gate.assert_not_called()
        cls.assert_not_called()


# --- the answer reopens the write, and only the answer ------------------------


class TestAnswerReopensTheWrite:
    def test_clean_answer_forwards_the_original_body_without_the_answer(
        self, client: Client, bot_user: BotUser
    ) -> None:
        _post(client, bot_user, _goal(PAIN_GOAL))  # the question is asked
        status, out, forwarded = _post(
            client, bot_user, _goal(PAIN_GOAL, **{SAFETY_ANSWER_FIELD: CLEAN_ANSWER})
        )
        assert status == 200
        assert forwarded == [_goal(PAIN_GOAL)], (
            "the original goal_text goes to Ayla, the answer does not"
        )
        assert SAFETY_ANSWER_FIELD not in forwarded[0]
        assert out == {"data": _DOC}

    def test_red_flag_in_the_answer_refuses_and_forwards_nothing(
        self, client: Client, bot_user: BotUser
    ) -> None:
        _post(client, bot_user, _goal(PAIN_GOAL))
        _, out, forwarded = _post(
            client, bot_user, _goal(PAIN_GOAL, **{SAFETY_ANSWER_FIELD: RED_FLAG})
        )
        assert forwarded == []
        assert out["safety"] == {"kind": KIND_RED_FLAG, "text": RED_FLAG_REPLY}

    def test_answer_without_a_question_on_record_is_not_clearance(
        self, client: Client, bot_user: BotUser
    ) -> None:
        """A client cannot skip the question by claiming it was answered."""

        _, out, forwarded = _post(
            client, bot_user, _goal(PAIN_GOAL, **{SAFETY_ANSWER_FIELD: CLEAN_ANSWER})
        )
        assert forwarded == []
        assert out["safety"]["kind"] == KIND_CLARIFY

    def test_asked_and_abandoned_asks_again(self, client: Client, bot_user: BotUser) -> None:
        """«asked» alone never clears — only «answered» does (D1-B: the flow waits for the answer)."""

        _post(client, bot_user, _goal(PAIN_GOAL))
        _, out, forwarded = _post(client, bot_user, _goal(PAIN_GOAL))
        assert forwarded == []
        assert out["safety"]["kind"] == KIND_CLARIFY

    def test_the_memo_is_per_person(self, client: Client, bot_user: BotUser, settings) -> None:
        _post(client, bot_user, _goal(PAIN_GOAL))
        _post(client, bot_user, _goal(PAIN_GOAL, **{SAFETY_ANSWER_FIELD: CLEAN_ANSWER}))
        other = BotUser.all_tenants.create(
            tenant=bot_user.tenant, channel="max", channel_user_id="17631", display_name="Ирина"
        )
        _, out, forwarded = _post(client, other, _goal(PAIN_GOAL))
        assert forwarded == []
        assert out["safety"]["kind"] == KIND_CLARIFY


# --- nothing of the person's words leaks into the log -------------------------


class TestNothingIsLogged:
    def test_neither_goal_text_nor_answer_appears_in_any_log_record(
        self, client: Client, bot_user: BotUser, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        _post(client, bot_user, _goal(PAIN_GOAL))
        _post(client, bot_user, _goal(PAIN_GOAL, **{SAFETY_ANSWER_FIELD: RED_FLAG}))
        _post(client, bot_user, _goal(PAIN_GOAL, **{SAFETY_ANSWER_FIELD: CLEAN_ANSWER}))
        rendered = "\n".join(r.getMessage() for r in caplog.records)
        assert rendered, (
            "the gate must leave a trace (kind + bot_user) — an empty log proves nothing"
        )
        for phrase in (PAIN_GOAL, RED_FLAG, CLEAN_ANSWER):
            assert phrase not in rendered
        for word in ("спина", "онемела", "температура"):
            assert word not in rendered.lower()


# --- copy: parity with the chat and with the Mini App; no diagnosis words -----

_DIAGNOSIS_WORDS = (
    "грыж",
    "остеохондроз",
    "протруз",
    "невралг",
    "артрит",
    "артроз",
    "сколиоз",
    "защемлен",
    "диагноз",
    "воспален",
)


class TestCopy:
    def test_questions_are_the_chat_questions(self) -> None:
        """D7 — the same questions as in the chat, not a new matrix."""

        for line in HEALTH_CLARIFY_QUESTIONS:
            assert line in SOFT_PAIN_REPLY, line

    def test_acknowledgement_and_questions_name_no_diagnosis(self) -> None:
        text = (HEALTH_ACKNOWLEDGEMENT_COPY + " ".join(HEALTH_CLARIFY_QUESTIONS)).lower()
        assert not [w for w in _DIAGNOSIS_WORDS if w in text]

    def test_mini_app_copy_is_the_same_string(self) -> None:
        ts = (REPO_ROOT / "apps" / "miniapp" / "src" / "lib" / "health-gate-copy.ts").read_text(
            encoding="utf-8"
        )
        m = re.search(r"HEALTH_ACKNOWLEDGEMENT_COPY\s*=\s*((?:\s*\"[^\"]*\"\s*\+?)+);", ts)
        assert m, "HEALTH_ACKNOWLEDGEMENT_COPY not found in health-gate-copy.ts"
        joined = "".join(re.findall(r"\"([^\"]*)\"", m.group(1)))
        assert joined == HEALTH_ACKNOWLEDGEMENT_COPY
        assert f'SAFETY_ANSWER_FIELD = "{SAFETY_ANSWER_FIELD}"' in ts
        for kind in (KIND_CLARIFY, KIND_RED_FLAG, KIND_CRISIS, KIND_BLOCK):
            assert f'"{kind}"' in ts, kind
