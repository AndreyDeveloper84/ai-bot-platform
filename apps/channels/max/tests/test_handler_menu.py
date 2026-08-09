"""MAX handler ↔ menu skill e2e tests (DRF-963 / Wave 1, variant A).

Drives the REAL production entry point — ``handle_max_event`` → registry
dispatch — rather than the skill in isolation, because the bug DRF-963
fixes lived in the wiring: production dispatch runs without
``ctx.intent``, so a phrase that no keyword matched was echoed back.

Pins the three user-visible outcomes:

  * U-5 — an unrecognised message answers with the honest fallback AND an
    inline keyboard, never with an echo.
  * U-1 — «Хочу массаж» reaches the booking skill.
  * menu taps — a ``cb:menu:*`` button and the equivalent typed phrase
    take the same route, and the raw payload never reaches the customer.

The booking skill's ``handle`` is patched to a sentinel: the assertion
under test is ROUTING (did the turn reach booking?), and the real handler
would need an LLM provider plus a YClients/Ayla backend.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from apps.channels.max import handler as max_handler
from apps.orchestrator.memory import short_term
from apps.skills.base import SkillResult
from apps.skills.menu.replies import FALLBACK_TEXT, HELP_TEXT
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)

_BOOKING_SENTINEL = "«booking skill answered»"


def _payload(*, text, user_id=31001, chat_id=41001, mid="menu-1"):
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Olga"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="menu-handler", name="Menu Handler")


@pytest.fixture
def sent(monkeypatch):
    """Capture outbound calls INCLUDING attachments (the keyboard)."""
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text, "attachments": attachments})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def events(monkeypatch):
    captured: list[tuple[str, dict]] = []
    real_emit = max_handler.emit

    def fake_emit(name, payload=None, **kwargs):
        captured.append((name, payload or {}))
        return real_emit(name, payload=payload, **kwargs) if False else None

    monkeypatch.setattr(max_handler, "emit", fake_emit)
    return captured


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def booking_spy():
    """Patch BookingSkill.handle and record the context it was given."""
    seen: list[str] = []

    def fake_handle(self, context):
        seen.append(context.message_text)
        return SkillResult(reply_text=_BOOKING_SENTINEL)

    with patch("apps.skills.booking.skill.BookingSkill.handle", fake_handle):
        yield seen


def _menu_callbacks(attachments) -> list[str]:
    assert attachments, "expected an inline keyboard"
    rows = attachments[0]["payload"]["buttons"]
    return [btn["payload"] for row in rows for btn in row if btn.get("type") == "callback"]


class TestHonestFallback:
    """U-5 — the bot must stop parroting."""

    def test_unrecognised_message_is_not_echoed(
        self, tenant, sent, fake_redis, settings, mark_welcomed
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant), trace_id_scope(str(uuid4())):
            mark_welcomed(user_id=31001, chat_id=41001)
            max_handler.handle_max_event(_payload(text="ааааа что вообще происходит"))

        assert len(sent) == 1
        assert sent[0]["text"] == FALLBACK_TEXT
        assert sent[0]["text"] != "ааааа что вообще происходит"

    def test_fallback_ships_a_working_menu(self, tenant, sent, fake_redis, settings, mark_welcomed):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant), trace_id_scope(str(uuid4())):
            mark_welcomed(user_id=31001, chat_id=41001)
            max_handler.handle_max_event(_payload(text="ыыы"))

        callbacks = _menu_callbacks(sent[0]["attachments"])
        assert "cb:menu:book" in callbacks
        assert "cb:menu:my_bookings" in callbacks
        assert "cb:menu:help" in callbacks

    def test_reply_kind_no_longer_reports_echo(
        self, tenant, sent, events, fake_redis, settings, mark_welcomed
    ):
        """The pilot's headline metric — «how often does the bot miss?» —
        must be answerable from the bus."""
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant), trace_id_scope(str(uuid4())):
            mark_welcomed(user_id=31001, chat_id=41001)
            max_handler.handle_max_event(_payload(text="ыыы"))

        outbound = [p for name, p in events if name == "channels.max.outbound.sent"]
        assert outbound and outbound[0]["reply_kind"] == "menu_fallback"
        assert outbound[0]["has_keyboard"] is True


class TestWidenedBookingCoverage:
    """U-1 — live phrasings from Wave 1 Validation reach booking."""

    @pytest.mark.parametrize(
        "text",
        ["Хочу массаж", "Мне бы маникюр", "есть свободное время на этой неделе"],
    )
    def test_service_phrasing_reaches_booking(
        self, tenant, sent, fake_redis, settings, mark_welcomed, booking_spy, text
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant), trace_id_scope(str(uuid4())):
            mark_welcomed(user_id=31001, chat_id=41001)
            max_handler.handle_max_event(_payload(text=text))

        assert booking_spy == [text], "booking skill did not claim the turn"
        assert sent[0]["text"] == _BOOKING_SENTINEL

    def test_small_talk_still_does_not_reach_booking(
        self, tenant, sent, fake_redis, settings, mark_welcomed, booking_spy
    ):
        """Over-claiming would spend two LLM calls on «спасибо»."""
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant), trace_id_scope(str(uuid4())):
            mark_welcomed(user_id=31001, chat_id=41001)
            max_handler.handle_max_event(_payload(text="спасибо"))

        assert booking_spy == []
        assert sent[0]["text"] == FALLBACK_TEXT

    def test_pain_complaint_still_goes_to_health_screening(
        self, tenant, sent, fake_redis, settings, mark_welcomed, booking_spy
    ):
        """«спина» is a service word for a massage salon, so the widened
        matcher COULD pull a health complaint into a booking flow. It
        doesn't: health_screening registers far earlier and claims the
        turn first. Pinned because the two vocabularies overlap by design.
        """
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant), trace_id_scope(str(uuid4())):
            mark_welcomed(user_id=31001, chat_id=41001)
            max_handler.handle_max_event(_payload(text="у меня болит спина"))

        assert booking_spy == []
        assert sent[0]["text"] != FALLBACK_TEXT
        assert "болит" in sent[0]["text"]  # the screening question


class TestMenuTaps:
    def test_book_button_routes_into_booking(
        self, tenant, sent, fake_redis, settings, mark_welcomed, booking_spy
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant), trace_id_scope(str(uuid4())):
            mark_welcomed(user_id=31001, chat_id=41001)
            max_handler.handle_max_event(_payload(text="cb:menu:book"))

        assert booking_spy == ["Хочу записаться"]
        # The raw slug must never surface to the customer.
        assert "cb:menu" not in sent[0]["text"]

    def test_my_bookings_button_routes_into_booking(
        self, tenant, sent, fake_redis, settings, mark_welcomed, booking_spy
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant), trace_id_scope(str(uuid4())):
            mark_welcomed(user_id=31001, chat_id=41001)
            max_handler.handle_max_event(_payload(text="cb:menu:my_bookings"))

        assert booking_spy == ["Покажи мои записи"]

    def test_help_button_answers_locally(
        self, tenant, sent, fake_redis, settings, mark_welcomed, booking_spy
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant), trace_id_scope(str(uuid4())):
            mark_welcomed(user_id=31001, chat_id=41001)
            max_handler.handle_max_event(_payload(text="cb:menu:help"))

        assert booking_spy == []
        assert sent[0]["text"] == HELP_TEXT
        assert _menu_callbacks(sent[0]["attachments"])

    def test_typed_help_matches_the_button(self, tenant, sent, fake_redis, settings, mark_welcomed):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant), trace_id_scope(str(uuid4())):
            mark_welcomed(user_id=31001, chat_id=41001)
            max_handler.handle_max_event(_payload(text="что ты умеешь?"))

        assert sent[0]["text"] == HELP_TEXT
