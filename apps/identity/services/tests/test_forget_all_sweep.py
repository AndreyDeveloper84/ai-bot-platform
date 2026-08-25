"""«Забудь всё» must survive the gate being switched off (DRF-1370).

The defect these tests were written against: ``request_forget_all`` recorded
an intent and nothing else, so after the person was told «я забыла всё» their
rows were still ``status='active'``, ``soft_deleted_at IS NULL``, and the only
thing standing between them and the prompt was the read gate in
``memory_reader.get_personal_context``.

So the shape of the proof is not «does the reader return nothing» — it always
did. It is: **turn the gate off and look again.** Every test in
``TestWithTheGateOff`` monkeypatches ``get_personal_context`` to hand back the
row regardless of its tombstones, which is exactly what a future read path
that forgets to call the gate would do. Before the sweep that read brings back
everything; after it, nothing.

# Two renderers, not one

``TestWithTheGateOff`` renders the post-sweep state through BOTH prompt-facing
paths, because they have different blind spots and either one alone passes on
a half-done erasure:

* :func:`apps.orchestrator.memory_block.build_concierge_memory_block` — the
  declared prefs + green ``MemoryEntry`` facts, through the real
  ``ayla_ai_core.build_memory_block``. **It never reads ``UPC.summary``.**
* :func:`apps.identity.services.memory_reader.read_personal_context` — the
  view that carries ``summary``, Ayla's running prose account of the person.
  **It never sees the declared prefs.**

``test_the_block_alone_would_have_passed_a_leaking_summary`` pins that gap
directly: it builds the exact half-swept state (entries buried, summary left)
and shows the block reporting "" while the other renderer hands back the
paragraph. That is the mistake this pairing exists to prevent.

The third and fourth renderers live upstream (``ai/personal_context_hint.py``
and ``ayla_ai_core.build_memory_block`` over the backend's declared context)
and belong to the Ayla-owned half of the erasure — DRF-1366/1367, pinned by
``users/tests/test_memory_erasure_matrix.py`` in ``beautygo_backend``. Nothing
this sweep touches is readable by them.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.identity.models import (
    BotUser,
    MemoryEntry,
    UserPersonalContext,
    UserPreferences,
)
from apps.identity.services import memory_key_policy, memory_reader
from apps.identity.services.forget_all_sweep import (
    pending_forget_all_user_ids,
    sweep_forget_all,
    sweep_pending_forget_all,
)
from apps.identity.services.memory_deleter import request_forget_all
from apps.identity.services.memory_reader import read_green_entries, read_personal_context
from apps.orchestrator import memory_block
from apps.orchestrator.memory_block import build_concierge_memory_block
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

_SUMMARY = "Мария, 34, ходит на маникюр раз в три недели, любит тишину в кресле."


def _upc(**overrides) -> UserPersonalContext:
    kwargs = {
        "user_id": uuid.uuid4(),
        "summary": _SUMMARY,
        "display_name_preferred": "Маша",
        "language_preferred": "ru",
    }
    kwargs.update(overrides)
    return UserPersonalContext.objects.create(**kwargs)


def _green(upc, *, key="diet", value="vegan", **overrides) -> MemoryEntry:
    kwargs = dict(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,  # CHECK 5
        kind="lifestyle",
        status=MemoryEntry.STATUS_ACTIVE,
        content={"key": key, "value": value},
    )
    kwargs.update(overrides)
    return MemoryEntry.objects.create(**kwargs)


def _yellow(upc) -> MemoryEntry:
    """A yellow row — consent-bearing (CHECK 2), inference-stamped (CHECK 1)."""

    return MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_YELLOW,
        source=MemoryEntry.SOURCE_INFERRED,
        last_inferred_at=timezone.now(),
        consent_at=timezone.now(),
        kind="other",
        content={"key": "family", "value": "двое детей"},
    )


#: Every module that holds its own reference to the gate. `memory_key_policy`
#: does `from memory_reader import get_personal_context` at import time, so
#: patching the definition module alone leaves ITS copy live — and a test that
#: made that mistake would watch `build_concierge_memory_block` return "" and
#: conclude the sweep worked, when in fact the gate it thought it had removed
#: was still standing. Found by running the demonstration rather than by
#: reading the test.
_GATE_HOLDERS = (memory_reader, memory_key_policy)


def _open_the_gate(monkeypatch) -> None:
    """Switch the read gate OFF — the Linear proof's «отключи гейт».

    Stands in for a future read path that queries memory without going through
    ``get_personal_context``. Before the sweep this brings the whole memory
    back; after it, the tombstones — not the gate — are what keep it away.
    """

    ungated = lambda user_id: UserPersonalContext.objects.filter(  # noqa: E731
        user_id=user_id
    ).first()
    for module in _GATE_HOLDERS:
        monkeypatch.setattr(module, "get_personal_context", ungated)


def test_the_gate_holder_list_is_complete():
    """If a third module binds the gate, this test says so before a proof lies.

    The list above is the reason every «gate off» assertion below means what it
    says. A module that imports the gate and is not in ``_GATE_HOLDERS`` would
    silently keep gating during those tests.
    """

    import ast
    import pathlib

    holders = {module.__name__ for module in _GATE_HOLDERS}
    found = set()
    root = pathlib.Path(memory_reader.__file__).parents[3]
    for path in (root / "apps").rglob("*.py"):
        if "tests" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        # Cheap substring gate before the expensive parse. CI runs this suite
        # against a 45-minute ceiling it already sits ~15 seconds under, so a
        # test that AST-parses every module in `apps/` is a cost this proof
        # does not need: a file that cannot contain the name cannot import it.
        if "get_personal_context" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "get_personal_context" for alias in node.names
            ):
                found.add(
                    str(path.relative_to(root).with_suffix("")).replace("\\", ".").replace("/", ".")
                )
    # privacy.py binds it too, but the export is not a prompt path and its
    # tests assert the gated behaviour on purpose — see export_coverage
    # KNOWN_LIMITS. Anything else appearing here needs a decision.
    unexpected = found - holders - {"apps.identity.services.privacy"}
    assert not unexpected, (
        "these modules bind the read gate and are not in _GATE_HOLDERS, so the "
        f"«gate off» proofs below do not actually reach them: {sorted(unexpected)}"
    )


def _block_for(user_id, monkeypatch) -> str:
    """``build_concierge_memory_block`` with both consent gates held open."""

    monkeypatch.setattr(
        memory_block,
        "get_declared_prefs",
        lambda bot_user: SimpleNamespace(
            status=memory_block.GateStatus.OK, context=SimpleNamespace(context={})
        ),
    )
    monkeypatch.setattr("apps.consent.memory.can_store_green_memory", lambda bot_user: True)
    return build_concierge_memory_block(SimpleNamespace(ayla_user_id=user_id))


class TestTheDefectThisClosed:
    def test_forget_all_leaves_the_rows_physically_present(self, monkeypatch):
        """Recording the intent erases nothing — only the gate hides it.

        This is the state DRF-1370 was filed about, and it is still the state
        in the window between the person's request and the hourly sweep. The
        assertion is kept (not inverted) because that window is real: it pins
        that ``request_forget_all`` is an intent and never claims more.
        """
        upc = _upc()
        entry = _green(upc)
        request_forget_all(upc.user_id)

        entry.refresh_from_db()
        assert entry.status == MemoryEntry.STATUS_ACTIVE
        assert entry.soft_deleted_at is None
        assert entry.delete_requested_at is None
        upc.refresh_from_db()
        assert upc.summary == _SUMMARY

        # The gate is doing all of the work, and only the gate. Switch it off
        # and BOTH renderers hand the memory straight back to the prompt —
        # which is what makes their emptiness after the sweep mean something.
        assert read_green_entries(upc.user_id) == []
        _open_the_gate(monkeypatch)
        assert len(read_green_entries(upc.user_id)) == 1
        assert "vegan" in _block_for(upc.user_id, monkeypatch)
        assert read_personal_context(upc.user_id).summary == _SUMMARY

    def test_the_sweep_turns_the_intent_into_tombstones(self):
        upc = _upc()
        entry = _green(upc)
        request_forget_all(upc.user_id)

        result = sweep_forget_all(upc.user_id)

        assert result.entries_deleted == 1
        assert result.context_fields_cleared == 3
        assert result.tombstoned is True

        entry.refresh_from_db()
        assert entry.status == MemoryEntry.STATUS_DELETED
        assert entry.soft_deleted_at is not None
        assert entry.delete_requested_at is not None
        # The tombstone says WHICH request buried it. `user_delete` would have
        # made «забудь про питание» and «забудь всё» indistinguishable later.
        assert entry.deletion_reason == MemoryEntry.DELETION_REASON_FORGET_ALL

        upc.refresh_from_db()
        assert upc.summary is None
        assert upc.display_name_preferred is None
        assert upc.language_preferred is None
        assert upc.soft_deleted_at is not None


class TestWithTheGateOff:
    """The proof the issue asked for: switch the gate off and look again."""

    def test_nothing_comes_back_through_either_renderer(self, monkeypatch):
        upc = _upc()
        _green(upc, key="diet", value="vegan")
        _green(upc, key="preferred_districts", value="Хамовники")
        request_forget_all(upc.user_id)
        sweep_forget_all(upc.user_id)

        _open_the_gate(monkeypatch)

        # Renderer 1 — the concierge prompt block, through the real ai-core.
        assert _block_for(upc.user_id, monkeypatch) == ""
        # Renderer 2 — the view that carries the summary. Blind to declared
        # prefs, and the only one of the two that would have caught a summary
        # left behind.
        view = read_personal_context(upc.user_id)
        assert view.summary is None
        assert view.green_facts == []
        assert view.is_empty()
        # And the raw read the management path uses.
        assert read_green_entries(upc.user_id) == []

    def test_the_block_alone_would_have_passed_a_leaking_summary(self, monkeypatch):
        """Why one renderer is not enough — the half-swept state, made by hand.

        Entries buried, ``summary`` left on the row. A sweep written to satisfy
        ``build_concierge_memory_block`` alone would ship exactly this, and the
        person's prose profile would keep reaching the prompt through the other
        reader.
        """
        upc = _upc()
        entry = _green(upc)
        request_forget_all(upc.user_id)
        # Bury the entry the way the sweep does, but leave the UPC columns.
        now = timezone.now()
        MemoryEntry.objects.filter(pk=entry.pk).update(
            delete_requested_at=now,
            soft_deleted_at=now,
            deletion_reason=MemoryEntry.DELETION_REASON_FORGET_ALL,
            status=MemoryEntry.STATUS_DELETED,
            updated_at=now,
        )

        _open_the_gate(monkeypatch)

        assert _block_for(upc.user_id, monkeypatch) == ""  # renderer 1: clean
        assert read_personal_context(upc.user_id).summary == _SUMMARY  # renderer 2: leaking

        # The real sweep closes it.
        sweep_forget_all(upc.user_id)
        assert read_personal_context(upc.user_id).summary is None

    def test_a_row_written_after_the_request_is_reburied(self, monkeypatch):
        """The write path does not honour the forget-all gate — so re-sweep.

        ``get_or_create_personal_context`` returns a forgotten UPC unchanged,
        so a later turn can still mint a fact under it. A one-shot sweep would
        leave that row live forever behind the gate alone.
        """
        upc = _upc()
        request_forget_all(upc.user_id)
        sweep_forget_all(upc.user_id)

        latecomer = _green(upc, key="price_range", value="3000")
        assert upc.user_id in pending_forget_all_user_ids()

        sweep_pending_forget_all()

        latecomer.refresh_from_db()
        assert latecomer.status == MemoryEntry.STATUS_DELETED
        _open_the_gate(monkeypatch)
        assert read_green_entries(upc.user_id) == []
        assert _block_for(upc.user_id, monkeypatch) == ""


class TestScope:
    def test_minor_lock_survives(self):
        """A safety lock is not a fact about the person — erasing it is a downgrade."""
        upc = _upc(minor_lock=True)
        request_forget_all(upc.user_id)
        sweep_forget_all(upc.user_id)
        upc.refresh_from_db()
        assert upc.minor_lock is True

    def test_yellow_is_left_for_its_own_stream(self):
        """Green-only, like the whole memory_deleter module. Named, not skipped."""
        upc = _upc()
        yellow = _yellow(upc)
        request_forget_all(upc.user_id)
        sweep_forget_all(upc.user_id)
        yellow.refresh_from_db()
        assert yellow.soft_deleted_at is None
        # And it was never reachable by the reader this sweep protects.
        assert read_green_entries(upc.user_id) == []

    def test_notification_settings_are_not_erased(self):
        """The decision, pinned: standing instructions are not memories.

        Deleting the row would reset ``notify_retention`` to its ``True``
        default — «забудь всё» would switch the nudges back ON. The chat
        wording was narrowed to say what stays instead; the export now shows
        these values so the person can see them rather than guess.
        """
        tenant = Tenant.objects.create(name="Формула тела", slug="formula")
        bot_user = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="42", ayla_user_id=uuid.uuid4()
        )
        prefs = UserPreferences.all_tenants.create(
            bot_user=bot_user,
            tenant=tenant,
            notify_retention=False,
            notify_promo=False,
            birthday_date=date(1991, 5, 17),
        )
        upc = _upc(user_id=bot_user.ayla_user_id)
        request_forget_all(upc.user_id)
        sweep_forget_all(upc.user_id)

        prefs.refresh_from_db()
        assert prefs.notify_retention is False  # not resurrected to the default
        assert prefs.birthday_date == date(1991, 5, 17)

    def test_a_user_who_never_asked_is_untouched(self):
        upc = _upc()
        entry = _green(upc)
        result = sweep_forget_all(upc.user_id)
        assert not result.changed
        entry.refresh_from_db()
        assert entry.status == MemoryEntry.STATUS_ACTIVE
        upc.refresh_from_db()
        assert upc.summary == _SUMMARY


class TestQueueBehaviour:
    def test_sweeping_twice_changes_nothing_the_second_time(self):
        upc = _upc()
        _green(upc)
        request_forget_all(upc.user_id)
        first = sweep_forget_all(upc.user_id)
        second = sweep_forget_all(upc.user_id)
        assert first.changed
        assert not second.changed
        upc.refresh_from_db()
        # The FIRST sweep's timestamp is the one that answers «when honoured».
        assert second.tombstoned is False

    def test_the_finished_are_not_scanned_again(self):
        upc = _upc()
        _green(upc)
        request_forget_all(upc.user_id)
        assert upc.user_id in pending_forget_all_user_ids()
        sweep_forget_all(upc.user_id)
        assert upc.user_id not in pending_forget_all_user_ids()

    def test_oldest_request_first(self):
        older, newer = _upc(), _upc()
        request_forget_all(older.user_id)
        request_forget_all(newer.user_id)
        UserPersonalContext.objects.filter(user_id=older.user_id).update(
            forget_all_requested_at=timezone.now() - timedelta(days=3)
        )
        assert pending_forget_all_user_ids()[0] == older.user_id

    def test_one_bad_row_does_not_stall_the_queue(self, monkeypatch):
        good = _upc()
        _green(good)
        request_forget_all(good.user_id)
        bad = _upc()
        request_forget_all(bad.user_id)

        real = sweep_forget_all

        def _explode(user_id):
            if user_id == bad.user_id:
                raise RuntimeError("boom")
            return real(user_id)

        monkeypatch.setattr("apps.identity.services.forget_all_sweep.sweep_forget_all", _explode)
        summary = sweep_pending_forget_all()

        assert summary["errors"] == 1
        assert summary["users_swept"] == 1
        assert summary["entries_deleted"] == 1


class TestTheDialogueHalf:
    """DRF-1369 — the sweep also обезличивает the переписка.

    The memory half of «забудь всё» landed first (DRF-1370). The dialogue was
    the surface nothing in the cascade touched, and it reached a prompt: the
    master's AI draft is assembled straight out of ``Message`` rows.
    """

    class _FakeRedis:
        def __init__(self):
            self.store: dict = {}
            self.deleted: list[str] = []

        def pipeline(self):
            outer = self

            class _Pipe:
                def __init__(self):
                    self.ops: list = []

                def rpush(self, key, value):
                    self.ops.append((key, value))

                def ltrim(self, *a):
                    pass

                def expire(self, *a):
                    pass

                def execute(self):
                    for key, value in self.ops:
                        outer.store.setdefault(key, []).append(value)
                    self.ops = []

            return _Pipe()

        def lrange(self, key, start, end):
            items = self.store.get(key, [])
            return items[start:] if end == -1 else items[start:end]

        def delete(self, key):
            self.deleted.append(key)
            self.store.pop(key, None)

    @pytest.fixture()
    def redis(self, monkeypatch):
        from apps.llm import pii_tokenizer
        from apps.orchestrator.memory import short_term

        fake = self._FakeRedis()
        monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
        monkeypatch.setattr(pii_tokenizer, "_redis_client", lambda: fake)
        return fake

    @staticmethod
    def _dialogue(upc, settings):
        from apps.conversations.models import Conversation, Message

        settings.STRICT_TENANT_SCOPE = "off"
        tenant = Tenant.objects.create(slug=f"fas-{uuid.uuid4().hex[:8]}", name="Sweep")
        bot_user = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id=f"fas-{uuid.uuid4().hex[:8]}",
            ayla_user_id=upc.user_id,
        )
        conversation = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
        # The thread predates the request — a thread OPENED afterwards is
        # deliberately out of the anonymiser's reach (see
        # apps/conversations/tests/test_erasure.py::TestTheCutoff).
        Conversation.all_tenants.filter(pk=conversation.pk).update(
            created_at=upc.forget_all_requested_at - timedelta(minutes=10)
        )
        conversation.refresh_from_db()
        return conversation, Message

    def test_the_cutoff_is_the_request_instant_not_now(self, redis, settings):
        """The whole reason the sweep can re-run hourly without harm.

        The sweep is deliberately re-entrant — the write path does not honour
        the forget-all gate, so it keeps coming back. With a «now» cutoff that
        re-entrancy would blank the live conversation of a forgotten person
        every hour for as long as they kept using the bot.
        """
        upc = _upc()
        request_forget_all(upc.user_id)
        upc.refresh_from_db()
        conversation, Message = self._dialogue(upc, settings)

        before = Message.all_tenants.create(
            tenant=conversation.tenant,
            conversation=conversation,
            role="user",
            content="я веган",
        )
        Message.all_tenants.filter(pk=before.pk).update(
            created_at=upc.forget_all_requested_at - timedelta(minutes=5)
        )
        after = Message.all_tenants.create(
            tenant=conversation.tenant,
            conversation=conversation,
            role="user",
            content="запиши меня на маникюр",
        )
        Message.all_tenants.filter(pk=after.pk).update(
            created_at=upc.forget_all_requested_at + timedelta(minutes=5)
        )

        result = sweep_forget_all(upc.user_id)

        before.refresh_from_db()
        after.refresh_from_db()
        assert result.conversations_anonymized == 1
        assert result.messages_archived == 1
        assert before.content == ""
        assert after.content == "запиши меня на маникюр"

    def test_an_unfinished_dialogue_keeps_the_person_in_the_queue(self, redis, settings):
        """The failure direction: a Redis outage must not tick the person off.

        The dialogue half writes to Redis before it writes to Postgres, so a
        Redis failure leaves the cutoff unmoved. Without the third term in
        ``pending_forget_all_user_ids`` the queue would consider the erasure
        finished because the MEMORY half succeeded — «success reported for work
        not done», which this cascade refuses everywhere else.
        """
        from apps.conversations.models import Conversation

        upc = _upc()
        _green(upc)
        request_forget_all(upc.user_id)
        upc.refresh_from_db()
        conversation, _ = self._dialogue(upc, settings)

        sweep_forget_all(upc.user_id)
        assert upc.user_id not in pending_forget_all_user_ids()

        # Now simulate the half that did not land.
        Conversation.all_tenants.filter(pk=conversation.pk).update(anonymized_through=None)

        assert upc.user_id in pending_forget_all_user_ids()


class TestWiring:
    def test_the_task_is_registered_on_the_beat(self, settings):
        entry = settings.CELERY_BEAT_SCHEDULE["identity_forget_all_sweep"]
        assert entry["task"] == "apps.identity.tasks.forget_all_sweep"

    def test_the_task_runs_the_sweep(self):
        from apps.identity.tasks import forget_all_sweep

        upc = _upc()
        _green(upc)
        request_forget_all(upc.user_id)

        summary = forget_all_sweep()

        assert summary["users_swept"] == 1
        assert summary["entries_deleted"] == 1
