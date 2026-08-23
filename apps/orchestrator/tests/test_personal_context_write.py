"""Explicit green-fact write orchestration tests (M-B2 / #1099)."""

from __future__ import annotations

import uuid

import pytest

from apps.consent.services import record_global_consent
from apps.identity.models import MemoryEntry, UserPersonalContext
from apps.identity.services import resolve_or_create_global_bot_user
from apps.identity.services.memory_reader import read_personal_context
from apps.orchestrator.memory.personal_context import record_explicit_green_facts

pytestmark = pytest.mark.django_db(transaction=True)


def _bot_user(uid="mbw-1", *, ayla=True):
    return resolve_or_create_global_bot_user(
        channel="max",
        channel_user_id=uid,
        ayla_user_id=uuid.uuid4() if ayla else None,
    )


def _consent(bot_user, settings):
    settings.STRICT_TENANT_SCOPE = "strict"
    record_global_consent(bot_user, source="welcome")


class TestRecordExplicitGreenFacts:
    def test_no_ayla_user_id_is_noop(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        bu = _bot_user("mbw-noayla", ayla=False)
        assert record_explicit_green_facts(bu, "я веган") == 0
        assert MemoryEntry.objects.count() == 0

    def test_no_consent_is_noop(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        bu = _bot_user("mbw-noconsent")
        # ayla_user_id present but PERSONAL_DATA never granted.
        assert record_explicit_green_facts(bu, "я веган") == 0
        assert MemoryEntry.objects.count() == 0

    def test_writes_green_fact_with_consent(self, settings):
        bu = _bot_user("mbw-ok")
        _consent(bu, settings)
        n = record_explicit_green_facts(bu, "кстати, я веган")
        assert n == 1
        entry = MemoryEntry.objects.get(user_id=bu.ayla_user_id)
        assert entry.sensitivity_zone == MemoryEntry.SENSITIVITY_GREEN
        assert entry.source == MemoryEntry.SOURCE_EXPLICIT
        assert entry.consent_at is None  # green: service-contract basis
        assert entry.content == {"key": "diet", "value": "vegan", "diet_type": "vegan"}
        # UPC parent auto-created.
        assert UserPersonalContext.objects.filter(user_id=bu.ayla_user_id).exists()

    def test_is_idempotent(self, settings):
        bu = _bot_user("mbw-dedup")
        _consent(bu, settings)
        assert record_explicit_green_facts(bu, "я веган") == 1
        assert record_explicit_green_facts(bu, "я веган") == 0  # already stored
        assert MemoryEntry.objects.filter(user_id=bu.ayla_user_id).count() == 1

    def test_written_fact_is_surfaced_back(self, settings):
        bu = _bot_user("mbw-loop")
        _consent(bu, settings)
        record_explicit_green_facts(bu, "я вегетарианка")
        view = read_personal_context(bu.ayla_user_id)
        assert any(
            f.content == {"key": "diet", "value": "vegetarian", "diet_type": "vegetarian"}
            for f in view.green_facts
        )

    def test_observe_log_has_count_not_value(self, settings, caplog):
        import logging

        bu = _bot_user("mbw-log")
        _consent(bu, settings)
        with caplog.at_level(logging.INFO, logger="apps.orchestrator.memory.personal_context"):
            record_explicit_green_facts(bu, "я веган")
        line = next(r.getMessage() for r in caplog.records if "green_written" in r.getMessage())
        assert "count=1" in line
        assert "vegan" not in line  # observe signal never carries the fact value

    def test_no_extractable_fact_is_noop(self, settings):
        bu = _bot_user("mbw-nofact")
        _consent(bu, settings)
        assert record_explicit_green_facts(bu, "хочу маникюр в Пензе") == 0
        assert MemoryEntry.objects.count() == 0

    def test_does_not_accrete_on_forgotten_upc(self, settings):
        # forget-all (soft-deleted UPC) but consent still active → no new write.
        from django.utils import timezone

        bu = _bot_user("mbw-forgotten")
        _consent(bu, settings)
        UserPersonalContext.objects.create(user_id=bu.ayla_user_id, soft_deleted_at=timezone.now())
        assert record_explicit_green_facts(bu, "я веган") == 0
        assert MemoryEntry.objects.filter(user_id=bu.ayla_user_id).count() == 0

    def test_does_not_accrete_during_forget_all_window(self, settings):
        from django.utils import timezone

        bu = _bot_user("mbw-forgetwin")
        _consent(bu, settings)
        UserPersonalContext.objects.create(
            user_id=bu.ayla_user_id, forget_all_requested_at=timezone.now()
        )
        assert record_explicit_green_facts(bu, "я веган") == 0
        assert MemoryEntry.objects.filter(user_id=bu.ayla_user_id).count() == 0
