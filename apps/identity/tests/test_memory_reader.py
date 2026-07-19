"""Green/summary memory reader tests (M-C1 / #1101)."""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone

from apps.identity.models import MemoryEntry, UserPersonalContext
from apps.identity.services.memory_reader import (
    PersonalContextView,
    get_or_create_personal_context,
    get_personal_context,
    read_personal_context,
)

pytestmark = pytest.mark.django_db


def _green(upc, **overrides):
    kwargs = dict(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        kind="lifestyle",
        content={"key": "diet", "value": "vegan"},
    )
    kwargs.update(overrides)
    return MemoryEntry.objects.create(**kwargs)


class TestReadPersonalContext:
    def test_no_upc_returns_empty_view(self):
        view = read_personal_context(uuid.uuid4())
        assert isinstance(view, PersonalContextView)
        assert view.is_empty()

    def test_surfaces_summary_and_green_facts(self):
        upc = UserPersonalContext.objects.create(
            user_id=uuid.uuid4(), summary="  Любит вечерние слоты  "
        )
        _green(upc, content={"key": "diet", "value": "vegan"})
        view = read_personal_context(upc.user_id)
        assert view.summary == "Любит вечерние слоты"  # trimmed
        assert len(view.green_facts) == 1
        assert view.green_facts[0].content == {"key": "diet", "value": "vegan"}
        assert view.green_facts[0].kind == "lifestyle"
        assert not view.is_empty()

    def test_blank_summary_becomes_none(self):
        upc = UserPersonalContext.objects.create(user_id=uuid.uuid4(), summary="   ")
        view = read_personal_context(upc.user_id)
        assert view.summary is None
        assert view.is_empty()

    def test_excludes_soft_deleted_and_delete_requested(self):
        upc = UserPersonalContext.objects.create(user_id=uuid.uuid4())
        _green(upc, content={"key": "keep"})
        _green(
            upc,
            content={"key": "withdrawn"},
            soft_deleted_at=timezone.now(),
            delete_requested_at=timezone.now(),
            deletion_reason=MemoryEntry.DELETION_REASON_USER_DELETE,
        )
        view = read_personal_context(upc.user_id)
        assert [f.content for f in view.green_facts] == [{"key": "keep"}]

    def test_excludes_non_green_zones(self):
        upc = UserPersonalContext.objects.create(user_id=uuid.uuid4())
        _green(upc, content={"key": "green_fact"})
        # A yellow row (consent_at set to satisfy CHECK 2) must NOT surface.
        MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_YELLOW,
            source=MemoryEntry.SOURCE_EXPLICIT,
            consent_at=timezone.now(),
            content={"key": "yellow_fact"},
        )
        view = read_personal_context(upc.user_id)
        assert [f.content for f in view.green_facts] == [{"key": "green_fact"}]

    def test_forgotten_upc_returns_empty(self):
        upc = UserPersonalContext.objects.create(
            user_id=uuid.uuid4(), summary="старое", soft_deleted_at=timezone.now()
        )
        _green(upc)
        view = read_personal_context(upc.user_id)
        assert view.is_empty()


class TestUpcHelpers:
    def test_get_personal_context_absent(self):
        assert get_personal_context(uuid.uuid4()) is None

    def test_get_personal_context_skips_forgotten(self):
        upc = UserPersonalContext.objects.create(
            user_id=uuid.uuid4(), soft_deleted_at=timezone.now()
        )
        assert get_personal_context(upc.user_id) is None

    def test_get_personal_context_skips_forget_all_window(self):
        # forget-all requested but sweep not yet run (soft_deleted_at still NULL).
        upc = UserPersonalContext.objects.create(
            user_id=uuid.uuid4(), forget_all_requested_at=timezone.now()
        )
        assert get_personal_context(upc.user_id) is None

    def test_read_empty_during_forget_all_window(self):
        upc = UserPersonalContext.objects.create(
            user_id=uuid.uuid4(), summary="старое", forget_all_requested_at=timezone.now()
        )
        _green(upc)
        assert read_personal_context(upc.user_id).is_empty()

    def test_get_or_create_is_idempotent(self):
        uid = uuid.uuid4()
        a = get_or_create_personal_context(uid)
        b = get_or_create_personal_context(uid)
        assert a.user_id == b.user_id == uid
        assert UserPersonalContext.objects.filter(user_id=uid).count() == 1
