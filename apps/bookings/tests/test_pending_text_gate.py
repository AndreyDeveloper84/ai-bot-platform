"""D-10 — text confirmation path for the booking preview gate.

Covers the additions in :mod:`apps.bookings.pending_actions` (confirm /
cancel vocabulary, :func:`latest_relevant_pending`) and the
:class:`apps.bookings.callbacks.BookingGateCallbackSkill` text path
(«Подтверждаю» / «Не надо» instead of a button tap).

Staging defect D-10: «Подтверждаю» fell to echo because (a) nothing
claimed the text turn and (b) the gate skill was registered AFTER echo.
These tests lock the boundary:

  * vocab claims ONLY with a relevant pending row for (tenant, bot_user);
  * confirm text executes exactly one mutation (CAS idempotency reused);
  * duplicate / expired / stale confirms are controlled no-ops;
  * without a pending row the vocab keeps its previous routing (echo).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.booking.models import BookingRequest, PendingBookingAction
from apps.bookings.callbacks import (
    REPLY_BOOK_ALREADY_HANDLED,
    REPLY_BOOK_CANCELLED_PREVIEW,
    REPLY_BOOK_EXPIRED,
    REPLY_NOT_FOUND,
    BookingGateCallbackSkill,
)
from apps.bookings.pending_actions import (
    create_pending,
    is_cancel_text,
    is_confirm_text,
    latest_relevant_pending,
    normalize_gate_text,
)
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.llm.router import reset_router_cache
from apps.skills.base import SkillContext
from apps.skills.registry import dispatch
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


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
    return Tenant.objects.create(slug="gate-text", name="Gate Text")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="gate-u1",
        chat_id="gate-u1",
        phone="79991234567",
        client_name="Anna",
        # Onboarded user — otherwise the welcome skill's first-contact
        # auto-trigger claims arbitrary text turns before echo.
        welcomed_at=timezone.now(),
    )


@pytest.fixture
def conversation(tenant: Tenant, bot_user: BotUser) -> Conversation:
    return Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)


def _ctx(conversation: Conversation, bot_user: BotUser, text: str) -> SkillContext:
    return SkillContext(
        conversation=conversation,
        bot_user=bot_user,
        message_text=text,
        trace_id="t-gate-text",
    )


def _make_booking(tenant: Tenant, bot_user: BotUser, *, yc_id: int = 555) -> BookingRequest:
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


class _FakeClient:
    """Minimal mutation double: cancel_record only."""

    def __init__(self) -> None:
        self.cancel_calls: list[int] = []

    def cancel_record(self, *, record_id: int) -> None:
        self.cancel_calls.append(record_id)


def _cancel_pending(tenant: Tenant, bot_user: BotUser, *, yc_id: int = 555):
    token = create_pending(
        tenant=tenant,
        bot_user=bot_user,
        kind=PendingBookingAction.Kind.CANCEL,
        payload={"record_id": yc_id, "reason": "по просьбе клиента"},
    )
    return PendingBookingAction.all_tenants.get(pk=token)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class TestGateVocab:
    @pytest.mark.parametrize(
        "text",
        ("Подтверждаю", "подтверждаю", "да", "Да", "ок", "Окей", "верно", "согласна", "угу"),
    )
    def test_confirm_positives(self, text: str) -> None:
        assert is_confirm_text(text) is True

    @pytest.mark.parametrize(
        "text",
        ("Отмена", "отмена", "не надо", "Нет", "передумала", "отбой", "стоп"),
    )
    def test_cancel_positives(self, text: str) -> None:
        assert is_cancel_text(text) is True

    @pytest.mark.parametrize(
        "text",
        (
            "да, но сначала вопрос",
            "подтверждаю запись",
            "нет записей",
            "отменить запись полностью",
            "ничего не надо",
            "когда подтверждать?",
            "",
            "   ",
        ),
    )
    def test_exact_match_negatives(self, text: str) -> None:
        # Substrings are NOT claims — only the whole normalized message.
        assert is_confirm_text(text) is False
        assert is_cancel_text(text) is False

    def test_trailing_punctuation_stripped(self) -> None:
        assert is_confirm_text("Подтверждаю!") is True
        assert is_confirm_text("да.") is True
        assert is_cancel_text("не надо…") is True

    def test_normalize_collapses_whitespace(self) -> None:
        assert normalize_gate_text("  Не   надо \n") == "не надо"


# ---------------------------------------------------------------------------
# latest_relevant_pending
# ---------------------------------------------------------------------------


class TestLatestRelevantPending:
    def test_no_rows(self, tenant: Tenant, bot_user: BotUser) -> None:
        assert latest_relevant_pending(tenant=tenant, bot_user=bot_user) is None

    def test_active_row(self, tenant: Tenant, bot_user: BotUser) -> None:
        row = _cancel_pending(tenant, bot_user)
        found = latest_relevant_pending(tenant=tenant, bot_user=bot_user)
        assert found is not None and found.pk == row.pk

    def test_recently_expired_row_is_relevant(self, tenant: Tenant, bot_user: BotUser) -> None:
        row = _cancel_pending(tenant, bot_user)
        PendingBookingAction.all_tenants.filter(pk=row.pk).update(
            expires_at=timezone.now() - timedelta(seconds=30),
        )
        found = latest_relevant_pending(tenant=tenant, bot_user=bot_user)
        assert found is not None and found.pk == row.pk

    def test_expired_row_outside_grace_is_not_relevant(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        # Review D-10 #1 — the settle grace is 90 seconds, not the
        # preview TTL: an expired preview must stop claiming text turns
        # once the double-tap / late-answer window has passed.
        row = _cancel_pending(tenant, bot_user)
        PendingBookingAction.all_tenants.filter(pk=row.pk).update(
            expires_at=timezone.now() - timedelta(minutes=5),
        )
        assert latest_relevant_pending(tenant=tenant, bot_user=bot_user) is None

    def test_ancient_expired_row_is_not_relevant(self, tenant: Tenant, bot_user: BotUser) -> None:
        row = _cancel_pending(tenant, bot_user)
        PendingBookingAction.all_tenants.filter(pk=row.pk).update(
            expires_at=timezone.now() - timedelta(hours=3),
        )
        assert latest_relevant_pending(tenant=tenant, bot_user=bot_user) is None

    def test_recently_consumed_row_is_relevant(self, tenant: Tenant, bot_user: BotUser) -> None:
        row = _cancel_pending(tenant, bot_user)
        PendingBookingAction.all_tenants.filter(pk=row.pk).update(
            consumed_at=timezone.now() - timedelta(minutes=1),
        )
        found = latest_relevant_pending(tenant=tenant, bot_user=bot_user)
        assert found is not None and found.pk == row.pk

    def test_consumed_row_outside_grace_is_not_relevant(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        # Review D-10 #1 — consumed 5 minutes ago: the "already handled"
        # canned reply must NOT hijack an unrelated да/нет turn.
        row = _cancel_pending(tenant, bot_user)
        PendingBookingAction.all_tenants.filter(pk=row.pk).update(
            consumed_at=timezone.now() - timedelta(minutes=5),
        )
        assert latest_relevant_pending(tenant=tenant, bot_user=bot_user) is None

    def test_ancient_consumed_row_is_not_relevant(self, tenant: Tenant, bot_user: BotUser) -> None:
        row = _cancel_pending(tenant, bot_user)
        PendingBookingAction.all_tenants.filter(pk=row.pk).update(
            consumed_at=timezone.now() - timedelta(hours=2),
        )
        assert latest_relevant_pending(tenant=tenant, bot_user=bot_user) is None

    def test_other_users_row_is_invisible(self, tenant: Tenant, bot_user: BotUser) -> None:
        stranger = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="gate-u2",
            chat_id="gate-u2",
            phone="79990000000",
            client_name="Boris",
        )
        _cancel_pending(tenant, stranger)
        assert latest_relevant_pending(tenant=tenant, bot_user=bot_user) is None


# ---------------------------------------------------------------------------
# Gate skill — matches() boundary
# ---------------------------------------------------------------------------


class TestGateTextMatches:
    def test_confirm_vocab_with_pending_matches(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        _cancel_pending(tenant, bot_user)
        skill = BookingGateCallbackSkill()
        assert skill.matches(_ctx(conversation, bot_user, "Подтверждаю")) is True

    def test_cancel_vocab_with_pending_matches(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        _cancel_pending(tenant, bot_user)
        skill = BookingGateCallbackSkill()
        assert skill.matches(_ctx(conversation, bot_user, "не надо")) is True

    def test_vocab_without_pending_does_not_match(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        skill = BookingGateCallbackSkill()
        assert skill.matches(_ctx(conversation, bot_user, "Подтверждаю")) is False
        assert skill.matches(_ctx(conversation, bot_user, "не надо")) is False

    def test_non_vocab_with_pending_does_not_match(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        _cancel_pending(tenant, bot_user)
        skill = BookingGateCallbackSkill()
        assert skill.matches(_ctx(conversation, bot_user, "а что там завтра?")) is False

    def test_callback_prefixes_still_match(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        skill = BookingGateCallbackSkill()
        assert (
            skill.matches(
                _ctx(conversation, bot_user, "cb:book:confirm:123e4567-e89b-12d3-a456-426614174000")
            )
            is True
        )
        assert (
            skill.matches(
                _ctx(conversation, bot_user, "cb:book:cancel:123e4567-e89b-12d3-a456-426614174000")
            )
            is True
        )


# ---------------------------------------------------------------------------
# Gate skill — handle() text path
# ---------------------------------------------------------------------------


class TestGateTextHandle:
    def test_confirm_text_executes_cancel_once(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        row = _cancel_pending(tenant, bot_user)
        client = _FakeClient()
        skill = BookingGateCallbackSkill()
        with (
            patch("apps.integrations.yclients.get_yclients_client", return_value=client),
            tenant_scope(tenant),
        ):
            result = skill.handle(_ctx(conversation, bot_user, "Подтверждаю"))
        assert client.cancel_calls == [555]
        row.refresh_from_db()
        assert row.consumed_at is not None
        assert result.reply_text  # deterministic reply, no echo
        booking = BookingRequest.all_tenants.get(bot_user=bot_user)
        assert booking.status == BookingRequest.Status.CANCELLED

    def test_duplicate_confirm_is_controlled_noop(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        _cancel_pending(tenant, bot_user)
        client = _FakeClient()
        skill = BookingGateCallbackSkill()
        with (
            patch("apps.integrations.yclients.get_yclients_client", return_value=client),
            tenant_scope(tenant),
        ):
            skill.handle(_ctx(conversation, bot_user, "Подтверждаю"))
            second = skill.handle(_ctx(conversation, bot_user, "Подтверждаю"))
        assert client.cancel_calls == [555]  # no duplicate mutation
        assert second.reply_text == REPLY_BOOK_ALREADY_HANDLED

    def test_expired_pending_confirm_gets_expired_reply(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        row = _cancel_pending(tenant, bot_user)
        # Expired inside the 90-second settle grace — still claimed, and
        # the controlled "too much time" reply fires instead of a mutation.
        PendingBookingAction.all_tenants.filter(pk=row.pk).update(
            expires_at=timezone.now() - timedelta(seconds=30),
        )
        client = _FakeClient()
        skill = BookingGateCallbackSkill()
        with (
            patch("apps.integrations.yclients.get_yclients_client", return_value=client),
            tenant_scope(tenant),
        ):
            result = skill.handle(_ctx(conversation, bot_user, "Подтверждаю"))
        assert result.reply_text == REPLY_BOOK_EXPIRED
        assert client.cancel_calls == []  # no mutation on stale state

    def test_cancel_text_discards_preview(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        row = _cancel_pending(tenant, bot_user)
        client = _FakeClient()
        skill = BookingGateCallbackSkill()
        with (
            patch("apps.integrations.yclients.get_yclients_client", return_value=client),
            tenant_scope(tenant),
        ):
            result = skill.handle(_ctx(conversation, bot_user, "не надо"))
        assert result.reply_text == REPLY_BOOK_CANCELLED_PREVIEW
        assert client.cancel_calls == []
        row.refresh_from_db()
        assert row.consumed_at is not None
        booking = BookingRequest.all_tenants.get(bot_user=bot_user)
        assert booking.status == BookingRequest.Status.CONFIRMED  # untouched

    def test_gate_decision_clears_booking_flow_state(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        """Review D-10 #4 — a gate decision ends an open continuation
        flow; otherwise skill_state["booking_flow"] lingers for the rest
        of its TTL and keeps claiming selection-shaped turns."""
        from apps.skills.booking.skill import _FLOW_STATE_KEY

        _make_booking(tenant, bot_user, yc_id=555)
        row = _cancel_pending(tenant, bot_user)
        # Consumed inside the settle grace → the text confirm resolves
        # to the controlled "already handled" reply via the gate.
        PendingBookingAction.all_tenants.filter(pk=row.pk).update(
            consumed_at=timezone.now() - timedelta(seconds=30),
        )
        Conversation.all_tenants.filter(pk=conversation.pk).update(
            skill_state={
                _FLOW_STATE_KEY: {
                    "flow": "cancel",
                    "stage": "awaiting_selection",
                    "bookings": [],
                    "expires_at": (timezone.now() + timedelta(minutes=5)).isoformat(),
                }
            }
        )
        conversation.refresh_from_db()  # the skill reads the instance, not the DB
        skill = BookingGateCallbackSkill()
        with tenant_scope(tenant):
            result = skill.handle(_ctx(conversation, bot_user, "Подтверждаю"))
        assert result.reply_text == REPLY_BOOK_ALREADY_HANDLED
        conversation.refresh_from_db()
        assert (conversation.skill_state or {}).get(_FLOW_STATE_KEY) is None

    def test_malformed_gate_callback_keeps_booking_flow_state(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        """Review round 2 — a malformed ``cb:book:…`` payload decided
        nothing; the flow clear must fire only on real gate decisions,
        not before token parsing."""
        from apps.skills.booking.skill import _FLOW_STATE_KEY

        Conversation.all_tenants.filter(pk=conversation.pk).update(
            skill_state={
                _FLOW_STATE_KEY: {
                    "flow": "cancel",
                    "stage": "awaiting_selection",
                    "bookings": [],
                    "expires_at": (timezone.now() + timedelta(minutes=5)).isoformat(),
                }
            }
        )
        conversation.refresh_from_db()  # the skill reads the instance, not the DB
        skill = BookingGateCallbackSkill()
        with tenant_scope(tenant):
            result = skill.handle(_ctx(conversation, bot_user, "cb:book:confirm:not-a-uuid"))
        assert result.reply_text == REPLY_NOT_FOUND
        conversation.refresh_from_db()
        assert (conversation.skill_state or {}).get(_FLOW_STATE_KEY) is not None


# ---------------------------------------------------------------------------
# Registry dispatch — production routing for text confirmations
# ---------------------------------------------------------------------------


class TestGateTextDispatch:
    def test_confirm_routes_to_gate_not_echo(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        _cancel_pending(tenant, bot_user)
        client = _FakeClient()
        with (
            patch("apps.integrations.yclients.get_yclients_client", return_value=client),
            tenant_scope(tenant),
        ):
            result = dispatch(_ctx(conversation, bot_user, "Подтверждаю"))
        assert result is not None
        # Echo would bounce the text back verbatim; the gate never does.
        assert result.reply_text != "Подтверждаю"
        assert client.cancel_calls == [555]

    def test_confirm_without_pending_falls_through_to_echo(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        with tenant_scope(tenant):
            result = dispatch(_ctx(conversation, bot_user, "Подтверждаю"))
        assert result is not None
        assert result.reply_text == "Подтверждаю"  # echo, zero mutation
        assert PendingBookingAction.all_tenants.count() == 0

    def test_stale_consumed_pending_does_not_hijack_yes(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        """Review D-10 #1 — a preview consumed 5 minutes ago (outside
        the 90-second settle grace) must NOT claim an unrelated «да»."""
        row = _cancel_pending(tenant, bot_user)
        PendingBookingAction.all_tenants.filter(pk=row.pk).update(
            consumed_at=timezone.now() - timedelta(minutes=5),
        )
        with tenant_scope(tenant):
            result = dispatch(_ctx(conversation, bot_user, "да"))
        assert result is not None
        assert result.reply_text == "да"  # echo — the gate no longer claims

    def test_callback_tap_routes_to_gate_not_echo(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        """D-10 latent shadowing: cb:book:* taps were echo-claimed because
        the gate skill registered after echo."""
        _make_booking(tenant, bot_user, yc_id=555)
        row = _cancel_pending(tenant, bot_user)
        client = _FakeClient()
        with (
            patch("apps.integrations.yclients.get_yclients_client", return_value=client),
            tenant_scope(tenant),
        ):
            result = dispatch(_ctx(conversation, bot_user, f"cb:book:confirm:{row.pk}"))
        assert result is not None
        assert not result.reply_text.startswith("cb:book:confirm")
        assert client.cancel_calls == [555]
