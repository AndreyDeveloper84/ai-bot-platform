"""BookingSkill tests (DRF-839 / Phase 1 / B3).

Mocks the LLM provider + the ``get_yclients_client`` factory so the
skill's two-call tool-use loop runs end-to-end in-process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings

from apps.booking.models import BookingRequest
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.integrations.yclients import (
    AvailableTime,
    BookingRecord,
    Service,
    Staff,
    YClientsAPIError,
    YClientsUnavailableError,
)
from apps.llm.protocol import (
    CompletionResult,
    LLMProviderUnavailable,
    LLMTransportError,
    ToolCall,
    UnknownTenantError,
)
from apps.llm.router import reset_router_cache
from apps.orchestrator.intent_router import IntentDecision
from apps.skills.base import SkillContext, SkillResult
from apps.skills.booking.skill import BookingSkill
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db(transaction=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, settings: pytest.FixtureRequest):
    settings.BASE_DIR = tmp_path  # type: ignore[attr-defined]
    settings.LLM_PROVIDER = "openai"  # type: ignore[attr-defined]
    settings.SKILL_LLM_PROVIDER = {}  # type: ignore[attr-defined]
    # Stabilization B2: tool-spec wiring tests below assume all 8
    # booking specs are advertised. Production default is False; this
    # fixture flips the flag so the spec list is unfiltered for the
    # broad behavioural coverage in this module. Per-test overrides
    # can flip it back if needed.
    settings.CERTIFICATE_PAYMENT_ENABLED = True  # type: ignore[attr-defined]
    reset_router_cache()
    cache.clear()
    yield
    cache.clear()
    reset_router_cache()


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="booking-skill", name="Booking Skill")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="u1",
        chat_id="u1",
        phone="79991234567",
        client_name="Anna",
    )


@pytest.fixture
def context(tenant: Tenant, bot_user: BotUser) -> SkillContext:
    conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
    return SkillContext(
        conversation=conv,
        bot_user=bot_user,
        message_text="запиши на массаж",
        trace_id="t-booking",
    )


# ---------------------------------------------------------------------------
# Fake YClients client + LLM helpers
# ---------------------------------------------------------------------------


class FakeYClients:
    def __init__(self) -> None:
        self.staff_rows: list[Staff] = []
        self.services_rows: list[Service] = []
        self.dates: list[str] = []
        self.times: list[AvailableTime] = []
        self.create_record_response: BookingRecord | None = None
        self.create_record_exc: Exception | None = None
        self.staff_exc: Exception | None = None
        self.services_exc: Exception | None = None
        self.create_calls: list[dict[str, Any]] = []
        self.dates_calls: list[dict[str, Any]] = []
        self.times_calls: list[dict[str, Any]] = []

    def get_staff(self, *, staff_id: int | None = None) -> list[Staff]:
        if self.staff_exc is not None:
            raise self.staff_exc
        return list(self.staff_rows)

    def get_services(
        self,
        *,
        staff_id: int | None = None,
        category_id: int | None = None,
    ) -> list[Service]:
        if self.services_exc is not None:
            raise self.services_exc
        return list(self.services_rows)

    def get_available_dates(
        self,
        *,
        staff_id: int | None = None,
        service_ids: list[int] | None = None,
    ) -> list[str]:
        self.dates_calls.append({"staff_id": staff_id, "service_ids": service_ids})
        return list(self.dates)

    def get_available_times(
        self,
        *,
        staff_id: int,
        date: str,
        service_ids: list[int] | None = None,
    ) -> list[AvailableTime]:
        self.times_calls.append({"staff_id": staff_id, "date": date, "service_ids": service_ids})
        return list(self.times)

    def create_record(self, **kwargs: Any) -> BookingRecord:
        self.create_calls.append(kwargs)
        if self.create_record_exc is not None:
            raise self.create_record_exc
        if self.create_record_response is None:
            return BookingRecord(record_id=12345, record_hash="h", raw={})
        return self.create_record_response

    def get_user_records(self) -> list[Any]:
        return []


def _staff(id_: int, name: str = "Olga", spec: str = "Массаж") -> Staff:
    return Staff(
        id=id_,
        name=name,
        specialization=spec,
        rating=4.5,
        avatar="",
        position="master",
        raw={},
    )


def _service(id_: int, title: str = "Массаж") -> Service:
    return Service(
        id=id_,
        title=title,
        price_min=1500.0,
        price_max=2500.0,
        duration_s=3600,
        category_id=None,
        raw={},
    )


def _completion(*, text: str = "", tool_calls: list[ToolCall] | None = None) -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=tool_calls or [],
        prompt_tokens=10,
        completion_tokens=20,
        model="gpt-4o-mini-mock",
        provider="openai",
        finish_reason="stop" if not tool_calls else "tool_calls",
    )


def _patch_provider_complete(side_effects: list[Any]) -> Any:
    from apps.llm.providers.openai_provider import OpenAIProvider

    return patch.object(OpenAIProvider, "complete", side_effect=side_effects)


def _patch_yclients(client: FakeYClients) -> Any:
    return patch("apps.integrations.yclients.get_yclients_client", return_value=client)


# ---------------------------------------------------------------------------
# matches()
# ---------------------------------------------------------------------------


class TestMatches:
    def test_intent_booking_matches(self, context: SkillContext) -> None:
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="x",
            intent=IntentDecision(
                intent="booking", skill="booking", confidence=0.9, risk_level="low"
            ),
        )
        assert BookingSkill().matches(ctx) is True

    def test_intent_other_does_not_match(self, context: SkillContext) -> None:
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="x",
            intent=IntentDecision(intent="faq", skill="faq", confidence=0.9, risk_level="low"),
        )
        assert BookingSkill().matches(ctx) is False

    def test_keyword_fallback_when_no_intent(self, context: SkillContext) -> None:
        # context fixture text starts with "запиши" — keyword match.
        assert BookingSkill().matches(context) is True

    def test_keyword_fallback_negative(self, tenant: Tenant, bot_user: BotUser) -> None:
        conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
        ctx = SkillContext(
            conversation=conv,
            bot_user=bot_user,
            message_text="привет",
        )
        assert BookingSkill().matches(ctx) is False

    def test_master_pick_callback_matches_before_intent(self, context: SkillContext) -> None:
        """Callback prefix takes precedence — intent classifier might mis-route
        a bare numeric string in the callback payload otherwise."""
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="cb:book:pick_master:42",
            intent=IntentDecision(intent="faq", skill="faq", confidence=0.9, risk_level="low"),
        )
        # Even with intent=faq, the callback prefix wins.
        assert BookingSkill().matches(ctx) is True


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestShowMastersFlow:
    def test_lists_masters(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11, "Ольга"), _staff(12, "Иван")]
        tc = ToolCall(id="c1", name="show_masters", arguments={"service_name": "массаж"})
        # Master-cards short-circuit (2026-05-21): Phase 3 LLM is skipped
        # when show_masters returns candidates. Only one completion is
        # consumed (Phase 1, the tool selection).
        completions = [_completion(tool_calls=[tc])]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert isinstance(result, SkillResult)
        assert result.should_handoff is False
        # Deterministic prompt, not LLM-generated text.
        assert result.reply_text == "Выберите мастера:"
        assert result.tool_calls_made == [tc]

    def test_emits_master_pick_keyboard(self, context: SkillContext, tenant: Tenant) -> None:
        """Each candidate master becomes one inline-keyboard button with
        callback ``cb:book:pick_master:<staff_id>:<service_id>``. The handler
        tap dispatches the date picker with service context — no LLM round-trip."""
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11, "Ольга"), _staff(12, "Иван")]
        tc = ToolCall(
            id="c1", name="show_masters", arguments={"service_name": "массаж", "service_id": 22}
        )
        with _patch_yclients(client), _patch_provider_complete([_completion(tool_calls=[tc])]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        # Platform-canonical envelope shape — matches what
        # ``apps.channels.max.handler._build_attachments`` consumes.
        assert result.action_data is not None
        atts = result.action_data["attachments"]
        assert len(atts) == 1
        assert atts[0]["type"] == "inline_keyboard"
        buttons = atts[0]["payload"]["buttons"]
        assert len(buttons) == 2
        labels = [b["label"] for b in buttons]
        # Names appear in labels (emoji prefix tolerated).
        assert any("Ольга" in lbl for lbl in labels)
        assert any("Иван" in lbl for lbl in labels)
        # Callback payloads carry master + service ids.
        callbacks = [b["callback"] for b in buttons]
        assert "cb:book:pick_master:11:22" in callbacks
        assert "cb:book:pick_master:12:22" in callbacks

    def test_show_masters_without_resolvable_service_hands_off(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """A master list with no grounded service_id must not emit broken buttons."""
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        tc = ToolCall(id="c1", name="show_masters", arguments={})
        with _patch_yclients(client), _patch_provider_complete([_completion(tool_calls=[tc])]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_missing_service_context"


class TestMasterPickCallback:
    """User taps a master card from the show_masters keyboard.

    2026-05-21: skip Phase 1 LLM, dispatch show_slots(master_id=<id>)
    deterministically, render slots via Phase 3 LLM as usual.
    """

    def test_matches_callback_prefix(self, context: SkillContext) -> None:
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="cb:book:pick_master:11",
        )
        assert BookingSkill().matches(ctx) is True

    def test_callback_dispatches_show_slots_no_phase1_llm(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """Master pick → date picker (NOT slots directly). UX feedback
        2026-05-21: "меня не спросили про дату, а сразу на завтра предложили
        время". After this PR master_pick fetches dates and renders a
        date-cards keyboard; show_slots fires on the subsequent date tap.

        Zero completions consumed — the short-circuit is fully deterministic.
        """
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = ["2026-05-22", "2026-05-23", "2026-05-25"]
        client.times = [
            AvailableTime(time="14:00", datetime="2026-05-22T14:00:00", seance_length_s=3600)
        ]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="cb:book:pick_master:11:22",
        )
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        assert result.should_handoff is False
        # Date-cards keyboard rendered deterministically.
        assert result.reply_text == "Выберите дату:"
        assert result.action_data is not None
        buttons = result.action_data["attachments"][0]["payload"]["buttons"]
        # One button per date, callback embeds master_id + date + service_id.
        assert len(buttons) == 3
        callbacks = [b["callback"] for b in buttons]
        assert "cb:book:pick_date:11:2026-05-22:22" in callbacks
        assert "cb:book:pick_date:11:2026-05-23:22" in callbacks
        assert "cb:book:pick_date:11:2026-05-25:22" in callbacks
        # No tool_call recorded — date picker is a direct YClients call,
        # not an LLM-grounded artefact.
        assert result.tool_calls_made == []
        # The selected service is forwarded to the dates lookup.
        assert client.dates_calls == [{"staff_id": 11, "service_ids": [22]}]

    def test_malformed_callback_id_handoffs_softly(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="cb:book:pick_master:NOT_AN_INT",
        )
        # No completion should be consumed — early-return before any LLM call.
        with _patch_yclients(FakeYClients()), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        assert "имя" in result.reply_text.lower() or "ещё раз" in result.reply_text.lower()
        assert result.tool_calls_made == []

    def test_master_pick_no_dates_returns_friendly_message(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """Empty dates list → friendly "no slots" reply, NOT handoff."""
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = []
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="cb:book:pick_master:11:22",
        )
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        assert result.should_handoff is False
        assert "нет свободных дат" in result.reply_text.lower()


class TestDatePickCallback:
    """User taps a date button from the date-picker keyboard.

    2026-05-21 UX fix: prevent show_slots auto-selecting the nearest
    date. Date-pick callback synthesises show_slots(master_id, date_from)
    so the existing slot-cards short-circuit takes over.
    """

    def test_matches_callback_prefix(self, context: SkillContext) -> None:
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="cb:book:pick_date:11:2026-05-22:22",
        )
        assert BookingSkill().matches(ctx) is True

    def test_callback_dispatches_show_slots_with_date_from(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """Date tap fires show_slots(master_id, date_from=<date>, service_id=<id>)
        directly — no Phase 1 LLM, no Phase 3 LLM (slot-cards short-circuit takes
        over). Zero completions consumed."""
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = ["2026-05-22"]
        client.times = [
            AvailableTime(time="14:00", datetime="2026-05-22T14:00:00", seance_length_s=3600)
        ]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="cb:book:pick_date:11:2026-05-22:22",
        )
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        assert result.should_handoff is False
        # Slot-cards short-circuit renders the keyboard.
        assert result.reply_text == "Выберите время:"
        assert result.action_data is not None
        buttons = result.action_data["attachments"][0]["payload"]["buttons"]
        assert "cb:book:pick_slot:11:22:2026-05-22T14:00:00" in [b["callback"] for b in buttons]
        # Synthetic show_slots tool_call recorded with date_from + service_id.
        assert len(result.tool_calls_made) == 1
        tc = result.tool_calls_made[0]
        assert tc.name == "show_slots"
        assert tc.arguments == {"master_id": 11, "date_from": "2026-05-22", "service_id": 22}

    def test_malformed_payload_handoffs_softly(self, context: SkillContext, tenant: Tenant) -> None:
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="cb:book:pick_date:NOT_VALID",  # missing :date segment
        )
        with _patch_yclients(FakeYClients()), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        assert "дату" in result.reply_text.lower() or "ещё раз" in result.reply_text.lower()
        assert result.tool_calls_made == []


class TestShowSlotsFlow:
    def test_lists_slots_as_buttons(self, context: SkillContext, tenant: Tenant) -> None:
        """Slot-cards short-circuit (2026-05-21): show_slots result is
        rendered as deterministic text + inline-keyboard, one button per
        slot. Phase 3 LLM is skipped — only the Phase 1 (tool selection)
        completion is consumed."""
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = ["2026-05-20"]
        client.times = [
            AvailableTime(time="14:00", datetime="2026-05-20T14:00:00", seance_length_s=3600)
        ]
        tc = ToolCall(id="c1", name="show_slots", arguments={"master_id": 11, "service_id": 22})
        # Only ONE completion mocked — short-circuit skips Phase 3 LLM.
        completions = [_completion(tool_calls=[tc])]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is False
        assert result.reply_text == "Выберите время:"
        # Keyboard envelope with cb:book:pick_slot:<master>:<service>:<datetime> callback.
        assert result.action_data is not None
        buttons = result.action_data["attachments"][0]["payload"]["buttons"]
        assert len(buttons) == 1
        assert buttons[0]["callback"] == "cb:book:pick_slot:11:22:2026-05-20T14:00:00"
        # Human-readable label includes the time.
        assert "14:00" in buttons[0]["label"]


class TestSlotPickCallback:
    """User taps a slot button from the show_slots keyboard.

    Unlike master-pick (deterministic show_slots dispatch), slot-pick
    needs the LLM to emit confirm_booking with master_id + service_id
    drawn from short-term conversation history. The skill overrides
    the prompt query with a synthesised "запиши меня на <datetime>"
    user text and lets Phase 1 LLM do the inference.
    """

    def test_matches_callback_prefix(self, context: SkillContext) -> None:
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="cb:book:pick_slot:11:22:2026-05-22T14:00:00",
        )
        assert BookingSkill().matches(ctx) is True

    def test_callback_synthesises_user_query_for_llm(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """The Phase 1 LLM prompt's ``query`` is overridden with the
        synthesised user-text that carries master_id + service_id.
        Regression guard: the mocked completion receives the synthesised
        query, not the raw callback payload."""
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="cb:book:pick_slot:11:22:2026-05-22T14:00:00",
        )
        # LLM returns plain text "ok, на 14:00" — no tool call needed
        # for this test (we just want to verify the prompt path).
        completions = [_completion(text="Ок, проверю слот.")]
        with _patch_yclients(client), _patch_provider_complete(completions) as mock_complete:
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        # No tool calls → small-talk path → returns LLM text verbatim.
        assert result.reply_text == "Ок, проверю слот."
        # Inspect the prompt that was actually sent: it should contain
        # the synthesised "запиши меня на <datetime>" string plus ids.
        sent_messages = mock_complete.call_args.args[0]
        user_msg = next(m for m in sent_messages if m["role"] == "user")
        assert "Запиши меня на" in user_msg["content"]
        assert "2026-05-22T14:00:00" in user_msg["content"]
        assert "11" in user_msg["content"]
        assert "22" in user_msg["content"]
        # Raw callback payload must NOT leak into the LLM context.
        assert "cb:book:pick_slot:" not in user_msg["content"]

    def test_empty_callback_payload_handoffs_softly(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="cb:book:pick_slot:",
        )
        with _patch_yclients(FakeYClients()), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        assert "время" in result.reply_text.lower() or "ещё раз" in result.reply_text.lower()
        assert result.tool_calls_made == []

    def test_old_slot_payload_without_master_service_is_rejected(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """Legacy pick_slot payload (only ISO datetime) must not be mis-parsed
        as master/service under BOOKING_VIA_AYLA_REST where ids are UUID strings.
        """

        class _FakeProvider:
            def get_services(self) -> list:
                return []

        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="cb:book:pick_slot:2026-08-06T14:00:00+03:00",
        )
        with override_settings(BOOKING_VIA_AYLA_REST=True):
            with (
                patch(
                    "apps.skills.booking.provider.get_booking_provider",
                    return_value=_FakeProvider(),
                ),
                _patch_provider_complete([]),
            ):
                with tenant_scope(tenant):
                    result = BookingSkill().handle(ctx)
        assert "контекст" in result.reply_text.lower() or "услуги" in result.reply_text.lower()
        assert result.tool_calls_made == []


class TestCreateFlowServiceContext:
    """RB1-D02 regression: service_id must survive every step of the
    button-driven create flow from service selection through slots.
    """

    def test_service_id_survives_master_pick(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = ["2026-05-22"]
        tc = ToolCall(
            id="c1",
            name="show_masters",
            arguments={"service_name": "массаж", "service_id": 22},
        )
        with _patch_yclients(client), _patch_provider_complete([_completion(tool_calls=[tc])]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        callbacks = [
            b["callback"] for b in result.action_data["attachments"][0]["payload"]["buttons"]
        ]
        assert "cb:book:pick_master:11:22" in callbacks

    def test_missing_service_context_on_master_pick_is_safe(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """Legacy master callback without service_id must not crash or
        call backend with incomplete data."""
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="cb:book:pick_master:11",
        )
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        assert result.should_handoff is False
        assert "контекст" in result.reply_text.lower() or "услуги" in result.reply_text.lower()
        # No backend lookup attempted without service context.
        assert client.dates_calls == []

    def test_service_id_survives_date_pick(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = ["2026-05-22"]
        client.times = [
            AvailableTime(time="14:00", datetime="2026-05-22T14:00:00", seance_length_s=3600)
        ]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="cb:book:pick_date:11:2026-05-22:22",
        )
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        assert result.should_handoff is False
        assert len(result.tool_calls_made) == 1
        assert result.tool_calls_made[0].arguments["service_id"] == 22

    def test_service_id_survives_slot_pick_prompt(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="cb:book:pick_slot:11:22:2026-05-22T14:00:00",
        )
        completions = [_completion(text="Ок, проверю слот.")]
        with _patch_yclients(client), _patch_provider_complete(completions) as mock_complete:
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        assert result.reply_text == "Ок, проверю слот."
        sent_messages = mock_complete.call_args.args[0]
        user_msg = next(m for m in sent_messages if m["role"] == "user")
        assert "11" in user_msg["content"]
        assert "22" in user_msg["content"]


class TestConfirmBookingFlow:
    def test_returns_preview_card_no_record_yet(
        self, context: SkillContext, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """B5: confirm_booking is preview-only.

        The skill returns a 2-button card; no YClients call happens
        until the user taps ✅ (covered by the gate-callback tests).
        """
        from apps.booking.models import PendingBookingAction

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11, "Ольга")]
        tc = ToolCall(
            id="c1",
            name="confirm_booking",
            arguments={
                "master_id": 11,
                "service_id": 22,
                "slot_datetime": "2026-05-20T14:00:00",
            },
        )
        completions = [
            _completion(tool_calls=[tc]),
            _completion(text="Записываю в 14:00 — подтверждаете?"),
        ]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is False
        # No BookingRequest created at preview time.
        assert BookingRequest.all_tenants.filter(tenant=tenant, bot_user=bot_user).count() == 0
        # No YClients create call.
        assert client.create_calls == []
        # Action data carries the keyboard.
        assert result.action_data is not None
        assert "attachments" in result.action_data
        assert "pending_action" in result.action_data
        assert result.action_data["pending_action"]["kind"] == PendingBookingAction.Kind.CONFIRM


class TestShowMyBookingsFlow:
    def test_returns_text(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        tc = ToolCall(id="c1", name="show_my_bookings", arguments={})
        completions = [
            _completion(tool_calls=[tc]),
            _completion(text="Записей пока нет."),
        ]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is False


class TestDirectReply:
    def test_no_tool_call_passes_through(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        completions = [_completion(text="Привет! Чем помочь?")]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is False
        assert "Привет" in result.reply_text
        assert result.tool_calls_made == []


# ---------------------------------------------------------------------------
# Handoff paths (the four distinct reasons)
# ---------------------------------------------------------------------------


class TestHandoffPaths:
    def test_no_masters_handoff(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = []  # empty staff list
        tc = ToolCall(id="c1", name="show_masters", arguments={"service_name": "x"})
        completions = [_completion(tool_calls=[tc])]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_no_masters"

    def test_confirm_booking_preview_does_not_call_yclients(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """B5: confirm_booking is preview-only.

        A YClients failure during create can no longer happen at the
        skill layer — the create moved to ``execute_confirm`` invoked
        by the gate-callback. Skill-layer YClients failure paths still
        cover prefetch + slot-fetch (other tests). This test asserts
        the preview-only contract.
        """
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        # ``create_record_exc`` is irrelevant at the preview stage; we
        # set it to prove it's NOT invoked.
        client.create_record_exc = YClientsAPIError("http_400")
        tc = ToolCall(
            id="c1",
            name="confirm_booking",
            arguments={
                "master_id": 11,
                "service_id": 22,
                "slot_datetime": "2026-05-20T14:00:00",
            },
        )
        completions = [
            _completion(tool_calls=[tc]),
            _completion(text="Записываю — подтверждаете?"),
        ]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        # Preview success — no handoff, no create.
        assert result.should_handoff is False
        assert client.create_calls == []
        assert result.action_data is not None

    def test_invalid_master_id_handoff(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        tc = ToolCall(id="c1", name="show_slots", arguments={"master_id": 9999})
        completions = [_completion(tool_calls=[tc])]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_invalid_master_id"

    def test_invalid_service_id_handoff(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        tc = ToolCall(
            id="c1",
            name="confirm_booking",
            arguments={
                "master_id": 11,
                "service_id": 9999,
                "slot_datetime": "2026-05-20T14:00:00",
            },
        )
        completions = [_completion(tool_calls=[tc])]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_invalid_service_id"

    def test_unknown_tool_handoff(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        tc = ToolCall(id="c1", name="search_some_other_tool", arguments={})
        completions = [_completion(tool_calls=[tc])]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_unknown_tool"

    def test_health_check_required_handoff(self, context: SkillContext, tenant: Tenant) -> None:
        from django.utils import timezone as dj_timezone

        from apps.catalog.models import CatalogService

        # Catalog row flagged as requiring a health check.
        with tenant_scope(tenant):
            CatalogService.objects.create(
                tenant=tenant,
                external_id=22,
                external_updated_at=dj_timezone.now(),
                slug="massage-gated",
                name="Массаж (по показаниям)",
                requires_health_check=True,
            )
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.create_record_response = BookingRecord(record_id=555, record_hash="", raw={})
        tc = ToolCall(
            id="c1",
            name="confirm_booking",
            arguments={
                "master_id": 11,
                "service_id": 22,
                "slot_datetime": "2026-05-20T14:00:00",
            },
        )
        completions = [_completion(tool_calls=[tc])]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_health_check_required"

    def test_yclients_prefetch_failure_handoff(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_exc = YClientsUnavailableError("circuit_open")
        with _patch_yclients(client):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_yclients_failure"

    def test_provider_lookup_failure_handoff(self, context: SkillContext, tenant: Tenant) -> None:
        # Skills retro residual #8: a misconfigured / circuit-broken LLM
        # provider used to surface as a raw 500 — now it produces the
        # same friendly handoff as a YClients outage.
        with patch(
            "apps.llm.router.get_router",
            side_effect=RuntimeError("provider config missing"),
        ):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_provider_failure"


# ---------------------------------------------------------------------------
# LLM Y3 envelope expansion (#473)
# ---------------------------------------------------------------------------


class TestLLMY3EnvelopeExpansion:
    """Per-skill coverage for issue #473.

    Each LLMError variant raised from ``provider.complete`` (or its
    transitive callees like ``cost_tracker.enforce_caps``) MUST be
    caught by the booking skill envelope and converted to a friendly
    handoff with ``reason="llm_error"``. Pre-#473 these would
    propagate as 500s because the envelope only wrapped router lookup,
    not completion.
    """

    def test_unknown_tenant_error_at_first_complete_returns_friendly_handoff(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """The headline #473 scenario — a stale UUID reaches
        ``enforce_caps`` via the provider.complete call site, which
        raises ``UnknownTenantError(LLMError)``. The skill envelope
        MUST catch + handoff cleanly.
        """

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        with (
            _patch_yclients(client),
            _patch_provider_complete([UnknownTenantError("stale tenant uuid")]),
        ):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "llm_error"

    def test_llm_provider_unavailable_at_first_complete_returns_friendly_handoff(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """LLMProviderUnavailable (e.g. missing API key) at the first
        completion MUST surface as ``llm_error`` handoff, not 500.
        """

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        with (
            _patch_yclients(client),
            _patch_provider_complete([LLMProviderUnavailable("OPENAI_API_KEY not set")]),
        ):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "llm_error"

    def test_transport_error_at_second_complete_returns_friendly_handoff(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """LLMTransportError raised on the SECOND completion (after
        tool dispatch) — distinct from the first-call path. Asserts
        the envelope catches at both wrap sites in the booking flow.

        Uses ``confirm_booking`` tool which DOES reach Phase 3 (the
        second LLM call) — ``show_masters`` would short-circuit at
        master-card render before the second call ever fires.
        """

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        tc = ToolCall(
            id="c1",
            name="confirm_booking",
            arguments={
                "master_id": 11,
                "service_id": 22,
                "slot_datetime": "2026-05-20T14:00:00",
            },
        )
        completions: list[Any] = [
            _completion(tool_calls=[tc]),
            LLMTransportError("vendor 5xx timeout"),
        ]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "llm_error"


# ---------------------------------------------------------------------------
# Tool spec wiring
# ---------------------------------------------------------------------------


class TestToolSpecWiring:
    def test_all_eight_specs_passed_on_first_call(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        captured_tools: list[Any] = []

        async def fake_complete(
            self_provider: Any,
            messages: list[dict[str, Any]],
            *,
            model: str | None = None,
            temperature: float = 0.0,
            tools: list[dict[str, Any]] | None = None,
            max_tokens: int | None = None,
        ) -> CompletionResult:
            captured_tools.append(tools)
            return _completion(text="ok")

        client = FakeYClients()
        client.services_rows = [_service(22)]
        from apps.llm.providers.openai_provider import OpenAIProvider

        with _patch_yclients(client), patch.object(OpenAIProvider, "complete", fake_complete):
            with tenant_scope(tenant):
                BookingSkill().handle(context)
        assert captured_tools
        first_tools = captured_tools[0]
        assert first_tools is not None
        names = {spec["name"] for spec in first_tools}
        assert names == {
            "cancel_booking",
            "reschedule_booking",
            "show_masters",
            "show_slots",
            "confirm_booking",
            "show_my_bookings",
            "calc_price",
            "buy_certificate",
        }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_booking_registered(self) -> None:
        from apps.skills.registry import registered

        names = [s.name for s in registered()]
        assert "booking" in names


class TestE0RegressionGuards:
    """E0#1 Variant A adversarial-round-2 regression guards (PR #955).

    Round-2 review flagged that two correctness fixes — F5 (Phase 3
    must NOT inject `known_masters`) и F11 (callback short-circuits
    must NOT pre-load the roster) — were enforced by single code
    lines с no behavioural tests. These tests lock the invariants
    so a future «consistency» refactor cannot silently undo them.
    """

    def test_callback_short_circuit_skips_roster_load(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """F11 regression guard — `pick_master` callback path returns
        early через `_render_date_picker`, никогда не строит prompt,
        и поэтому MUST NOT trigger the catalog-mirror SELECT for the
        master roster.

        Pre-F11: roster load ran unconditionally above the callback
        branching → one wasted indexed SELECT per pick_master tap.
        Post-F11: load sits after callback branches.
        """
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = ["2026-05-22"]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="cb:book:pick_master:11",
        )
        with (
            _patch_yclients(client),
            _patch_provider_complete([]),
            patch("apps.skills.booking.skill._load_tenant_master_roster") as mock_roster,
        ):
            with tenant_scope(tenant):
                BookingSkill().handle(ctx)
        mock_roster.assert_not_called()

    def test_phase3_second_prompt_does_not_inject_known_masters(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """F5 regression guard — Phase 3 (second LLM call, после
        tool dispatch) MUST pass `known_masters=None`. Authoritative
        truth for the current service/time context is
        `candidate_masters` (from `show_masters` tool result); the
        full roster would compete and produce contradictory advice
        when a known-but-not-offered master is mentioned.

        Pre-F5: original PR passed `known_masters=known_masters` on
        both Phase 1 + Phase 3.
        Post-F5: Phase 1 keeps roster (для name-grounding decision);
        Phase 3 explicitly passes `None`.
        """
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11, "Ольга")]
        # confirm_booking tool reaches Phase 3 (no short-circuit на
        # show_masters / show_slots tool result).
        tc = ToolCall(
            id="c1",
            name="confirm_booking",
            arguments={
                "master_id": 11,
                "service_id": 22,
                "slot_datetime": "2026-05-20T14:00:00",
            },
        )
        completions = [
            _completion(tool_calls=[tc]),
            _completion(text="Подтверждаете запись?"),
        ]
        with (
            _patch_yclients(client),
            _patch_provider_complete(completions),
            patch(
                "apps.skills.booking.skill.build_booking_prompt",
                wraps=__import__(
                    "apps.skills.booking.skill", fromlist=["build_booking_prompt"]
                ).build_booking_prompt,
            ) as mock_build,
        ):
            with tenant_scope(tenant):
                BookingSkill().handle(context)

        # First call = Phase 1, should carry the roster.
        # Second call = Phase 3, MUST pass known_masters=None.
        assert mock_build.call_count >= 2, (
            f"Expected ≥2 prompt builds (Phase 1 + Phase 3), got {mock_build.call_count}"
        )
        phase1_kwargs = mock_build.call_args_list[0].kwargs
        phase3_kwargs = mock_build.call_args_list[1].kwargs

        # Phase 1 may carry roster (list или None depending on mirror state).
        # The critical invariant — Phase 3 MUST be None, never a list.
        assert phase3_kwargs.get("known_masters") is None, (
            "F5 regression: Phase 3 second LLM call leaked known_masters into "
            f"the prompt — got {phase3_kwargs.get('known_masters')!r}, "
            "expected None."
        )
        # Sanity — Phase 1 still gets the roster keyword (как list или []).
        assert "known_masters" in phase1_kwargs


# ---------------------------------------------------------------------------
# Health-check gating — fail-closed backstop under flag-ON (#1016 / PR-A)
# ---------------------------------------------------------------------------


class TestServiceRequiresHealthCheck:
    """Direct coverage for ``_service_requires_health_check``.

    PR-B grounding: under ``BOOKING_VIA_AYLA_REST`` the helper grounds the
    flag against the mirror's ``ayla_service_id`` (UUID) column. A matched
    ``requires_health_check=False`` row lets the booking through; a UUID
    miss OR a non-UUID id FAILS CLOSED (``True`` → human handoff) rather
    than fail OPEN. The flag-OFF (int ``external_id``) path is unchanged.
    """

    _AYLA_UUID = "11111111-1111-1111-1111-111111111111"

    def _make_row(self, tenant: Tenant, *, ayla_service_id, gated: bool, external_id: int) -> None:
        from django.utils import timezone as dj_timezone

        from apps.catalog.models import CatalogService

        with tenant_scope(tenant):
            CatalogService.objects.create(
                tenant=tenant,
                external_id=external_id,
                external_updated_at=dj_timezone.now(),
                slug=f"svc-{external_id}",
                name=f"Service {external_id}",
                requires_health_check=gated,
                ayla_service_id=ayla_service_id,
            )

    def test_flag_on_grounded_gated_returns_true(self, tenant: Tenant) -> None:
        from apps.skills.booking.skill import _service_requires_health_check

        self._make_row(tenant, ayla_service_id=self._AYLA_UUID, gated=True, external_id=22)
        with override_settings(BOOKING_VIA_AYLA_REST=True):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, self._AYLA_UUID) is True

    def test_flag_on_grounded_not_gated_passes(self, tenant: Tenant) -> None:
        # The crux of PR-B: a matched, NOT-gated row must let the booking through.
        from apps.skills.booking.skill import _service_requires_health_check

        self._make_row(tenant, ayla_service_id=self._AYLA_UUID, gated=False, external_id=22)
        with override_settings(BOOKING_VIA_AYLA_REST=True):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, self._AYLA_UUID) is False

    def test_flag_on_uuid_miss_fails_closed(self, tenant: Tenant) -> None:
        # Valid UUID but no mirror row carries that ayla_service_id yet.
        from apps.skills.booking.skill import _service_requires_health_check

        with override_settings(BOOKING_VIA_AYLA_REST=True):
            with tenant_scope(tenant):
                assert (
                    _service_requires_health_check(tenant, "22222222-2222-2222-2222-222222222222")
                    is True
                )

    def test_flag_on_non_uuid_fails_closed(self, tenant: Tenant) -> None:
        # A non-UUID id (e.g. a stray legacy int) can't be grounded → fail closed.
        from apps.skills.booking.skill import _service_requires_health_check

        with override_settings(BOOKING_VIA_AYLA_REST=True):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, 22) is True

    def test_flag_off_uses_mirror_true(self, tenant: Tenant) -> None:
        from django.utils import timezone as dj_timezone

        from apps.catalog.models import CatalogService
        from apps.skills.booking.skill import _service_requires_health_check

        with tenant_scope(tenant):
            CatalogService.objects.create(
                tenant=tenant,
                external_id=22,
                external_updated_at=dj_timezone.now(),
                slug="gated",
                name="Gated",
                requires_health_check=True,
            )
        with override_settings(BOOKING_VIA_AYLA_REST=False):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, 22) is True

    def test_flag_off_missing_row_defaults_false(self, tenant: Tenant) -> None:
        from apps.skills.booking.skill import _service_requires_health_check

        with override_settings(BOOKING_VIA_AYLA_REST=False):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, 9999) is False

    def test_flag_off_mirror_false_returns_false(self, tenant: Tenant) -> None:
        from django.utils import timezone as dj_timezone

        from apps.catalog.models import CatalogService
        from apps.skills.booking.skill import _service_requires_health_check

        with tenant_scope(tenant):
            CatalogService.objects.create(
                tenant=tenant,
                external_id=22,
                external_updated_at=dj_timezone.now(),
                slug="not-gated",
                name="Not gated",
                requires_health_check=False,
            )
        with override_settings(BOOKING_VIA_AYLA_REST=False):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, 22) is False
