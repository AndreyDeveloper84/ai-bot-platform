"""The master's assistant loop (DRF-1061 step 1).

The model is stubbed here on purpose: what these tests judge is the loop
around it — which tool ran, what the second call was handed, and what
happens when something fails. Judging the model's words needs a live model
and belongs in a nightly run, not in CI (the same split the replay gate
makes explicit).

Two properties get the most weight:

* **the tool result comes back as an ordinary message, not `role="tool"`.**
  The Anthropic adapter cannot express a tool message — written the obvious
  way, this would work on OpenAI and break silently the moment an operator
  flipped `SKILL_LLM_PROVIDER`.
* **every failure ends in a sentence.** This runs inside the MAX consumer:
  an exception there is a message the person never receives and a retry
  that fails the same way.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.booking.models import RemoteBookingProxy
from apps.catalog.models import CatalogMaster
from apps.master_api.services import assistant as mod
from apps.master_api.services.assistant import (
    BUSY_TEXT,
    COST_TEXT,
    FAILED_TEXT,
    NO_MASTER_TEXT,
    answer_master_question,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

MSK = dt_timezone(timedelta(hours=3))
NOW = datetime(2026, 8, 24, 12, 0, tzinfo=MSK)


@dataclass
class FakeToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = "call-1"


@dataclass
class FakeResult:
    text: str = ""
    tool_calls: list[FakeToolCall] = field(default_factory=list)
    prompt_tokens: int = 10
    completion_tokens: int = 5
    model: str = "gpt-4o-mini"
    provider: str = "openai"
    finish_reason: str = "stop"


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="asst-salon", name="Формула тела", timezone="Europe/Moscow")


@pytest.fixture
def master(tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Ольга",
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
    )


def _visit(master, *, at: datetime, minutes: int = 60):
    return RemoteBookingProxy.all_tenants.create(
        tenant=master.tenant,
        appointment_id=uuid.uuid4(),
        specialist_id=master.id,
        start_at=at,
        end_at=at + timedelta(minutes=minutes),
        status="confirmed",
    )


@pytest.fixture
def llm():
    """Stub the provider round trip, recording every call's messages."""

    calls: list[dict[str, Any]] = []
    scripted: list[FakeResult] = []

    def fake_complete(messages, *, tenant, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        return scripted.pop(0) if scripted else FakeResult(text="ответ")

    with patch.object(mod, "_complete", side_effect=fake_complete):
        yield {"calls": calls, "script": scripted}


class TestTheSimpleCase:
    def test_a_plain_answer_needs_one_call(self, master, llm):
        llm["script"].append(FakeResult(text="Пока свободно."))

        reply = answer_master_question(master=master, text="привет", now=NOW)

        assert reply.text == "Пока свободно."
        assert len(llm["calls"]) == 1
        assert reply.tool_name == ""

    def test_the_tools_are_offered_on_the_first_call(self, master, llm):
        answer_master_question(master=master, text="что у меня завтра", now=NOW)

        offered = {t["name"] for t in llm["calls"][0]["tools"]}
        assert offered == {"my_day", "my_week", "free_slots"}

    def test_todays_date_is_in_the_prompt(self, master, llm):
        # Without it the model lives at its training cutoff and rejects
        # real near-future dates (DRF-988).
        answer_master_question(master=master, text="что у меня завтра", now=NOW)

        system = llm["calls"][0]["messages"][0]["content"]
        assert "2026-08-24" in system


class TestTheToolRoundTrip:
    def test_the_tool_runs_and_a_second_call_words_the_answer(self, master, llm):
        _visit(master, at=datetime(2026, 8, 25, 10, 0, tzinfo=MSK))
        llm["script"].extend(
            [
                FakeResult(tool_calls=[FakeToolCall("my_day", {"date": "2026-08-25"})]),
                FakeResult(text="Завтра одна запись в 10:00."),
            ]
        )

        reply = answer_master_question(master=master, text="что у меня завтра", now=NOW)

        assert reply.text == "Завтра одна запись в 10:00."
        assert reply.tool_name == "my_day"
        assert len(llm["calls"]) == 2

    def test_the_result_returns_as_a_plain_message_not_a_tool_message(self, master, llm):
        """The property that keeps this working on both providers.

        `role="tool"` is unrepresentable in the Anthropic adapter, so the
        obvious loop would break the moment an operator switched provider —
        in production, silently, on a surface staff depend on.
        """

        llm["script"].extend(
            [
                FakeResult(tool_calls=[FakeToolCall("my_day", {"date": "2026-08-25"})]),
                FakeResult(text="ok"),
            ]
        )

        answer_master_question(master=master, text="что у меня завтра", now=NOW)

        roles = [m["role"] for m in llm["calls"][1]["messages"]]
        assert "tool" not in roles
        assert roles[-1] == "user"

    def test_the_second_call_offers_no_tools(self, master, llm):
        # One tool per turn: a master between clients does not need a chain.
        llm["script"].extend(
            [
                FakeResult(tool_calls=[FakeToolCall("my_day", {"date": "2026-08-25"})]),
                FakeResult(text="ok"),
            ]
        )

        answer_master_question(master=master, text="что у меня завтра", now=NOW)

        assert llm["calls"][1]["tools"] is None

    def test_the_data_reaches_the_model(self, master, llm):
        _visit(master, at=datetime(2026, 8, 25, 10, 0, tzinfo=MSK))
        llm["script"].extend(
            [
                FakeResult(tool_calls=[FakeToolCall("my_day", {"date": "2026-08-25"})]),
                FakeResult(text="ok"),
            ]
        )

        answer_master_question(master=master, text="что у меня завтра", now=NOW)

        payload = llm["calls"][1]["messages"][-1]["content"]
        assert "my_day" in payload
        assert "10:00" in payload

    def test_only_the_first_tool_call_runs(self, master, llm):
        llm["script"].extend(
            [
                FakeResult(
                    tool_calls=[
                        FakeToolCall("my_day", {"date": "2026-08-25"}),
                        FakeToolCall(
                            "my_week", {"date_from": "2026-08-24", "date_to": "2026-08-30"}
                        ),
                    ]
                ),
                FakeResult(text="ok"),
            ]
        )

        reply = answer_master_question(master=master, text="и день и неделя", now=NOW)

        assert reply.tool_name == "my_day"
        assert len(llm["calls"]) == 2


class TestFailuresBecomeSentences:
    def test_a_provider_outage_on_the_first_call(self, master):
        with patch.object(mod, "_complete", side_effect=RuntimeError("502")):
            reply = answer_master_question(master=master, text="что у меня завтра", now=NOW)

        assert reply.text == FAILED_TEXT

    def test_a_provider_outage_on_the_second_call(self, master, llm):
        calls = {"n": 0}

        def flaky(messages, *, tenant, tools=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return FakeResult(tool_calls=[FakeToolCall("my_day", {"date": "2026-08-25"})])
            raise RuntimeError("502")

        with patch.object(mod, "_complete", side_effect=flaky):
            reply = answer_master_question(master=master, text="что у меня завтра", now=NOW)

        assert reply.text == FAILED_TEXT

    def test_a_bad_argument_is_explained_not_invented(self, master, llm):
        """Making up an answer here is how a master trusts a wrong number."""

        llm["script"].append(FakeResult(tool_calls=[FakeToolCall("my_day", {"date": "в четверг"})]))

        reply = answer_master_question(master=master, text="что в четверг", now=NOW)

        assert "не понимаю дату" in reply.text
        assert len(llm["calls"]) == 1

    def test_an_unknown_tool_is_survived(self, master, llm):
        llm["script"].append(FakeResult(tool_calls=[FakeToolCall("rm_rf", {})]))

        reply = answer_master_question(master=master, text="сделай что-нибудь", now=NOW)

        assert "неизвестный инструмент" in reply.text

    def test_an_empty_completion_is_not_sent_as_silence(self, master, llm):
        llm["script"].append(FakeResult(text="   "))

        reply = answer_master_question(master=master, text="привет", now=NOW)

        assert reply.text == FAILED_TEXT

    def test_no_master_no_crash(self):
        assert answer_master_question(master=None, text="привет").text == NO_MASTER_TEXT


class TestGuards:
    def test_a_red_flag_never_reaches_the_model(self, master, llm):
        """Safety runs before the limiter and before the provider."""

        reply = answer_master_question(master=master, text="я думаю о суициде", now=NOW)

        assert "8-800-2000-122" in reply.text
        assert llm["calls"] == []

    def test_the_rate_limit_answers_calmly(self, master, llm):
        from apps.master_api.services.ai_draft_limits import RateLimitResult

        with (
            patch.object(
                mod,
                "answer_master_question",
                wraps=answer_master_question,
            ),
            patch(
                "apps.master_api.services.ai_draft_limits.check_and_consume_rate_limit",
                return_value=RateLimitResult(allowed=False, slug="rate_limited"),
            ),
        ):
            reply = answer_master_question(master=master, text="что завтра", now=NOW)

        assert reply.text == BUSY_TEXT
        assert llm["calls"] == []

    def test_the_cost_cap_answers_calmly(self, master, llm):
        from apps.master_api.services.ai_draft_limits import RateLimitResult

        with patch(
            "apps.master_api.services.ai_draft_limits.check_cost_cap",
            return_value=RateLimitResult(allowed=False, slug="cost_cap_exceeded"),
        ):
            reply = answer_master_question(master=master, text="что завтра", now=NOW)

        assert reply.text == COST_TEXT
        assert llm["calls"] == []

    def test_a_forbidden_reply_is_replaced_before_sending(self, master, llm):
        from apps.orchestrator.safety.outbound import REPLACEMENT_TEXT

        llm["script"].append(FakeResult(text="У клиентки аллергия, ей нельзя эту процедуру."))

        reply = answer_master_question(master=master, text="что с Ириной", now=NOW)

        assert reply.text == REPLACEMENT_TEXT
        assert "medical" in reply.blocked_categories

    def test_a_long_reply_is_trimmed(self, master, llm):
        llm["script"].append(FakeResult(text="а" * 2000))

        reply = answer_master_question(master=master, text="расскажи", now=NOW)

        assert len(reply.text) <= mod.MAX_REPLY_CHARS


class TestTelemetry:
    def test_tokens_from_both_calls_are_summed(self, master, llm):
        llm["script"].extend(
            [
                FakeResult(
                    tool_calls=[FakeToolCall("my_day", {"date": "2026-08-25"})],
                    prompt_tokens=100,
                    completion_tokens=10,
                ),
                FakeResult(text="ok", prompt_tokens=200, completion_tokens=20),
            ]
        )

        reply = answer_master_question(master=master, text="что завтра", now=NOW)

        assert reply.tokens_in == 300
        assert reply.tokens_out == 30

    def test_cost_is_a_decimal_even_for_an_unknown_model(self, master, llm):
        llm["script"].append(FakeResult(text="ok", model="model-that-does-not-exist"))

        reply = answer_master_question(master=master, text="привет", now=NOW)

        assert isinstance(reply.llm_cost_usd, Decimal)
