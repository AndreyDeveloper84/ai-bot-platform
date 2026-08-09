"""D-10 — booking-flow continuation (multi-turn reschedule via channel).

Staging defect D-10 (2026-08-04): the Simple Reschedule channel flow
«Перенеси мою запись → Первую, на 9 августа в 20:00 → Подтверждаю»
broke because (a) continuation turns carried no keyword and fell to
echo, (b) the Phase-1 LLM prompt had no bookings/date grounding so no
``PendingBookingAction`` was ever persisted, and (c) the confirm gate
was registered after echo.

These tests run the REAL production dispatch shape —
``apps.skills.registry.dispatch(SkillContext(intent=None))`` — across
full multi-turn sequences, asserting per turn: selected skill, flow
stage, pending rows, confirm keyboard and mutation counters.

Sequences (per the D-10 window prompt):

  * A: request → «Первую» → «На 9 августа в 20:00» → «Подтверждаю»
  * B: request → combined selection+slot → «Подтверждаю»
  * C: request → selection+slot → ✅ callback tap
  * D: request → selection+slot → «не надо» (cancel)

Plus flow-state unit coverage, disambiguation negatives and routing
boundaries.
"""

from __future__ import annotations

import contextlib

from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.booking.models import BookingRequest, PendingBookingAction
from apps.bookings.pending_actions import create_pending
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.integrations.yclients import (
    AvailableTime,
    BookingRecord,
    Service,
    Staff,
    UserRecord,
)
from apps.llm.protocol import CompletionResult, ToolCall
from apps.llm.providers.openai_provider import OpenAIProvider
from apps.llm.router import reset_router_cache
from apps.skills.base import SkillContext, SkillResult
from apps.skills.booking.lookup import _SELECTION_SPACED_TIME, looks_like_flow_selection
from apps.skills.booking.skill import (
    _FLOW_STATE_KEY,
    BookingSkill,
    _clear_flow_state,
    _read_flow_state,
    _write_flow_state,
)
from apps.skills.registry import dispatch
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


# ---------------------------------------------------------------------------
# Fixtures + doubles
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, settings: pytest.FixtureRequest):
    settings.BASE_DIR = tmp_path  # type: ignore[attr-defined]
    settings.LLM_PROVIDER = "openai"  # type: ignore[attr-defined]
    settings.SKILL_LLM_PROVIDER = {}  # type: ignore[attr-defined]
    reset_router_cache()
    cache.clear()
    yield
    cache.clear()
    reset_router_cache()


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="flow-d10", name="Flow D10")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="flow-u1",
        chat_id="flow-u1",
        phone="79991234567",
        client_name="Anna",
        # Onboarded user — otherwise the welcome skill's first-contact
        # auto-trigger claims arbitrary text turns before echo.
        welcomed_at=timezone.now(),
    )


@pytest.fixture
def conversation(tenant: Tenant, bot_user: BotUser) -> Conversation:
    return Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)


class _FakeYClients:
    """Booking provider double covering preview + execute paths."""

    def __init__(self) -> None:
        self.user_records: list[UserRecord] = []
        self.times: list[AvailableTime] = []
        self.cancel_calls: list[int] = []
        self.create_calls: list[dict[str, Any]] = []

    # prefetch
    def get_services(self, **_: Any) -> list[Any]:
        return [
            Service(
                id=22,
                title="Массаж",
                price_min=1500.0,
                price_max=2500.0,
                duration_s=3600,
                category_id=None,
                raw={},
            )
        ]

    def get_staff(self, *, staff_id: Any = None) -> list[Any]:
        return [
            Staff(
                id=11,
                name="Ольга",
                specialization="Массаж",
                rating=4.5,
                avatar="",
                position="master",
                raw={},
            )
        ]

    def get_available_dates(self, **_: Any) -> list[str]:
        return []

    # bookings / slots
    def get_user_records(self) -> list[UserRecord]:
        return list(self.user_records)

    def get_available_times(self, **_: Any) -> list[AvailableTime]:
        return list(self.times)

    # mutations
    def cancel_record(self, *, record_id: int) -> None:
        self.cancel_calls.append(record_id)

    def create_record(self, **kwargs: Any) -> BookingRecord:
        self.create_calls.append(kwargs)
        return BookingRecord(record_id=999, record_hash="h9", raw={})


def _user_record(*, id_: int, dt: str) -> UserRecord:
    return UserRecord(
        id=id_,
        services=[{"id": 22, "title": "Массаж"}],
        company={},
        staff={"id": 11, "name": "Ольга"},
        date=dt,
        datetime=dt,
        seance_length=3600,
        raw={},
    )


def _make_booking(tenant: Tenant, bot_user: BotUser, *, yc_id: int) -> BookingRequest:
    return BookingRequest.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        service_name="Массаж",
        master_name="Ольга",
        client_name="Anna",
        client_phone="79991234567",
        comment=f"Bot booking | yclients_record_id={yc_id}",
        source="bot",
        status=BookingRequest.Status.CONFIRMED,
    )


def _three_bookings(tenant: Tenant, bot_user: BotUser, client: _FakeYClients) -> None:
    """Three upcoming bookings (staging fixture shape: 6/7/8 августа)."""
    for yc_id, days in ((551, 2), (552, 3), (554, 4)):
        _make_booking(tenant, bot_user, yc_id=yc_id)
        dt = (timezone.now() + timedelta(days=days)).replace(microsecond=0).isoformat()
        client.user_records.append(_user_record(id_=yc_id, dt=dt))


def _ctx(conversation: Conversation, bot_user: BotUser, text: str) -> SkillContext:
    return SkillContext(
        conversation=conversation,
        bot_user=bot_user,
        message_text=text,
        trace_id="t-d10",
    )


def _completion(*, text: str = "", tool_calls: list[ToolCall] | None = None) -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=tool_calls or [],
        prompt_tokens=10,
        completion_tokens=20,
        model="mock",
        provider="openai",
        finish_reason="stop" if not tool_calls else "tool_calls",
    )


def _tool_call(name: str, arguments: dict[str, Any]) -> ToolCall:
    return ToolCall(id=f"call:{name}", name=name, arguments=arguments)


def _future_iso(hours: int = 72) -> str:
    return (timezone.now() + timedelta(hours=hours)).replace(microsecond=0).isoformat()


def _slot_for(iso_dt: str) -> AvailableTime:
    return AvailableTime(time=iso_dt.split("T", 1)[1][:5], datetime=iso_dt, seance_length_s=3600)


@contextlib.contextmanager
def _booking_handle_spy():
    """Record every entry into ``BookingSkill.handle`` for the block.

    DRF-963 review, ось `tests` (F1): «the reply was the menu fallback» is
    NOT evidence that booking stayed out of a turn. The menu skill emits
    the identical fallback when it DOES route into booking and booking
    escalates — and in this test environment booking always escalates,
    because ``YCLIENTS_PARTNER_TOKEN`` is empty, so every entry collapses
    to the same text. Asserting on the entry itself is the only proxy that
    survives a matcher regression (proven by mutation: widening the
    service vocabulary to swallow «спасибо» / «Подтверждаю» / «Первую»
    left the text-only assertions green).
    """
    from apps.skills.booking.skill import BookingSkill

    entered: list[str] = []
    original = BookingSkill.handle

    def _spy(self, context):
        entered.append(context.message_text)
        return original(self, context)

    with patch.object(BookingSkill, "handle", _spy):
        yield entered


def _flow_state(conversation: Conversation) -> dict[str, Any] | None:
    conversation.refresh_from_db()
    return (conversation.skill_state or {}).get(_FLOW_STATE_KEY)


def _pending_rows() -> list[PendingBookingAction]:
    return list(PendingBookingAction.all_tenants.order_by("created_at"))


# ---------------------------------------------------------------------------
# Flow-state unit coverage
# ---------------------------------------------------------------------------


class TestFlowSelectionDetector:
    """Review D-10 #2 — pure-text boundary of the continuation claim."""

    @pytest.mark.parametrize(
        "text",
        (
            "Первую",
            "первая",
            "вторую",
            "последнюю",
            "2",
            "12",
            "На 9 августа в 20:00",
            "20:00",
            "в 8 вечера",
            "на завтра",
            "в пятницу",
            "9 августа",
            "послезавтра в 10:30",
            # Review round 2 — daypart / relative-time answers to
            # «во сколько?» used to fall through to echo.
            "утром",
            "днём",
            "вечером",
            "ночью",
            "давайте попозже",
            "пораньше",
            "в обед",
            # Review round 3 — spaced time «20 00» (channel users drop
            # the colon); used to fall through to echo with a live flow.
            "20 00",
            "19 30",
            "в 8 30",
            "с 10 00 до 12 00",
            # Review round 3.1 — bare and zero-padded hour forms.
            "8 30",
            "08 30",
        ),
    )
    def test_selection_positives(self, text: str) -> None:
        assert looks_like_flow_selection(text) is True

    @pytest.mark.parametrize(
        "text",
        (
            "спасибо",
            "Хочу маникюр",
            "как дела",
            "подскажите адрес",
            "",
            "   ",
            # Review round 3 — spaced-time detector must stay narrow:
            # ages, pets, short numbers and phone fragments are not
            # selection-shaped. («9 августа» is covered by the date
            # detector and asserted at the spaced-time level below.)
            "мне 30 лет",
            "у меня 3 кота",
            "1 2",
            "8 999 123 45 67",
            "24 61",
        ),
    )
    def test_selection_negatives(self, text: str) -> None:
        assert looks_like_flow_selection(text) is False


class TestSpacedTimeDetector:
    """Review round 3 — the spaced-time regex in isolation, so the
    boundary stays narrow even if other detectors change."""

    @pytest.mark.parametrize(
        "text",
        ("20 00", "19 30", "8 30", "08 30", "в 8 30", "с 10 00 до 12 00"),
    )
    def test_spaced_time_positives(self, text: str) -> None:
        assert _SELECTION_SPACED_TIME.search(text) is not None

    @pytest.mark.parametrize(
        "text",
        (
            "мне 30 лет",
            "у меня 3 кота",
            # Not a spaced-time match — claimed by the date detector at
            # the looks_like_flow_selection() level instead.
            "9 августа",
            "1 2",
            "8 999 123 45 67",
            "24 61",
        ),
    )
    def test_spaced_time_negatives(self, text: str) -> None:
        assert _SELECTION_SPACED_TIME.search(text) is None


class TestFlowStateStore:
    def test_write_read_clear_roundtrip(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        from apps.skills.booking.tools import BookingRow

        row = BookingRow(
            record_id=551,
            visit_at="2026-08-06T17:00:00",
            master_name="Ольга",
            service_name="Массаж",
            status="CONFIRMED",
        )
        with tenant_scope(tenant):
            _write_flow_state(conversation, flow="reschedule", bookings=[row])
            state = _read_flow_state(conversation)
            assert state is not None
            assert state["flow"] == "reschedule"
            assert state["stage"] == "awaiting_selection"
            assert state["bookings"][0]["record_id"] == "551"
            _clear_flow_state(conversation)
            assert _read_flow_state(conversation) is None

    def test_expired_state_is_not_read(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        stale = {
            "flow": "reschedule",
            "stage": "awaiting_selection",
            "bookings": [],
            "expires_at": (timezone.now() - timedelta(minutes=1)).isoformat(),
        }
        Conversation.all_tenants.filter(pk=conversation.pk).update(
            skill_state={_FLOW_STATE_KEY: stale}
        )
        conversation.refresh_from_db()
        assert _read_flow_state(conversation) is None


class TestContinuationMatches:
    def test_claims_selection_turn_with_fresh_state(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        with tenant_scope(tenant):
            _write_flow_state(conversation, flow="reschedule", bookings=[])
        assert BookingSkill().matches(_ctx(conversation, bot_user, "Первую")) is True

    def test_does_not_claim_with_expired_state(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        stale = {
            "flow": "reschedule",
            "stage": "awaiting_selection",
            "bookings": [],
            "expires_at": (timezone.now() - timedelta(minutes=1)).isoformat(),
        }
        Conversation.all_tenants.filter(pk=conversation.pk).update(
            skill_state={_FLOW_STATE_KEY: stale}
        )
        conversation.refresh_from_db()
        assert BookingSkill().matches(_ctx(conversation, bot_user, "Первую")) is False

    def test_yields_confirm_vocab_to_gate_when_pending_live(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        with tenant_scope(tenant):
            _write_flow_state(conversation, flow="reschedule", bookings=[])
        create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.RESCHEDULE,
            payload={"record_id": 551, "new_datetime": _future_iso()},
        )
        # Booking yields; the gate skill (registered right after) claims.
        assert BookingSkill().matches(_ctx(conversation, bot_user, "Подтверждаю")) is False

    def test_does_not_claim_offtopic_turn_with_fresh_state(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        """Review D-10 #2 — «спасибо» / «Хочу маникюр» are NOT flow
        continuations; the claim is bounded to selection-shaped turns."""
        with tenant_scope(tenant):
            _write_flow_state(conversation, flow="reschedule", bookings=[])
        skill = BookingSkill()
        assert skill.matches(_ctx(conversation, bot_user, "спасибо")) is False
        assert skill.matches(_ctx(conversation, bot_user, "Хочу маникюр")) is False

    def test_claims_selection_shaped_turns_with_fresh_state(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        with tenant_scope(tenant):
            _write_flow_state(conversation, flow="reschedule", bookings=[])
        skill = BookingSkill()
        assert skill.matches(_ctx(conversation, bot_user, "На 9 августа в 20:00")) is True
        assert skill.matches(_ctx(conversation, bot_user, "2")) is True
        assert skill.matches(_ctx(conversation, bot_user, "в пятницу")) is True

    def test_confirm_vocab_without_pending_is_claimed(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        """Review round 2 — «Подтверждаю» with a fresh flow but NO live
        pending is exactly the D-10 root-cause degradation (Phase 1
        answered with free-text «Подтверждаете?» and made no tool call,
        so no pending row exists). Booking claims the turn for a
        Phase-1 replay with flow grounding; the gate stays the owner
        whenever a pending row IS live."""
        with tenant_scope(tenant):
            _write_flow_state(conversation, flow="reschedule", bookings=[])
        assert BookingSkill().matches(_ctx(conversation, bot_user, "Подтверждаю")) is True

    def test_booking_request_with_fresh_state_still_claimed(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        """Review round 2 regression — the flow claim must ADD matches,
        not shadow the standard fallbacks: fresh booking requests and
        personal lookups while the flow is alive still route to booking
        (an unconditional selection-check return echo'ed them)."""
        with tenant_scope(tenant):
            _write_flow_state(conversation, flow="reschedule", bookings=[])
        skill = BookingSkill()
        assert skill.matches(_ctx(conversation, bot_user, "Хочу записаться на стрижку")) is True
        assert skill.matches(_ctx(conversation, bot_user, "Когда у меня следующая запись?")) is True
        assert skill.matches(_ctx(conversation, bot_user, "Отмените все мои записи")) is True
        assert skill.matches(_ctx(conversation, bot_user, "Перенеси мою запись")) is True

    def test_does_not_claim_callback_texts(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        with tenant_scope(tenant):
            _write_flow_state(conversation, flow="reschedule", bookings=[])
        ctx = _ctx(
            conversation,
            bot_user,
            "cb:book:confirm:123e4567-e89b-12d3-a456-426614174000",
        )
        assert BookingSkill().matches(ctx) is False


# ---------------------------------------------------------------------------
# Sequence A — full 4-turn happy path through production dispatch
# ---------------------------------------------------------------------------


class TestSequenceA:
    def test_full_reschedule_flow(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        conversation: Conversation,
    ) -> None:
        client = _FakeYClients()
        _three_bookings(tenant, bot_user, client)
        new_dt = _future_iso(120)
        client.times = [_slot_for(new_dt)]

        with (
            patch("apps.integrations.yclients.get_yclients_client", return_value=client),
            tenant_scope(tenant),
        ):
            # ── Turn 1: «Перенеси мою запись» → disambiguation ──────
            with patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[
                    _completion(
                        tool_calls=[_tool_call("show_my_bookings", {})],
                    ),
                    _completion(text="Какую из них перенести?"),
                ],
            ):
                r1 = dispatch(_ctx(conversation, bot_user, "Перенеси мою запись"))
            assert r1 is not None and r1.meta.get("skill") == "booking"
            assert _pending_rows() == []  # no mutation, no pending yet
            state = _flow_state(conversation)
            assert state is not None
            assert state["flow"] == "reschedule"
            assert len(state["bookings"]) == 3

            # ── Turn 2: «Первую» → booking claims via flow state ────
            with patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[_completion(text="На когда перенести?")],
            ) as llm2:
                r2 = dispatch(_ctx(conversation, bot_user, "Первую"))
            assert r2 is not None and r2.meta.get("skill") == "booking"
            assert llm2.call_count == 1  # Phase-1 only, no tool
            assert _pending_rows() == []
            assert _flow_state(conversation) is not None  # flow alive

            # ── Turn 3: «На 9 августа в 20:00» → preview + keyboard ─
            with patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[
                    _completion(
                        tool_calls=[
                            _tool_call(
                                "reschedule_booking",
                                {"record_id": 551, "new_datetime": new_dt},
                            )
                        ],
                    ),
                    _completion(text="Переношу на новое время. Подтверждаете?"),
                ],
            ):
                r3 = dispatch(_ctx(conversation, bot_user, "На 9 августа в 20:00"))
            assert r3 is not None and r3.meta.get("skill") == "booking"
            rows = _pending_rows()
            assert len(rows) == 1
            assert rows[0].kind == PendingBookingAction.Kind.RESCHEDULE
            assert rows[0].consumed_at is None
            # Confirm keyboard attached via action_data attachments.
            attachments = (r3.action_data or {}).get("attachments") or []
            assert attachments and attachments[0]["type"] == "inline_keyboard"
            buttons = attachments[0]["payload"]["buttons"]
            assert any(b["callback"].startswith("cb:book:confirm:") for b in buttons)
            # Flow state closed — the gate owns confirmation from here.
            assert _flow_state(conversation) is None
            assert client.cancel_calls == [] and client.create_calls == []

            # ── Turn 4: «Подтверждаю» → exactly one mutation ────────
            r4 = dispatch(_ctx(conversation, bot_user, "Подтверждаю"))
            assert r4 is not None
            assert r4.reply_text != "Подтверждаю"  # not echo
            assert client.cancel_calls == [551]
            assert len(client.create_calls) == 1
            rows[0].refresh_from_db()
            assert rows[0].consumed_at is not None
            old = BookingRequest.all_tenants.get(
                bot_user=bot_user, comment__contains="yclients_record_id=551"
            )
            assert old.status == BookingRequest.Status.RESCHEDULED


# ---------------------------------------------------------------------------
# Sequence B — combined selection+slot turn
# ---------------------------------------------------------------------------


class TestSequenceB:
    def test_combined_selection_slot_turn(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        conversation: Conversation,
    ) -> None:
        client = _FakeYClients()
        _three_bookings(tenant, bot_user, client)
        new_dt = _future_iso(120)
        client.times = [_slot_for(new_dt)]

        with (
            patch("apps.integrations.yclients.get_yclients_client", return_value=client),
            tenant_scope(tenant),
        ):
            with patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[
                    _completion(tool_calls=[_tool_call("show_my_bookings", {})]),
                    _completion(text="Какую из них перенести?"),
                ],
            ):
                dispatch(_ctx(conversation, bot_user, "Перенеси мою запись"))
            assert _flow_state(conversation) is not None

            with patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[
                    _completion(
                        tool_calls=[
                            _tool_call(
                                "reschedule_booking",
                                {"record_id": 552, "new_datetime": new_dt},
                            )
                        ],
                    ),
                    _completion(text="Переношу вторую запись. Подтверждаете?"),
                ],
            ):
                r2 = dispatch(_ctx(conversation, bot_user, "Первую, на 9 августа в 20:00"))
            assert r2 is not None and r2.meta.get("skill") == "booking"
            rows = _pending_rows()
            assert len(rows) == 1
            assert rows[0].payload["record_id"] == 552

            dispatch(_ctx(conversation, bot_user, "Подтверждаю"))
            assert client.cancel_calls == [552]
            assert len(client.create_calls) == 1


# ---------------------------------------------------------------------------
# Sequence C — callback confirmation
# ---------------------------------------------------------------------------


class TestSequenceC:
    def test_callback_confirm_executes(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        conversation: Conversation,
    ) -> None:
        client = _FakeYClients()
        _three_bookings(tenant, bot_user, client)
        new_dt = _future_iso(120)
        client.times = [_slot_for(new_dt)]

        with (
            patch("apps.integrations.yclients.get_yclients_client", return_value=client),
            tenant_scope(tenant),
        ):
            with patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[
                    _completion(tool_calls=[_tool_call("show_my_bookings", {})]),
                    _completion(text="Какую из них перенести?"),
                ],
            ):
                dispatch(_ctx(conversation, bot_user, "Перенеси мою запись"))
            with patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[
                    _completion(
                        tool_calls=[
                            _tool_call(
                                "reschedule_booking",
                                {"record_id": 551, "new_datetime": new_dt},
                            )
                        ],
                    ),
                    _completion(text="Подтверждаете?"),
                ],
            ):
                dispatch(_ctx(conversation, bot_user, "Первую, на 9 августа в 20:00"))
            token = _pending_rows()[0].pk

            # ✅ tap — routed to the gate skill (NOT echo) after the
            # D-10 registration-order repair.
            r = dispatch(_ctx(conversation, bot_user, f"cb:book:confirm:{token}"))
            assert r is not None
            assert not r.reply_text.startswith("cb:book:confirm")
            assert client.cancel_calls == [551]
            assert len(client.create_calls) == 1


# ---------------------------------------------------------------------------
# Sequence D — text cancel at the confirm stage
# ---------------------------------------------------------------------------


class TestSequenceD:
    def test_text_cancel_discards_preview(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        conversation: Conversation,
    ) -> None:
        client = _FakeYClients()
        _three_bookings(tenant, bot_user, client)
        new_dt = _future_iso(120)
        client.times = [_slot_for(new_dt)]

        with (
            patch("apps.integrations.yclients.get_yclients_client", return_value=client),
            tenant_scope(tenant),
        ):
            with patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[
                    _completion(tool_calls=[_tool_call("show_my_bookings", {})]),
                    _completion(text="Какую из них перенести?"),
                ],
            ):
                dispatch(_ctx(conversation, bot_user, "Перенеси мою запись"))
            with patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[
                    _completion(
                        tool_calls=[
                            _tool_call(
                                "reschedule_booking",
                                {"record_id": 551, "new_datetime": new_dt},
                            )
                        ],
                    ),
                    _completion(text="Подтверждаете?"),
                ],
            ):
                dispatch(_ctx(conversation, bot_user, "Первую, на 9 августа в 20:00"))
            assert len(_pending_rows()) == 1

            r = dispatch(_ctx(conversation, bot_user, "не надо"))
            assert r is not None
            assert client.cancel_calls == [] and client.create_calls == []
            row = _pending_rows()[0]
            assert row.consumed_at is not None  # discarded, not executed
            old = BookingRequest.all_tenants.get(
                bot_user=bot_user, comment__contains="yclients_record_id=551"
            )
            assert old.status == BookingRequest.Status.CONFIRMED


# ---------------------------------------------------------------------------
# Disambiguation + negative regression
# ---------------------------------------------------------------------------


class TestNegatives:
    def test_selection_without_flow_state_is_not_claimed_by_booking(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        conversation: Conversation,
    ) -> None:
        """A selection-shaped turn with no live flow must NOT enter booking.

        The observable proxy used to be a verbatim echo; DRF-963 replaced
        the last-resort reply with the honest menu fallback. That reply
        alone is NOT sufficient evidence any more: the menu skill produces
        the identical text when it DOES route into booking and booking
        fails with ``should_handoff`` (and in this test env booking always
        fails — ``YCLIENTS_PARTNER_TOKEN`` is empty). So assert on the
        thing that actually matters: booking's ``handle`` is never entered.
        """
        from apps.skills.menu.replies import FALLBACK_TEXT

        with tenant_scope(tenant), _booking_handle_spy() as entered:
            result = dispatch(_ctx(conversation, bot_user, "Первую"))
        assert entered == []
        assert result is not None
        assert result.reply_text == FALLBACK_TEXT  # not booking, not echo
        assert _pending_rows() == []

    def test_foreign_record_id_rejected_no_pending(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        conversation: Conversation,
    ) -> None:
        """LLM hallucinates a record_id the user does not own — the tool
        validation rejects it, no pending row is persisted."""
        client = _FakeYClients()
        _three_bookings(tenant, bot_user, client)
        new_dt = _future_iso(120)
        client.times = [_slot_for(new_dt)]
        with (
            patch("apps.integrations.yclients.get_yclients_client", return_value=client),
            tenant_scope(tenant),
        ):
            with patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[
                    _completion(tool_calls=[_tool_call("show_my_bookings", {})]),
                    _completion(text="Какую из них перенести?"),
                ],
            ):
                dispatch(_ctx(conversation, bot_user, "Перенеси мою запись"))
            with patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[
                    _completion(
                        tool_calls=[
                            _tool_call(
                                "reschedule_booking",
                                {"record_id": 424242, "new_datetime": new_dt},
                            )
                        ],
                    ),
                ],
            ):
                r = dispatch(_ctx(conversation, bot_user, "Первую, на 9 августа в 20:00"))
            assert r is not None
            assert r.should_handoff is True
            assert r.handoff_reason == "booking_invalid_record_id"
            assert _pending_rows() == []

    def test_occupied_slot_creates_no_pending(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        conversation: Conversation,
    ) -> None:
        client = _FakeYClients()
        _three_bookings(tenant, bot_user, client)
        new_dt = _future_iso(120)
        client.times = []  # nothing free — slot_unavailable clarification
        with (
            patch("apps.integrations.yclients.get_yclients_client", return_value=client),
            tenant_scope(tenant),
        ):
            with patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[
                    _completion(tool_calls=[_tool_call("show_my_bookings", {})]),
                    _completion(text="Какую из них перенести?"),
                ],
            ):
                dispatch(_ctx(conversation, bot_user, "Перенеси мою запись"))
            with patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[
                    _completion(
                        tool_calls=[
                            _tool_call(
                                "reschedule_booking",
                                {"record_id": 551, "new_datetime": new_dt},
                            )
                        ],
                    ),
                    _completion(text="Это время занято, подобрать соседнее?"),
                ],
            ):
                r = dispatch(_ctx(conversation, bot_user, "Первую, на 9 августа в 20:00"))
            assert r is not None and r.meta.get("skill") == "booking"
            assert _pending_rows() == []
            assert client.cancel_calls == [] and client.create_calls == []

    def test_flow_abort_clears_state_without_llm(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        conversation: Conversation,
    ) -> None:
        client = _FakeYClients()
        _three_bookings(tenant, bot_user, client)
        with (
            patch("apps.integrations.yclients.get_yclients_client", return_value=client),
            tenant_scope(tenant),
        ):
            with patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[
                    _completion(tool_calls=[_tool_call("show_my_bookings", {})]),
                    _completion(text="Какую из них перенести?"),
                ],
            ):
                dispatch(_ctx(conversation, bot_user, "Перенеси мою запись"))
            assert _flow_state(conversation) is not None

            with patch.object(OpenAIProvider, "complete") as llm:
                r = dispatch(_ctx(conversation, bot_user, "не надо"))
            assert r is not None and r.meta.get("skill") == "booking"
            assert "не переношу" in r.reply_text
            assert llm.call_count == 0  # deterministic abort, no LLM
            assert _flow_state(conversation) is None
            assert _pending_rows() == []

    def test_duplicate_text_confirm_no_double_mutation(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        conversation: Conversation,
    ) -> None:
        client = _FakeYClients()
        _three_bookings(tenant, bot_user, client)
        new_dt = _future_iso(120)
        client.times = [_slot_for(new_dt)]
        with (
            patch("apps.integrations.yclients.get_yclients_client", return_value=client),
            tenant_scope(tenant),
        ):
            with patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[
                    _completion(tool_calls=[_tool_call("show_my_bookings", {})]),
                    _completion(text="Какую из них перенести?"),
                ],
            ):
                dispatch(_ctx(conversation, bot_user, "Перенеси мою запись"))
            with patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[
                    _completion(
                        tool_calls=[
                            _tool_call(
                                "reschedule_booking",
                                {"record_id": 551, "new_datetime": new_dt},
                            )
                        ],
                    ),
                    _completion(text="Подтверждаете?"),
                ],
            ):
                dispatch(_ctx(conversation, bot_user, "Первую, на 9 августа в 20:00"))

            dispatch(_ctx(conversation, bot_user, "Подтверждаю"))
            second = dispatch(_ctx(conversation, bot_user, "Подтверждаю"))
            assert client.cancel_calls == [551]
            assert len(client.create_calls) == 1
            assert second is not None
            assert "уже" in second.reply_text.lower()


# ---------------------------------------------------------------------------
# Routing boundaries (must NOT regress)
# ---------------------------------------------------------------------------


class TestRoutingBoundaries:
    def test_readonly_lookup_opens_no_flow(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        conversation: Conversation,
    ) -> None:
        client = _FakeYClients()
        _three_bookings(tenant, bot_user, client)
        with (
            patch("apps.integrations.yclients.get_yclients_client", return_value=client),
            tenant_scope(tenant),
        ):
            with patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[_completion(text="Ваша ближайшая запись — 6 августа.")],
            ):
                r = dispatch(_ctx(conversation, bot_user, "Когда у меня следующая запись?"))
            assert r is not None and r.meta.get("skill") == "booking"
            assert _flow_state(conversation) is None  # read-only ≠ flow
            assert _pending_rows() == []

    def test_faq_question_still_routes_to_faq(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        conversation: Conversation,
    ) -> None:
        from apps.skills.faq.skill import FAQSkill

        with (
            patch.object(
                FAQSkill,
                "handle",
                return_value=SkillResult(
                    reply_text="faq answer", action_type="faq", meta={"skill": "faq"}
                ),
            ),
            tenant_scope(tenant),
        ):
            result = dispatch(_ctx(conversation, bot_user, "Как записаться?"))
        assert result is not None
        assert result.meta.get("skill") == "faq"
        assert _flow_state(conversation) is None

    def test_offtopic_turn_with_fresh_flow_is_not_claimed_by_booking(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        conversation: Conversation,
    ) -> None:
        """Review D-10 #2 — an off-topic turn while the flow is fresh must
        not be pulled into booking (zero LLM calls) and the flow state
        survives for the next selection-shaped turn.

        DRF-963 changed only the last-resort REPLY (echo → honest menu
        fallback); «спасибо» must still cost zero LLM calls, which also
        pins that the widened U-1 matcher doesn't over-claim gratitude.
        The ``handle`` spy is the load-bearing assertion — booking bails
        out before its LLM call when the provider is unconfigured, so a
        zero call count alone would not notice an over-claiming matcher.
        """
        from apps.skills.menu.replies import FALLBACK_TEXT

        with tenant_scope(tenant), _booking_handle_spy() as entered:
            _write_flow_state(conversation, flow="reschedule", bookings=[])
            with patch.object(OpenAIProvider, "complete") as llm:
                result = dispatch(_ctx(conversation, bot_user, "спасибо"))
        assert entered == []
        assert result is not None
        assert result.reply_text == FALLBACK_TEXT  # not booking, not echo
        assert llm.call_count == 0
        assert _flow_state(conversation) is not None

    def test_booking_request_with_fresh_flow_stays_in_booking(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        conversation: Conversation,
    ) -> None:
        """Review round 2 — mirror of the echo test above: a fresh
        booking request while the flow is alive must stay in the booking
        skill (not echo), must NOT be grounded with the stale
        «АКТИВНЫЙ СЦЕНАРИЙ… НЕ начинай сначала» block (that context
        belongs to a different request), and the flow state survives
        for a later selection-shaped turn."""
        client = _FakeYClients()
        captured: list[Any] = []

        def _capture(messages: Any, **_: Any) -> CompletionResult:
            captured.append(messages)
            return _completion(text="Конечно, на какое время подобрать?")

        with tenant_scope(tenant):
            _write_flow_state(conversation, flow="reschedule", bookings=[])
            with (
                patch("apps.integrations.yclients.get_yclients_client", return_value=client),
                patch.object(OpenAIProvider, "complete", side_effect=_capture),
            ):
                result = dispatch(_ctx(conversation, bot_user, "Хочу записаться на стрижку"))
        assert result is not None
        assert result.meta.get("skill") == "booking"
        assert len(captured) == 1
        assert "АКТИВНЫЙ СЦЕНАРИЙ" not in str(captured[0])
        assert _flow_state(conversation) is not None

    def test_spaced_time_turn_with_fresh_flow_stays_in_booking(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        conversation: Conversation,
    ) -> None:
        """Review round 3 — spaced time «20 00» (no colon) while the
        booking flow is alive must be claimed by the booking skill as a
        continuation turn: grounded with the «АКТИВНЫЙ СЦЕНАРИЙ» block,
        not echoed, and no mutation before an explicit confirmation
        (no pending preview row is created by a bare time answer)."""
        client = _FakeYClients()
        captured: list[Any] = []

        def _capture(messages: Any, **_: Any) -> CompletionResult:
            captured.append(messages)
            return _completion(text="Подтверждаете перенос на 20:00?")

        with tenant_scope(tenant):
            _write_flow_state(conversation, flow="reschedule", bookings=[])
            with (
                patch("apps.integrations.yclients.get_yclients_client", return_value=client),
                patch.object(OpenAIProvider, "complete", side_effect=_capture),
            ):
                result = dispatch(_ctx(conversation, bot_user, "20 00"))
        assert result is not None
        assert result.meta.get("skill") == "booking"
        assert result.reply_text != "20 00"  # not echo
        assert len(captured) == 1
        assert "АКТИВНЫЙ СЦЕНАРИЙ" in str(captured[0])  # continuation grounding
        assert _pending_rows() == []  # mutation=0 until explicit confirmation
        assert _flow_state(conversation) is not None
