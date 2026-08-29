"""BookingSkill tests (DRF-839 / Phase 1 / B3).

Mocks the LLM provider + the ``get_yclients_client`` factory so the
skill's two-call tool-use loop runs end-to-end in-process.
"""

from __future__ import annotations

import datetime as _dt

import httpx
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
from apps.skills.booking.provider import AylaYClientsAdapter, YClientsScheduleUnavailableError
from apps.skills.booking.skill import BookingSkill
from apps.skills.booking.tools import SCHEDULE_UNAVAILABLE_TEXT
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db(transaction=True)


# ---------------------------------------------------------------------------
# The date every keyboard tap in this file lands on (DRF-1407)
# ---------------------------------------------------------------------------
#
# ``pick_slot`` refuses a slot that is already in the past — «Контекст
# записи устарел. Начните выбор услуги заново.» (``skill.py``,
# ``booking.pick_slot.past_slot``). That guard is correct: a keyboard can
# outlive the day it was drawn for.
#
# So a literal date here is a fuse, not a fixture. This file held
# ``2026-09-22`` in sixty-two places; on 21.09 two tests would have gone
# red, on 23.09 fourteen — all at once, all with a message about stale
# booking context, and all landing on whoever happened to be pushing that
# week rather than on whoever wrote them.
#
# Computed once at import, from the clock, so it is always ahead of the
# guard. The offset is generous on purpose: nothing in the flow caps how
# far ahead a slot may be, and a month of runway means a slow CI queue or
# a machine with a skewed clock still cannot reach it.
BOOKING_DATE_DAYS_AHEAD = 30

#: ``YYYY-MM-DD``, a month out. Interpolated into every ``pick_date`` /
#: ``pick_slot`` callback, every ``AvailableTime``, and every assertion
#: about them — one definition, so the tap and the expectation cannot
#: drift apart.
BOOKING_DATE = (
    (_dt.datetime.now(tz=_dt.timezone.utc) + _dt.timedelta(days=BOOKING_DATE_DAYS_AHEAD))
    .date()
    .isoformat()
)

# Russian month / weekday abbreviations, spelled out here rather than
# imported from ``apps.skills.booking.skill``. The two assertions below
# check the sentence the part-picker puts above the keyboard; an
# assertion that borrows the formatter it is checking cannot tell you the
# formatter is wrong.
_RU_MONTHS_SHORT = (
    "янв",
    "фев",
    "мар",
    "апр",
    "мая",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
)
_RU_WEEKDAYS_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def _ru_day_label(iso_date: str) -> str:
    """«22 сен (Вт)» — the day as the part-picker prompt names it."""

    d = _dt.date.fromisoformat(iso_date)
    return f"{d.day} {_RU_MONTHS_SHORT[d.month - 1]} ({_RU_WEEKDAYS_SHORT[d.weekday()]})"


#: What ``BOOKING_DATE`` reads as on screen. Derived, never restated — a
#: second hand-written copy of the same day is free to drift from the
#: first, and then the test stops checking the thing it was written for.
BOOKING_DAY_LABEL = _ru_day_label(BOOKING_DATE)


def _booking_date(offset_days: int) -> str:
    """A second/third date for the date picker, relative to BOOKING_DATE.

    The picker test hands the fake provider three free days and asserts a
    button for each. Two of them used to be literal May dates sitting
    beside one relative date — a fixture half alive and half dead, which
    is exactly the state that hides a filter: if the picker ever started
    dropping days in the past, those two buttons would vanish and the
    ``len(buttons) == 3`` above would be the only thing to say so.
    """

    return (_dt.date.fromisoformat(BOOKING_DATE) + _dt.timedelta(days=offset_days)).isoformat()


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
        # Approximate the real provider: a slot belongs to the requested day
        # when its ISO datetime starts with that day. Slots with no datetime
        # are treated as belonging to any day (legacy stub behaviour).
        return [t for t in self.times if not t.datetime or str(t.datetime).startswith(date)]

    def create_record(self, **kwargs: Any) -> BookingRecord:
        self.create_calls.append(kwargs)
        if self.create_record_exc is not None:
            raise self.create_record_exc
        if self.create_record_response is None:
            return BookingRecord(record_id=12345, record_hash="h", raw={})
        return self.create_record_response

    def get_user_records(self) -> list[Any]:
        return []


def _staff(id_: int | str, name: str = "Olga", spec: str = "Массаж") -> Staff:
    return Staff(
        id=id_,  # type: ignore[arg-type]  # flag-ON path carries Ayla UUID strings
        name=name,
        specialization=spec,
        rating=4.5,
        avatar="",
        position="master",
        raw={},
    )


def _service(id_: int | str, title: str = "Массаж") -> Service:
    return Service(
        id=id_,  # type: ignore[arg-type]  # flag-ON path carries Ayla UUID strings
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

    def test_show_masters_resolves_service_name_by_substring(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """Free-text service_name can be resolved when it matches exactly one catalog title."""
        client = FakeYClients()
        client.services_rows = [_service(22, title="Маникюр классический")]
        client.staff_rows = [_staff(11)]
        tc = ToolCall(id="c1", name="show_masters", arguments={"service_name": "маникюр"})
        with _patch_yclients(client), _patch_provider_complete([_completion(tool_calls=[tc])]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is False
        assert result.action_data is not None
        callbacks = [
            b["callback"] for b in result.action_data["attachments"][0]["payload"]["buttons"]
        ]
        assert "cb:book:pick_master:11:22" in callbacks

    def test_show_masters_ambiguous_service_name_hands_off(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """A service_name matching multiple catalog titles must not guess."""
        client = FakeYClients()
        client.services_rows = [
            _service(22, title="Маникюр классический"),
            _service(23, title="Маникюр французский"),
        ]
        client.staff_rows = [_staff(11)]
        tc = ToolCall(id="c1", name="show_masters", arguments={"service_name": "маникюр"})
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
        client.dates = [BOOKING_DATE, _booking_date(1), _booking_date(3)]
        client.times = [
            AvailableTime(time="14:00", datetime=f"{BOOKING_DATE}T14:00:00", seance_length_s=3600)
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
        assert f"cb:book:pick_date:11:{BOOKING_DATE}:22" in callbacks
        assert f"cb:book:pick_date:11:{_booking_date(1)}:22" in callbacks
        assert f"cb:book:pick_date:11:{_booking_date(3)}:22" in callbacks
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
            message_text=f"cb:book:pick_date:11:{BOOKING_DATE}:22",
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
        client.dates = [BOOKING_DATE]
        client.times = [
            AvailableTime(time="14:00", datetime=f"{BOOKING_DATE}T14:00:00", seance_length_s=3600)
        ]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_date:11:{BOOKING_DATE}:22",
        )
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        assert result.should_handoff is False
        # DRF-1325: the date tap now asks «утро / день / вечер» first — a bare
        # list of every free time of a day IS the calendar this ticket is
        # about. The one exception is this fixture's shape: a single slot at
        # 14:00 means only ONE part has anything, and a one-button question is
        # not a question, so the times are rendered straight away. The keyboard
        # is therefore unchanged; only the sentence above it names the day and
        # the part it belongs to.
        assert result.reply_text == f"{BOOKING_DAY_LABEL}, днём — выберите время:"
        assert result.action_data is not None
        buttons = result.action_data["attachments"][0]["payload"]["buttons"]
        assert f"cb:book:pick_slot:11:22:{BOOKING_DATE}T14:00:00" in [
            b["callback"] for b in buttons
        ]
        # No synthetic show_slots tool_call any more: the part picker reads the
        # day's times directly, the same way the date picker has always read
        # the dates list — neither is an LLM-grounded artefact. The audit row
        # is written explicitly instead (see _render_part_picker).
        assert result.tool_calls_made == []
        assert client.times_calls == [{"staff_id": 11, "date": BOOKING_DATE, "service_ids": [22]}]

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

    def test_pick_date_does_not_call_get_available_dates(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """DRF-997: when the user already tapped a date, show_slots must
        fetch the day's times directly — no 14-day fan-out via
        get_available_dates."""
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        # Deliberately empty: if show_slots still called get_available_dates,
        # it would conclude there are no dates and never reach get_available_times.
        client.dates = []
        client.times = [
            AvailableTime(time="14:00", datetime=f"{BOOKING_DATE}T14:00:00", seance_length_s=3600)
        ]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_date:11:{BOOKING_DATE}:22",
        )
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        assert result.should_handoff is False
        # DRF-1325 renamed the prompt (see TestDatePickCallback above); the
        # property this test exists for — no 14-day fan-out once the user has
        # named a day — is unchanged and still asserted below.
        assert result.reply_text == f"{BOOKING_DAY_LABEL}, днём — выберите время:"
        assert client.dates_calls == []
        assert client.times_calls == [{"staff_id": 11, "date": BOOKING_DATE, "service_ids": [22]}]


class TestRateLimitedScheduleUnavailable:
    """DRF-997: a backend 429 must not be misclassified as "no slots" and
    must not hand the user off to a manager."""

    def test_429_on_date_pick_does_not_handoff(
        self, context: SkillContext, tenant: Tenant, settings: Any
    ) -> None:
        settings.BOOKING_VIA_AYLA_REST = True

        from apps.integrations.ayla import booking_client as bc

        bc.reset_ayla_booking_client()
        slot_attempts: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            path = req.url.path
            if path == "/api/v1/internal/services/":
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": "svc-uuid-1",
                                "name": "Массаж",
                                "price": "1500.00",
                                "duration_minutes": 60,
                            }
                        ]
                    },
                )
            if path == "/api/v1/internal/specialists/":
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": "master-uuid",
                                "display_name": "Ольга",
                                "specialization": "Массаж",
                                "rating": 4.5,
                                "position": "master",
                            }
                        ]
                    },
                )
            slot_attempts.append(req)
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                json={"error": {"code": "RATE_LIMITED"}},
            )

        ayla_client = bc.AylaBookingHTTPClient(
            base_url="https://ayla.test",
            api_token="secret-tok",
            transport=httpx.MockTransport(handler),
        )
        adapter = AylaYClientsAdapter(
            client=ayla_client,
            external_user_id="bot:telegram:42",
            client_id="client-uuid",
        )

        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_date:master-uuid:{BOOKING_DATE}:svc-uuid-1",
        )
        with (
            patch("apps.skills.booking.provider.get_booking_provider", return_value=adapter),
            _patch_provider_complete([]) as mock_complete,
        ):
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert result.should_handoff is False
        assert "переключаю" not in result.reply_text.lower()
        assert "сервис расписания" in result.reply_text.lower()
        assert len(slot_attempts) == bc.RATE_LIMIT_MAX_RETRIES + 1


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

    RB1.1-D05: pick_slot is a fully deterministic short-circuit — same
    pattern as pick_master / pick_date. The callback payload carries
    master + service + slot; the skill validates them against live
    tenant data, re-checks slot availability, and builds the confirm
    preview + PendingBookingAction WITHOUT any LLM call. (The previous
    synth-query → Phase-1 LLM path looped back to show_masters because
    the stateless prompt refused to ground raw UUIDs.)
    """

    def test_matches_callback_prefix(self, context: SkillContext) -> None:
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:11:22:{BOOKING_DATE}T14:00:00",
        )
        assert BookingSkill().matches(ctx) is True

    def test_valid_pick_slot_creates_preview_without_llm(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """Valid tap → confirm preview card + PendingBookingAction, zero
        completions consumed (no Phase-1 tool choice, no Phase-3
        rephrase)."""
        from apps.booking.models import PendingBookingAction

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11, "Ольга")]
        client.times = [
            AvailableTime(time="14:00", datetime=f"{BOOKING_DATE}T14:00:00", seance_length_s=3600)
        ]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:11:22:{BOOKING_DATE}T14:00:00",
        )
        with _patch_yclients(client), _patch_provider_complete([]) as mock_complete:
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert result.should_handoff is False
        assert "Подтверждаете?" in result.reply_text
        # Preview keyboard carries the pending token.
        assert result.action_data is not None
        pending_meta = result.action_data["pending_action"]
        assert pending_meta["kind"] == "confirm"
        token = pending_meta["token"]
        buttons = result.action_data["attachments"][0]["payload"]["buttons"]
        assert any(token in b["callback"] for b in buttons)
        # PendingBookingAction persisted with the full selection context.
        row = PendingBookingAction.all_tenants.get(pk=token)
        assert row.kind == PendingBookingAction.Kind.CONFIRM
        assert row.payload["master_id"] == 11
        assert row.payload["service_id"] == 22
        assert row.payload["slot_datetime"] == f"{BOOKING_DATE}T14:00:00"
        assert row.consumed_at is None
        # Synthetic confirm_booking call recorded for telemetry; the LLM
        # never picked a tool.
        assert [tc.name for tc in result.tool_calls_made] == ["confirm_booking"]
        # Slot availability was re-checked against the provider.
        assert client.times_calls == [{"staff_id": 11, "date": BOOKING_DATE, "service_ids": [22]}]

    def test_unknown_service_rejected_locally(self, context: SkillContext, tenant: Tenant) -> None:
        """Service id outside the tenant catalog → safe local reply, no
        pending row, no availability call."""
        from apps.booking.models import PendingBookingAction

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:11:99:{BOOKING_DATE}T14:00:00",
        )
        with _patch_yclients(client), _patch_provider_complete([]) as mock_complete:
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert result.should_handoff is False
        assert "контекст" in result.reply_text.lower()
        assert client.times_calls == []
        assert PendingBookingAction.all_tenants.count() == 0

    def test_unknown_master_rejected_locally(self, context: SkillContext, tenant: Tenant) -> None:
        """Master id outside the tenant roster → safe local reply, no
        pending row, no availability call."""
        from apps.booking.models import PendingBookingAction

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = []  # master 11 not on the roster
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:11:22:{BOOKING_DATE}T14:00:00",
        )
        with _patch_yclients(client), _patch_provider_complete([]) as mock_complete:
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert result.should_handoff is False
        assert "контекст" in result.reply_text.lower()
        assert client.times_calls == []
        assert PendingBookingAction.all_tenants.count() == 0

    def test_slot_taken_offers_fresh_alternatives(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """Tapped slot no longer offered → deterministic «занято» reply
        with a fresh slot keyboard; no pending row is created."""
        from apps.booking.models import PendingBookingAction

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.times = [
            AvailableTime(time="15:00", datetime=f"{BOOKING_DATE}T15:00:00", seance_length_s=3600)
        ]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:11:22:{BOOKING_DATE}T14:00:00",
        )
        with _patch_yclients(client), _patch_provider_complete([]) as mock_complete:
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert result.should_handoff is False
        assert "занято" in result.reply_text.lower()
        assert result.action_data is not None
        buttons = result.action_data["attachments"][0]["payload"]["buttons"]
        assert [b["callback"] for b in buttons] == [
            f"cb:book:pick_slot:11:22:{BOOKING_DATE}T15:00:00"
        ]
        assert PendingBookingAction.all_tenants.count() == 0

    def test_slot_taken_without_alternatives(self, context: SkillContext, tenant: Tenant) -> None:
        """No slots left that day → plain safe message, no keyboard, no
        pending row, no handoff."""
        from apps.booking.models import PendingBookingAction

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.times = []
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:11:22:{BOOKING_DATE}T14:00:00",
        )
        with _patch_yclients(client), _patch_provider_complete([]) as mock_complete:
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert result.should_handoff is False
        assert "занято" in result.reply_text.lower()
        assert result.action_data is None
        assert PendingBookingAction.all_tenants.count() == 0

    def test_duplicate_tap_reuses_same_pending(self, context: SkillContext, tenant: Tenant) -> None:
        """Second tap on the same slot button returns the existing
        preview token instead of stacking a second pending row."""
        from apps.booking.models import PendingBookingAction

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.times = [
            AvailableTime(time="14:00", datetime=f"{BOOKING_DATE}T14:00:00", seance_length_s=3600)
        ]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:11:22:{BOOKING_DATE}T14:00:00",
        )
        with _patch_yclients(client), _patch_provider_complete([]) as mock_complete:
            with tenant_scope(tenant):
                first = BookingSkill().handle(ctx)
                second = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert first.action_data is not None
        assert second.action_data is not None
        token_first = first.action_data["pending_action"]["token"]
        token_second = second.action_data["pending_action"]["token"]
        assert token_first == token_second
        assert PendingBookingAction.all_tenants.count() == 1
        # Reuse happens BEFORE the availability re-check: the second tap
        # must not hit the provider again.
        assert len(client.times_calls) == 1

    def test_slot_recheck_provider_failure_handoffs(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """Provider error on the availability re-check → friendly
        handoff, no pending row."""

        class _FailingTimesClient(FakeYClients):
            def get_available_times(self, **kwargs: Any) -> list:
                raise YClientsAPIError("boom")

        from apps.booking.models import PendingBookingAction

        client = _FailingTimesClient()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:11:22:{BOOKING_DATE}T14:00:00",
        )
        with _patch_yclients(client), _patch_provider_complete([]) as mock_complete:
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_yclients_failure"
        assert PendingBookingAction.all_tenants.count() == 0

    def test_empty_callback_payload_rejected_locally(
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
        assert result.should_handoff is False
        assert "время" in result.reply_text.lower() or "ещё раз" in result.reply_text.lower()
        assert result.tool_calls_made == []

    def test_health_check_gated_service_handoffs_on_pick_slot(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """The deterministic pick_slot path honours the same health-check
        gate as the LLM confirm path — gated service → handoff, no pending
        row, no availability call."""
        from django.utils import timezone as dj_timezone

        from apps.booking.models import PendingBookingAction
        from apps.catalog.models import CatalogService

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
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:11:22:{BOOKING_DATE}T14:00:00",
        )
        with _patch_yclients(client), _patch_provider_complete([]) as mock_complete:
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_health_check_required"
        assert client.times_calls == []
        assert PendingBookingAction.all_tenants.count() == 0

    def test_offset_drift_slot_still_matches(self, context: SkillContext, tenant: Tenant) -> None:
        """Same instant in a different ISO offset format must still match
        the availability re-check (flag-ON Ayla datetimes carry offsets)."""
        from apps.booking.models import PendingBookingAction

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.times = [
            AvailableTime(
                time="11:00",
                datetime=f"{BOOKING_DATE}T11:00:00+00:00",
                seance_length_s=3600,
            )
        ]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:11:22:{BOOKING_DATE}T14:00:00+03:00",
        )
        with _patch_yclients(client), _patch_provider_complete([]) as mock_complete:
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert result.should_handoff is False
        assert "Подтверждаете?" in result.reply_text
        assert PendingBookingAction.all_tenants.count() == 1

    def test_flag_on_uuid_pick_slot_creates_pending(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """BOOKING_VIA_AYLA_REST=True: UUID ids survive the deterministic
        path end-to-end into the PendingBookingAction payload. This is the
        configuration where RB1.1-D05 manifested live.

        Health-gate fail-closed semantics are covered by separate unit tests.
        This test isolates the gate locally to keep successful deterministic
        booking-path coverage separate from the fail-closed gate behavior.
        """
        import uuid as _uuid

        from django.utils import timezone as dj_timezone

        from apps.booking.models import PendingBookingAction
        from apps.catalog.models import CatalogService

        master_uuid = "11111111-1111-4111-8111-111111111111"
        service_uuid = "22222222-2222-4222-8222-222222222222"
        with tenant_scope(tenant):
            # The health-check gate fails closed on a mirror miss under
            # flag-ON — a matched, not-gated row lets the booking through.
            CatalogService.objects.create(
                tenant=tenant,
                external_id=22,
                external_updated_at=dj_timezone.now(),
                slug="svc-22",
                name="Service 22",
                requires_health_check=False,
                ayla_service_id=_uuid.UUID(service_uuid),
            )
        client = FakeYClients()
        client.services_rows = [_service(service_uuid)]
        client.staff_rows = [_staff(master_uuid, "Ольга")]
        client.times = [
            AvailableTime(time="14:00", datetime=f"{BOOKING_DATE}T14:00:00", seance_length_s=3600)
        ]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:{master_uuid}:{service_uuid}:{BOOKING_DATE}T14:00:00",
        )
        with override_settings(BOOKING_VIA_AYLA_REST=True):
            with (
                patch(
                    "apps.skills.booking.provider.get_booking_provider",
                    return_value=client,
                ),
                patch(
                    "apps.skills.booking.skill._service_requires_health_check",
                    return_value=False,
                ),
                _patch_provider_complete([]) as mock_complete,
            ):
                with tenant_scope(tenant):
                    result = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert result.should_handoff is False
        assert "Подтверждаете?" in result.reply_text
        assert result.action_data is not None
        token = result.action_data["pending_action"]["token"]
        row = PendingBookingAction.all_tenants.get(pk=token)
        assert row.payload["master_id"] == master_uuid
        assert row.payload["service_id"] == service_uuid
        assert row.payload["slot_datetime"] == f"{BOOKING_DATE}T14:00:00"
        assert client.times_calls == [
            {"staff_id": master_uuid, "date": BOOKING_DATE, "service_ids": [service_uuid]}
        ]

    def test_pick_slot_with_canonical_catalog_service_accepted(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """DRF-1004 regression: through the REAL Ayla HTTP client + adapter,
        a service served by the canonical ``catalog/salon-services/`` feed
        must pass the pick_slot catalog check. Pre-fix the client read the
        dead legacy ``services/`` feed (empty upstream), so EVERY pick_slot
        ended in ``unknown_service`` / stale-context."""
        import uuid as _uuid

        from django.utils import timezone as dj_timezone

        from apps.booking.models import PendingBookingAction
        from apps.catalog.models import CatalogService
        from apps.integrations.ayla.booking_client import AylaBookingHTTPClient
        from apps.skills.booking.provider import AylaYClientsAdapter

        master_uuid = "11111111-1111-4111-8111-111111111111"
        service_uuid = "22222222-2222-4222-8222-222222222222"
        with tenant_scope(tenant):
            CatalogService.objects.create(
                tenant=tenant,
                external_id=22,
                external_updated_at=dj_timezone.now(),
                slug="svc-22",
                name="Service 22",
                requires_health_check=False,
                ayla_service_id=_uuid.UUID(service_uuid),
            )
        catalog_requests: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            path = req.url.path
            if path.endswith("catalog/salon-services/"):
                catalog_requests.append(req)
                return httpx.Response(
                    200,
                    json={
                        "count": 1,
                        "next": None,
                        "results": [
                            {
                                "id": service_uuid,
                                "tenant": str(tenant.id),
                                "template": None,
                                "category": None,
                                "name": "УЗ-кавитация — 1 зона",
                                "duration_minutes": 40,
                                "base_price": "2800.00",
                            }
                        ],
                    },
                )
            if path.endswith("internal/specialists/"):
                return httpx.Response(
                    200, json=[{"id": master_uuid, "display_name": "Ольга", "rating": 4.9}]
                )
            if path.endswith(f"specialists/{master_uuid}/slots/"):
                return httpx.Response(200, json={"slots": [f"{BOOKING_DATE}T14:00:00"]})
            return httpx.Response(404, json={})

        client = AylaBookingHTTPClient(
            base_url="https://ayla.test",
            api_token="secret-tok",  # noqa: S106  # pragma: allowlist secret
            transport=httpx.MockTransport(handler),
        )
        adapter = AylaYClientsAdapter(client=client, external_user_id="bot:max:u1")
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:{master_uuid}:{service_uuid}:{BOOKING_DATE}T14:00:00",
        )
        with override_settings(BOOKING_VIA_AYLA_REST=True):
            with (
                patch(
                    "apps.skills.booking.provider.get_booking_provider",
                    return_value=adapter,
                ),
                patch(
                    "apps.skills.booking.skill._service_requires_health_check",
                    return_value=False,
                ),
                _patch_provider_complete([]) as mock_complete,
            ):
                with tenant_scope(tenant):
                    result = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert result.should_handoff is False
        assert "Подтверждаете?" in result.reply_text
        assert result.action_data is not None
        token = result.action_data["pending_action"]["token"]
        row = PendingBookingAction.all_tenants.get(pk=token)
        assert row.payload["service_id"] == service_uuid
        # The catalog read was scoped to the active tenant (DRF-1004 §4.1).
        assert catalog_requests
        assert catalog_requests[0].url.params["tenant"] == str(tenant.id)

    def test_staff_fetch_failure_handoffs_not_stale(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """Provider blip on the roster fetch must hand off, not tell the
        user their context is stale — the two failure modes are different."""

        class _FailingStaffClient(FakeYClients):
            def get_staff(self, *, staff_id: Any = None) -> list:
                raise YClientsUnavailableError("circuit_open")

        from apps.booking.models import PendingBookingAction

        client = _FailingStaffClient()
        client.services_rows = [_service(22)]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:11:22:{BOOKING_DATE}T14:00:00",
        )
        with _patch_yclients(client), _patch_provider_complete([]) as mock_complete:
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_yclients_failure"
        assert PendingBookingAction.all_tenants.count() == 0

    def test_new_preview_supersedes_earlier_pending(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """Tap slot A then slot B → pending A is superseded (consumed), so
        two different preview cards can't both be executed into two real
        bookings."""
        from apps.booking.models import PendingBookingAction

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.times = [
            AvailableTime(time="14:00", datetime=f"{BOOKING_DATE}T14:00:00", seance_length_s=3600),
            AvailableTime(time="15:00", datetime=f"{BOOKING_DATE}T15:00:00", seance_length_s=3600),
        ]

        def _tap(dt: str) -> SkillResult:
            return BookingSkill().handle(
                SkillContext(
                    conversation=context.conversation,
                    bot_user=context.bot_user,
                    message_text=f"cb:book:pick_slot:11:22:{dt}",
                )
            )

        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                first = _tap(f"{BOOKING_DATE}T14:00:00")
                second = _tap(f"{BOOKING_DATE}T15:00:00")
        assert first.action_data is not None
        assert second.action_data is not None
        token_a = first.action_data["pending_action"]["token"]
        token_b = second.action_data["pending_action"]["token"]
        assert token_a != token_b
        row_a = PendingBookingAction.all_tenants.get(pk=token_a)
        row_b = PendingBookingAction.all_tenants.get(pk=token_b)
        # A is consumed (superseded) — a ✅ tap on the old card gets the
        # "already handled" reply instead of executing a second create.
        assert row_a.consumed_at is not None
        assert row_b.consumed_at is None

    def test_past_slot_rejected_locally(self, context: SkillContext, tenant: Tenant) -> None:
        """A day-old keyboard's slot is already in the past — recover
        locally instead of hitting the provider with a date it 4xx-es on."""
        from apps.booking.models import PendingBookingAction

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="cb:book:pick_slot:11:22:2020-01-01T14:00:00",
        )
        with _patch_yclients(client), _patch_provider_complete([]) as mock_complete:
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert result.should_handoff is False
        assert "контекст" in result.reply_text.lower()
        assert client.times_calls == []
        assert PendingBookingAction.all_tenants.count() == 0

    def test_naive_aware_wall_clock_match(self, context: SkillContext, tenant: Tenant) -> None:
        """Provider emits both offset-aware and naive datetimes; the same
        wall-clock slot in mixed forms must NOT be reported as taken."""
        from apps.booking.models import PendingBookingAction

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.times = [
            AvailableTime(
                time="14:00",
                datetime=f"{BOOKING_DATE}T14:00:00+03:00",  # aware form
                seance_length_s=3600,
            )
        ]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:11:22:{BOOKING_DATE}T14:00:00",  # naive form
        )
        with _patch_yclients(client), _patch_provider_complete([]) as mock_complete:
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert result.should_handoff is False
        assert "Подтверждаете?" in result.reply_text
        assert PendingBookingAction.all_tenants.count() == 1

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
        client.dates = [BOOKING_DATE]
        tc = ToolCall(
            id="c1",
            name="show_masters",
            arguments={"service_name": "массаж", "service_id": 22},
        )
        with _patch_yclients(client), _patch_provider_complete([_completion(tool_calls=[tc])]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.action_data is not None
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
        client.dates = [BOOKING_DATE]
        client.times = [
            AvailableTime(time="14:00", datetime=f"{BOOKING_DATE}T14:00:00", seance_length_s=3600)
        ]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_date:11:{BOOKING_DATE}:22",
        )
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        assert result.should_handoff is False
        # DRF-1325: the date tap no longer synthesises a show_slots ToolCall,
        # so "the service id survived the tap" is now read off the call the
        # flow actually makes — and off the slot buttons, which is where the
        # id has to be for the NEXT tap to work.
        assert result.tool_calls_made == []
        assert client.times_calls == [{"staff_id": 11, "date": BOOKING_DATE, "service_ids": [22]}]
        assert result.action_data is not None
        buttons = result.action_data["attachments"][0]["payload"]["buttons"]
        assert all(":22:" in b["callback"] for b in buttons)

    def test_service_id_survives_slot_pick_pending(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """RB1.1-D05: pick_slot is deterministic — the service_id from the
        callback lands in the PendingBookingAction payload without any LLM
        round-trip."""
        from apps.booking.models import PendingBookingAction

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.times = [
            AvailableTime(time="14:00", datetime=f"{BOOKING_DATE}T14:00:00", seance_length_s=3600)
        ]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:11:22:{BOOKING_DATE}T14:00:00",
        )
        with _patch_yclients(client), _patch_provider_complete([]) as mock_complete:
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert result.should_handoff is False
        assert result.action_data is not None
        token = result.action_data["pending_action"]["token"]
        row = PendingBookingAction.all_tenants.get(pk=token)
        assert row.payload["service_id"] == 22

    def test_full_button_chain_master_date_slot_to_preview(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """End-to-end button chain: exactly one Phase-1 completion (the
        initial show_masters); every callback step is deterministic and
        the chain terminates in a confirm preview + pending row."""
        from apps.booking.models import PendingBookingAction

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11, "Ольга")]
        client.dates = [BOOKING_DATE]
        client.times = [
            AvailableTime(time="14:00", datetime=f"{BOOKING_DATE}T14:00:00", seance_length_s=3600)
        ]
        tc = ToolCall(
            id="c1",
            name="show_masters",
            arguments={"service_name": "массаж", "service_id": 22},
        )
        skill = BookingSkill()

        def _tap(text: str) -> SkillResult:
            return skill.handle(
                SkillContext(
                    conversation=context.conversation,
                    bot_user=context.bot_user,
                    message_text=text,
                )
            )

        def _callbacks(res: SkillResult) -> list[str]:
            assert res.action_data is not None
            return [b["callback"] for b in res.action_data["attachments"][0]["payload"]["buttons"]]

        with (
            _patch_yclients(client),
            _patch_provider_complete([_completion(tool_calls=[tc])]) as mock_complete,
        ):
            with tenant_scope(tenant):
                r_master = skill.handle(context)
                cb_master = next(
                    c for c in _callbacks(r_master) if c.startswith("cb:book:pick_master:")
                )
                r_date = _tap(cb_master)
                cb_date = next(c for c in _callbacks(r_date) if c.startswith("cb:book:pick_date:"))
                r_slot = _tap(cb_date)
                cb_slot = next(c for c in _callbacks(r_slot) if c.startswith("cb:book:pick_slot:"))
                r_preview = _tap(cb_slot)
        # Only the very first turn consumed an LLM completion.
        assert mock_complete.call_count == 1
        assert "Подтверждаете?" in r_preview.reply_text
        assert r_preview.action_data is not None
        token = r_preview.action_data["pending_action"]["token"]
        row = PendingBookingAction.all_tenants.get(pk=token)
        assert row.kind == PendingBookingAction.Kind.CONFIRM
        assert row.payload["master_id"] == 11
        assert row.payload["service_id"] == 22
        assert row.payload["slot_datetime"] == f"{BOOKING_DATE}T14:00:00"


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

    def test_second_llm_preview_supersedes_first(
        self, context: SkillContext, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Two consecutive LLM-path previews → only the latest is executable.

        "запиши к Анне в 15:00" then "а лучше в 17:00": both go through the
        confirm_booking tool; the first pending must be superseded so the
        two preview cards can't both be ✅-executed into two real bookings.
        """
        from apps.booking.models import PendingBookingAction

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11, "Ольга")]

        def _preview(slot: str) -> SkillResult:
            tc = ToolCall(
                id="c1",
                name="confirm_booking",
                arguments={
                    "master_id": 11,
                    "service_id": 22,
                    "slot_datetime": slot,
                },
            )
            completions = [
                _completion(tool_calls=[tc]),
                _completion(text="Записываю — подтверждаете?"),
            ]
            with _patch_provider_complete(completions):
                with tenant_scope(tenant):
                    return BookingSkill().handle(context)

        with _patch_yclients(client):
            first = _preview("2026-05-20T15:00:00")
            second = _preview("2026-05-20T17:00:00")
        assert first.action_data is not None
        assert second.action_data is not None
        token_a = first.action_data["pending_action"]["token"]
        token_b = second.action_data["pending_action"]["token"]
        assert token_a != token_b
        row_a = PendingBookingAction.all_tenants.get(pk=token_a)
        row_b = PendingBookingAction.all_tenants.get(pk=token_b)
        assert row_a.consumed_at is not None
        assert row_b.consumed_at is None


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
        client.dates = [BOOKING_DATE]
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

    #1034 / #1121: under ``BOOKING_VIA_AYLA_REST`` the helper fails CLOSED
    whenever it cannot prove a verdict. Every case below calls it WITHOUT a
    master, which is exactly the unprovable case — the resolved verdict is
    per (master×service), so no master means no verdict, and the
    service-level ``CatalogService.requires_health_check`` is still NOT
    trusted as a stand-in (it carries one of three inputs to Ayla's
    escalate-only OR, so a service-level ``False`` remains a fail-OPEN risk
    for a specific master).

    DRF-1353 added the provable case; it lives in
    ``TestResolvedHealthCheckGate`` below. The flag-OFF (int
    ``external_id``) path is unchanged (lenient default).
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

    def test_flag_on_gated_row_fails_closed(self, tenant: Tenant) -> None:
        from apps.skills.booking.skill import _service_requires_health_check

        self._make_row(tenant, ayla_service_id=self._AYLA_UUID, gated=True, external_id=22)
        with override_settings(BOOKING_VIA_AYLA_REST=True):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, self._AYLA_UUID) is True

    def test_flag_on_not_gated_row_still_fails_closed(self, tenant: Tenant) -> None:
        # #1034 fail-closed: even a service-level NOT-gated row must NOT let the
        # booking through under flag-ON — the resolved (master×service) source
        # (S3B PR-2) is the only trusted one. Guards against a fail-OPEN regress.
        from apps.skills.booking.skill import _service_requires_health_check

        self._make_row(tenant, ayla_service_id=self._AYLA_UUID, gated=False, external_id=22)
        with override_settings(BOOKING_VIA_AYLA_REST=True):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, self._AYLA_UUID) is True

    def test_flag_on_no_row_fails_closed(self, tenant: Tenant) -> None:
        from apps.skills.booking.skill import _service_requires_health_check

        with override_settings(BOOKING_VIA_AYLA_REST=True):
            with tenant_scope(tenant):
                assert (
                    _service_requires_health_check(tenant, "22222222-2222-2222-2222-222222222222")
                    is True
                )

    def test_flag_off_grounds_against_external_id(self, tenant: Tenant) -> None:
        # Legacy YClients path unchanged: reads service-level requires_health_check
        # by int external_id; missing row → lenient default False.
        from apps.skills.booking.skill import _service_requires_health_check

        self._make_row(tenant, ayla_service_id=self._AYLA_UUID, gated=True, external_id=22)
        with override_settings(BOOKING_VIA_AYLA_REST=False):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, 22) is True
                assert _service_requires_health_check(tenant, 999) is False  # miss → lenient

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


# ---------------------------------------------------------------------------
# DRF-1353 — resolved (master×service) health-check verdict
# ---------------------------------------------------------------------------


class TestResolvedHealthCheckGate:
    """The gate reads Ayla's resolved per-edge verdict, mirrored on
    ``MasterService.resolved_requires_health_check``.

    Four salons of the pilot's five could not book at all because the gate
    fell through to a one-tenant allowlist. The verdict is the real source:
    ``True`` gates, ``False`` opens, ``NULL``/absent stays unprovable and
    therefore closed.
    """

    _MASTER = "11111111-1111-4111-8111-111111111111"
    _SERVICE = "22222222-2222-4222-8222-222222222222"
    _OTHER_MASTER = "44444444-4444-4444-8444-444444444444"

    def _edge(self, tenant: Tenant, *, resolved: bool | None, with_row: bool = True) -> None:
        """Mirror one master×service edge with the given resolved verdict."""
        import uuid as _uuid

        from django.utils import timezone as dj_timezone

        from apps.catalog.models import CatalogMaster, CatalogService, MasterService

        with tenant_scope(tenant):
            master = CatalogMaster.objects.create(
                id=_uuid.UUID(self._MASTER),
                tenant=tenant,
                external_updated_at=dj_timezone.now(),
                name="Ольга",
            )
            service = CatalogService.objects.create(
                tenant=tenant,
                external_id=22,
                external_updated_at=dj_timezone.now(),
                slug="svc-22",
                name="Массаж головы",
                # Deliberately False: the salon layer says "no screening" in
                # all five pilot tenants. The gate must not read this column.
                requires_health_check=False,
                ayla_service_id=_uuid.UUID(self._SERVICE),
            )
            if with_row:
                MasterService.objects.create(
                    tenant=tenant,
                    master=master,
                    service=service,
                    ayla_specialist_service_id=_uuid.uuid4(),
                    resolved_requires_health_check=resolved,
                )

    def test_resolved_false_opens_the_gate_without_any_allowlist(self, tenant: Tenant) -> None:
        """The four blocked salons: Ayla says no screening → booking proceeds,
        with an EMPTY allowlist."""
        from apps.skills.booking.skill import _service_requires_health_check

        self._edge(tenant, resolved=False)
        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset(),
        ):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, self._SERVICE, self._MASTER) is False

    def test_resolved_true_gates_even_for_an_allowlisted_tenant(self, tenant: Tenant) -> None:
        """The negative case, and the tightening: a service that genuinely
        needs screening still routes to a human on the ONE tenant the old
        allowlist made unreachable by the gate."""
        from apps.skills.booking.skill import _service_requires_health_check

        self._edge(tenant, resolved=True)
        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset({str(tenant.id)}),
        ):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, self._SERVICE, self._MASTER) is True

    def test_resolved_true_writes_no_gate_disabled_audit(self, tenant: Tenant) -> None:
        """A gate that FIRED is not a gate that was disabled — the DRF-1005
        audit row must not appear for it, or the audit trail stops meaning
        anything."""
        from apps.audit.models import AuditLog
        from apps.skills.booking.skill import _service_requires_health_check

        self._edge(tenant, resolved=True)
        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset({str(tenant.id)}),
        ):
            with tenant_scope(tenant):
                _service_requires_health_check(tenant, self._SERVICE, self._MASTER)
        assert not AuditLog.all_tenants.filter(action="booking.health_check_gate_disabled").exists()

    def test_null_column_stays_unprovable_and_closed(self, tenant: Tenant) -> None:
        """An edge row that was never synced (operator-owned MM4 row, or a
        row written before the column existed) proves nothing."""
        from apps.skills.booking.skill import _service_requires_health_check

        self._edge(tenant, resolved=None)
        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset(),
        ):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, self._SERVICE, self._MASTER) is True

    def test_null_column_still_falls_back_to_the_allowlist(self, tenant: Tenant) -> None:
        """Unknown keeps DRF-1005's original job intact — the allowlist is
        demoted, not deleted."""
        from apps.skills.booking.skill import _service_requires_health_check

        self._edge(tenant, resolved=None)
        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset({str(tenant.id)}),
        ):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, self._SERVICE, self._MASTER) is False

    def test_no_edge_row_fails_closed(self, tenant: Tenant) -> None:
        from apps.skills.booking.skill import _service_requires_health_check

        self._edge(tenant, resolved=False, with_row=False)
        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset(),
        ):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, self._SERVICE, self._MASTER) is True

    def test_other_master_on_the_same_service_is_not_borrowed(self, tenant: Tenant) -> None:
        """The verdict is per EDGE. A permissive verdict for one master must
        never answer for another — that is the fail-OPEN risk #1121 named."""
        from apps.skills.booking.skill import _service_requires_health_check

        self._edge(tenant, resolved=False)
        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset(),
        ):
            with tenant_scope(tenant):
                assert (
                    _service_requires_health_check(tenant, self._SERVICE, self._OTHER_MASTER)
                    is True
                )

    def test_non_uuid_ids_fail_closed_without_raising(self, tenant: Tenant) -> None:
        """A stray legacy int reaching the UUID columns is an unresolvable
        edge, not a permissive one — and must not blow up the customer turn."""
        from apps.skills.booking.skill import _service_requires_health_check

        self._edge(tenant, resolved=False)
        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset(),
        ):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, 22, 11) is True

    def test_foreign_tenant_edge_is_not_read(self, tenant: Tenant) -> None:
        """The lookup is tenant-pinned: another tenant's permissive verdict
        for the same ids can never open this tenant's gate."""
        from apps.skills.booking.skill import _service_requires_health_check
        from apps.tenancy.models import Tenant as TenantModel

        other = TenantModel.objects.create(name="Other", slug="other-tenant")
        self._edge(other, resolved=False)
        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset(),
        ):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, self._SERVICE, self._MASTER) is True

    def test_pick_slot_reaches_confirmation_on_resolved_false(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """End-to-end on the exact path that failed on the pilot: tap a time
        → confirmation card, not a handoff, with an empty allowlist."""
        from apps.booking.models import PendingBookingAction
        from apps.handoff.models import AdminTask

        self._edge(tenant, resolved=False)
        client = FakeYClients()
        client.services_rows = [_service(self._SERVICE)]
        client.staff_rows = [_staff(self._MASTER, "Ольга")]
        client.times = [
            AvailableTime(time="14:00", datetime=f"{BOOKING_DATE}T14:00:00", seance_length_s=3600)
        ]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=(
                f"cb:book:pick_slot:{self._MASTER}:{self._SERVICE}:{BOOKING_DATE}T14:00:00"
            ),
        )
        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset(),
        ):
            with (
                patch(
                    "apps.skills.booking.provider.get_booking_provider",
                    return_value=client,
                ),
                _patch_provider_complete([]) as mock_complete,
            ):
                with tenant_scope(tenant):
                    result = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert result.should_handoff is False
        assert PendingBookingAction.all_tenants.count() == 1
        assert AdminTask.all_tenants.count() == 0

    def test_pick_slot_handoffs_on_resolved_true(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """The negative, end-to-end: a genuinely gated edge still answers
        "нужна консультация" and leaves no pending booking."""
        from apps.booking.models import PendingBookingAction

        self._edge(tenant, resolved=True)
        client = FakeYClients()
        client.services_rows = [_service(self._SERVICE)]
        client.staff_rows = [_staff(self._MASTER, "Ольга")]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=(
                f"cb:book:pick_slot:{self._MASTER}:{self._SERVICE}:{BOOKING_DATE}T14:00:00"
            ),
        )
        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset({str(tenant.id)}),
        ):
            with (
                patch(
                    "apps.skills.booking.provider.get_booking_provider",
                    return_value=client,
                ),
                _patch_provider_complete([]),
            ):
                with tenant_scope(tenant):
                    result = BookingSkill().handle(ctx)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_health_check_required"
        assert client.times_calls == []
        assert PendingBookingAction.all_tenants.count() == 0

    def test_confirm_path_passes_the_grounded_master(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """The LLM confirm path must resolve the same verdict as pick_slot;
        before DRF-1353 it only ever had the service id."""
        self._edge(tenant, resolved=True)
        client = FakeYClients()
        client.services_rows = [_service(self._SERVICE)]
        client.staff_rows = [_staff(self._MASTER, "Ольга")]
        tc = ToolCall(
            id="c1",
            name="confirm_booking",
            arguments={
                "master_id": self._MASTER,
                "service_id": self._SERVICE,
                "slot_datetime": f"{BOOKING_DATE}T14:00:00",
            },
        )
        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset({str(tenant.id)}),
        ):
            with (
                patch(
                    "apps.skills.booking.provider.get_booking_provider",
                    return_value=client,
                ),
                _patch_provider_complete([_completion(tool_calls=[tc])]),
            ):
                with tenant_scope(tenant):
                    result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_health_check_required"


# ---------------------------------------------------------------------------
# DRF-1005 — tenant-scoped health-check gate allowlist (Controlled Pilot)
# ---------------------------------------------------------------------------


class TestHealthCheckGateAllowlist:
    """DRF-1005: ``BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS`` allowlist.

    Owner decision 2026-08-12 (variant 3): for explicitly listed pilot
    tenants the flag-ON (Ayla REST) health-check gate is DISABLED so the
    automatic booking funnel works end-to-end; every other tenant keeps
    the fail-closed default (#1034 / #1121). Every gate-disabled
    evaluation must leave an audit trail. Controlled Pilot only — the
    canonical resolved (master×service) source replaces this setting.
    """

    _AYLA_UUID = "11111111-1111-1111-1111-111111111111"
    _OTHER_TENANT = "33333333-3333-3333-3333-333333333333"

    def test_allowlisted_tenant_gate_opens(self, tenant: Tenant) -> None:
        from apps.skills.booking.skill import _service_requires_health_check

        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset({str(tenant.id)}),
        ):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, self._AYLA_UUID) is False

    def test_tenant_not_in_allowlist_stays_fail_closed(self, tenant: Tenant) -> None:
        from apps.skills.booking.skill import _service_requires_health_check

        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset({self._OTHER_TENANT}),
        ):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, self._AYLA_UUID) is True

    def test_empty_allowlist_stays_fail_closed(self, tenant: Tenant) -> None:
        from apps.skills.booking.skill import _service_requires_health_check

        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset(),
        ):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, self._AYLA_UUID) is True

    def test_malformed_injected_value_fails_closed(self, tenant: Tenant) -> None:
        """A malformed value injected past settings load (e.g. a raw test
        override) must neither crash the customer turn nor silently widen
        access: the gate stays closed."""
        from apps.skills.booking.skill import _service_requires_health_check

        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS="not-a-uuid",
        ):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, self._AYLA_UUID) is True

    def test_no_tenant_scope_stays_fail_closed(self, tenant: Tenant) -> None:
        """Outside an active ``tenant_scope`` there is no identity to match
        against the allowlist → gate stays closed even when the tenant is
        listed."""
        from apps.skills.booking.skill import _service_requires_health_check

        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset({str(tenant.id)}),
        ):
            assert _service_requires_health_check(tenant, self._AYLA_UUID) is True

    def test_gate_disabled_writes_audit(self, tenant: Tenant) -> None:
        """Owner requirement: disabling a medical screening check must be
        traceable — every gate-disabled evaluation writes an audit row."""
        from apps.audit.models import AuditLog
        from apps.skills.booking.skill import _service_requires_health_check

        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset({str(tenant.id)}),
        ):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, self._AYLA_UUID) is False
                row = AuditLog.all_tenants.get(
                    tenant=tenant,
                    action="booking.health_check_gate_disabled",
                )
        assert row.payload["tenant_id"] == str(tenant.id)
        assert row.payload["service_id"] == self._AYLA_UUID

    def test_gate_closed_writes_no_audit(self, tenant: Tenant) -> None:
        """The fail-closed default is the status quo — no extra audit
        noise for the regular handoff path."""
        from apps.audit.models import AuditLog
        from apps.skills.booking.skill import _service_requires_health_check

        with override_settings(BOOKING_VIA_AYLA_REST=True):
            with tenant_scope(tenant):
                assert _service_requires_health_check(tenant, self._AYLA_UUID) is True
        assert not AuditLog.all_tenants.filter(action="booking.health_check_gate_disabled").exists()

    def test_pick_slot_allowlisted_tenant_reaches_confirmation(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """Acceptance: tenant in the allowlist → pick_slot reaches the
        confirmation card; no handoff, hence no AdminTask downstream."""
        import uuid as _uuid

        from django.utils import timezone as dj_timezone

        from apps.booking.models import PendingBookingAction
        from apps.catalog.models import CatalogService
        from apps.handoff.models import AdminTask

        master_uuid = "11111111-1111-4111-8111-111111111111"
        service_uuid = "22222222-2222-4222-8222-222222222222"
        with tenant_scope(tenant):
            CatalogService.objects.create(
                tenant=tenant,
                external_id=22,
                external_updated_at=dj_timezone.now(),
                slug="svc-22",
                name="Service 22",
                requires_health_check=False,
                ayla_service_id=_uuid.UUID(service_uuid),
            )
        client = FakeYClients()
        client.services_rows = [_service(service_uuid)]
        client.staff_rows = [_staff(master_uuid, "Ольга")]
        client.times = [
            AvailableTime(time="14:00", datetime=f"{BOOKING_DATE}T14:00:00", seance_length_s=3600)
        ]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:{master_uuid}:{service_uuid}:{BOOKING_DATE}T14:00:00",
        )
        with override_settings(
            BOOKING_VIA_AYLA_REST=True,
            BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=frozenset({str(tenant.id)}),
        ):
            with (
                patch(
                    "apps.skills.booking.provider.get_booking_provider",
                    return_value=client,
                ),
                _patch_provider_complete([]) as mock_complete,
            ):
                with tenant_scope(tenant):
                    result = BookingSkill().handle(ctx)
        mock_complete.assert_not_called()
        assert result.should_handoff is False
        assert "Подтверждаете?" in result.reply_text
        assert PendingBookingAction.all_tenants.count() == 1
        assert AdminTask.all_tenants.count() == 0

    def test_pick_slot_non_allowlisted_tenant_still_handoffs(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """Default-protection regression: flag-ON without an allowlist
        entry keeps the fail-closed handoff on pick_slot."""
        import uuid as _uuid

        from django.utils import timezone as dj_timezone

        from apps.booking.models import PendingBookingAction
        from apps.catalog.models import CatalogService

        master_uuid = "11111111-1111-4111-8111-111111111111"
        service_uuid = "22222222-2222-4222-8222-222222222222"
        with tenant_scope(tenant):
            CatalogService.objects.create(
                tenant=tenant,
                external_id=22,
                external_updated_at=dj_timezone.now(),
                slug="svc-22",
                name="Service 22",
                requires_health_check=False,
                ayla_service_id=_uuid.UUID(service_uuid),
            )
        client = FakeYClients()
        client.services_rows = [_service(service_uuid)]
        client.staff_rows = [_staff(master_uuid, "Ольга")]
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:{master_uuid}:{service_uuid}:{BOOKING_DATE}T14:00:00",
        )
        with override_settings(BOOKING_VIA_AYLA_REST=True):
            with (
                patch(
                    "apps.skills.booking.provider.get_booking_provider",
                    return_value=client,
                ),
                _patch_provider_complete([]),
            ):
                with tenant_scope(tenant):
                    result = BookingSkill().handle(ctx)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_health_check_required"
        assert PendingBookingAction.all_tenants.count() == 0

    def test_health_check_handoff_uses_policy_text_pick_slot(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """DRF-1005 §3.3: the health-check handoff is a POLICY, not a
        failure — the user must see the dedicated consultation text, not
        the generic failure fallback."""
        from django.utils import timezone as dj_timezone

        from apps.catalog.models import CatalogService
        from apps.skills.booking.skill import (
            _FALLBACK_HANDOFF_TEXT,
            _HEALTH_CHECK_HANDOFF_TEXT,
        )

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
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=f"cb:book:pick_slot:11:22:{BOOKING_DATE}T14:00:00",
        )
        with _patch_yclients(client), _patch_provider_complete([]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_health_check_required"
        assert result.reply_text == _HEALTH_CHECK_HANDOFF_TEXT
        assert result.reply_text != _FALLBACK_HANDOFF_TEXT

    def test_health_check_handoff_uses_policy_text_llm_path(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """Same policy text on the LLM confirm path (skill.py confirm
        gate) — both health-check branches share the wording."""
        from django.utils import timezone as dj_timezone

        from apps.catalog.models import CatalogService
        from apps.skills.booking.skill import (
            _FALLBACK_HANDOFF_TEXT,
            _HEALTH_CHECK_HANDOFF_TEXT,
        )

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
        assert result.reply_text == _HEALTH_CHECK_HANDOFF_TEXT
        assert result.reply_text != _FALLBACK_HANDOFF_TEXT


# ---------------------------------------------------------------------------
# DRF-997: transient 429 schedule outage must not hand off
# ---------------------------------------------------------------------------


class TestScheduleUnavailableNoHandoff:
    """YClientsScheduleUnavailableError (bounded 429 retry exceeded) is a
    transient infrastructure blip, not a reason to escalate to a manager.
    The skill must surface the deterministic retry text and keep
    ``should_handoff`` false.
    """

    def test_prefetch_services_schedule_unavailable_no_handoff(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """DRF-997 Critical: a 429 on the initial service-catalog fetch is the
        first step of the booking funnel and must NOT become a manager handoff.
        """
        client = FakeYClients()
        client.services_exc = YClientsScheduleUnavailableError("429")
        with _patch_yclients(client):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is False
        assert not result.handoff_reason
        assert SCHEDULE_UNAVAILABLE_TEXT in result.reply_text

    def test_show_masters_schedule_unavailable_no_handoff(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_exc = YClientsScheduleUnavailableError("429")
        tc = ToolCall(id="c1", name="show_masters", arguments={"service_name": "массаж"})
        with _patch_yclients(client), _patch_provider_complete([_completion(tool_calls=[tc])]):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is False
        assert result.reply_text == SCHEDULE_UNAVAILABLE_TEXT

    def test_execute_tool_confirm_schedule_unavailable_no_handoff(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        from apps.skills.booking.skill import (
            CONFIRM_BOOKING_TOOL_SPEC,
            _execute_tool,
        )
        from apps.skills.booking.tools import BookingToolResult

        schedule_unavailable = BookingToolResult(
            text=SCHEDULE_UNAVAILABLE_TEXT, error="schedule_unavailable"
        )
        with patch("apps.skills.booking.skill.confirm_booking", return_value=schedule_unavailable):
            result, handoff_reason = _execute_tool(
                tool_name=CONFIRM_BOOKING_TOOL_SPEC["name"],
                arguments={},
                tenant=tenant,
                bot_user=bot_user,
                yclients=FakeYClients(),
                allowed_service_ids=set(),
                service_lookup={},
                tenant_id=str(tenant.id),
            )
        assert handoff_reason == ""
        assert result.error == "schedule_unavailable"
        assert result.text == SCHEDULE_UNAVAILABLE_TEXT

    def test_execute_tool_cancel_schedule_unavailable_no_handoff(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        from apps.skills.booking.skill import (
            CANCEL_BOOKING_TOOL_SPEC,
            _execute_tool,
        )
        from apps.skills.booking.tools import BookingToolResult

        schedule_unavailable = BookingToolResult(
            text=SCHEDULE_UNAVAILABLE_TEXT, error="schedule_unavailable"
        )
        with patch("apps.skills.booking.skill.cancel_booking", return_value=schedule_unavailable):
            result, handoff_reason = _execute_tool(
                tool_name=CANCEL_BOOKING_TOOL_SPEC["name"],
                arguments={},
                tenant=tenant,
                bot_user=bot_user,
                yclients=FakeYClients(),
                allowed_service_ids=set(),
                service_lookup={},
                tenant_id=str(tenant.id),
            )
        assert handoff_reason == ""
        assert result.error == "schedule_unavailable"
        assert result.text == SCHEDULE_UNAVAILABLE_TEXT

    def test_execute_tool_reschedule_schedule_unavailable_no_handoff(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        from apps.skills.booking.skill import (
            RESCHEDULE_BOOKING_TOOL_SPEC,
            _execute_tool,
        )
        from apps.skills.booking.tools import BookingToolResult

        schedule_unavailable = BookingToolResult(
            text=SCHEDULE_UNAVAILABLE_TEXT, error="schedule_unavailable"
        )
        with patch(
            "apps.skills.booking.skill.reschedule_booking", return_value=schedule_unavailable
        ):
            result, handoff_reason = _execute_tool(
                tool_name=RESCHEDULE_BOOKING_TOOL_SPEC["name"],
                arguments={},
                tenant=tenant,
                bot_user=bot_user,
                yclients=FakeYClients(),
                allowed_service_ids=set(),
                service_lookup={},
                tenant_id=str(tenant.id),
            )
        assert handoff_reason == ""
        assert result.error == "schedule_unavailable"
        assert result.text == SCHEDULE_UNAVAILABLE_TEXT
