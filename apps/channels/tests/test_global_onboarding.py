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
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


@pytest.fixture
def spy_direct_show_masters(monkeypatch):
    """DRF-1102 — the deterministic show-masters branch. A general
    booking/service phrase (e.g. «маникюр в Пензе») now reaches THIS
    function instead of ``generate_concierge_reply``, skipping the LLM."""
    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Вот мастера, которые могут подойти:"))
    monkeypatch.setattr(max_handler, "generate_direct_show_masters_reply", spy)
    return spy


@pytest.fixture(autouse=True)
def _penza_is_a_place_we_serve():
    """One bookable master in Пенза (DRF-1328).

    «маникюр в Пензе» names a city, and since DRF-1328 the deterministic
    branch claims a turn only when it can account for EVERY word. A city is
    accounted for exactly when the marketplace has someone bookable there
    (``apps.marketplace.discovery.strip_known_cities`` — live data, by
    DRF-1283's design). With no masters anywhere, «пензе» is an unknown word
    and the turn goes to the model instead; this file is about onboarding, so
    it needs the contour where the branch can run.
    """
    from datetime import datetime, timezone

    from apps.catalog.models import CatalogMaster
    from apps.tenancy.models import Tenant

    tenant = Tenant.objects.create(slug="salon-penza-1328-onb", name="SPAtrium", city="Пенза")
    CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Архипкин Денис",
        specialization="массаж",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        external_updated_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
    )


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


def _granted_types(bot_user) -> set[str]:
    """Active granted consent types for this user (sentinel tenant)."""
    return set(
        ConsentRecord.all_tenants.filter(
            bot_user=bot_user, granted=True, withdrawn_at__isnull=True
        ).values_list("consent_type", flat=True)
    )


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

    def test_first_contact_greeting_only_true(self):
        """BOT-001 §17 шаг 3 — «First message is a greeting only» →
        контекстное приветствие. Это по-прежнему onboarding."""
        u = SimpleNamespace(welcomed_at=None)
        assert needs_onboarding(u, "привет") is True

    def test_first_contact_with_actionable_intent_false(self):
        """DRF-1205 / BOT-001 P1 «Intent Before Ceremony»: «If the user's
        first message contains a clear actionable intent, Ayla MUST
        progress that intent immediately. Greeting or scripted
        introduction MUST NOT delay useful action.»

        §17 шаг 2 говорит то же: «Progress intent immediately … Skip
        scripted greeting.» До правки этот путь смотрел только на
        ``/start``, два префикса колбэков и ``welcomed_at`` — содержимое
        сообщения не исследовалось вовсе."""
        u = SimpleNamespace(welcomed_at=None)
        assert needs_onboarding(u, "хочу массаж") is False
        assert needs_onboarding(u, "маникюр в Пензе") is False
        assert needs_onboarding(u, "есть окошко на завтра") is False

    def test_first_contact_but_conversation_already_under_way_false(self):
        """DRF-1207, второй путь. Глобальный путь не проходит через
        `WelcomeSkill.matches` и его guard — приветствие всплывало посреди
        разговора, если первый ход забрала другая ветка и `welcomed_at`
        остался NULL. BOT-001 P1 / CDP анти-паттерн «Amnesia»."""
        bot_user, conversation = _global_user_and_conv(user_id=91001)
        from apps.conversations.services import record_global_message

        # Ход 1 достался другой ветке: входящее + ответ бота.
        record_global_message(conversation, role="user", content="хочу массаж")
        record_global_message(conversation, role="assistant", content="Вот мастера:")
        # Ход 2 — новое входящее, записанное до ветвления ответа.
        record_global_message(conversation, role="user", content="спасибо")

        assert bot_user.welcomed_at is None
        assert needs_onboarding(bot_user, "спасибо", conversation) is False

    def test_first_contact_single_inbound_row_still_greets(self):
        """Настоящий первый контакт: канал записал ровно одну строку."""
        bot_user, conversation = _global_user_and_conv(user_id=91002)
        from apps.conversations.services import record_global_message

        record_global_message(conversation, role="user", content="привет")

        assert needs_onboarding(bot_user, "привет", conversation) is True

    def test_explicit_start_mid_conversation_still_onboards(self):
        """`/start` — явный жест, guard его не касается (паритет с
        основным путём)."""
        bot_user, conversation = _global_user_and_conv(user_id=91003)
        from apps.conversations.services import record_global_message

        record_global_message(conversation, role="user", content="хочу массаж")
        record_global_message(conversation, role="assistant", content="Вот мастера:")
        record_global_message(conversation, role="user", content="/start")

        assert needs_onboarding(bot_user, "/start", conversation) is True

    def test_first_contact_unrecognised_text_still_greets(self):
        """Сигнал намерения намеренно узкий: нераспознанное первое
        сообщение остаётся greeting-driven entry (§6), а не проваливается
        в discovery."""
        u = SimpleNamespace(welcomed_at=None)
        assert needs_onboarding(u, "ыаывпаып") is True

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

    # --- DRF-1311: the tap grants memory_green too --------------------- #

    def test_consent_yes_journals_memory_green_too(self):
        """The S2 disclosure IS the memory disclosure — record it as one.

        «Я буду помнить о тебе только то, что поможет рекомендовать точнее»
        (+ S2a «Запоминаю: твои сообщения мне, выбранные цели, питание и
        вода…»). Writing only ``personal_data`` left
        :func:`has_memory_consent` False forever (DRF-1311).
        """
        bot_user, conv = _global_user_and_conv()
        run_onboarding_turn(conv, bot_user, "/start")

        run_onboarding_turn(conv, bot_user, "cb:welcome:consent_yes")

        assert _granted_types(bot_user) == {
            ConsentRecord.ConsentType.PERSONAL_DATA.value,
            ConsentRecord.ConsentType.MEMORY_GREEN.value,
        }

    def test_memory_green_row_carries_the_same_disclosure_version(self):
        """Both types must cite the SAME text — the user saw one screen."""
        from apps.channels.max.global_onboarding import CONSENT_DOCUMENT_VERSION

        bot_user, conv = _global_user_and_conv()
        run_onboarding_turn(conv, bot_user, "/start")
        run_onboarding_turn(conv, bot_user, "cb:welcome:consent_yes")

        row = ConsentRecord.all_tenants.get(
            bot_user=bot_user,
            consent_type=ConsentRecord.ConsentType.MEMORY_GREEN,
            granted=True,
        )
        assert row.document_version == CONSENT_DOCUMENT_VERSION
        assert row.tenant == bot_user.tenant  # sentinel

    def test_onboarded_user_can_read_memory_the_gate_actually_opens(self):
        """The regression DRF-1311 needed: onboarding OPENS the read gate.

        Every unit around this passed on 23.08 while the pilot's first stored
        fact was unreadable: the write gate (``can_store_green_memory`` →
        PERSONAL_DATA) said yes and the read gate
        (``has_memory_consent`` → memory_green) said no, because nothing ever
        granted memory_green. Asserting the two gates AGREE after onboarding
        is the only assertion that spans that seam.
        """
        from apps.consent.memory import can_store_green_memory
        from apps.consent.services import has_memory_consent

        bot_user, conv = _global_user_and_conv()
        bot_user.ayla_user_id = uuid.uuid4()
        bot_user.save(update_fields=["ayla_user_id"])
        run_onboarding_turn(conv, bot_user, "/start")
        run_onboarding_turn(conv, bot_user, "cb:welcome:consent_yes")

        assert can_store_green_memory(bot_user) is True  # write side
        assert has_memory_consent(bot_user.ayla_user_id, "green") is True  # read side

    def test_consent_yes_via_s2a_also_journals(self):
        # The S2a-fold path (cb:welcome:consent_yes_via_s2a) also stamps consent
        # + renders S5, so it too must write the server journal.
        bot_user, conv = _global_user_and_conv()
        run_onboarding_turn(conv, bot_user, "/start")

        reply = run_onboarding_turn(conv, bot_user, "cb:welcome:consent_yes_via_s2a")

        assert reply.text == GLOBAL_S5_TEXT
        assert _granted_types(bot_user) == {
            ConsentRecord.ConsentType.PERSONAL_DATA.value,
            ConsentRecord.ConsentType.MEMORY_GREEN.value,
        }

    def test_repeat_consent_yes_is_idempotent_no_duplicate_journal(self):
        bot_user, conv = _global_user_and_conv()
        run_onboarding_turn(conv, bot_user, "/start")
        run_onboarding_turn(conv, bot_user, "cb:welcome:consent_yes")
        # Second tap re-renders S5 → journal is attempted again, but get_or_create
        # keeps it to a single active-grant row PER TYPE.
        run_onboarding_turn(conv, bot_user, "cb:welcome:consent_yes")

        rows = ConsentRecord.all_tenants.filter(bot_user=bot_user, granted=True)
        assert rows.count() == 2  # personal_data + memory_green, one each
        assert len(_granted_types(bot_user)) == 2

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
            _msg_payload(text="привет", mid="m-1"), trace_id=str(uuid.uuid4())
        )

        spy_discovery.assert_not_called()
        assert len(mock_send) == 1
        assert mock_send[0]["text"] == GLOBAL_WELCOME_TEXT
        assert current_tenant() is None

    def test_first_message_with_intent_is_progressed_not_greeted(
        self, mock_send, fake_redis, spy_discovery, spy_direct_show_masters
    ):
        """DRF-1205 — церемония не перехватывает первое сообщение с
        понятным намерением (BOT-001 P1, §17 шаг 2; CDP-02)."""
        max_handler.handle_global_max_event(
            _msg_payload(text="хочу массаж", mid="m-1"), trace_id=str(uuid.uuid4())
        )

        assert len(mock_send) == 1
        assert mock_send[0]["text"] != GLOBAL_WELCOME_TEXT
        spy_direct_show_masters.assert_called_once()

    def test_full_consent_flow_then_discovery(
        self, mock_send, fake_redis, spy_discovery, spy_direct_show_masters
    ):
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
        spy_direct_show_masters.assert_not_called()

        bot_user = resolve_or_create_global_bot_user(channel="max", channel_user_id=str(uid))
        assert bot_user.consent_at is not None

        # 4) a normal message now flows to the normal reply pipeline — DRF-1102
        # made «маникюр в Пензе» a deterministic show-masters short-circuit
        # (it names a service), so THAT function runs, not the concierge LLM.
        max_handler.handle_global_max_event(
            _msg_payload(text="маникюр в Пензе", user_id=uid, mid="b"),
            trace_id=str(uuid.uuid4()),
        )
        spy_discovery.assert_not_called()
        spy_direct_show_masters.assert_called_once()

    def test_consent_refuse_then_can_still_search(
        self, mock_send, fake_redis, spy_discovery, spy_direct_show_masters
    ):
        uid = 5151
        max_handler.handle_global_max_event(
            _msg_payload(text="привет", user_id=uid, mid="a"), trace_id=str(uuid.uuid4())
        )
        max_handler.handle_global_max_event(
            _callback_payload(payload="cb:welcome:consent_refuse", user_id=uid, callback_id="r1"),
            trace_id=str(uuid.uuid4()),
        )
        spy_discovery.assert_not_called()
        spy_direct_show_masters.assert_not_called()

        # Variant A: refused user can still search. «массаж завтра» names a
        # service (DRF-1102 deterministic branch), so it short-circuits to
        # show masters directly rather than the concierge LLM.
        max_handler.handle_global_max_event(
            _msg_payload(text="массаж завтра", user_id=uid, mid="b"),
            trace_id=str(uuid.uuid4()),
        )
        spy_discovery.assert_not_called()
        spy_direct_show_masters.assert_called_once()

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


# --------------------------------------------------------------------------- #
# DRF-1207 (второй путь) — приветствие посреди разговора на глобальном пути    #
# --------------------------------------------------------------------------- #
@pytest.fixture
def spy_visits(monkeypatch):
    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Ваши записи: пока пусто."))
    monkeypatch.setattr(max_handler, "route_visits", spy)
    return spy


class TestGlobalWelcomeDoesNotWakeUpMidConversation:
    """Глобальный путь зовёт `WelcomeSkill.handle()` напрямую, минуя
    `matches()` и его guard `_flow_already_established`. Собственного
    guard'а здесь не было: признаком первого контакта считался только
    `welcomed_at IS NULL`.

    Аудит этот второй путь не заметил — он локализовал нарушение одним
    местом в `welcome/skill.py`.
    """

    def test_second_turn_is_not_greeted(self, mock_send, fake_redis, spy_visits, spy_discovery):
        """Ход 1 забирает ветка «покажи мои записи» — она стоит ПЕРЕД
        onboarding, поэтому `welcomed_at` остаётся NULL. Ход 2 не должен
        получить полное приветствие посреди идущего разговора.

        BOT-001 P1 «Intent Before Ceremony» + CDP §5 «Amnesia».
        """
        max_handler.handle_global_max_event(
            _msg_payload(text="покажи мои записи", user_id=90501, mid="g-1"),
            trace_id=str(uuid.uuid4()),
        )
        assert len(mock_send) == 1
        assert mock_send[0]["text"] != GLOBAL_WELCOME_TEXT

        max_handler.handle_global_max_event(
            _msg_payload(text="спасибо", user_id=90501, mid="g-2"),
            trace_id=str(uuid.uuid4()),
        )
        assert len(mock_send) == 2
        assert mock_send[1]["text"] != GLOBAL_WELCOME_TEXT

    def test_genuine_first_contact_is_still_greeted(self, mock_send, fake_redis, spy_discovery):
        """Обратная сторона: настоящий первый контакт приветствие получает."""
        max_handler.handle_global_max_event(
            _msg_payload(text="привет", user_id=90502, mid="g-3"),
            trace_id=str(uuid.uuid4()),
        )
        assert mock_send[0]["text"] == GLOBAL_WELCOME_TEXT
