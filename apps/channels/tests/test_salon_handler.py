"""Salon bot conversation — onboarding by code and role-aware replies (DRF-1061).

The acceptance criterion for the whole epic runs through this handler:
«владелец открывает салонного бота, вводит код приглашения и становится
администратором». These tests are that sentence, plus what happens when it
goes wrong.

Two properties get the most attention:

* **the reply goes out as the salon bot**, not the client bot — a wrong
  sender is invisible in logs and obvious only to the person who receives
  a message from the wrong avatar;
* **ordinary chat is not an invite attempt** — a staff member saying
  «привет» must not burn their rate-limit budget.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from apps.channels.bot_registry import BotEntry
from apps.channels.max.salon_handler import _extract_code, handle_salon_max_event
from apps.identity.models import BotUser
from apps.identity.services.staff_invites import issue_staff_invite
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import StaffInvite, Tenant, TenantStaff

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "700700"
CHAT_ID = "555"

SALON_BOT = BotEntry(
    slug="salon",
    webhook_secret="wh-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="formula-tela",
    stream="max_salon",
)


@pytest.fixture
def tenant() -> Tenant:
    obj, _ = Tenant.all_objects.get_or_create(
        slug="formula-tela", defaults={"name": "Формула тела"}
    )
    return obj


@pytest.fixture(autouse=True)
def _registry(settings):
    settings.MAX_BOT_REGISTRY = (SALON_BOT,)
    settings.MAX_BOT_TOKEN = "token-client"  # pragma: allowlist secret


@pytest.fixture
def sent():
    with patch("apps.channels.max.outbound.send_message") as mock:
        yield mock


def _payload(text: str, *, update_id: int = 1) -> dict:
    return {
        "update_type": "message_created",
        "update_id": update_id,
        "timestamp": 1_700_000_000_000,
        "message": {
            "sender": {"user_id": int(CHANNEL_USER_ID), "name": "Владелец", "is_bot": False},
            "recipient": {"chat_id": int(CHAT_ID), "user_id": 999, "chat_type": "dialog"},
            "body": {"mid": f"mid-{update_id}", "seq": 1, "text": text, "attachments": []},
        },
    }


def _handle(text: str, tenant: Tenant, *, update_id: int = 1) -> None:
    with tenant_scope(tenant):
        handle_salon_max_event(_payload(text, update_id=update_id))


class TestExtractCode:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("AYLA-7K3M", "AYLA-7K3M"),
            ("7K3M", "7K3M"),
            ("  ayla-7k3m  ", "ayla-7k3m"),
            ("/start inv_AYLA7K3M", "AYLA7K3M"),
        ],
    )
    def test_recognises_both_shapes(self, text, expected):
        assert _extract_code(text) == expected

    @pytest.mark.parametrize(
        "text",
        ["привет", "", "/start", "/start ref_user_12345", "хочу записаться", "меню"],
    )
    def test_ordinary_chat_is_not_a_code(self, text):
        # Must be None, or «привет» costs the person a rate-limit attempt.
        assert _extract_code(text) is None


class TestOnboarding:
    def test_owner_types_the_code_and_becomes_staff(self, tenant, sent):
        """The epic's acceptance criterion, end to end."""

        _, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)

        _handle(code, tenant)

        person = BotUser.all_tenants.get(channel_user_id=CHANNEL_USER_ID)
        assert TenantStaff.all_tenants.filter(
            tenant=tenant, bot_user=person, role="admin", deactivated_at__isnull=True
        ).exists()
        assert "администратор" in sent.call_args.kwargs["text"].lower()

    def test_the_link_path_needs_no_typing(self, tenant, sent):
        _, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        normalized = code.replace("-", "")

        _handle(f"/start inv_{normalized}", tenant)

        person = BotUser.all_tenants.get(channel_user_id=CHANNEL_USER_ID)
        assert TenantStaff.all_tenants.filter(bot_user=person, role="admin").exists()

    def test_a_stranger_is_asked_for_a_code(self, tenant, sent):
        _handle("привет", tenant)

        assert "код приглашения" in sent.call_args.kwargs["text"]
        assert not TenantStaff.all_tenants.exists()

    def test_a_bad_code_is_refused_without_detail(self, tenant, sent):
        _handle("AYLA-2222", tenant)

        text = sent.call_args.kwargs["text"]
        assert "не подошёл" in text
        # Must not disclose whether it was unknown, used or expired.
        assert "истёк" in text or "использован" in text
        assert not TenantStaff.all_tenants.exists()

    def test_existing_staff_get_the_menu_not_the_code_prompt(self, tenant, sent):
        _, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)
        _handle(code, tenant, update_id=1)
        sent.reset_mock()

        _handle("привет", tenant, update_id=2)

        text = sent.call_args.kwargs["text"]
        assert "код приглашения" not in text
        assert "Формула тела" in text


class TestSenderIdentity:
    def test_replies_go_out_as_the_salon_bot(self, tenant):
        """A wrong sender is invisible in logs and obvious to the recipient."""

        seen: list[str] = []

        def _capture(**kwargs):
            from apps.channels.max.outbound import _token

            seen.append(_token())
            return {}

        with patch("apps.channels.max.outbound.send_message", side_effect=_capture):
            _handle("привет", tenant)

        assert seen == ["token-salon"], "must not answer as the client bot"


class TestIdempotency:
    def test_a_redelivered_update_is_not_processed_twice(self, tenant, sent):
        _, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)

        _handle(code, tenant, update_id=7)
        _handle(code, tenant, update_id=7)

        # Second delivery short-circuits: one reply, one staff row.
        assert sent.call_count == 1
        assert TenantStaff.all_tenants.count() == 1


class TestDefensive:
    def test_unsupported_update_type_is_skipped_quietly(self, tenant, sent):
        with tenant_scope(tenant):
            handle_salon_max_event({"update_type": "chat_title_changed", "update_id": 3})

        # Tolerate-and-skip: a lifecycle update must not retry-storm the PEL.
        sent.assert_not_called()

    def test_without_tenant_scope_it_refuses_to_guess(self, sent):
        # Attaching a person to the wrong salon is worse than not answering.
        handle_salon_max_event(_payload("привет"))

        sent.assert_not_called()
        assert not BotUser.all_tenants.filter(channel_user_id=CHANNEL_USER_ID).exists()
