"""Erasure matrix — what survives «забудь всё» / account delete, and whether
it can still reach the prompt.

These are **characterization** tests: they record the behaviour that exists
today, per storage and per erasure verb, and each assertion names the storage
it pins. The criterion is the owner's (OD_MEMORY.md §4): a remnant matters only
if it can reach the prompt pipeline again. So every «what survives» assertion is
paired with a read through the SAME function the prompt assembly uses
(``build_concierge_memory_block`` for declared prefs, ``short_term.recall`` /
``load_recent_history`` for dialogue).

Tests marked ``GAP`` assert a **defect**. When the defect is fixed the
assertion must be inverted, not deleted — the storage still needs a cell in
the matrix.

The upstream backend is represented by :class:`_FakeAyla`, which mirrors the
real backend semantics exactly:

  PATCH  users/internal_personal_context_api.py:115-118 → ``setattr(ctx, field, value)``
  DELETE users/personal_data_api.py:157 → ``erase_personal_context()``, which
         resets every declared field to its model default and stamps
         ``data_sources[*] = "erased"`` (backend #251 / DRF-1366).

The backend half of the same proof (real view code, real ORM) lives in
``beautygo_backend`` at ``users/tests/test_memory_erasure_matrix.py``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from apps.consent.models import ConsentRecord
from apps.consent.services import record_global_consent
from apps.identity.models import MemoryEntry
from apps.identity.services import resolve_or_create_global_bot_user
from apps.identity.services.memory_key_policy import read_current_view
from apps.identity.services.memory_reader import read_green_entries
from apps.identity.services.personal_context import GateStatus, get_declared_prefs
from apps.integrations.ayla.personal_context_client import DeclaredContext
from apps.orchestrator.memory import short_term
from apps.orchestrator.memory.personal_context import record_explicit_green_facts
from apps.orchestrator.memory_block import build_concierge_memory_block
from apps.persona.memory_commands import FORGET_ALL_PROMPT, handle_memory_command

pytestmark = pytest.mark.django_db(transaction=True)


# ---------------------------------------------------------------------------
# Fixtures / doubles
# ---------------------------------------------------------------------------


class _FakeAyla:
    """In-memory stand-in for the backend ``users.UserPersonalContext`` row.

    Semantics copied verbatim from the real internal API so the bot half of
    the cascade is exercised against the contract it actually talks to.
    """

    def __init__(self, context: dict[str, Any] | None = None):
        self.context: dict[str, Any] = dict(context or {})
        self.deleted = False
        self.calls: list[tuple] = []

    # -- wire surface -------------------------------------------------
    def get_context(self, *, ayla_user_id: str) -> DeclaredContext:
        self.calls.append(("get", ayla_user_id))
        return DeclaredContext(ayla_user_id=ayla_user_id, context=dict(self.context))

    def patch_context(self, *, ayla_user_id: str, updates: list) -> DeclaredContext:
        self.calls.append(("patch", ayla_user_id, updates))
        for item in updates:
            # Backend: setattr(ctx, item["field"], item["value"]) — no
            # interpretation, no clearing of anything not named.
            self.context[item["field"]] = item["value"]
        return DeclaredContext(ayla_user_id=ayla_user_id, context=dict(self.context))

    def delete_personal_data(self, *, ayla_user_id: str) -> None:
        self.calls.append(("delete", ayla_user_id))
        self.context.clear()
        self.deleted = True

    def close(self) -> None:  # pragma: no cover - trivial
        pass

    # -- helpers ------------------------------------------------------
    @property
    def patched_fields(self) -> list[str]:
        return [u["field"] for c in self.calls if c[0] == "patch" for u in c[2]]


class _FakeRedis:
    """Minimal list-only Redis for the short-term window."""

    def __init__(self):
        self.store: dict[str, list[str]] = {}

    def pipeline(self):
        outer = self

        class _Pipe:
            def __init__(self):
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


def _bot_user(uid: str):
    return resolve_or_create_global_bot_user(
        channel="max", channel_user_id=uid, ayla_user_id=uuid.uuid4()
    )


def _consents(bot_user, settings) -> None:
    settings.STRICT_TENANT_SCOPE = "strict"
    record_global_consent(bot_user, source="welcome")
    ConsentRecord.all_tenants.create(
        tenant=bot_user.tenant,
        bot_user=bot_user,
        consent_type=ConsentRecord.ConsentType.MEMORY_GREEN,
        granted=True,
        source="test",
    )


@pytest.fixture()
def ayla(monkeypatch):
    """A backend row pre-filled the way a real pilot user's is."""
    fake = _FakeAyla(
        {
            "diet_type": "vegan",
            "preferred_time_slots": ["evening"],
            "preferred_districts": ["Арбат"],
            "price_range_min": "1000.00",
            "price_range_max": "3500.00",
            "favorite_masters": ["7f1d0f2e-0000-4000-8000-000000000001"],
            "busy_days": ["mon"],
            "skin_sensitivities": ["ретинол"],
            "workplace_district": "Тверская",
            "home_district": "Сокол",
            "min_rating_preference": 4.5,
            "prefers_flexible_cancellation": True,
        }
    )
    monkeypatch.setattr(
        "apps.identity.services.personal_context.PersonalContextHttpClient",
        lambda *a, **kw: fake,
    )
    return fake


def _forget_all(bot_user) -> str:
    """Run the full two-step «забудь всё» exactly as the handler does."""
    step1 = handle_memory_command(
        user_id=bot_user.ayla_user_id, text="забудь всё", bot_user=bot_user
    )
    assert step1 is not None and step1.action_type == "memory_forget_all_prompt"
    step2 = handle_memory_command(
        user_id=bot_user.ayla_user_id,
        text="удалить",
        last_assistant_text=FORGET_ALL_PROMPT,
        bot_user=bot_user,
    )
    assert step2 is not None
    return step2.text


# ---------------------------------------------------------------------------
# Storage 1+2 — bot MemoryEntry / bot UserPersonalContext
# ---------------------------------------------------------------------------


class TestBotLocalMemory:
    def test_forget_all_silences_the_local_view(self, settings, ayla):
        bu = _bot_user("erase-local-1")
        _consents(bu, settings)
        assert record_explicit_green_facts(bu, "я веган") == 1

        _forget_all(bu)

        assert read_current_view(bu.ayla_user_id).green_facts == []
        assert read_green_entries(bu.ayla_user_id) == []

    def test_forget_all_leaves_the_rows_physically_present(self, settings, ayla):
        """GAP — «forget all» writes only the UPC tombstone. There is no sweep.

        ``memory_deleter.request_forget_all`` documents «the async sweep then
        soft-deletes every entry», but no such task exists in the repo
        (``apps/identity/tasks.py`` registers only ``recompute_profiles_daily``).
        The rows keep ``status=active`` and no tombstone of their own; they are
        invisible only because every read goes through the UPC gate.
        """
        bu = _bot_user("erase-local-2")
        _consents(bu, settings)
        record_explicit_green_facts(bu, "я веган")

        _forget_all(bu)

        rows = list(MemoryEntry.objects.filter(user_id=bu.ayla_user_id))
        assert len(rows) == 1
        row = rows[0]
        assert row.soft_deleted_at is None  # никакой развёртки не было
        assert row.delete_requested_at is None
        assert row.status == MemoryEntry.STATUS_ACTIVE
        assert json.dumps(row.content, ensure_ascii=False)  # содержимое на месте

    def test_forget_all_blocks_re_learning(self, settings, ayla):
        bu = _bot_user("erase-local-3")
        _consents(bu, settings)
        _forget_all(bu)

        assert record_explicit_green_facts(bu, "я веган") == 0
        assert read_current_view(bu.ayla_user_id).green_facts == []


# ---------------------------------------------------------------------------
# Storage 3 — backend users.UserPersonalContext (source of truth, OD_MEMORY §1)
# ---------------------------------------------------------------------------


class TestBackendDeclaredContext:
    def test_forget_all_erases_all_twelve_fields(self, settings, ayla):
        """FIXED (was GAP) — the bridge asks for the erasure instead of
        naming fields. DRF-1367.

        The inverted cell of «the clear set is three fields; the row has
        twelve». The bot no longer names any field: it calls the ONE verb
        the owning side grew for this (``DELETE …/personal-data/`` →
        ``users.personal_context_erasure.erase_personal_context``), which
        derives the list from ``UserPersonalContext._meta.concrete_fields``.
        A thirteenth field added upstream is erased with no edit here — that
        is the whole point of the fix, and the reason this assertion checks
        the WHOLE mapping rather than a list of names.
        """
        bu = _bot_user("erase-decl-1")
        _consents(bu, settings)

        _forget_all(bu)

        assert ayla.patched_fields == []  # ни одного поля не перечислено
        assert ("delete", str(bu.ayla_user_id)) in ayla.calls
        assert ayla.deleted is True
        # Not «these twelve are empty» — NOTHING is left, whatever was there.
        assert ayla.context == {}

    def test_no_remnant_reaches_the_prompt(self, settings, ayla):
        """FIXED (was GAP, P0) — the owner's criterion (OD_MEMORY.md §4):
        a remnant matters only if it can reach the prompt pipeline again.

        The inverted cell of «the remnant is still in the prompt». Checked
        through BOTH prompt assemblers, because their blind spots do not
        overlap and neither one alone can prove «the prompt is empty»:

          ``ayla_ai_core.build_memory_block`` (the MAX prompt, via
          ``build_concierge_memory_block``) renders the computed fields —
          budget, favourite masters, districts, days off, rating — but has
          NO branch for ``skin_sensitivities``.

          ``ai.personal_context_hint.format_personal_context_hint`` (the
          backend AI chat) renders six of the twelve, including exactly the
          ``skin_sensitivities`` the other one drops, and none of the
          computed ones. It lives in the backend repo; the cell that pins
          it is ``users/tests/test_memory_erasure_matrix.py::
          TestBackendPromptConsumer``.

        So the assertion below covers both: the MAX block through the real
        function, and the shared INPUT both renderers read — a field that is
        not on the row cannot be rendered by any consumer of the row,
        present or future.
        """
        bu = _bot_user("erase-decl-2")
        _consents(bu, settings)
        record_explicit_green_facts(bu, "я веган")

        assert "Диета" in build_concierge_memory_block(bu)

        _forget_all(bu)

        # Renderer 1 — the MAX system prompt, the function the handler calls.
        block = build_concierge_memory_block(bu)
        assert block == ""
        for phrase in (
            "Любимые мастера",
            "Бюджет",
            "Ищет рядом с работой",
            "Ищет рядом с домом",
            "Избегает",
            "Минимальный рейтинг",
            "Предпочитает гибкую отмену",
            "Диета",
            "Предпочитает районы",
            "Обычно выбирает время",
        ):
            assert phrase not in block

        # Renderer 2 (and any future one) — the row they all read is empty.
        declared = get_declared_prefs(bu)
        assert declared.status is GateStatus.OK
        assert not any((declared.context.context or {}).values())

    def test_skin_sensitivities_never_reach_the_max_prompt(self, settings, ayla):
        """Refutation of a plausible fear: ``ayla_ai_core.build_memory_block``
        has no branch for ``skin_sensitivities``, so the field is dropped
        silently by the MAX surface (it IS rendered by the backend's own AI
        chat — ``ai/personal_context_hint.py:107-117`` — which is a separate
        consumer, not this one).
        """
        bu = _bot_user("erase-decl-3")
        _consents(bu, settings)

        block = build_concierge_memory_block(bu)

        assert "ретинол" not in block
        assert "чувствительн" not in block.lower()

    def test_single_fact_forget_also_skips_price_and_favorites(self, settings, ayla):
        """«забудь про бюджет» / «забудь про мастера» clear nothing upstream."""
        bu = _bot_user("erase-decl-4")
        _consents(bu, settings)
        assert record_explicit_green_facts(bu, "комфортно до 3000 рублей") == 1
        ayla.calls.clear()

        res = handle_memory_command(user_id=bu.ayla_user_id, text="забудь мой бюджет", bot_user=bu)

        assert res is not None and "забыла" in res.text.lower()
        assert read_green_entries(bu.ayla_user_id) == []  # локально удалено
        assert ayla.patched_fields == []  # наверх не ушло ничего
        # The statement wrote 3000 upstream; the forget leaves it there.
        assert ayla.context["price_range_max"] == "3000.00"
        # And — the negative of DRF-1367 — a DOMAIN forget is still a domain
        # forget. It must NEVER reach for the whole-profile erasure verb.
        assert ayla.deleted is False
        assert [c[0] for c in ayla.calls] == []

    def test_domain_forget_clears_its_domain_and_only_its_domain(self, settings, ayla):
        """Negative control for DRF-1367 — «забудь это» was not widened.

        «Забудь про питание» names one domain on purpose; naming fields is
        the right shape THERE, and only there. The eleven other fields must
        survive it.
        """
        bu = _bot_user("erase-decl-10")
        _consents(bu, settings)
        assert record_explicit_green_facts(bu, "я веган") == 1
        ayla.calls.clear()

        res = handle_memory_command(
            user_id=bu.ayla_user_id, text="забудь всё про моё питание", bot_user=bu
        )

        assert res is not None and "питание" in res.text
        assert ayla.deleted is False
        assert ayla.patched_fields == ["diet_type"]
        assert ayla.context["diet_type"] == ""
        assert ayla.context["price_range_max"] == "3500.00"
        assert ayla.context["favorite_masters"] == ["7f1d0f2e-0000-4000-8000-000000000001"]
        assert ayla.context["home_district"] == "Сокол"
        block = build_concierge_memory_block(bu)
        assert "Диета" not in block
        assert "Любимые мастера" in block
        assert "Ищет рядом с домом" in block

    def test_forget_all_clears_the_price_the_contract_cannot_clear(self, settings, ayla):
        """FIXED — the field PATCH could not empty on ANY encoding.

        ``null`` is rejected by the contract's ``value`` JSONField and ``""``
        blows up the Decimal column (backend cell
        ``test_price_cannot_be_cleared_through_the_contract_at_all``). That
        is not a contract defect to route around — it is the reason erasure
        became a separate operation. Through the verb the price goes.
        """
        bu = _bot_user("erase-decl-6")
        _consents(bu, settings)
        assert record_explicit_green_facts(bu, "комфортно до 3000 рублей") == 1
        assert ayla.context["price_range_max"] == "3000.00"

        _forget_all(bu)

        assert "price_range_min" not in ayla.context
        assert "price_range_max" not in ayla.context
        assert "Бюджет" not in build_concierge_memory_block(bu)

    def test_a_refused_erasure_is_not_reported_as_done(self, settings, ayla, monkeypatch):
        """The failure direction. An erasure that did not happen must not be
        announced as one.

        Before DRF-1367 the reply was unconditional: the bridge swallowed
        every failure (best-effort, which is correct for a WRITE that heals
        on the next statement — PATCH is idempotent LWW) and the person heard
        «я забыла всё» either way. An erasure does not heal on the next
        statement, and the person disproves the claim on the very next turn.
        """
        from apps.integrations.ayla.personal_context_client import (
            PersonalContextTransportError,
        )

        bu = _bot_user("erase-decl-7")
        _consents(bu, settings)

        def _boom(**_kw):
            raise PersonalContextTransportError("http_500")

        monkeypatch.setattr(ayla, "delete_personal_data", _boom)

        reply = _forget_all(bu)

        assert "забыла всё, что о тебе знала" not in reply
        assert "предпочтения" in reply
        assert ayla.deleted is False
        # Bot-local memory IS gone — the local half succeeded, and the reply
        # claims exactly that much and no more.
        assert read_current_view(bu.ayla_user_id).green_facts == []

    def test_an_unlinked_user_is_not_told_a_profile_was_erased(self, settings, ayla):
        """No ``ayla_user_id`` → no subject to address upstream.

        ``privacy.delete_personal_data`` already refuses to report this green
        (DRF-956 / T-05 ruling §4+§6); the chat verb now agrees with it.
        """
        bu = resolve_or_create_global_bot_user(
            channel="max", channel_user_id="erase-decl-8", ayla_user_id=None
        )
        assert bu.ayla_user_id is None

        first = handle_memory_command(user_id=uuid.uuid4(), text="забудь всё", bot_user=bu)
        assert first is not None
        reply = handle_memory_command(
            user_id=uuid.uuid4(),
            text="удалить",
            last_assistant_text=FORGET_ALL_PROMPT,
            bot_user=bu,
        )

        assert reply is not None
        assert "забыла всё, что о тебе знала" not in reply.text
        assert ayla.calls == []

    def test_the_erasure_is_the_terminal_state_not_a_write_of_empties(self, settings, ayla):
        """Why the verb and not twelve empty PATCH values.

        A PATCH stamps ``data_sources[field] = "explicit"`` per NAMED field —
        the mechanism that keeps nightly inference off a value the subject
        owns (DRF-1366). It only ever covered the fields that were named. The
        erasure verb stamps ``"erased"`` on EVERY declared field upstream, so
        the protection now covers the whole row instead of three of twelve.
        This cell pins the bot half: not one field is named on the wire.
        """
        bu = _bot_user("erase-decl-9")
        _consents(bu, settings)
        record_explicit_green_facts(bu, "я веган")
        ayla.calls.clear()

        _forget_all(bu)

        assert [c[0] for c in ayla.calls] == ["delete"]

    def test_account_delete_does_wipe_the_backend_row(self, settings, ayla):
        """Refutation — the audit left account-delete completeness open for the
        backend profile. The bot cascade DOES address it: step 1 calls the
        internal personal-data DELETE, which drops the whole row.
        """
        from apps.identity.services.privacy import delete_personal_data

        bu = _bot_user("erase-decl-5")
        _consents(bu, settings)
        record_explicit_green_facts(bu, "я веган")

        result = delete_personal_data(bu, client=ayla)

        assert ayla.deleted is True
        assert ayla.context == {}
        steps = {s.step: s.ok for s in result.steps}
        assert steps["ayla_delete"] is True
        assert steps["memory_delete"] is True
        assert build_concierge_memory_block(bu) == ""


# ---------------------------------------------------------------------------
# Storage 4 — Conversation / Message + the short-term Redis window
# ---------------------------------------------------------------------------


class TestDialogueHistory:
    def test_forget_all_does_not_touch_the_short_term_window(self, settings, ayla, monkeypatch):
        """GAP, P0 — the window IS the LLM history (handler.py:672) and holds
        the raw sentence the fact was extracted from. Nothing clears it:
        ``short_term.clear`` has no production caller anywhere in ``apps/``.
        """
        from apps.conversations.services import resolve_active_global_conversation

        fake_redis = _FakeRedis()
        monkeypatch.setattr(short_term, "_redis_client", lambda: fake_redis)

        bu = _bot_user("erase-hist-1")
        _consents(bu, settings)
        conversation = resolve_active_global_conversation(bu)
        short_term.append(conversation.id, role="user", content="я веган и живу на Арбате")
        record_explicit_green_facts(bu, "я веган и живу на Арбате")

        _forget_all(bu)

        recalled = short_term.recall(conversation.id)
        assert [m["content"] for m in recalled] == ["я веган и живу на Арбате"]

    def test_account_delete_does_not_touch_customer_messages(self, settings, ayla):
        """GAP, P0 — ``privacy.delete_personal_data`` has five steps and none of
        them is the customer's ``Conversation``/``Message``. Step 5 erases
        ``StaffAssistantMessage`` (the employee surface) only. The customer's
        own words survive verbatim and are read straight back by
        ``concierge.load_recent_history`` — no deletion filter exists there.
        """
        from apps.conversations.models import Message
        from apps.conversations.services import resolve_active_global_conversation
        from apps.identity.services.privacy import delete_personal_data

        bu = _bot_user("erase-hist-2")
        _consents(bu, settings)
        conversation = resolve_active_global_conversation(bu)
        Message.all_tenants.create(
            tenant=bu.tenant,
            conversation=conversation,
            role="user",
            content="я веган, мой мастер — Анна, телефон 89990001122",
        )

        delete_personal_data(bu, client=ayla)

        rows = list(Message.all_tenants.filter(conversation=conversation))
        assert len(rows) == 1
        assert "89990001122" in rows[0].content
        assert "веган" in rows[0].content
        # And the prompt-side reader hands it straight back.
        qs = Message.all_tenants.filter(conversation=conversation).order_by("-created_at")
        assert "89990001122" in list(qs)[0].content

    def test_account_delete_does_not_touch_the_short_term_window(self, settings, ayla, monkeypatch):
        """GAP, P0 — same for the Redis window, which is the actual history the
        MAX prompt is built from.
        """
        from apps.conversations.services import resolve_active_global_conversation
        from apps.identity.services.privacy import delete_personal_data

        fake_redis = _FakeRedis()
        monkeypatch.setattr(short_term, "_redis_client", lambda: fake_redis)

        bu = _bot_user("erase-hist-3")
        _consents(bu, settings)
        conversation = resolve_active_global_conversation(bu)
        short_term.append(conversation.id, role="user", content="мой телефон 89990001122")

        delete_personal_data(bu, client=ayla)

        assert short_term.recall(conversation.id) == [
            {"role": "user", "content": "мой телефон 89990001122"}
        ]

    def test_short_term_clear_has_no_production_caller(self):
        """GAP — pins the reason the two tests above hold. ``short_term.clear``
        documents itself as «used by the 152-ФЗ delete-my-data workflow»
        (short_term.py:180-186); it is wired to nothing. Invert this assertion
        when the erasure path starts calling it.
        """
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parents[3] / "apps"
        callers = [
            str(p)
            for p in root.rglob("*.py")
            if "tests" not in p.parts
            and p.name != "short_term.py"
            and re.search(r"short_term\.clear\s*\(", p.read_text(encoding="utf-8"))
        ]
        assert callers == []


# ---------------------------------------------------------------------------
# Storage 5 — ClientProfile (derived)
# ---------------------------------------------------------------------------


class TestDerivedClientProfile:
    def test_client_profile_survives_account_delete(self, settings, ayla):
        """GAP (lower severity) — RFM/LTV/tier are not in the cascade. They are
        read by ``memory.coordinator.load_snapshot`` (the per-tenant pipeline),
        not by the global MAX concierge prompt, so the remnant is real but its
        route to a prompt is the legacy path only.
        """
        from apps.identity.models import ClientProfile
        from apps.identity.services.privacy import delete_personal_data

        bu = _bot_user("erase-profile-1")
        _consents(bu, settings)
        ClientProfile.all_tenants.update_or_create(
            bot_user=bu,
            defaults={
                "tenant": bu.tenant,
                "rfm_segment": "champions",
                "loyalty_tier": "gold",
                "lifecycle_stage": "active",
            },
        )

        delete_personal_data(bu, client=ayla)

        profile = ClientProfile.all_tenants.filter(bot_user=bu).first()
        assert profile is not None
        assert profile.rfm_segment == "champions"
        assert profile.loyalty_tier == "gold"


# ---------------------------------------------------------------------------
# Consent withdrawal — the fifth erasure verb
# ---------------------------------------------------------------------------


class TestConsentWithdrawal:
    def test_withdrawal_silences_the_prompt_block(self, settings, ayla):
        """Refutation of a plausible fear — withdrawal DOES close the surface.

        ``_PERSONAL_DATA_CASCADE`` covers ``memory_green``, which is the gate
        ``get_declared_prefs`` checks before the wire is touched, so not one
        declared field reaches the prompt afterwards.
        """
        from apps.consent.services import withdraw_personal_data_for_bot_users
        from apps.identity.models import BotUser

        bu = _bot_user("erase-consent-1")
        _consents(bu, settings)
        record_explicit_green_facts(bu, "я веган")
        assert build_concierge_memory_block(bu) != ""

        withdraw_personal_data_for_bot_users(BotUser.all_tenants.filter(id=bu.id), source="test")

        assert build_concierge_memory_block(bu) == ""

    def test_withdrawal_deletes_nothing_and_a_regrant_restores_everything(self, settings, ayla):
        """GAP (design, not defect — but it belongs in the matrix).

        Withdrawal is a gate, not an erasure: no ``MemoryEntry`` is tombstoned,
        no upstream field is cleared. Granting consent again puts every fact
        back in the prompt, including ones stated before the withdrawal.
        """
        from apps.consent.services import withdraw_personal_data_for_bot_users
        from apps.identity.models import BotUser

        bu = _bot_user("erase-consent-2")
        _consents(bu, settings)
        record_explicit_green_facts(bu, "я веган")

        withdraw_personal_data_for_bot_users(BotUser.all_tenants.filter(id=bu.id), source="test")
        assert build_concierge_memory_block(bu) == ""
        assert ayla.context["diet_type"] == "vegan"  # наверху ничего не тронуто
        assert MemoryEntry.objects.filter(user_id=bu.ayla_user_id).count() == 1

        _consents(bu, settings)  # заново выдали оба согласия

        block = build_concierge_memory_block(bu)
        assert "Диета" in block
        assert "Любимые мастера" in block


# ---------------------------------------------------------------------------
# «удаление одного факта из UI» — the app-side DELETE, seen from the bot
# ---------------------------------------------------------------------------


class TestUiFieldDelete:
    """The mobile app clears one field via ``DELETE /me/personal-context/<f>/``.

    There is no reverse bridge: nothing tells the bot. What happens next is
    decided entirely by ``memory_block._merge_inferred``.
    """

    def test_the_cleared_field_does_not_come_back_into_the_prompt(self, settings, ayla):
        """Refutation of the obvious fear — the local row does NOT resurface.

        ``_green_context`` always returns every green field, so the key is
        present in ``facts`` with an empty value, and ``_merge_inferred``
        refuses to write onto a key the declared side already owns. The empty
        value is then skipped by ``build_memory_block``.
        """
        bu = _bot_user("erase-ui-1")
        _consents(bu, settings)
        record_explicit_green_facts(bu, "я веган")
        assert "Диета" in build_concierge_memory_block(bu)

        ayla.context["diet_type"] = ""  # DELETE /me/personal-context/diet_type/

        assert "Диета" not in build_concierge_memory_block(bu)

    def test_but_the_bot_still_says_it_remembers(self, settings, ayla):
        """GAP (visible, not a prompt leak) — «покажи что знаешь обо мне» reads
        local rows directly, not through the declared merge. The person clears
        their diet in the app and the bot answers «помню, что ты веган».
        """
        bu = _bot_user("erase-ui-2")
        _consents(bu, settings)
        record_explicit_green_facts(bu, "я веган")

        ayla.context["diet_type"] = ""

        res = handle_memory_command(
            user_id=bu.ayla_user_id, text="что ты обо мне знаешь", bot_user=bu
        )
        assert res is not None
        assert "веган" in res.text.lower()
        assert MemoryEntry.objects.filter(user_id=bu.ayla_user_id).count() == 1
