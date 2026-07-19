"""Tests for global (tenant-less) MAX onboarding — welcome + 152-ФЗ consent (#1046).

Two layers:

* unit — ``needs_onboarding`` truth table + ``run_onboarding_turn`` wrapping,
  including the server consent journal and the ``current_tenant() is None``
  invariant;
* integration — the 7 acceptance scenarios driven through
  ``handle_global_max_event`` with ``GLOBAL_BOT_ONBOARDING`` toggled, proving the
  onboarding branch fires, discovery is NOT called during onboarding, and the
  legacy (flag-off) path is unchanged.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.max.global_onboarding import (
    GLOBAL_S5_TEXT,
    GLOBAL_WELCOME_TEXT,
    needs_onboarding,
    run_onboarding_turn,
)
from apps.consent.models import ConsentRecord
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.orchestrator.memory import short_term
from apps.tenancy.context import current_tenant

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text, "attachments": attachments})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def spy_discovery(monkeypatch):
    """Replace generate_concierge_reply with a spy so we can assert it is NOT
    called during onboarding (and IS on the normal discovery path)."""
    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Какая услуга интересует?"))
    monkeypatch.setattr(max_handler, "generate_concierge_reply", spy)
    return spy


@pytest.fixture(autouse=True)
def _onboarding_on(settings):
    settings.GLOBAL_BOT_ONBOARDING = True
    # Make the tenant-less invariant load-bearing: TenantScopedManager reads
    # STRICT_TENANT_SCOPE (default "audit" only logs); "strict" raises
    # CrossTenantError on any TenantScoped read at current_tenant() is None.
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.STRICT_TENANT_REFUSE = True


def _msg_payload(*, text: str, user_id: int = 7777, chat_id: int = 8888, mid: str = "m-1") -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _callback_payload(
    *, payload: str, user_id: int = 7777, chat_id: int = 8888, callback_id: str = "cb-1"
) -> dict:
    return {
        "update_type": "message_callback",
        "timestamp": 1731320000000,
        "callback": {
            "callback_id": callback_id,
            "user": {"user_id": user_id, "name": "Иван"},
            "payload": payload,
        },
        "message": {"recipient": {"chat_id": chat_id, "chat_type": "dialog"}},
    }


def _global_user_and_conv(user_id: int = 7777):
    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), chat_id="8888"
    )
    conversation = resolve_active_global_conversation(bot_user)
    return bot_user, conversation


# --------------------------------------------------------------------------- #
# needs_onboarding — truth table                                              #
# --------------------------------------------------------------------------- #
class TestNeedsOnboarding:
    def test_slash_start_true(self):
        u = SimpleNamespace(welcomed_at=object())  # already welcomed
        assert needs_onboarding(u, "/start") is True

    def test_slash_start_with_payload_true(self):
        u = SimpleNamespace(welcomed_at=object())
        assert needs_onboarding(u, "/start qr_salon_1") is True

    def test_cb_welcome_callback_true(self):
        u = SimpleNamespace(welcomed_at=object())
        assert needs_onboarding(u, "cb:welcome:consent_yes") is True

    def test_first_contact_welcomed_at_none_true(self):
        u = SimpleNamespace(welcomed_at=None)
        assert needs_onboarding(u, "хочу массаж") is True

    def test_welcomed_plain_message_false(self):
        u = SimpleNamespace(welcomed_at=object())
        assert needs_onboarding(u, "хочу массаж") is False

    def test_welcomed_discover_book_callback_false(self):
        # A cb:discover:book:* handoff tap must NOT be captured by onboarding.
        u = SimpleNamespace(welcomed_at=object())
        assert needs_onboarding(u, "cb:discover:book:t:m") is False

    def test_unwelcomed_discover_book_callback_false(self):
        # Regression (CR Finding 1): even for a NEVER-welcomed user (welcomed_at
        # IS NULL — every pre-flag global user), a booking handoff tap must reach
        # the booking flow, not be swallowed by the welcome greeting.
        u = SimpleNamespace(welcomed_at=None)
        assert needs_onboarding(u, "cb:discover:book:t:m") is False


# --------------------------------------------------------------------------- #
# run_onboarding_turn — wrapping + consent journal + invariant                #
# --------------------------------------------------------------------------- #
class TestRunOnboardingTurn:
    def test_first_contact_returns_marketplace_welcome(self):
        bot_user, conv = _global_user_and_conv()
        assert bot_user.welcomed_at is None

        reply = run_onboarding_turn(conv, bot_user, "хочу массаж")

        assert reply.text == GLOBAL_WELCOME_TEXT
        # Only the «Начать» button — no salon/wellness buttons leak through.
        assert reply.action_data == {
            "buttons": [{"label": "▶️ Начать", "callback": "cb:welcome:start_s2"}],
            "button_columns": 1,
        }
        bot_user.refresh_from_db()
        assert bot_user.welcomed_at is not None
        assert current_tenant() is None

    def test_consent_yes_returns_marketplace_s5_and_stamps_consent(self):
        bot_user, conv = _global_user_and_conv()
        run_onboarding_turn(conv, bot_user, "/start")  # stamp welcomed_at

        reply = run_onboarding_turn(conv, bot_user, "cb:welcome:consent_yes")

        assert reply.text == GLOBAL_S5_TEXT
        assert reply.action_data is None  # wellness grid dropped
        bot_user.refresh_from_db()
        assert bot_user.consent_at is not None

    def test_consent_yes_writes_server_journal_row(self):
        bot_user, conv = _global_user_and_conv()
        run_onboarding_turn(conv, bot_user, "/start")

        run_onboarding_turn(conv, bot_user, "cb:welcome:consent_yes")

        rows = ConsentRecord.all_tenants.filter(
            bot_user=bot_user,
            consent_type=ConsentRecord.ConsentType.PERSONAL_DATA,
            granted=True,
        )
        assert rows.count() == 1
        assert rows.first().tenant == bot_user.tenant  # sentinel

    def test_consent_yes_journal_stamps_document_version(self):
        from apps.channels.max.global_onboarding import CONSENT_DOCUMENT_VERSION

        bot_user, conv = _global_user_and_conv()
        run_onboarding_turn(conv, bot_user, "/start")
        run_onboarding_turn(conv, bot_user, "cb:welcome:consent_yes")

        row = ConsentRecord.all_tenants.filter(bot_user=bot_user, granted=True).first()
        # 152-ФЗ informed consent — the row must prove WHICH disclosure was shown.
        assert row.document_version == CONSENT_DOCUMENT_VERSION

    def test_consent_yes_via_s2a_also_journals(self):
        # The S2a-fold path (cb:welcome:consent_yes_via_s2a) also stamps consent
        # + renders S5, so it too must write the server journal.
        bot_user, conv = _global_user_and_conv()
        run_onboarding_turn(conv, bot_user, "/start")

        reply = run_onboarding_turn(conv, bot_user, "cb:welcome:consent_yes_via_s2a")

        assert reply.text == GLOBAL_S5_TEXT
        assert ConsentRecord.all_tenants.filter(bot_user=bot_user, granted=True).count() == 1

    def test_repeat_consent_yes_is_idempotent_no_duplicate_journal(self):
        bot_user, conv = _global_user_and_conv()
        run_onboarding_turn(conv, bot_user, "/start")
        run_onboarding_turn(conv, bot_user, "cb:welcome:consent_yes")
        # Second tap re-renders S5 → journal is attempted again, but get_or_create
        # keeps it to a single active-grant row.
        run_onboarding_turn(conv, bot_user, "cb:welcome:consent_yes")

        rows = ConsentRecord.all_tenants.filter(bot_user=bot_user, granted=True)
        assert rows.count() == 1

    def test_consent_refuse_passes_through_and_leaves_consent_null(self):
        bot_user, conv = _global_user_and_conv()
        run_onboarding_turn(conv, bot_user, "/start")

        reply = run_onboarding_turn(conv, bot_user, "cb:welcome:consent_refuse")

        assert reply.text  # soft-exit text, verbatim
        assert reply.text not in (GLOBAL_WELCOME_TEXT, GLOBAL_S5_TEXT)
        bot_user.refresh_from_db()
        assert bot_user.consent_at is None
        assert ConsentRecord.all_tenants.filter(bot_user=bot_user).count() == 0

    def test_s2_consent_prompt_passes_through_with_buttons(self):
        bot_user, conv = _global_user_and_conv()
        run_onboarding_turn(conv, bot_user, "/start")

        reply = run_onboarding_turn(conv, bot_user, "cb:welcome:start_s2")

        # S2 consent text + its 3-button keyboard flow through untouched.
        assert reply.action_data is not None
        buttons = reply.action_data.get("buttons") or []
        callbacks = {b["callback"] for b in buttons}
        assert "cb:welcome:consent_yes" in callbacks
        assert "cb:welcome:consent_refuse" in callbacks


# --------------------------------------------------------------------------- #
# Integration — through handle_global_max_event                               #
# --------------------------------------------------------------------------- #
class TestHandlerIntegration:
    def test_first_message_shows_welcome_and_skips_discovery(
        self, mock_send, fake_redis, spy_discovery
    ):
        max_handler.handle_global_max_event(
            _msg_payload(text="хочу массаж", mid="m-1"), trace_id=str(uuid.uuid4())
        )

        spy_discovery.assert_not_called()
        assert len(mock_send) == 1
        assert mock_send[0]["text"] == GLOBAL_WELCOME_TEXT
        assert current_tenant() is None

    def test_full_consent_flow_then_discovery(self, mock_send, fake_redis, spy_discovery):
        uid = 4242
        # 1) first contact → welcome
        max_handler.handle_global_max_event(
            _msg_payload(text="привет", user_id=uid, mid="a"), trace_id=str(uuid.uuid4())
        )
        # 2) tap «Начать» → S2 consent prompt
        max_handler.handle_global_max_event(
            _callback_payload(payload="cb:welcome:start_s2", user_id=uid, callback_id="c1"),
            trace_id=str(uuid.uuid4()),
        )
        # 3) consent yes → S5 marketplace CTA + consent stamped
        max_handler.handle_global_max_event(
            _callback_payload(payload="cb:welcome:consent_yes", user_id=uid, callback_id="c2"),
            trace_id=str(uuid.uuid4()),
        )
        assert mock_send[-1]["text"] == GLOBAL_S5_TEXT
        spy_discovery.assert_not_called()

        bot_user = resolve_or_create_global_bot_user(channel="max", channel_user_id=str(uid))
        assert bot_user.consent_at is not None

        # 4) a normal message now flows to discovery
        max_handler.handle_global_max_event(
            _msg_payload(text="маникюр в Пензе", user_id=uid, mid="b"),
            trace_id=str(uuid.uuid4()),
        )
        spy_discovery.assert_called_once()

    def test_consent_refuse_then_can_still_search(self, mock_send, fake_redis, spy_discovery):
        uid = 5151
        max_handler.handle_global_max_event(
            _msg_payload(text="привет", user_id=uid, mid="a"), trace_id=str(uuid.uuid4())
        )
        max_handler.handle_global_max_event(
            _callback_payload(payload="cb:welcome:consent_refuse", user_id=uid, callback_id="r1"),
            trace_id=str(uuid.uuid4()),
        )
        spy_discovery.assert_not_called()

        # Variant A: refused user can still search.
        max_handler.handle_global_max_event(
            _msg_payload(text="массаж завтра", user_id=uid, mid="b"),
            trace_id=str(uuid.uuid4()),
        )
        spy_discovery.assert_called_once()

        bot_user = resolve_or_create_global_bot_user(channel="max", channel_user_id=str(uid))
        assert bot_user.consent_at is None

    def test_unwelcomed_booking_tap_reaches_handoff_not_welcome(
        self, monkeypatch, mock_send, fake_redis, spy_discovery
    ):
        # CR Finding 1 regression: a brand-new (welcomed_at IS NULL) user taps a
        # master card at flag flip → must enter the booking handoff, NOT get the
        # welcome greeting.
        from apps.orchestrator.discovery import DiscoveryReply

        handoff_spy = MagicMock(return_value=DiscoveryReply(text="Открываю запись…"))
        monkeypatch.setattr(max_handler, "_discovery_handoff_reply", handoff_spy)

        tenant_id, master_id = uuid.uuid4(), uuid.uuid4()
        max_handler.handle_global_max_event(
            _callback_payload(
                payload=f"cb:discover:book:{tenant_id}:{master_id}",
                user_id=6161,
                callback_id="bk1",
            ),
            trace_id=str(uuid.uuid4()),
        )

        handoff_spy.assert_called_once()
        assert mock_send[-1]["text"] == "Открываю запись…"
        assert mock_send[-1]["text"] != GLOBAL_WELCOME_TEXT

    def test_flag_off_goes_straight_to_discovery(
        self, settings, mock_send, fake_redis, spy_discovery
    ):
        settings.GLOBAL_BOT_ONBOARDING = False
        max_handler.handle_global_max_event(
            _msg_payload(text="привет", mid="m-1"), trace_id=str(uuid.uuid4())
        )
        # Legacy behaviour — no onboarding, discovery runs.
        spy_discovery.assert_called_once()
        assert mock_send[0]["text"] != GLOBAL_WELCOME_TEXT
