"""Key-policy conflict-resolution tests (read-side supersession).

Write path keeps history (a changed fact lands as a NEW live row, the old
one stays live); these tests pin the READ-side rule: consumers never
surface mutually exclusive values of one key, an explicit correction
beats a fresher inferred row, and legacy conflicting rows resolve
deterministically.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.identity.models import MemoryEntry, UserPersonalContext
from apps.identity.services import memory_key_policy
from apps.identity.services.memory_key_policy import (
    CARDINALITY_MULTI,
    read_current_view,
    select_current_facts,
)
from apps.identity.services.memory_reader import read_green_entries
from apps.orchestrator import memory_block
from apps.orchestrator.memory_block import build_concierge_memory_block
from apps.persona.memory_surface import render_current_personal_context

pytestmark = pytest.mark.django_db

_T0 = timezone.now() - timedelta(days=10)


def _upc() -> UserPersonalContext:
    return UserPersonalContext.objects.create(user_id=uuid.uuid4())


def _green(upc, *, source=MemoryEntry.SOURCE_EXPLICIT, created_at=None, **overrides):
    kwargs = dict(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=source,
        kind="lifestyle",
        content={"key": "diet", "value": "vegan"},
    )
    if source in (MemoryEntry.SOURCE_INFERRED, MemoryEntry.SOURCE_SIGNAL):
        kwargs.setdefault("last_inferred_at", timezone.now())  # CHECK 1
    kwargs.update(overrides)
    entry = MemoryEntry.objects.create(**kwargs)
    if created_at is not None:  # auto_now_add — override post-create
        MemoryEntry.objects.filter(pk=entry.pk).update(created_at=created_at)
        entry.created_at = created_at
    return entry


def _block_for(user_id, monkeypatch) -> str:
    """build_concierge_memory_block with consent gates open, no declared prefs."""

    monkeypatch.setattr(
        memory_block,
        "get_declared_prefs",
        lambda bot_user: SimpleNamespace(
            status=memory_block.GateStatus.OK, context=SimpleNamespace(context={})
        ),
    )
    monkeypatch.setattr("apps.consent.memory.can_store_green_memory", lambda bot_user: True)
    return build_concierge_memory_block(SimpleNamespace(ayla_user_id=user_id))


class TestExplicitCorrection:
    def test_block_and_surface_show_only_the_new_value(self, monkeypatch):
        upc = _upc()
        _green(upc, created_at=_T0)  # vegan (stale)
        _green(
            upc,
            created_at=_T0 + timedelta(days=1),
            content={"key": "diet", "value": "keto", "display": "сидит на кето-диете"},
        )

        block = _block_for(upc.user_id, monkeypatch)
        assert "Диета: keto" in block
        assert "vegan" not in block

        out = render_current_personal_context(upc.user_id)
        assert out is not None
        assert "кето" in out
        assert "веганского" not in out


class TestInferredDoesNotDisplaceExplicit:
    def test_fresher_inferred_loses_to_explicit(self, monkeypatch):
        upc = _upc()
        _green(upc, created_at=_T0)  # explicit vegan (older)
        _green(
            upc,
            source=MemoryEntry.SOURCE_INFERRED,
            created_at=_T0 + timedelta(days=5),  # fresher inferred keto
            content={"key": "diet", "value": "keto"},
        )

        selected = select_current_facts(read_green_entries(upc.user_id))
        assert [f.content["value"] for f in selected] == ["vegan"]

        block = _block_for(upc.user_id, monkeypatch)
        assert "Диета: vegan" in block
        assert "keto" not in block

        out = render_current_personal_context(upc.user_id)
        assert out is not None
        assert "веганского" in out
        assert "keto" not in out


class TestMultiValueKey:
    def test_multi_values_coexist_in_block_and_surface(self, monkeypatch):
        monkeypatch.setitem(
            memory_key_policy._KEY_CARDINALITY, "preferred_districts", CARDINALITY_MULTI
        )
        upc = _upc()
        _green(
            upc,
            created_at=_T0,
            content={
                "key": "preferred_districts",
                "value": "Центр",
                "display": "любит Центр",
            },
        )
        _green(
            upc,
            created_at=_T0 + timedelta(days=1),
            content={
                "key": "preferred_districts",
                "value": "Набережная",
                "display": "любит Набережную",
            },
        )

        view = read_current_view(upc.user_id)
        assert [f.content["value"] for f in view.green_facts] == ["Центр", "Набережная"]

        block = _block_for(upc.user_id, monkeypatch)
        assert "Предпочитает районы: Центр, Набережная" in block

        out = render_current_personal_context(upc.user_id)
        assert out is not None
        assert "любит Центр" in out
        assert "любит Набережную" in out


class TestSingleKeyConsistency:
    def test_resolver_never_returns_conflicting_values(self):
        upc = _upc()
        _green(upc, created_at=_T0)  # explicit vegan
        _green(
            upc,
            created_at=_T0 + timedelta(days=1),
            content={"key": "diet", "value": "keto"},
        )
        _green(
            upc,
            source=MemoryEntry.SOURCE_INFERRED,
            created_at=_T0 + timedelta(days=2),
            content={"key": "diet", "value": "vegetarian"},
        )

        selected = select_current_facts(read_green_entries(upc.user_id))
        by_key: dict[str, list[str]] = {}
        for fact in selected:
            key = fact.content.get("key")
            if memory_key_policy.key_cardinality(key) == CARDINALITY_MULTI:
                continue
            by_key.setdefault(key, []).append(fact.content.get("value"))
        # The freshest explicit row wins; no key carries two live values.
        assert by_key == {"diet": ["keto"]}


class TestLegacyConflictDeterminism:
    def test_equal_created_at_rows_resolve_stably(self):
        upc = _upc()
        stale = _green(upc, content={"key": "diet", "value": "vegan"})
        other = _green(upc, content={"key": "diet", "value": "keto"})
        same_ts = _T0 + timedelta(days=3)
        MemoryEntry.objects.filter(pk__in=[stale.pk, other.pk]).update(created_at=same_ts)

        first = select_current_facts(read_green_entries(upc.user_id))
        second = select_current_facts(read_green_entries(upc.user_id))

        assert [f.pk for f in first] == [f.pk for f in second]
        # Stable tiebreak at equal created_at: the larger entry id wins.
        expected = stale if stale.id.int > other.id.int else other
        assert [f.pk for f in first] == [expected.pk]

    def test_view_is_stable_across_reads(self):
        upc = _upc()
        _green(upc, created_at=_T0)
        _green(upc, created_at=_T0 + timedelta(days=1), content={"key": "diet", "value": "keto"})

        one = read_current_view(upc.user_id)
        two = read_current_view(upc.user_id)
        assert [f.content for f in one.green_facts] == [f.content for f in two.green_facts]
