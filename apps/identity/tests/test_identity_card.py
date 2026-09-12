"""§12 (owner 11.09) — the read-only identity card and `/whoami`.

Two audiences, one card: the operator sees tenants and masks; the person
sees their own values and other salons as a number. Both sides of each
rule are asserted — what is shown AND what is not — because the harm here
is a value that leaks, and a test that only checks presence would pass on
a card that prints everything.
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.channels.bot_registry import BotEntry
from apps.channels.max.salon_handler import handle_salon_max_event
from apps.consent.models import ConsentRecord
from apps.conversations.models import Conversation, Message
from apps.identity.models import BotUser, MemoryEntry, UserPersonalContext
from apps.identity.services import identity_card as card_mod
from apps.identity.services.identity_card import (
    BLOCKED_BY_IDENTITY,
    build_card,
    mask_name,
    mask_phone,
    render_for_operator,
    render_for_person,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

CID = "999000777"
NAME = "Анастасия"
PHONE = "+79991234567"


@pytest.fixture
def salon() -> Tenant:
    return Tenant.objects.create(slug="card-salon", name="Салон")


@pytest.fixture
def other() -> Tenant:
    return Tenant.objects.create(slug="card-other", name="Другой")


@pytest.fixture
def third() -> Tenant:
    return Tenant.objects.create(slug="card-third", name="Третий")


def _person(salon: Tenant, other: Tenant, third: Tenant) -> BotUser:
    """One identity in three salons: owner + master card here, plain elsewhere."""
    ayla = uuid.uuid4()
    home = BotUser.all_tenants.create(
        tenant=salon,
        channel="max",
        channel_user_id=CID,
        display_name=NAME,
        phone=PHONE,
        ayla_user_id=ayla,
    )
    second = BotUser.all_tenants.create(
        tenant=other, channel="max", channel_user_id=CID, ayla_user_id=ayla
    )
    last = BotUser.all_tenants.create(tenant=third, channel="max", channel_user_id=CID)
    # DRF-1836 (12.09.2026): the card orders shells by `first_seen`, then `id`.
    # `first_seen` is auto_now_add — three creates in one tick tie, and the
    # tie-break is a random UUID, so «creation order» was a coincidence the
    # test asserted (1 red in 5). State the fact the test is about: the
    # shells appeared in this order. `update()` is the one path auto_now_add
    # does not overwrite.
    base = timezone.now() - timedelta(days=3)
    for offset, row in enumerate((home, second, last)):
        BotUser.all_tenants.filter(pk=row.pk).update(first_seen=base + timedelta(days=offset))
    TenantStaff.all_tenants.create(tenant=salon, bot_user=home, role=TenantStaff.Role.OWNER)
    CatalogMaster.all_tenants.create(
        tenant=salon,
        name=NAME,
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
        linked_bot_user=home,
        accepted_at=timezone.now(),
    )
    with tenant_scope(salon):
        conv = Conversation.objects.create(tenant=salon, bot_user=home)
        Message.objects.create(
            tenant=salon, conversation=conv, role=Message.Role.USER, content="привет"
        )
    ConsentRecord.all_tenants.create(
        tenant=salon,
        bot_user=home,
        consent_type=ConsentRecord.ConsentType.PERSONAL_DATA.value,
        granted=True,
        source="test:fixture",
    )
    upc = UserPersonalContext.objects.create(user_id=ayla)
    MemoryEntry.objects.create(
        user_id=ayla,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        consent_at=timezone.now(),
        content={"likes": "маникюр"},
        source_tenant_id=salon.id,
    )
    return home


# --- masks -------------------------------------------------------------------


class TestMasks:
    def test_name_is_first_letter_and_length(self):
        assert mask_name(NAME) == "А… (9)"
        assert mask_name("") == "нет"
        assert mask_name(None) == "нет"

    def test_phone_is_presence_and_length_never_a_digit(self):
        masked = mask_phone(PHONE)
        assert masked == "есть, длина 12"
        assert not any(ch.isdigit() and ch != "1" and ch != "2" for ch in masked.replace("12", ""))
        assert PHONE[-4:] not in masked
        assert mask_phone("") == "нет"


# --- the card ----------------------------------------------------------------


class TestTheCard:
    def test_every_shell_is_there_with_its_context(self, salon, other, third):
        _person(salon, other, third)
        card = build_card("max", CID)
        assert card.found
        assert [s.tenant_slug for s in card.shells] == ["card-salon", "card-other", "card-third"]
        home = card.shells[0]
        assert home.roles == ("owner",)
        assert home.has_master_card is True
        assert home.conversations == 1
        assert home.last_message_at is not None
        assert home.consents == 1
        assert home.has_ayla_link is True
        assert card.shells[2].has_ayla_link is False
        assert len(card.ayla_user_ids) == 1
        assert card.memory_entries == 1
        assert card.status == BLOCKED_BY_IDENTITY

    def test_unknown_identity_is_a_named_absence(self):
        card = build_card("max", "404")
        assert not card.found
        assert "оболочек BotUser нет" in render_for_operator(card)

    def test_building_the_card_writes_nothing(self, salon, other, third):
        _person(salon, other, third)
        with CaptureQueriesContext(connection) as ctx:
            build_card("max", CID)
        verbs = {q["sql"].lstrip().split(" ", 1)[0].upper() for q in ctx.captured_queries}
        assert verbs, "the card must have READ something"
        assert verbs <= {"SELECT", "SAVEPOINT", "RELEASE"}, sorted(verbs)


# --- the operator's rendering --------------------------------------------------


class TestTheOperatorSeesMasksAndTenants:
    def test_tenants_yes_values_no(self, salon, other, third):
        _person(salon, other, third)
        text = render_for_operator(build_card("max", CID))
        # Present: tenants by slug, role, master card, the status, the masks.
        for slug in ("card-salon", "card-other", "card-third"):
            assert slug in text
        assert "owner" in text
        assert BLOCKED_BY_IDENTITY in text
        assert "А… (9)" in text
        assert "есть, длина 12" in text
        # Absent: the values themselves.
        assert NAME not in text
        assert PHONE not in text
        assert PHONE[-4:] not in text

    def test_what_is_not_here_is_named(self, salon, other, third):
        _person(salon, other, third)
        text = render_for_operator(build_card("max", CID))
        assert "не в этой карточке" in text
        assert "каталог" in text

    def test_the_command_prints_the_operator_rendering(self, salon, other, third):
        _person(salon, other, third)
        out = io.StringIO()
        call_command("identity_card", "--account", f"max:{CID}", stdout=out)
        text = out.getvalue()
        assert "card-salon" in text
        assert BLOCKED_BY_IDENTITY in text
        assert NAME not in text
        assert PHONE not in text


# --- the person's rendering ------------------------------------------------------


class TestThePersonSeesTheirOwnValues:
    def test_salon_bot_shows_this_salon_and_counts_the_rest(self, salon, other, third):
        _person(salon, other, third)
        text = render_for_person(build_card("max", CID), tenant_slug="card-salon")
        assert NAME in text
        assert PHONE in text
        assert "роль здесь: owner" in text
        assert "карточка мастера: есть" in text
        assert "ещё в салонах: 2" in text
        # Other salons are a number, never a name.
        assert "card-other" not in text
        assert "card-third" not in text
        assert "card-salon" not in text

    def test_client_bot_shows_the_global_shell_and_memory(self, salon, other, third):
        _person(salon, other, third)
        text = render_for_person(build_card("max", CID), tenant_slug=None)
        assert "записей в памяти: 1" in text
        assert "ещё в салонах" not in text or "ещё в салонах: 0" not in text
        assert "card-" not in text

    def test_a_stranger_gets_nothing_but_the_sentence(self):
        text = render_for_person(build_card("max", "404"), tenant_slug="card-salon")
        assert "не знаю" in text
        assert "card-" not in text


# --- the doors ---------------------------------------------------------------------


SALON_BOT = BotEntry(
    slug="salon",
    webhook_secret="wh-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="card-salon",
    stream="max_salon",
    web_app="id583_bot",
)


class TestTheSalonDoor:
    @pytest.fixture(autouse=True)
    def _registry(self, settings):
        settings.MAX_BOT_REGISTRY = (SALON_BOT,)
        settings.MAX_BOT_TOKEN = "token-client"  # pragma: allowlist secret

    def _message(self, text: str) -> dict:
        return {
            "update_type": "message_created",
            "timestamp": int(timezone.now().timestamp() * 1000) + uuid.uuid4().int % 100000,
            "message": {
                "sender": {"user_id": int(CID), "name": NAME},
                "recipient": {"chat_id": 315714313, "chat_type": "dialog"},
                "body": {"mid": str(uuid.uuid4()), "seq": 1, "text": text, "attachments": []},
            },
        }

    def test_whoami_answers_with_the_persons_card_in_this_salon(self, salon, other, third):
        _person(salon, other, third)
        with patch("apps.channels.max.outbound.send_message") as sent, tenant_scope(salon):
            handle_salon_max_event(self._message("/whoami"))
        assert sent.called
        text = sent.call_args.kwargs["text"]
        assert "роль здесь: owner" in text
        assert NAME in text
        assert "ещё в салонах: 2" in text
        assert "card-other" not in text

    def test_whoami_beats_ask_for_code_for_someone_with_no_role(self, salon):
        """Человек без роли получает карточку, не «введите код» — как и раньше.

        12.09.2026 (DRF-1784): у такого человека нет рабочей строки, и бот
        отвечает ему по личности, не читая тенант записи, — раздела «здесь»
        («роль здесь: клиент») в карточке больше нет: салона «здесь» у него
        пока нет. Раньше строка `customer` в тенанте записи делала салон
        «здешним» для любого написавшего.
        """
        BotUser.all_tenants.create(tenant=salon, channel="max", channel_user_id=CID)
        with patch("apps.channels.max.outbound.send_message") as sent, tenant_scope(salon):
            handle_salon_max_event(self._message("/whoami"))
        text = sent.call_args.kwargs["text"]
        assert "Что я о вас знаю" in text
        assert "роль здесь" not in text
        assert "код" not in text.lower()


class TestTheClientDoor:
    def test_whoami_is_a_typed_command_above_onboarding(self, settings, monkeypatch):
        from apps.channels.handlers import GlobalMaxHandler
        from apps.channels.max import handler as max_handler
        from apps.identity.services import resolve_or_create_global_bot_user

        from apps.orchestrator.memory import short_term
        from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

        settings.STRICT_TENANT_SCOPE = "strict"
        settings.GLOBAL_BOT_ONBOARDING = True
        monkeypatch.setattr(short_term, "_redis_client", lambda fake=_FakeRedis(): fake)
        calls: list[dict] = []
        monkeypatch.setattr(
            max_handler,
            "send_message",
            lambda *, chat_id, text, attachments=None, timeout=10.0: (
                calls.append({"text": text}) or {"ok": True}
            ),
        )
        bu = resolve_or_create_global_bot_user(
            channel="max", channel_user_id="7777", ayla_user_id=None
        )
        assert bu.welcomed_at is None, "never welcomed — onboarding would claim a plain message"
        payload = {
            "update_type": "message_created",
            "timestamp": 1731320000000,
            "message": {
                "sender": {"user_id": 7777, "name": "Иван"},
                "recipient": {"chat_id": 8777, "chat_type": "dialog"},
                "body": {"mid": "who-1", "seq": 1, "text": "/whoami", "attachments": []},
            },
        }
        GlobalMaxHandler()(
            {"data": json.dumps(payload), "trace_id": str(uuid.uuid4()), "resolved_tenant_id": ""}
        )
        assert len(calls) == 1
        assert "Что я о вас знаю" in calls[0]["text"]
        assert "записей в памяти: 0" in calls[0]["text"]


# --- the module's surface ------------------------------------------------------------


class TestTheModuleNamesItsLimits:
    def test_not_here_lists_the_catalog_side(self):
        card = build_card("max", "404")
        assert any("каталог" in item for item in card.not_here)

    def test_whoami_is_exact_match(self):
        assert card_mod.WHOAMI_COMMAND == "/whoami"
