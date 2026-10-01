"""Payload contract of the ``internal_chat.*`` events — executable form (DRF-2683).

The readable form is the «Payload contracts» block above the
``INTERNAL_CHAT_*`` constants in :mod:`apps.events.vocabulary`. That
block once cited a document section that never existed and drifted six
fields away from what :mod:`apps.internal_chat.services` really sends.
A comment cannot go red; this file can.

Every expected key set and type below is written as a LITERAL on
purpose. A table built from the emitters or from a shared constant
would move together with the code and pass after any change to it.
Adding, removing or renaming a payload key therefore fails here until
the contract — the comment and these literals — is changed deliberately.

What is pinned, per event:

* the exact key set — no missing key, no extra key;
* the type of every value as it travels (after a JSON round trip, so a
  ``TextChoices`` member is seen as the plain ``str`` it becomes);
* the analytics-bus payload and the audit-log payload being the same dict.

What is NOT pinned: the envelope ``emit`` adds on its own (tenant,
``trace_id``, ``distinct_id``) and the value vocabularies beyond the
cases exercised here.
"""

from __future__ import annotations

import copy
import json
import uuid

import pytest

from apps.events import vocabulary
from apps.internal_chat import services
from apps.internal_chat.models import SenderRoleChoices, StatusChoices, TopicChoices


pytestmark = pytest.mark.django_db

NoneType = type(None)

THREAD_CREATED = "internal_chat.thread_created"
MESSAGE_SENT = "internal_chat.message_sent"
THREAD_STATUS_CHANGED = "internal_chat.thread_status_changed"
THREAD_ASSIGNED = "internal_chat.thread_assigned"
ESCALATED_TO_FOUNDER = "internal_chat.escalated_to_founder"
MARKED_READ = "internal_chat.marked_read"
# Audit log only — never on the analytics bus, not in CANONICAL_EVENTS.
THREAD_FIELDS_PATCHED = "internal_chat.thread_fields_patched"


class _Recorder:
    """What the service handed to the bus and to the audit log, in order."""

    def __init__(self) -> None:
        self.bus: list[tuple[str, dict]] = []
        self.audit: list[tuple[str, dict]] = []

    def bus_payloads(self, name: str) -> list[dict]:
        return [payload for event, payload in self.bus if event == name]

    def audit_payloads(self, name: str) -> list[dict]:
        return [payload for action, payload in self.audit if action == name]

    def one(self, name: str) -> dict:
        """The single bus payload of ``name``, as it travels (JSON round trip)."""

        payloads = self.bus_payloads(name)
        assert len(payloads) == 1, f"expected one {name}, got {len(payloads)}"
        return json.loads(json.dumps(payloads[0]))


@pytest.fixture
def recorded(monkeypatch) -> _Recorder:
    """Record ``emit`` / ``write_audit`` calls made by the service layer.

    The real functions still run — the recorder only looks.
    """

    recorder = _Recorder()
    real_emit = services.emit
    real_write_audit = services.write_audit

    def emit(event_name, *args, **kwargs):
        recorder.bus.append((event_name, copy.deepcopy(kwargs["properties"])))
        return real_emit(event_name, *args, **kwargs)

    def write_audit(action, *args, **kwargs):
        recorder.audit.append((action, copy.deepcopy(kwargs["payload"])))
        return real_write_audit(action, *args, **kwargs)

    monkeypatch.setattr(services, "emit", emit)
    monkeypatch.setattr(services, "write_audit", write_audit)
    return recorder


def _assert_shape(payload: dict, expected: dict[str, tuple[type, ...]]) -> None:
    assert set(payload) == set(expected)
    for key, allowed in expected.items():
        # ``type(...) in`` rather than isinstance: a bool must not pass as int.
        assert type(payload[key]) in allowed, (key, payload[key])


def _open_thread(tenant, master, actor):
    """A thread with no first message — the admin's «summon master» path."""

    return services.create_thread(
        tenant=tenant, master=master, topic=TopicChoices.GENERAL, actor=actor
    )


# --- the vocabulary and this file name the same six events ----------------


def test_contract_covers_every_internal_chat_slug():
    declared = {
        value for name, value in vars(vocabulary).items() if name.startswith("INTERNAL_CHAT_")
    }
    assert declared == {
        "internal_chat.thread_created",
        "internal_chat.message_sent",
        "internal_chat.thread_status_changed",
        "internal_chat.thread_assigned",
        "internal_chat.escalated_to_founder",
        "internal_chat.marked_read",
    }
    assert declared <= vocabulary.CANONICAL_EVENTS


# --- internal_chat.thread_created -----------------------------------------

_THREAD_CREATED_SHAPE = {
    "tenant_id": (str,),
    "thread_id": (str,),
    "master_id": (str,),
    "topic": (str,),
    "linked_artifact_type": (str,),
    "linked_artifact_id": (str, NoneType),
    "actor_id": (str,),
    "is_sensitive": (bool,),
    "first_message_id": (str, NoneType),
}


def test_thread_created_with_first_message_and_linked_artifact(
    recorded, tenant, master, master_bot_user
):
    artifact_id = uuid.uuid4()
    thread = services.create_thread(
        tenant=tenant,
        master=master,
        topic=TopicChoices.OFFBOARDING_DISCUSSION,
        actor=master_bot_user,
        first_message_body="Хочу обсудить уход",
        linked_artifact_type="booking",
        linked_artifact_id=artifact_id,
    )

    payload = recorded.one(THREAD_CREATED)
    _assert_shape(payload, _THREAD_CREATED_SHAPE)
    first_message = thread.messages.get()
    assert payload == {
        "tenant_id": str(tenant.id),
        "thread_id": str(thread.id),
        "master_id": str(master.id),
        "topic": "offboarding_discussion",
        "linked_artifact_type": "booking",
        "linked_artifact_id": str(artifact_id),
        "actor_id": str(master_bot_user.id),
        "is_sensitive": True,
        "first_message_id": str(first_message.id),
    }
    # The embedded first message is carried by ``first_message_id`` only:
    # no ``message_sent`` accompanies it — the bus saw this one event.
    assert [event for event, _ in recorded.bus] == ["internal_chat.thread_created"]


def test_thread_created_without_first_message_or_artifact(recorded, tenant, master, admin_bot_user):
    _open_thread(tenant, master, admin_bot_user)

    payload = recorded.one(THREAD_CREATED)
    _assert_shape(payload, _THREAD_CREATED_SHAPE)
    assert payload["first_message_id"] is None
    assert payload["linked_artifact_id"] is None
    assert payload["linked_artifact_type"] == ""
    assert payload["is_sensitive"] is False


# --- internal_chat.message_sent -------------------------------------------

_MESSAGE_SENT_SHAPE = {
    "tenant_id": (str,),
    "thread_id": (str,),
    "message_id": (str,),
    "sender_role": (str,),
    "sender_user_id": (str, NoneType),
    "has_attachments": (bool,),
}


def test_message_sent_by_a_user(recorded, tenant, thread, admin_bot_user):
    message = services.send_message(
        thread=thread,
        sender_role=SenderRoleChoices.ADMIN,
        sender_user=admin_bot_user,
        body="Посмотрим ставку",
    )

    payload = recorded.one(MESSAGE_SENT)
    _assert_shape(payload, _MESSAGE_SENT_SHAPE)
    assert payload == {
        "tenant_id": str(tenant.id),
        "thread_id": str(thread.id),
        "message_id": str(message.id),
        "sender_role": "admin",
        "sender_user_id": str(admin_bot_user.id),
        # Known: a literal False in the emitter, whatever the message carries.
        "has_attachments": False,
    }


def test_message_sent_by_the_system_has_no_sender_user(recorded, thread):
    services.send_message(
        thread=thread,
        sender_role=SenderRoleChoices.SYSTEM,
        sender_user=None,
        body="Тред закрыт автоматически",
    )

    payload = recorded.one(MESSAGE_SENT)
    _assert_shape(payload, _MESSAGE_SENT_SHAPE)
    assert payload["sender_role"] == "system"
    assert payload["sender_user_id"] is None


# --- internal_chat.thread_status_changed ----------------------------------

_STATUS_CHANGED_SHAPE = {
    "tenant_id": (str,),
    "thread_id": (str,),
    "from_status": (str,),
    "to_status": (str,),
    "actor_id": (str,),
}


def test_status_changed_on_close(recorded, tenant, thread, admin_bot_user):
    services.close_thread(thread=thread, actor=admin_bot_user)

    payload = recorded.one(THREAD_STATUS_CHANGED)
    _assert_shape(payload, _STATUS_CHANGED_SHAPE)
    assert payload == {
        "tenant_id": str(tenant.id),
        "thread_id": str(thread.id),
        "from_status": "master_responded",
        "to_status": "resolved",
        "actor_id": str(admin_bot_user.id),
    }


def test_status_changed_on_patch_of_status_alone(recorded, thread, admin_bot_user):
    services.patch_thread_fields(
        thread=thread, actor=admin_bot_user, status=StatusChoices.ACTIVE_DISCUSSION
    )

    payload = recorded.one(THREAD_STATUS_CHANGED)
    _assert_shape(payload, _STATUS_CHANGED_SHAPE)
    assert payload["from_status"] == "master_responded"
    assert payload["to_status"] == "active_discussion"


def test_status_changed_on_patch_with_topic_carries_the_topic_pair(
    recorded, tenant, thread, admin_bot_user
):
    services.patch_thread_fields(
        thread=thread,
        actor=admin_bot_user,
        topic=TopicChoices.GENERAL,
        status=StatusChoices.ACTIVE_DISCUSSION,
    )

    payload = recorded.one(THREAD_STATUS_CHANGED)
    _assert_shape(
        payload,
        {
            "tenant_id": (str,),
            "thread_id": (str,),
            "from_status": (str,),
            "to_status": (str,),
            "actor_id": (str,),
            "from_topic": (str,),
            "to_topic": (str,),
        },
    )
    assert payload == {
        "tenant_id": str(tenant.id),
        "thread_id": str(thread.id),
        "from_status": "master_responded",
        "to_status": "active_discussion",
        "actor_id": str(admin_bot_user.id),
        "from_topic": "earnings_dispute",
        "to_topic": "general",
    }


# --- internal_chat.thread_assigned ----------------------------------------

_THREAD_ASSIGNED_SHAPE = {
    "tenant_id": (str,),
    "thread_id": (str,),
    "assigned_admin_id": (str, NoneType),
    "previous_admin_id": (str, NoneType),
    "actor_id": (str,),
}


def test_thread_assigned_first_assignment(recorded, tenant, thread, admin_bot_user, owner_bot_user):
    services.assign_admin(thread=thread, assigned_admin=admin_bot_user, actor=owner_bot_user)

    payload = recorded.one(THREAD_ASSIGNED)
    _assert_shape(payload, _THREAD_ASSIGNED_SHAPE)
    assert payload == {
        "tenant_id": str(tenant.id),
        "thread_id": str(thread.id),
        "assigned_admin_id": str(admin_bot_user.id),
        "previous_admin_id": None,
        "actor_id": str(owner_bot_user.id),
    }


def test_thread_assigned_reassignment_names_the_previous_admin(
    recorded, thread, admin_bot_user, owner_bot_user
):
    services.assign_admin(thread=thread, assigned_admin=admin_bot_user, actor=owner_bot_user)
    services.assign_admin(thread=thread, assigned_admin=owner_bot_user, actor=owner_bot_user)

    payload = json.loads(json.dumps(recorded.bus_payloads(THREAD_ASSIGNED)[1]))
    _assert_shape(payload, _THREAD_ASSIGNED_SHAPE)
    assert payload["assigned_admin_id"] == str(owner_bot_user.id)
    assert payload["previous_admin_id"] == str(admin_bot_user.id)


def test_thread_assigned_unassignment(recorded, thread, admin_bot_user, owner_bot_user):
    services.assign_admin(thread=thread, assigned_admin=admin_bot_user, actor=owner_bot_user)
    services.assign_admin(thread=thread, assigned_admin=None, actor=owner_bot_user)

    payload = json.loads(json.dumps(recorded.bus_payloads(THREAD_ASSIGNED)[1]))
    _assert_shape(payload, _THREAD_ASSIGNED_SHAPE)
    assert payload["assigned_admin_id"] is None
    assert payload["previous_admin_id"] == str(admin_bot_user.id)


# --- internal_chat.escalated_to_founder -----------------------------------


@pytest.mark.parametrize(
    ("reason", "reason_class"),
    [("Админ не отвечает неделю", "provided"), ("", "empty"), ("   ", "empty")],
)
def test_escalated_to_founder(
    recorded, tenant, master, thread, master_bot_user, reason, reason_class
):
    services.escalate_to_founder(thread=thread, actor=master_bot_user, reason=reason)

    payload = recorded.one(ESCALATED_TO_FOUNDER)
    _assert_shape(
        payload,
        {
            "tenant_id": (str,),
            "thread_id": (str,),
            "master_id": (str,),
            "from_status": (str,),
            "reason_class": (str,),
            "actor_id": (str,),
        },
    )
    # The class, never the master's text.
    assert payload == {
        "tenant_id": str(tenant.id),
        "thread_id": str(thread.id),
        "master_id": str(master.id),
        "from_status": "master_responded",
        "reason_class": reason_class,
        "actor_id": str(master_bot_user.id),
    }


# --- internal_chat.marked_read --------------------------------------------


def test_marked_read_once_per_catch_up(recorded, tenant, thread, admin_bot_user):
    services.send_message(
        thread=thread,
        sender_role=SenderRoleChoices.MASTER,
        sender_user=thread.master.linked_bot_user,
        body="И ещё вопрос",
    )

    count = services.mark_read(thread=thread, reader_role="admin", reader_user=admin_bot_user)

    assert count == 2
    payload = recorded.one(MARKED_READ)
    _assert_shape(
        payload,
        {
            "tenant_id": (str,),
            "thread_id": (str,),
            "reader_role": (str,),
            "reader_user_id": (str,),
            "count": (int,),
        },
    )
    assert payload == {
        "tenant_id": str(tenant.id),
        "thread_id": str(thread.id),
        "reader_role": "admin",
        "reader_user_id": str(admin_bot_user.id),
        "count": 2,
    }

    # Nothing left to read → no second event: ``count`` is never 0 on the bus.
    assert services.mark_read(thread=thread, reader_role="admin", reader_user=admin_bot_user) == 0
    assert len(recorded.bus_payloads(MARKED_READ)) == 1


# --- internal_chat.thread_fields_patched — audit log only ------------------


def test_fields_patched_goes_to_the_audit_log_and_not_to_the_bus(
    recorded, tenant, thread, admin_bot_user
):
    services.patch_thread_fields(thread=thread, actor=admin_bot_user, subject="Новая тема")
    services.patch_thread_fields(thread=thread, actor=admin_bot_user, topic=TopicChoices.GENERAL)

    quiet, retagged = (
        json.loads(json.dumps(payload))
        for payload in recorded.audit_payloads(THREAD_FIELDS_PATCHED)
    )
    assert quiet == {
        "tenant_id": str(tenant.id),
        "thread_id": str(thread.id),
        "actor_id": str(admin_bot_user.id),
        "fields_changed": ["subject"],
    }
    assert retagged == {
        "tenant_id": str(tenant.id),
        "thread_id": str(thread.id),
        "actor_id": str(admin_bot_user.id),
        "fields_changed": ["topic"],
        "from_topic": "earnings_dispute",
        "to_topic": "general",
    }
    # The fixture's own ``thread_created`` proves the bus recorder was live.
    assert recorded.bus_payloads(THREAD_CREATED) != []
    assert recorded.bus_payloads(THREAD_FIELDS_PATCHED) == []
    assert THREAD_FIELDS_PATCHED not in vocabulary.CANONICAL_EVENTS


# --- bus payload == audit payload, for all six ----------------------------


def test_bus_and_audit_log_carry_the_same_payload(
    recorded, tenant, master, master_bot_user, admin_bot_user
):
    thread = services.create_thread(
        tenant=tenant,
        master=master,
        topic=TopicChoices.EARNINGS_DISPUTE,
        actor=master_bot_user,
        first_message_body="Ставка не та",
    )
    services.send_message(
        thread=thread,
        sender_role=SenderRoleChoices.ADMIN,
        sender_user=admin_bot_user,
        body="Смотрю",
    )
    services.mark_read(thread=thread, reader_role="master", reader_user=master_bot_user)
    services.assign_admin(thread=thread, assigned_admin=admin_bot_user, actor=admin_bot_user)
    services.escalate_to_founder(thread=thread, actor=master_bot_user, reason="Не согласна")
    services.close_thread(thread=thread, actor=admin_bot_user)

    assert [event for event, _ in recorded.bus] == [
        THREAD_CREATED,
        MESSAGE_SENT,
        MARKED_READ,
        THREAD_ASSIGNED,
        ESCALATED_TO_FOUNDER,
        THREAD_STATUS_CHANGED,
    ]
    assert recorded.audit == recorded.bus
