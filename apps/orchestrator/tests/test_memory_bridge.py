"""DRF-1261 — supersession on correction + the Ayla declared-prefs bridge.

Covers the four proof steps' mechanics:
  1. a stated preference lands locally AND is bridged to Ayla (PATCH LWW);
  2. «покажи» merges bot-local facts with Ayla declared prefs;
  3. a correction supersedes the old row (reason=changed), the new is active;
  4. «забудь всё про питание» removes the domain and clears the Ayla field.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from apps.consent.models import ConsentRecord
from apps.consent.services import record_global_consent
from apps.identity.models import MemoryEntry
from apps.identity.services import resolve_or_create_global_bot_user
from apps.identity.services.memory_key_policy import read_current_view
from apps.identity.services.memory_reader import read_green_entries
from apps.integrations.ayla.personal_context_client import DeclaredContext
from apps.orchestrator.memory import ayla_bridge
from apps.orchestrator.memory.ayla_bridge import (
    bridge_candidates_to_ayla,
    clear_declared_fields,
)
from apps.orchestrator.memory.personal_context import record_explicit_green_facts
from apps.persona.memory_commands import handle_memory_command
from apps.persona.memory_extract import extract_green_facts

pytestmark = pytest.mark.django_db(transaction=True)


def _bot_user(uid="bridge-1"):
    return resolve_or_create_global_bot_user(
        channel="max", channel_user_id=uid, ayla_user_id=uuid.uuid4()
    )


def _consents(bot_user, settings):
    """Both bases: PERSONAL_DATA (local writes) + memory_green (Ayla wire)."""
    settings.STRICT_TENANT_SCOPE = "strict"
    record_global_consent(bot_user, source="welcome")
    ConsentRecord.all_tenants.create(
        tenant=bot_user.tenant,
        bot_user=bot_user,
        consent_type=ConsentRecord.ConsentType.MEMORY_GREEN,
        granted=True,
        source="test",
    )


def _no_bridge(monkeypatch):
    monkeypatch.setattr(
        "apps.orchestrator.memory.ayla_bridge.bridge_candidates_to_ayla",
        lambda bot_user, candidates: 0,
    )


class _StubClient:
    """PersonalContextHttpClient stand-in recording wire calls."""

    def __init__(self, context=None):
        self.context = context or {}
        self.calls: list[tuple] = []

    def get_context(self, *, ayla_user_id: str):
        self.calls.append(("get", ayla_user_id))
        return DeclaredContext(ayla_user_id=ayla_user_id, context=dict(self.context))

    def patch_context(self, *, ayla_user_id: str, updates: list):
        self.calls.append(("patch", ayla_user_id, updates))
        return DeclaredContext(ayla_user_id=ayla_user_id, context=dict(self.context))

    def close(self) -> None:
        pass


def _patches(client: _StubClient) -> list:
    return [c for c in client.calls if c[0] == "patch"]


class TestCorrectionSupersession:
    def test_correction_supersedes_previous_value(self, settings, monkeypatch):
        _no_bridge(monkeypatch)
        bu = _bot_user("corr-1")
        _consents(bu, settings)

        assert record_explicit_green_facts(bu, "я веган") == 1
        assert record_explicit_green_facts(bu, "я на кето") == 1

        rows = {e.content["value"]: e for e in read_green_entries(bu.ayla_user_id)}
        old, new = rows["vegan"], rows["keto"]
        assert old.status == MemoryEntry.STATUS_SUPERSEDED
        assert old.superseded_by_id == new.id
        assert old.supersession_reason == MemoryEntry.SUPERSESSION_CHANGED
        assert old.updated_at is not None
        assert new.status == MemoryEntry.STATUS_ACTIVE
        # History is kept: the superseded row is NOT tombstoned.
        assert old.soft_deleted_at is None
        # The current view surfaces exactly the new value.
        values = [f.content["value"] for f in read_current_view(bu.ayla_user_id).green_facts]
        assert values == ["keto"]

    def test_retraction_supersedes_with_none(self, settings, monkeypatch):
        _no_bridge(monkeypatch)
        bu = _bot_user("corr-2")
        _consents(bu, settings)

        record_explicit_green_facts(bu, "я веган")
        assert record_explicit_green_facts(bu, "я теперь снова ем мясо") == 1

        rows = {e.content["value"]: e for e in read_green_entries(bu.ayla_user_id)}
        assert rows["vegan"].status == MemoryEntry.STATUS_SUPERSEDED
        assert rows["vegan"].supersession_reason == MemoryEntry.SUPERSESSION_CHANGED
        assert rows["none"].status == MemoryEntry.STATUS_ACTIVE

    def test_identical_repeat_does_not_supersede(self, settings, monkeypatch):
        _no_bridge(monkeypatch)
        bu = _bot_user("corr-3")
        _consents(bu, settings)
        record_explicit_green_facts(bu, "я веган")
        assert record_explicit_green_facts(bu, "я веган") == 0  # dedup
        (only,) = read_green_entries(bu.ayla_user_id)
        assert only.status == MemoryEntry.STATUS_ACTIVE

    def test_multi_key_values_coexist(self, settings, monkeypatch):
        _no_bridge(monkeypatch)
        bu = _bot_user("corr-4")
        _consents(bu, settings)
        record_explicit_green_facts(bu, "мне удобно утром")
        record_explicit_green_facts(bu, "мне удобно вечером")
        rows = read_green_entries(bu.ayla_user_id)
        assert {e.content["value"] for e in rows} == {"morning", "evening"}
        assert all(e.status == MemoryEntry.STATUS_ACTIVE for e in rows)


class TestBridgeMapping:
    def _candidate(self, text):
        return extract_green_facts(text)

    def test_diet_type_bridged(self, settings):
        bu = _bot_user("map-1")
        _consents(bu, settings)
        client = _StubClient()
        n = bridge_candidates_to_ayla(bu, self._candidate("я веган"), client=client)
        assert n == 1
        _, _, updates = _patches(client)[0]
        assert updates == [{"field": "diet_type", "value": "vegan", "source": "explicit"}]

    def test_slots_union_merge(self, settings):
        bu = _bot_user("map-2")
        _consents(bu, settings)
        client = _StubClient(context={"preferred_time_slots": ["morning"]})
        n = bridge_candidates_to_ayla(bu, self._candidate("мне удобно вечером"), client=client)
        assert n == 1
        _, _, updates = _patches(client)[0]
        assert updates == [
            {
                "field": "preferred_time_slots",
                "value": ["morning", "evening"],
                "source": "explicit",
            }
        ]

    def test_slots_no_duplicate_when_already_declared(self, settings):
        bu = _bot_user("map-2b")
        _consents(bu, settings)
        client = _StubClient(context={"preferred_time_slots": ["evening"]})
        n = bridge_candidates_to_ayla(bu, self._candidate("мне удобно вечером"), client=client)
        assert n == 0
        assert _patches(client) == []

    def test_price_min_max_bridged(self, settings):
        bu = _bot_user("map-3")
        _consents(bu, settings)
        client = _StubClient()
        n = bridge_candidates_to_ayla(
            bu, self._candidate("ориентируюсь на бюджет от 1500 до 3000"), client=client
        )
        assert n == 2
        _, _, updates = _patches(client)[0]
        assert updates == [
            {"field": "price_range_min", "value": "1500.00", "source": "explicit"},
            {"field": "price_range_max", "value": "3000.00", "source": "explicit"},
        ]

    def test_retraction_clears_diet_type(self, settings):
        bu = _bot_user("map-4")
        _consents(bu, settings)
        client = _StubClient()
        n = bridge_candidates_to_ayla(bu, self._candidate("я теперь снова ем мясо"), client=client)
        assert n == 1
        _, _, updates = _patches(client)[0]
        assert updates == [{"field": "diet_type", "value": "", "source": "explicit"}]

    def test_favorite_masters_never_bridged(self, settings, caplog):
        """Contract gap: the field wants SpecialistProfile UUIDs; a stated
        NAME is stored bot-side only and the skip is logged, not silent."""
        bu = _bot_user("map-5")
        _consents(bu, settings)
        client = _StubClient()
        with caplog.at_level("INFO", logger="apps.orchestrator.memory.ayla_bridge"):
            n = bridge_candidates_to_ayla(bu, self._candidate("мой мастер — Анна"), client=client)
        assert n == 0
        assert client.calls == []
        assert "favorite_master_unbridgeable" in caplog.text

    def test_gate_closed_no_wire(self, settings):
        """No memory_green consent → the gate fires BEFORE any wire call."""
        settings.STRICT_TENANT_SCOPE = "strict"
        bu = _bot_user("map-6")
        record_global_consent(bu, source="welcome")  # PERSONAL_DATA only
        client = _StubClient()
        assert bridge_candidates_to_ayla(bu, self._candidate("я веган"), client=client) == 0
        assert client.calls == []


class TestBridgeCalledFromWritePath:
    def test_all_extracted_candidates_offered(self, settings, monkeypatch):
        """A repeated statement still reaches the bridge (idempotent LWW
        heals a transient upstream failure) even though the local dedup
        writes nothing new."""
        bu = _bot_user("path-1")
        _consents(bu, settings)
        seen: list[list] = []
        monkeypatch.setattr(
            "apps.orchestrator.memory.ayla_bridge.bridge_candidates_to_ayla",
            lambda bot_user, candidates: seen.append(list(candidates)) or 0,
        )
        record_explicit_green_facts(bu, "я веган")
        record_explicit_green_facts(bu, "я веган")  # local dedup: 0 written
        assert len(seen) == 2
        assert all(c[0].content["value"] == "vegan" for c in seen)


class TestClearOnForget:
    def test_clear_maps_known_keys(self, settings):
        bu = _bot_user("clr-1")
        _consents(bu, settings)
        client = _StubClient()
        n = clear_declared_fields(bu, ["diet", "preferred_time_slots"], client=client)
        assert n == 2
        _, _, updates = _patches(client)[0]
        assert updates == [
            {"field": "diet_type", "value": "", "source": "explicit"},
            {"field": "preferred_time_slots", "value": [], "source": "explicit"},
        ]

    def test_price_range_has_no_clear_encoding(self, settings, caplog):
        """Contract gap: null is rejected by the serializer and "" breaks the
        Decimal column — the skip must be logged, never guessed."""
        bu = _bot_user("clr-2")
        _consents(bu, settings)
        client = _StubClient()
        with caplog.at_level("WARNING", logger="apps.orchestrator.memory.ayla_bridge"):
            n = clear_declared_fields(bu, ["price_range"], client=client)
        assert n == 0
        assert client.calls == []
        assert "clear_skipped" in caplog.text

    def test_domain_forget_triggers_bridge_clear(self, settings, monkeypatch):
        bu = _bot_user("clr-3")
        _consents(bu, settings)
        monkeypatch.setattr(
            "apps.orchestrator.memory.ayla_bridge.bridge_candidates_to_ayla",
            lambda bot_user, candidates: 0,
        )
        cleared: list[list] = []
        monkeypatch.setattr(
            "apps.orchestrator.memory.ayla_bridge.clear_declared_fields",
            lambda bot_user, keys: cleared.append(list(keys)) or 0,
        )
        record_explicit_green_facts(bu, "я веган")

        res = handle_memory_command(
            user_id=bu.ayla_user_id, text="забудь всё про моё питание", bot_user=bu
        )

        assert res is not None and "забыла" in res.text.lower()
        assert cleared == [["diet"]]
        assert read_green_entries(bu.ayla_user_id) == []


class TestShowMergesDeclared:
    def test_declared_only_fact_is_shown(self, settings, monkeypatch):
        """A fact that lives only Ayla-side (ask-flow wrote it) still shows —
        the person must see the FULL active memory."""
        bu = _bot_user("show-1")
        _consents(bu, settings)
        monkeypatch.setattr(
            "apps.identity.services.personal_context.get_declared_prefs",
            lambda bot_user, client=None: SimpleNamespace(
                status=ayla_bridge.GateStatus.OK,
                context=SimpleNamespace(
                    context={"diet_type": "vegan", "price_range_max": "3500.00"}
                ),
            ),
        )

        res = handle_memory_command(
            user_id=bu.ayla_user_id, text="покажи что знаешь обо мне", bot_user=bu
        )

        assert res is not None
        assert "веганского питания" in res.text
        assert "3 500" in res.text

    def test_bridged_fact_is_not_duplicated(self, settings, monkeypatch):
        """The local row and its bridged declared copy render ONCE."""
        bu = _bot_user("show-2")
        _consents(bu, settings)
        monkeypatch.setattr(
            "apps.orchestrator.memory.ayla_bridge.bridge_candidates_to_ayla",
            lambda bot_user, candidates: 0,
        )
        record_explicit_green_facts(bu, "я веган")
        monkeypatch.setattr(
            "apps.identity.services.personal_context.get_declared_prefs",
            lambda bot_user, client=None: SimpleNamespace(
                status=ayla_bridge.GateStatus.OK,
                context=SimpleNamespace(context={"diet_type": "vegan"}),
            ),
        )

        res = handle_memory_command(
            user_id=bu.ayla_user_id, text="покажи что знаешь обо мне", bot_user=bu
        )

        assert res is not None
        assert res.text.count("веганского питания") == 1
