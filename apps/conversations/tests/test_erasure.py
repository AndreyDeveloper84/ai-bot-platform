"""Anonymisation mechanics — the cutoff, the idempotence, the named term.

DRF-1369. The «does it reach a prompt» half lives in
``test_dialogue_reader_registry.py``; this file pins the behaviour that makes
the anonymiser safe to run on a schedule against a person who is still using
the bot.

The trap these cells exist for: «забудь всё» is not the end of the dialogue.
``forget_all_sweep`` re-runs hourly by design — the write path does not honour
the forget-all gate, so a one-shot sweep would leave live rows behind. An
anonymiser with a «now» cutoff, or one that stamps a cutoff on threads opened
after the request, would turn that re-run into an hourly wipe of the person's
current conversation. Both directions are pinned below.
"""

from __future__ import annotations

import random
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.conversations.erasure import (
    ANONYMIZED_DIALOGUE_RETENTION_DAYS,
    anonymize_dialogue,
    shell_ids_for_person,
)
from apps.conversations.models import AiDraft, ArchivedMessage, Conversation, Message
from apps.conversations.tasks import purge_expired_archived_messages
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)

FORGET_ALL = ArchivedMessage.Reason.FORGET_ALL


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict = {}
        self.deleted: list[str] = []

    def pipeline(self):
        outer = self

        class _Pipe:
            def __init__(self) -> None:
                self.ops: list = []

            def rpush(self, key, value):
                self.ops.append(("rpush", key, value))

            def ltrim(self, key, start, end):
                self.ops.append(("ltrim", key, start, end))

            def expire(self, key, ttl):
                self.ops.append(("expire", key, ttl))

            def execute(self):
                for op in self.ops:
                    if op[0] == "rpush":
                        outer.store.setdefault(op[1], []).append(op[2])
                self.ops = []

        return _Pipe()

    def lrange(self, key, start, end):
        items = self.store.get(key, [])
        return items[start:] if end == -1 else items[start:end]

    def delete(self, key):
        self.deleted.append(key)
        self.store.pop(key, None)


@pytest.fixture()
def fake_redis(monkeypatch) -> _FakeRedis:
    from apps.llm import pii_tokenizer
    from apps.orchestrator.memory import short_term

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    monkeypatch.setattr(pii_tokenizer, "_redis_client", lambda: fake)
    return fake


@pytest.fixture()
def person(settings) -> BotUser:
    settings.STRICT_TENANT_SCOPE = "off"
    tenant = Tenant.objects.create(slug=f"er-{uuid.uuid4().hex[:8]}", name="Erasure")
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"er-{uuid.uuid4().hex[:8]}",
        ayla_user_id=uuid.uuid4(),
    )


def _conversation(bot_user: BotUser) -> Conversation:
    return Conversation.all_tenants.create(tenant=bot_user.tenant, bot_user=bot_user)


def _message(conversation: Conversation, text: str, *, role: str = "user", at=None) -> Message:
    """A turn. ``at`` pins ``created_at`` explicitly.

    ``created_at`` is ``auto_now_add``, and the Windows wall clock ticks about
    every 15 ms — two rows created back to back can share a timestamp, which
    would make the cutoff cells flap on the clock instead of on the behaviour.
    """

    row = Message.all_tenants.create(
        tenant=conversation.tenant,
        conversation=conversation,
        role=role,
        content=text,
        rendered_text=text,
    )
    if at is not None:
        Message.all_tenants.filter(pk=row.pk).update(created_at=at)
        row.refresh_from_db()
    return row


class TestTheCutoff:
    def test_turns_after_the_request_are_the_person_s_own_again(self, person, fake_redis):
        """The heart of «cutoff, not flag».

        Someone says «забудь всё» and keeps talking. Their next sentence is not
        covered by a request that predates it, and a bot that blanked it would
        be unable to hold a conversation with anyone who had ever used the verb.
        """
        conversation = _conversation(person)
        cutoff = timezone.now()
        old = _message(conversation, "старое: я веган", at=cutoff - timedelta(minutes=5))
        new = _message(
            conversation, "новое: запиши меня на маникюр", at=cutoff + timedelta(minutes=5)
        )

        anonymize_dialogue([person.id], through=cutoff, reason=FORGET_ALL)

        old.refresh_from_db()
        new.refresh_from_db()
        assert old.content == ""
        assert new.content == "новое: запиши меня на маникюр"

    def test_a_thread_opened_after_the_request_is_not_touched(self, person, fake_redis):
        """The hourly-sweep trap, pinned.

        ``forget_all_sweep`` re-runs against the same user forever. A thread
        STARTED after the request holds no pre-cutoff turn, so anonymising it
        would move a cutoff and wipe a live Redis window for nothing — every
        hour, for every new conversation the person opens.
        """
        # A second of slack: on Windows the wall clock granularity is ~15 ms,
        # and a cutoff taken in the same tick as the row's `created_at` would
        # make this cell flap on the boundary rather than on the behaviour.
        cutoff = timezone.now() - timedelta(seconds=1)
        later = _conversation(person)
        turn = _message(later, "здравствуйте")
        # Positive guard: a thread with nothing in it would satisfy every
        # assertion below for free, and would say nothing about the cutoff.
        assert turn.content == "здравствуйте"

        result = anonymize_dialogue([person.id], through=cutoff, reason=FORGET_ALL)

        later.refresh_from_db()
        turn.refresh_from_db()
        assert result.conversations == 0
        assert later.anonymized_through is None
        assert fake_redis.deleted == []
        assert turn.content == "здравствуйте"  # and the turn is still readable

    def test_the_cutoff_only_moves_forward(self, person, fake_redis):
        conversation = _conversation(person)
        _message(conversation, "первое")
        first = timezone.now()
        anonymize_dialogue([person.id], through=first, reason=FORGET_ALL)

        anonymize_dialogue([person.id], through=first - timedelta(hours=1), reason=FORGET_ALL)

        conversation.refresh_from_db()
        assert conversation.anonymized_through == first


class TestIdempotence:
    def test_a_second_run_moves_nothing(self, person, fake_redis):
        conversation = _conversation(person)
        _message(conversation, "я веган")
        cutoff = timezone.now()

        first = anonymize_dialogue([person.id], through=cutoff, reason=FORGET_ALL)
        second = anonymize_dialogue([person.id], through=cutoff, reason=FORGET_ALL)

        assert first.messages_archived == 1
        assert second.conversations == 0
        assert second.messages_archived == 0
        assert ArchivedMessage.all_tenants.filter(conversation=conversation).count() == 1

    def test_a_second_run_does_not_re_clear_the_live_window(self, person, fake_redis):
        """Re-clearing would empty the window of a person who is talking now."""
        conversation = _conversation(person)
        _message(conversation, "я веган")
        cutoff = timezone.now()

        anonymize_dialogue([person.id], through=cutoff, reason=FORGET_ALL)
        # Positive guard: if the first run cleared nothing, «the second run
        # cleared nothing» is true and meaningless.
        assert f"conv:{conversation.id}:msgs" in fake_redis.deleted

        fake_redis.deleted.clear()
        anonymize_dialogue([person.id], through=cutoff, reason=FORGET_ALL)

        assert fake_redis.deleted == []

    def test_both_redis_stores_go_on_the_first_run(self, person, fake_redis):
        conversation = _conversation(person)
        _message(conversation, "я веган")

        anonymize_dialogue([person.id], through=timezone.now(), reason=FORGET_ALL)

        assert fake_redis.deleted == [
            f"conv:{conversation.id}:msgs",
            f"pii_tokenmap:{conversation.id}",
        ]


class TestReach:
    def test_every_shell_of_the_person_is_covered(self, person, fake_redis, settings):
        """The dialogue is cross-tenant for the same reason memory is.

        The pilot resolves one shell under the ``global_bot`` sentinel and
        another under the MAX tenant. Anonymising only the requesting shell
        would report an erasure and leave the words on the row the bot actually
        talks to.
        """
        other_tenant = Tenant.objects.create(slug=f"er2-{uuid.uuid4().hex[:8]}", name="Other")
        sibling = BotUser.all_tenants.create(
            tenant=other_tenant,
            channel="max",
            channel_user_id=person.channel_user_id,
            ayla_user_id=person.ayla_user_id,
        )
        here = _conversation(person)
        there = _conversation(sibling)
        _message(here, "я веган")
        _message(there, "и живу на Арбате")

        ids = shell_ids_for_person(bot_user=person)
        assert set(ids) == {person.id, sibling.id}
        # Positive guard on BOTH rows: the second shell is the whole point of
        # this cell, and «its message is empty» is free if it was never written.
        assert Message.all_tenants.filter(conversation=here).first().content == "я веган"
        assert Message.all_tenants.filter(conversation=there).first().content == "и живу на Арбате"

        anonymize_dialogue(ids, through=timezone.now(), reason=FORGET_ALL)

        assert Message.all_tenants.filter(conversation=here).first().content == ""
        assert Message.all_tenants.filter(conversation=there).first().content == ""

    def test_an_unsent_master_draft_is_cleared_too(self, person, fake_redis):
        """``AiDraft.content`` is the master's compose box, quoting the customer.

        Layer 1 clears it at terminal status; an ACTIVE draft would otherwise
        carry the erased person's words into the master's screen after the
        erasure landed.
        """
        from apps.catalog.models import CatalogMaster

        conversation = _conversation(person)
        _message(conversation, "я веган")
        master = CatalogMaster.all_tenants.create(
            tenant=person.tenant,
            name="Анна",
            external_id=random.randint(10**6, 10**7),
            external_updated_at=timezone.now(),
        )
        draft = AiDraft.all_tenants.create(
            tenant=person.tenant,
            conversation=conversation,
            master=master,
            content="Здравствуйте! Вы писали, что вы веган…",
        )

        result = anonymize_dialogue([person.id], through=timezone.now(), reason=FORGET_ALL)

        draft.refresh_from_db()
        assert result.drafts_cleared == 1
        assert draft.content == ""


class TestTheNamedTerm:
    def test_the_term_is_stamped_per_row_at_archive_time(self, person, fake_redis, settings):
        """«Бессрочно» is the absence of a decision (OD_MEMORY.md §4).

        Stamped per row rather than computed at purge time so a later change to
        the setting cannot retroactively shorten a term someone was promised.
        """
        settings.ANONYMIZED_DIALOGUE_RETENTION_DAYS = ANONYMIZED_DIALOGUE_RETENTION_DAYS
        conversation = _conversation(person)
        _message(conversation, "я веган")
        before = timezone.now()

        anonymize_dialogue([person.id], through=timezone.now(), reason=FORGET_ALL)

        row = ArchivedMessage.all_tenants.get(conversation=conversation)
        expected = before + timedelta(days=ANONYMIZED_DIALOGUE_RETENTION_DAYS)
        assert abs((row.retention_until - expected).total_seconds()) < 60

    def test_the_purge_deletes_only_what_is_past_its_term(self, person, fake_redis):
        """The term is enforced by a task, not by a docstring — DRF-1370's lesson."""
        conversation = _conversation(person)
        _message(conversation, "я веган")
        anonymize_dialogue([person.id], through=timezone.now(), reason=FORGET_ALL)
        fresh = ArchivedMessage.all_tenants.get(conversation=conversation)

        assert purge_expired_archived_messages() == 0
        assert ArchivedMessage.all_tenants.filter(pk=fresh.pk).exists()

        ArchivedMessage.all_tenants.filter(pk=fresh.pk).update(
            retention_until=timezone.now() - timedelta(seconds=1)
        )
        assert purge_expired_archived_messages() == 1
        assert not ArchivedMessage.all_tenants.filter(pk=fresh.pk).exists()

    def test_the_message_row_survives_the_purge(self, person, fake_redis):
        """The purge ends the retention of the BODY, not of the conversation.

        A hard delete of the Message row would take booking-dispute metadata
        (roles, timestamps, the thread itself) with it — and conversations are
        forensic data the model docstring says is never hard-deleted here.
        """
        conversation = _conversation(person)
        message = _message(conversation, "я веган")
        anonymize_dialogue([person.id], through=timezone.now(), reason=FORGET_ALL)
        ArchivedMessage.all_tenants.filter(conversation=conversation).update(
            retention_until=timezone.now() - timedelta(seconds=1)
        )

        # Positive guard: the archive really did hold the words, so «the
        # Message row survived an empty purge» cannot pass by accident.
        assert ArchivedMessage.all_tenants.filter(conversation=conversation).count() == 1

        assert purge_expired_archived_messages() == 1

        message.refresh_from_db()
        assert message.content == ""
        assert Conversation.all_tenants.filter(pk=conversation.pk).exists()
