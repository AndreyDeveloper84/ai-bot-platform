"""The страж: no reader of the переписка may reach a prompt after erasure.

DRF-1369 / ``OD_MEMORY.md`` §4. The owner asked for a **guarantee**, and the
contour had already shown what a non-guarantee looks like: ``short_term.clear``
carried the docstring «used by the 152-ФЗ delete-my-data workflow» and had no
caller anywhere in ``apps/``. Written by someone who meant it. Never called.

So this file does not assert «the three readers we know about are safe». It
discovers the readers (:func:`apps.conversations.dialogue_readers.
discover_read_sites`), demands that each one be classified, and then — for
every reader classified as feeding a prompt — **runs it against a real
anonymised conversation and reads what comes back**.

Four ways to go red, and only the last one is about today:

    ``TestRegistryCoverage``  a reader exists that nobody classified
    ``TestRegistryIsCurrent`` the registry names a reader that is gone
    ``TestEveryPromptReaderIsProbed``  a prompt-bound claim with no probe
    ``TestNothingReachesThePrompt``    a probe hands back the person's words

The point of the first and third is the reader written next month by someone
who has never read this ticket: it is red until classified, and red again
unless the classification is true.

``TestTheArchiveIsStillReadable`` is the negative. An anonymisation that hides
the text from everyone including an incident review is deletion under another
name, and the owner ruled against that explicitly — «это единственная запись
того, что бот на самом деле сказал человеку, и она нужна при разборе инцидента
и спора о брони».
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable

import pytest
from django.utils import timezone

from apps.conversations.dialogue_readers import DIALOGUE_READERS, discover_read_sites
from apps.conversations.erasure import anonymize_dialogue, read_anonymized_dialogue
from apps.conversations.models import ArchivedMessage, Conversation, Message
from apps.identity.models import BotUser
from apps.orchestrator.memory import short_term
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)

APPS_ROOT = Path(__file__).resolve().parents[2]

#: What the person said. Two markers: a phrase that only a real body read can
#: produce, and a phone number — so a probe that leaks «the sentence minus the
#: identifiers» is caught as well as one that leaks the whole row.
SECRET_PHRASE = "москитная сетка на балконе"
SECRET_PHONE = "89990001122"
CUSTOMER_TEXT = f"я веган, {SECRET_PHRASE}, мой телефон {SECRET_PHONE}"
ASSISTANT_TEXT = f"Записала. Перезвоню на {SECRET_PHONE}, спрошу про {SECRET_PHRASE}."


class _FakeRedis:
    """List + string Redis good enough for the window and the token map."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

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
                    elif op[0] == "ltrim":
                        outer.store[op[1]] = outer.store.get(op[1], [])[op[2] :]
                self.ops = []

        return _Pipe()

    def lrange(self, key, start, end):
        items = self.store.get(key, [])
        return items[start:] if end == -1 else items[start:end]

    def delete(self, key):
        self.store.pop(key, None)

    # The PII tokeniser touches these on its clear path only.
    def hgetall(self, key):
        return self.store.get(key, {})


@pytest.fixture()
def fake_redis(monkeypatch) -> _FakeRedis:
    from apps.llm import pii_tokenizer

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    monkeypatch.setattr(pii_tokenizer, "_redis_client", lambda: fake)
    return fake


@pytest.fixture()
def seeded(fake_redis, settings) -> Conversation:
    """A conversation with the person's words still in it. NOT yet erased.

    Split out from :func:`erased` on purpose. «After the erasure there is no
    phone number» is a negative claim, and a negative claim on data that was
    never there is not a test — it is a fixture that rotted quietly. Every cell
    below that asserts an absence first asserts the presence on the same rows,
    through the same reader.
    """

    settings.STRICT_TENANT_SCOPE = "off"
    tenant = Tenant.objects.create(slug=f"drf1369-{uuid.uuid4().hex[:8]}", name="Guard")
    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"guard-{uuid.uuid4().hex[:8]}",
        ayla_user_id=uuid.uuid4(),
    )
    conversation = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
    Message.all_tenants.create(
        tenant=tenant,
        conversation=conversation,
        role=Message.Role.USER,
        content=CUSTOMER_TEXT,
        rendered_text=CUSTOMER_TEXT,
    )
    Message.all_tenants.create(
        tenant=tenant,
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content=ASSISTANT_TEXT,
        rendered_text=ASSISTANT_TEXT,
        action_type="show_masters",
        action_data={"clarification": {"question": CUSTOMER_TEXT, "options": [SECRET_PHRASE]}},
    )
    short_term.append(conversation.id, role="user", content=CUSTOMER_TEXT)
    short_term.append(conversation.id, role="assistant", content=ASSISTANT_TEXT)
    # The tokeniser reverse map — the store that holds the raw phone so the
    # model's reply can be de-tokenised. Seeded by hand because the tokenizer's
    # Lua path needs a real Redis; the erasure must delete the key either way.
    fake_redis.store[f"pii_tokenmap:{conversation.id}"] = {"rev:<PHONE_deadbeef_1>": SECRET_PHONE}
    return conversation


def _erase(conversation: Conversation) -> Conversation:
    """Run «забудь всё» over the thread and hand it back, reloaded."""

    anonymize_dialogue(
        [conversation.bot_user_id],
        through=timezone.now(),
        reason=ArchivedMessage.Reason.FORGET_ALL,
    )
    conversation.refresh_from_db()
    return conversation


@pytest.fixture()
def erased(seeded) -> Conversation:
    """The same conversation, after «забудь всё»."""

    return _erase(seeded)


# ---------------------------------------------------------------------------
# The probes — one per reader classified ``reaches_prompt=True``.
#
# Each returns EVERYTHING that reader could hand an LLM, as one string. Adding
# a prompt-bound reader to the registry without adding a probe here fails
# TestEveryPromptReaderIsProbed; adding a probe that leaks fails
# TestNothingReachesThePrompt.
# ---------------------------------------------------------------------------


def _probe_ai_drafts_history(conversation: Conversation) -> str:
    from apps.master_api.services.ai_drafts import _recent_history

    return " ".join(
        f"{m.content or ''} {m.rendered_text or ''}" for m in _recent_history(conversation)
    )


def _probe_ai_drafts_latest(conversation: Conversation) -> str:
    from apps.master_api.services.ai_drafts import _latest_customer_message

    msg = _latest_customer_message(conversation)
    return "" if msg is None else f"{msg.content or ''} {msg.rendered_text or ''}"


def _probe_handler_retry_text(conversation: Conversation) -> str:
    from apps.channels.max.handler import _last_user_content

    return _last_user_content(None, conversation) or ""


def _probe_handler_clarification(conversation: Conversation) -> str:
    from apps.channels.max.handler import _last_clarification_offer

    question, options = _last_clarification_offer(conversation)
    return f"{question} {' '.join(options)}"


def _probe_max_prompt_window(conversation: Conversation) -> str:
    # The line the MAX handler runs to build its prompt history
    # (`handler.py`, «Prior short-term history … feeds the discovery prompt»).
    return " ".join(str(m.get("content") or "") for m in short_term.recall(conversation.id))


def _probe_coordinator_snapshot(conversation: Conversation) -> str:
    from apps.orchestrator.memory.coordinator import load_snapshot

    return " ".join(str(m.get("content") or "") for m in load_snapshot(conversation).history)


def _probe_concierge_evidence(conversation: Conversation) -> str:
    from apps.orchestrator.concierge import _conversation_text

    return _conversation_text(conversation, "")


def _probe_concierge_store_history(conversation: Conversation) -> str:
    from apps.orchestrator.concierge import GlobalConversationStore

    rows = GlobalConversationStore().load_recent_history(conversation)
    return " ".join(f"{m.content or ''} {m.rendered_text or ''}" for m in rows)


PROBES: dict[str, Callable[[Conversation], str]] = {
    "apps.master_api.services.ai_drafts:_recent_history": _probe_ai_drafts_history,
    "apps.master_api.services.ai_drafts:_latest_customer_message": _probe_ai_drafts_latest,
    "apps.channels.max.handler:_last_user_content": _probe_handler_retry_text,
    "apps.channels.max.handler:_last_clarification_offer": _probe_handler_clarification,
    "apps.channels.max.handler:_handle_global_max_event_inner": _probe_max_prompt_window,
    "apps.orchestrator.memory.coordinator:load_snapshot": _probe_coordinator_snapshot,
    "apps.orchestrator.concierge:_conversation_text": _probe_concierge_evidence,
    "apps.orchestrator.concierge:GlobalConversationStore.load_recent_history": (
        _probe_concierge_store_history
    ),
}


def _prompt_bound() -> list[str]:
    return sorted(k for k, v in DIALOGUE_READERS.items() if v.reaches_prompt)


# ---------------------------------------------------------------------------


class TestRegistryCoverage:
    def test_every_discovered_reader_is_classified(self) -> None:
        """A new reader of the переписка is red until its author classifies it.

        This is the assertion that makes the guard outlive today's three
        readers. It does not know what the readers are — it finds them.
        """

        found = discover_read_sites(APPS_ROOT)
        unclassified = {
            key: f"{site.storage} @ line {site.lineno}"
            for key, site in sorted(found.items())
            if key not in DIALOGUE_READERS
        }
        assert unclassified == {}, (
            "New reader(s) of dialogue text. Add a row to "
            "apps/conversations/dialogue_readers.py::DIALOGUE_READERS saying "
            "whether the text reaches an LLM prompt and why that is safe after "
            "«удалить всё» (OD_MEMORY.md §4). If it does reach a prompt, add a "
            "probe to PROBES in this file."
        )


_PLAIN = """
from apps.conversations.models import Message


def build_prompt(conversation):
    return list(Message.all_tenants.filter(conversation=conversation))
"""

_ALIASED = """
from apps.conversations.models import Message as M


def build_prompt(conversation):
    return list(M.all_tenants.filter(conversation=conversation))
"""

_PROJECTION = """
from apps.conversations.models import Message


def preview(conversation):
    return (
        Message.all_tenants.filter(conversation=conversation)
        .values_list("content", flat=True)
        .first()
    )
"""

_COUNT_ONLY = """
from apps.conversations.models import Message


def how_many(conversation):
    return Message.all_tenants.filter(conversation=conversation).count()
"""

_REDIS = """
from apps.orchestrator.memory import short_term


def history(conversation):
    return short_term.recall(conversation.id)
"""


class TestTheScannerCannotBeWalkedPast:
    """What the discovery half catches, proven on synthetic modules.

    ``TestRegistryCoverage`` can only fail if the scanner actually sees the
    read. A scanner with a hole passes it forever and says nothing — the same
    false comfort as the ``short_term.clear`` docstring. So the detector gets
    its own cells, including the cheapest evasion there is.
    """

    @staticmethod
    def _scan(tmp_path, source: str):
        pkg = tmp_path / "apps" / "probe"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "reader.py").write_text(source, encoding="utf-8")
        return discover_read_sites(tmp_path / "apps")

    def test_a_plain_read_is_found(self, tmp_path) -> None:
        assert "apps.probe.reader:build_prompt" in self._scan(tmp_path, _PLAIN)

    def test_an_aliased_import_does_not_walk_past(self, tmp_path) -> None:
        """``import Message as M`` is the cheapest way around a name match."""
        found = self._scan(tmp_path, _ALIASED)

        assert "apps.probe.reader:build_prompt" in found, (
            "an aliased model import walked past the scanner — a guard that a "
            "rename defeats reports what it was told, not what is there"
        )

    def test_a_projection_naming_a_text_column_is_a_read(self, tmp_path) -> None:
        assert "apps.probe.reader:preview" in self._scan(tmp_path, _PROJECTION)

    def test_a_count_is_not_a_read(self, tmp_path) -> None:
        """Aggregations cannot hand anyone a body, and pinning them would churn
        the registry over code that carries no risk."""
        assert self._scan(tmp_path, _COUNT_ONLY) == {}

    def test_the_redis_window_read_is_found(self, tmp_path) -> None:
        found = self._scan(tmp_path, _REDIS)

        assert "apps.probe.reader:history" in found
        assert found["apps.probe.reader:history"].storage == "redis_window"


class TestRegistryIsCurrent:
    def test_no_registry_entry_outlives_its_reader(self) -> None:
        """Registry rot is a defect too — a stale row hides a real gap.

        A registry that keeps entries for readers that no longer exist stops
        being a description of the code and becomes folklore, and the day the
        counts stop matching nobody can tell which direction the drift went.
        """

        found = discover_read_sites(APPS_ROOT)
        stale = sorted(set(DIALOGUE_READERS) - set(found))
        assert stale == [], "Registry names readers that no longer exist — delete the rows."


class TestEveryPromptReaderIsProbed:
    def test_prompt_bound_readers_have_probes(self) -> None:
        """A ``reaches_prompt=True`` claim must be executable, not editorial."""

        missing = sorted(set(_prompt_bound()) - set(PROBES))
        assert missing == [], (
            "Prompt-bound reader(s) with no probe. The registry says these can "
            "put dialogue text into an LLM prompt; add a probe so the erasure "
            "is proven by running them, not by asserting about them."
        )

    def test_no_probe_outlives_its_registry_row(self) -> None:
        orphans = sorted(set(PROBES) - set(_prompt_bound()))
        assert orphans == [], "Probe(s) for readers no longer marked prompt-bound."


class TestNothingReachesThePrompt:
    """The run the ruling asks for: erase, then assemble, then look."""

    @pytest.mark.parametrize("key", _prompt_bound())
    def test_probe_returns_nothing_of_the_erased_person(self, key: str, seeded) -> None:
        """Before and after, through the same reader, on the same rows.

        The «after» half alone would pass on a broken fixture, a probe wired to
        the wrong object, or a reader that returns "" for reasons of its own —
        every one of which looks exactly like a working erasure. So the «before»
        half runs first and has to see the words, which makes this cell a
        statement about the ERASURE rather than about the emptiness.
        """
        before = PROBES[key](seeded)
        assert SECRET_PHRASE in before, (
            f"{key} did not see the person's words BEFORE the erasure — this "
            "probe proves nothing about the erasure until it does. Fix the "
            "probe or the fixture; do not weaken the assertion."
        )

        erased = _erase(seeded)

        text = PROBES[key](erased)
        assert SECRET_PHRASE not in text, f"{key} still hands the erased person's words to a prompt"
        assert SECRET_PHONE not in text, f"{key} still hands the erased person's phone to a prompt"
        assert "веган" not in text, f"{key} still hands an erased fact to a prompt"

    def test_the_redis_window_is_empty_not_merely_stale(self, seeded) -> None:
        """The window that ``short_term.clear`` was written for, and never called by.

        Not «the TTL will get it»: сутки — это сутки, and «удалить» is heard as
        «сейчас».
        """
        assert [m["content"] for m in short_term.recall(seeded.id)] == [
            CUSTOMER_TEXT,
            ASSISTANT_TEXT,
        ], "the window was never populated — an empty-after assertion would be free"

        assert short_term.recall(_erase(seeded).id) == []

    def test_the_token_map_goes_too(self, seeded, fake_redis) -> None:
        """The reverse map holds the raw phone, keyed by conversation.

        Emptying the sentence and leaving ``rev:<PHONE_…>`` → «89990001122»
        behind would be an anonymisation that keeps the number.
        """
        key = f"pii_tokenmap:{seeded.id}"
        assert SECRET_PHONE in str(fake_redis.store.get(key)), (
            "the token map was never seeded — «the key is gone» would be free"
        )

        _erase(seeded)

        assert key not in fake_redis.store


class TestTheArchiveIsStillReadable:
    """The negative. Anonymisation is not deletion wearing its name."""

    def test_incident_review_can_still_read_what_the_bot_said(self, erased) -> None:
        rows = read_anonymized_dialogue(erased, purpose="incident_review:test")

        assert [r.role for r in rows] == [Message.Role.USER, Message.Role.ASSISTANT]
        bodies = " ".join(r.body for r in rows)
        # The words survive — this is «единственная запись того, что бот на
        # самом деле сказал человеку».
        assert SECRET_PHRASE in bodies
        assert "веган" in bodies
        assert "Записала" in bodies

    def test_but_the_direct_identifiers_do_not(self, erased) -> None:
        rows = read_anonymized_dialogue(erased, purpose="incident_review:test")
        blob = " ".join(f"{r.body} {r.rendered_body}" for r in rows)

        assert SECRET_PHONE not in blob
        assert "[PHONE]" in blob

    def test_a_read_without_a_purpose_is_refused(self, erased) -> None:
        with pytest.raises(ValueError):
            read_anonymized_dialogue(erased, purpose="  ")

    def test_the_archive_has_no_second_door_through_the_admin(self) -> None:
        """Registering it in the Django admin would be an unaudited read.

        The archive's one sanctioned reader demands a ``purpose`` and writes a
        row per read — the same posture as ``RedZoneReader``. An admin
        changelist would hand the same text to a staff member with neither,
        and it would do it by adding four lines to a file nobody would connect
        to this ticket. So the absence is asserted rather than assumed.
        """
        from django.contrib import admin as django_admin

        assert ArchivedMessage not in django_admin.site._registry, (
            "ArchivedMessage is registered in the Django admin. That is a read "
            "path around read_anonymized_dialogue: no purpose, no audit row. "
            "If an operator surface is genuinely needed, build it on that "
            "function so the read is recorded."
        )
