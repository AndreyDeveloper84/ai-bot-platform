"""Global-path catalog chips end to end (DRF-1304).

The owner's call on 23.08 (docs/REPLY_CONCIERGE_SURFACE.md): «показывать
кнопками, а не абзацем» — a list of five salons as a paragraph makes the
person type a name back, when what they want is one of them.

The rule that comes with it is the one this file exists to pin: **a chip must
lead to something that really executes**. So these tests do not check the
renderer (``apps/orchestrator/tests/test_catalog_surface.py`` does); they check
the whole path a finger takes — the tap arrives as ``event.text``, the ladder
catches it BEFORE the concierge, a by-id read answers it, and the outbound
message carries a real MAX keyboard so the next tap exists too.

Three invariants:

* a catalog tap NEVER reaches the LLM — it is a deterministic transition, and
  the model must not be handed a raw ``cb:…`` string to interpret;
* a stale or malformed tap still gets an answer, not a fall-through;
* the greeting does not swallow a tap on a card the bot itself drew;
* the raw ``cb:…`` payload never lands in dialog history (DRF-988: the model
  read such payloads as things the person had said and hallucinated on them).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.channels.max import handler as max_handler
from apps.orchestrator.discovery import (
    CALLBACK_CATALOG_MASTERS_PREFIX,
    CALLBACK_CATALOG_SERVICES_PREFIX,
    CALLBACK_DISCOVER_BOOK_PREFIX,
    CATALOG_STALE_CARD_TEXT,
)
from apps.orchestrator.memory import short_term
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _payload(*, text: str, user_id: int, chat_id: int, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _run_global(text: str, *, user_id: int = 909, chat_id: int = 909, mid: str) -> None:
    max_handler.handle_global_max_event(
        _payload(text=text, user_id=user_id, chat_id=chat_id, mid=mid),
        trace_id=str(uuid.uuid4()),
    )


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
def spy_concierge(monkeypatch):
    from unittest.mock import MagicMock

    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Какая услуга интересует?"))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


@pytest.fixture
def salon() -> SimpleNamespace:
    """A salon with one bookable master performing one real service."""
    tenant = Tenant.objects.create(slug="bodyformula", name="BodyFormula", city="Пенза")
    ts = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=ts,
        name="Анна",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        raw={"address": "Пенза, ул. Леонова, 15а"},
    )
    service = CatalogService.all_tenants.create(
        tenant=tenant,
        external_updated_at=ts,
        slug="massazh-spiny",
        name="Массаж спины",
        is_active=True,
        price_from=Decimal("1700"),
        duration_min=45,
    )
    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    return SimpleNamespace(tenant=tenant, master=master, service=service)


def _keyboard(sent: dict) -> list[list[dict]]:
    """The MAX wire keyboard of an outbound message ([] when there is none)."""
    for attachment in sent["attachments"] or []:
        if attachment.get("type") == "inline_keyboard":
            return attachment["payload"]["buttons"]
    return []


class TestCatalogTaps:
    def test_salon_tap_answers_with_that_salons_services(
        self, mock_send, fake_redis, spy_concierge, salon
    ):
        _run_global(f"{CALLBACK_CATALOG_SERVICES_PREFIX}{salon.tenant.id}", mid="chip1")

        sent = mock_send[-1]
        assert "Массаж спины" in sent["text"]
        assert "1700" in sent["text"]
        # A callback is a deterministic transition — the LLM never sees it.
        spy_concierge.assert_not_called()

    def test_salon_tap_ships_a_real_keyboard_so_the_next_tap_exists(
        self, mock_send, fake_redis, spy_concierge, salon
    ):
        _run_global(f"{CALLBACK_CATALOG_SERVICES_PREFIX}{salon.tenant.id}", mid="chip2")

        rows = _keyboard(mock_send[-1])
        assert rows == [
            [
                {
                    "type": "callback",
                    "text": "Массаж спины",
                    "payload": f"{CALLBACK_CATALOG_MASTERS_PREFIX}{salon.service.id}",
                }
            ]
        ]

    def test_service_tap_lands_on_a_master_ready_to_book(
        self, mock_send, fake_redis, spy_concierge, salon
    ):
        _run_global(f"{CALLBACK_CATALOG_MASTERS_PREFIX}{salon.service.id}", mid="chip3")

        sent = mock_send[-1]
        assert "Анна" in sent["text"]
        payload = _keyboard(sent)[0][0]["payload"]
        # The chain ends in the booking handoff seam, service context included.
        assert payload.startswith(CALLBACK_DISCOVER_BOOK_PREFIX)
        assert payload.endswith(f":{salon.service.id}")
        spy_concierge.assert_not_called()

    def test_stale_tap_is_answered_not_dropped(self, mock_send, fake_redis, spy_concierge):
        _run_global(f"{CALLBACK_CATALOG_SERVICES_PREFIX}{uuid.uuid4()}", mid="chip4")

        assert mock_send[-1]["text"] == CATALOG_STALE_CARD_TEXT
        # DRF-1492 — the line says «Нажмите "Показать салоны"», so the message
        # that carries it carries the button. Asserted on the CHANNEL, where
        # the keyboard either reaches the wire or does not.
        assert [b["payload"] for b in _keyboard(mock_send[-1])[0]] == ["cb:catalog:salons"]
        # The worst outcome would be the raw «cb:…» string reaching the model,
        # which would answer it as if the person had said it.
        spy_concierge.assert_not_called()

    def test_malformed_tap_is_answered_not_raised(self, mock_send, fake_redis, spy_concierge):
        _run_global(f"{CALLBACK_CATALOG_SERVICES_PREFIX}не-uuid", mid="chip5")

        assert mock_send[-1]["text"] == CATALOG_STALE_CARD_TEXT
        assert [b["payload"] for b in _keyboard(mock_send[-1])[0]] == ["cb:catalog:salons"]
        spy_concierge.assert_not_called()

    def test_an_unknown_catalog_slug_is_answered_with_a_button_too(
        self, mock_send, fake_redis, spy_concierge
    ):
        """DRF-1492 — the ladder in the handler routes ``cb:catalog:*`` by
        PREFIX, so a slug no branch claims («cb:catalog:salons:moscow», a
        renamed verb, a keyboard older than the grammar) reaches the executor
        too. Its own fallback is keyboardless, and the stale line now names a
        button — so the executor answers this class itself.
        """
        _run_global("cb:catalog:salons:moscow", mid="chip6b")

        assert mock_send[-1]["text"] == CATALOG_STALE_CARD_TEXT
        assert [b["payload"] for b in _keyboard(mock_send[-1])[0]] == ["cb:catalog:salons"]
        spy_concierge.assert_not_called()

    def test_the_show_salons_chip_answers_with_the_salon_list(
        self, mock_send, fake_redis, spy_concierge, salon
    ):
        """The entry point of the chain, as a button, end to end on MAX."""
        _run_global("cb:catalog:salons", mid="chip6c")

        assert salon.tenant.name in mock_send[-1]["text"]
        assert [b["payload"] for b in _keyboard(mock_send[-1])[0]] == [
            f"{CALLBACK_CATALOG_SERVICES_PREFIX}{salon.tenant.id}"
        ]
        spy_concierge.assert_not_called()

    def test_tap_never_lands_in_dialog_history(self, mock_send, fake_redis, spy_concierge, salon):
        from apps.conversations.models import Message

        _run_global(f"{CALLBACK_CATALOG_SERVICES_PREFIX}{salon.tenant.id}", mid="chip7")

        user_turns = [m.content for m in Message.all_tenants.filter(role="user")]
        assert all(not c.startswith("cb:") for c in user_turns), user_turns
        # The answer IS recorded — that is the grounding the next turn needs.
        assert any(
            "Массаж спины" in m.content for m in Message.all_tenants.filter(role="assistant")
        )

    def test_greeting_does_not_swallow_a_tap(
        self, mock_send, fake_redis, spy_concierge, salon, settings
    ):
        # Every global BotUser that predates the onboarding flag has
        # welcomed_at IS NULL — this is the shape in which a real pilot user
        # taps a chip for the first time.
        settings.GLOBAL_BOT_ONBOARDING = True

        _run_global(f"{CALLBACK_CATALOG_SERVICES_PREFIX}{salon.tenant.id}", mid="chip6")

        assert "Массаж спины" in mock_send[-1]["text"]
