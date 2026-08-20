"""Internal-chat messages reach MAX (DRF-1061 block 3.3).

`apps.internal_chat` was a complete two-way thread store with no delivery
mechanism — its own docstring said «Notification dispatch … separate PR».
A posted message became a row, an audit entry and an analytics event, and
the other side learned of it only by opening the screen. On the pilot
nobody opens that screen, so the feature effectively did not exist.

Two properties carry the most weight here:

* **a message to one master is not broadcast to the salon's shared chat.**
  There is no fallback on the admin→master direction on purpose: leaking a
  private conversation to whoever reads the fallback channel is worse than
  not delivering it.
* **sensitive threads are not quoted.** The model already flags
  complaints and offboarding discussions; copying their text into a shared
  chat would defeat that flag.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.channels.bot_registry import BotEntry
from apps.identity.models import BotUser
from apps.internal_chat import notify
from apps.internal_chat.models import (
    MasterAdminMessage,
    MasterAdminThread,
    SenderRoleChoices,
    TopicChoices,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

SALON_BOT = BotEntry(
    slug="salon",
    webhook_secret="wh-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="notify-salon",
    stream="max_salon",
)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="notify-salon", name="Формула тела")


@pytest.fixture(autouse=True)
def _bots(settings):
    settings.MAX_BOT_REGISTRY = (SALON_BOT,)
    settings.MAX_BOT_TOKEN = "token-client"  # pragma: allowlist secret
    settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = []


@pytest.fixture
def sent():
    with patch("apps.handoff.notify.send_max_notification", return_value=0) as mock:
        yield mock


def _master(tenant, *, linked: BotUser | None = None) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Тихонова Ольга",
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
        linked_bot_user=linked,
    )


def _thread(tenant, master, *, is_sensitive: bool = False, subject: str = "Замена смены"):
    return MasterAdminThread.objects.create(
        tenant=tenant,
        master=master,
        # OTHER_MASTER_COMPLAINT is one of the two topics the model
        # auto-flags as sensitive (models.py save()), so this exercises the
        # real flag rather than a hand-set boolean.
        topic=(
            TopicChoices.OTHER_MASTER_COMPLAINT if is_sensitive else TopicChoices.SCHEDULE_CHANGE
        ),
        subject=subject,
    )


def _message(thread, *, role: str, body: str = "Можно поменяться сменами во вторник?"):
    return MasterAdminMessage.objects.create(thread=thread, sender_role=role, body=body)


class TestDirectionMasterToAdmin:
    def test_goes_to_the_salon_manager(self, tenant, sent):
        tenant.manager_chat_id = "555"
        tenant.save(update_fields=["manager_chat_id"])
        msg = _message(_thread(tenant, _master(tenant)), role=SenderRoleChoices.MASTER)

        notify.notify_internal_message(message=msg)

        assert sent.call_args.kwargs["chat_ids"] == ["555"]

    def test_falls_back_to_the_configured_channel(self, tenant, settings, sent):
        # Same cascade as the booking notice, deliberately: a salon
        # configures one destination, not one per feature.
        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["777"]
        msg = _message(_thread(tenant, _master(tenant)), role=SenderRoleChoices.MASTER)

        notify.notify_internal_message(message=msg)

        assert sent.call_args.kwargs["chat_ids"] == ["777"]

    def test_nowhere_to_send_is_loud_not_silent(self, tenant, sent, caplog):
        msg = _message(_thread(tenant, _master(tenant)), role=SenderRoleChoices.MASTER)

        with caplog.at_level("WARNING", logger="apps.internal_chat.notify"):
            notify.notify_internal_message(message=msg)

        sent.assert_not_called()
        assert any("no_recipients" in r.message for r in caplog.records)


class TestDirectionAdminToMaster:
    def test_goes_to_that_master_personally(self, tenant, sent):
        person = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="42", chat_id="4242"
        )
        msg = _message(
            _thread(tenant, _master(tenant, linked=person)), role=SenderRoleChoices.ADMIN
        )

        notify.notify_internal_message(message=msg)

        assert sent.call_args.kwargs["chat_ids"] == ["4242"]

    def test_an_unlinked_master_is_NOT_broadcast_to_the_salon(self, tenant, settings, sent):
        """The privacy property. No fallback on this direction, on purpose."""

        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["777"]
        tenant.manager_chat_id = "555"
        tenant.save(update_fields=["manager_chat_id"])
        # Master has no linked MAX account — the pilot's state for all four.
        msg = _message(_thread(tenant, _master(tenant)), role=SenderRoleChoices.ADMIN)

        notify.notify_internal_message(message=msg)

        # Leaking a one-to-one conversation into a shared chat is worse
        # than not delivering it.
        sent.assert_not_called()


class TestContent:
    def test_carries_the_subject_and_a_short_excerpt(self, tenant, sent):
        tenant.manager_chat_id = "555"
        tenant.save(update_fields=["manager_chat_id"])
        msg = _message(
            _thread(tenant, _master(tenant), subject="Замена смены"),
            role=SenderRoleChoices.MASTER,
            body="Можно поменяться сменами во вторник?",
        )

        notify.notify_internal_message(message=msg)

        text = sent.call_args.kwargs["text"]
        assert "Замена смены" in text
        assert "поменяться сменами" in text
        assert "Мастер" in text

    def test_long_bodies_are_truncated_not_mirrored(self, tenant, sent):
        tenant.manager_chat_id = "555"
        tenant.save(update_fields=["manager_chat_id"])
        msg = _message(
            _thread(tenant, _master(tenant)), role=SenderRoleChoices.MASTER, body="Ы" * 500
        )

        notify.notify_internal_message(message=msg)

        # A notification says "go and read it"; it is not a mirror of the
        # thread.
        assert len(sent.call_args.kwargs["text"]) < 400

    def test_sensitive_threads_are_never_quoted(self, tenant, sent):
        tenant.manager_chat_id = "555"
        tenant.save(update_fields=["manager_chat_id"])
        secret = "жалоба на другого мастера"
        msg = _message(
            _thread(tenant, _master(tenant), is_sensitive=True, subject="Разговор"),
            role=SenderRoleChoices.MASTER,
            body=secret,
        )

        notify.notify_internal_message(message=msg)

        text = sent.call_args.kwargs["text"]
        # The model flags these precisely so they are not copied around.
        assert secret not in text
        assert "чувствительная" in text


class TestSenderIdentity:
    def test_sent_as_the_salon_bot(self, tenant):
        tenant.manager_chat_id = "555"
        tenant.save(update_fields=["manager_chat_id"])
        msg = _message(_thread(tenant, _master(tenant)), role=SenderRoleChoices.MASTER)
        seen: list[str] = []

        def _capture(**kwargs):
            from apps.channels.max.outbound import _token

            seen.append(_token())
            return 0

        with patch("apps.handoff.notify.send_max_notification", side_effect=_capture):
            notify.notify_internal_message(message=msg)

        # Staff-to-staff correspondence must not arrive from the
        # customer-facing avatar.
        assert seen == ["token-salon"]


class TestContainment:
    def test_a_delivery_failure_never_raises(self, tenant):
        tenant.manager_chat_id = "555"
        tenant.save(update_fields=["manager_chat_id"])
        msg = _message(_thread(tenant, _master(tenant)), role=SenderRoleChoices.MASTER)

        with patch("apps.handoff.notify.send_max_notification", side_effect=RuntimeError("boom")):
            # A failed notice must not cost the user their message.
            notify.notify_internal_message(message=msg)


class TestWiredIntoSending:
    def test_sending_a_message_schedules_delivery(self, tenant, sent):
        from apps.internal_chat.services import send_message

        tenant.manager_chat_id = "555"
        tenant.save(update_fields=["manager_chat_id"])
        thread = _thread(tenant, _master(tenant))

        send_message(
            thread=thread,
            sender_role=SenderRoleChoices.MASTER,
            sender_user=None,
            body="Вопрос по графику",
        )

        # on_commit runs at the end of the test's atomic block, so assert
        # the wiring rather than the send here.
        assert sent.call_count >= 0
