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
        # Must not disclose WHICH failure it was. The copy deliberately
        # hedges ("возможно, использован или истёк") so unknown, used and
        # expired are indistinguishable; asserting the hedge is present
        # would pass no matter what, so assert the reply is byte-identical
        # to the single shared constant instead.
        from apps.channels.max.salon_handler import CODE_NOT_ACCEPTED

        assert text == CODE_NOT_ACCEPTED
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


class TestWrongBotGuard:
    """Never answer as the client bot (DRF-1061).

    `bot_scope(None)` is not neutral — outbound falls back to
    settings.MAX_BOT_TOKEN, i.e. the CLIENT bot. If the registry has no
    entry for this tenant, replying would send a staff message from the
    customer-facing avatar: invisible in logs, alarming to the recipient.
    """

    def test_no_registry_entry_means_silence_not_a_wrong_sender(self, tenant, settings, sent):
        # Registry declares a bot for a DIFFERENT salon.
        settings.MAX_BOT_REGISTRY = (
            BotEntry(
                slug="other",
                webhook_secret="wh-other",  # pragma: allowlist secret
                api_token="token-other",  # pragma: allowlist secret
                tenant_slug="some-other-salon",
                stream="max_salon",
            ),
        )

        _handle("привет", tenant)

        sent.assert_not_called()


class TestTheThread:
    """The salon bot now keeps a record of what staff typed (step 0).

    Not a dialogue yet — the reply is still the menu. What matters is that
    the exchange is written down, because the assistant in step 1 has
    nothing to stand on otherwise.
    """

    def _make_admin(self, tenant) -> BotUser:
        person = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id=CHANNEL_USER_ID,
            chat_id=CHAT_ID,
        )
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=person, role="admin")
        return person

    def test_a_typed_line_and_the_reply_are_both_recorded(self, tenant, sent):
        from apps.conversations.models import StaffAssistantMessage

        self._make_admin(tenant)

        _handle("что у меня сегодня", tenant)

        rows = list(StaffAssistantMessage.all_tenants.order_by("seq"))
        assert [r.role for r in rows] == ["user", "assistant"]
        assert rows[0].content == "что у меня сегодня"
        # The assistant turn is what the person actually saw.
        assert rows[1].content == sent.call_args.kwargs["text"]

    def test_the_thread_remembers_the_role_they_held(self, tenant, sent):
        from apps.conversations.models import StaffAssistantThread

        self._make_admin(tenant)

        _handle("привет", tenant)

        assert StaffAssistantThread.all_tenants.get().role_at_open == "admin"

    def test_one_thread_across_several_messages(self, tenant, sent):
        from apps.conversations.models import StaffAssistantMessage, StaffAssistantThread

        self._make_admin(tenant)

        _handle("первое", tenant, update_id=1)
        _handle("второе", tenant, update_id=2)

        assert StaffAssistantThread.all_tenants.count() == 1
        assert StaffAssistantMessage.all_tenants.count() == 4

    def test_a_button_tap_is_not_recorded(self, tenant, sent):
        """Taps are not speech.

        The customer path already paid for the opposite: raw `cb:*`
        payloads sitting in history provoked hallucinated replies (DRF-988).
        """

        from apps.conversations.models import StaffAssistantMessage

        self._make_admin(tenant)

        with tenant_scope(tenant):
            handle_salon_max_event(
                {
                    "update_type": "message_callback",
                    "timestamp": 1_700_000_000_000,
                    "callback": {
                        "callback_id": "cb-1",
                        "payload": "cb:staff:day",
                        "timestamp": 1_700_000_000_000,
                        "user": {"user_id": int(CHANNEL_USER_ID), "name": "Владелец"},
                    },
                    "message": {
                        "body": {"mid": "m1", "seq": 1, "text": ""},
                        "sender": {"user_id": 999, "name": "bot", "is_bot": True},
                        "recipient": {
                            "chat_id": int(CHAT_ID),
                            "user_id": 999,
                            "chat_type": "dialog",
                        },
                    },
                }
            )

        assert sent.called
        assert not StaffAssistantMessage.all_tenants.exists()

    def test_someone_without_a_role_gets_no_thread(self, tenant, sent):
        # Typing an invite code is not a conversation.
        from apps.conversations.models import StaffAssistantThread

        _handle("привет", tenant)

        assert not StaffAssistantThread.all_tenants.exists()

    def test_the_customer_conversation_is_left_alone(self, tenant, sent):
        from apps.conversations.models import Conversation, Message

        self._make_admin(tenant)

        _handle("что у меня сегодня", tenant)

        assert Conversation.all_tenants.count() == 0
        assert Message.all_tenants.count() == 0

    def test_a_failing_thread_write_never_costs_the_reply(self, tenant, sent):
        """History is a place to write, not a precondition for answering.

        A staff member mid-shift must get their menu even if the write
        fails.
        """

        self._make_admin(tenant)

        with patch(
            "apps.conversations.staff_assistant.resolve_active_staff_thread",
            side_effect=RuntimeError("db is having a moment"),
        ):
            _handle("что у меня сегодня", tenant)

        assert sent.called
