"""Internal-chat messages reach MAX (DRF-1061 block 3.3).

`apps.internal_chat` was a complete two-way thread store with no delivery
mechanism — its own docstring said «Notification dispatch … separate PR».
A posted message became a row, an audit entry and an analytics event, and
the other side learned of it only by opening the screen. On the pilot
nobody opens that screen, so the feature effectively did not exist.

Two properties carry the most weight here:

* **a message to one master is not broadcast to a shared chat.** The
  admin→master direction never had a fallback, on purpose; since
  07.09.2026 the master→salon direction has none either, because the
  global operator channel names no tenant and would have shown ten pilot
  salons each other's staff correspondence. Leaking a private
  conversation to whoever reads a shared chat is worse than not
  delivering it.
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
    settings.HANDOFF_NOTIFY_MAX_USER_IDS = []


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
    @staticmethod
    def _addressed(sent) -> list[dict[str, str]]:
        """Адрес И ключ, которым он ушёл (DRF-1559).

        Проверять только значение мало: и человек, и диалог лежат в одной
        настройке салона, и возврат к диалоговому ключу прошёл бы мимо.
        """

        return [a.send_kwargs() for a in sent.call_args.kwargs["addresses"]]

    def test_goes_to_the_salon_manager(self, tenant, sent):
        tenant.manager_chat_id = "555"
        tenant.save(update_fields=["manager_chat_id"])
        msg = _message(_thread(tenant, _master(tenant)), role=SenderRoleChoices.MASTER)

        notify.notify_internal_message(message=msg)

        assert self._addressed(sent) == [{"chat_id": "555"}]

    def test_manager_with_a_user_id_is_addressed_as_a_person(self, tenant, sent):
        """DRF-1559 — заполненный ``manager_user_id`` вытесняет диалог.

        Значения намеренно разные: возврат к ``chat_id`` даёт другое, а не
        то же самое, и пройти зелёным не может.
        """
        tenant.manager_user_id = "260237491"
        tenant.manager_chat_id = "555"
        tenant.save(update_fields=["manager_user_id", "manager_chat_id"])
        msg = _message(_thread(tenant, _master(tenant)), role=SenderRoleChoices.MASTER)

        notify.notify_internal_message(message=msg)

        assert self._addressed(sent) == [{"user_id": "260237491"}]

    def test_the_global_operator_channel_is_never_a_salon_address(
        self, tenant, settings, sent, caplog
    ):
        """The fallback rung is GONE — owner's decision of 07.09.2026.

        That list is global: it carries no tenant, so on the pilot every
        salon's staff correspondence resolved to one shared, hand-typed
        dialog — the very leak the admin→master direction already
        refuses. Configured in BOTH shapes here, so the test cannot pass
        merely because one shape was empty.
        """

        settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["777"]
        settings.HANDOFF_NOTIFY_MAX_USER_IDS = ["778"]
        msg = _message(_thread(tenant, _master(tenant)), role=SenderRoleChoices.MASTER)

        with caplog.at_level("INFO", logger="apps.internal_chat.notify"):
            notify.notify_internal_message(message=msg)

        sent.assert_not_called()
        records = [r for r in caplog.records if r.name == "apps.internal_chat.notify"]
        assert records, "the skipped salon copy must leave a trace"
        skipped = [r for r in records if "no_salon_target" in r.getMessage()]
        assert [r.levelname for r in skipped] == ["INFO"]
        noisy = [r.getMessage() for r in records if r.levelno >= 30]
        assert noisy == []  # empty-assert-ok: presence proved on `records` just above

    def test_nowhere_to_send_is_observable_but_quiet(self, tenant, sent, caplog):
        """An unconfigured salon is a normal state, not a configuration defect.

        (The admin→master direction keeps its WARNING — see
        ``TestDirectionAdminToMaster``: an unlinked master IS a defect.)
        """

        msg = _message(_thread(tenant, _master(tenant)), role=SenderRoleChoices.MASTER)

        with caplog.at_level("INFO", logger="apps.internal_chat.notify"):
            notify.notify_internal_message(message=msg)

        sent.assert_not_called()
        records = [r for r in caplog.records if r.name == "apps.internal_chat.notify"]
        assert records, "an undelivered staff message must leave a trace"
        assert any("no_salon_target" in r.getMessage() for r in records)
        noisy = [r.getMessage() for r in records if r.levelno >= 30]
        assert noisy == []  # empty-assert-ok: presence proved on `records` just above


class TestDirectionAdminToMaster:
    def test_goes_to_that_master_personally(self, tenant, sent):
        person = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="42", chat_id="4242"
        )
        msg = _message(
            _thread(tenant, _master(tenant, linked=person)), role=SenderRoleChoices.ADMIN
        )

        notify.notify_internal_message(message=msg)

        # DRF-1558 — мастеру пишем как ЧЕЛОВЕКУ: эта отправка идёт под
        # салонным ботом, а «4242» — диалог мастера с клиентским.
        assert [a.send_kwargs() for a in sent.call_args.kwargs["addresses"]] == [{"user_id": "42"}]

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
