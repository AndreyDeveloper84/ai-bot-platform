"""Сроки производной памяти Ф4 (DRF-2782).

Решение владельца 05.10: неподтверждённые предположения — 30 дней;
подтверждённые — 180 дней, затем повторное подтверждение или удаление.
Срок ставит писатель в ``expires_at``; свип исполняет наступивший:

* неподтверждённый вывод с прошедшим сроком — надгробие ``ttl_purge``;
* подтверждённый — строка цела, ``status='expired'`` («нужно
  переподтвердить»);
* срок не наступил — цел; явный факт человека, жёлтая/красная зона,
  неизвестное происхождение, «забудь всё» — не трогаются;
* выключатель закрыт по умолчанию.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.conf import settings
from django.test import override_settings

from apps.audit.models import AuditLog
from apps.identity.models import MemoryEntry, UserPersonalContext
from apps.identity.services.memory_deleter import sweep_expired_inferences
from apps.identity.tasks import memory_inference_ttl_sweep

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 5, 2, 50, tzinfo=UTC)
CONFIRMED = "user_confirmed_inference"


def _upc(*, forgotten: bool = False) -> UserPersonalContext:
    return UserPersonalContext.objects.create(
        user_id=uuid.uuid4(),
        forget_all_requested_at=NOW - timedelta(days=1) if forgotten else None,
    )


def _inference(
    upc: UserPersonalContext,
    *,
    expires_in_days: float,
    provenance: str | None = None,
    status: str | None = "active",
) -> MemoryEntry:
    """Зелёный вывод Ф4 с ``expires_at`` = NOW + ``expires_in_days``."""
    entry = MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_INFERRED,
        last_inferred_at=NOW - timedelta(days=40),
        content={"key": "preferred_time_slots", "value": "evening"},
    )
    MemoryEntry.objects.filter(pk=entry.pk).update(
        provenance=provenance,
        status=status,
        expires_at=NOW + timedelta(days=expires_in_days),
    )
    entry.refresh_from_db()
    return entry


def _sweep(**kwargs):
    return sweep_expired_inferences(now=NOW, **kwargs)


def _reload(entry: MemoryEntry) -> MemoryEntry:
    entry.refresh_from_db()
    return entry


class TestUnconfirmedInferences:
    def test_an_expired_unconfirmed_inference_is_tombstoned(self) -> None:
        upc = _upc()
        expired = _inference(upc, expires_in_days=-1)
        fresh = _inference(upc, expires_in_days=5)

        result = _sweep()

        assert _reload(fresh).soft_deleted_at is None
        row = _reload(expired)
        assert row.soft_deleted_at == NOW
        assert row.delete_requested_at == NOW
        assert row.deletion_reason == "ttl_purge"
        assert row.status == "deleted"
        assert result.unconfirmed_deleted == 1
        assert result.confirmed_flagged == 0


class TestConfirmedInferences:
    def test_an_expired_confirmed_inference_is_flagged_not_deleted(self) -> None:
        upc = _upc()
        expired = _inference(upc, expires_in_days=-1, provenance=CONFIRMED)
        fresh = _inference(upc, expires_in_days=100, provenance=CONFIRMED)

        result = _sweep()

        assert _reload(fresh).status == "active"
        row = _reload(expired)
        assert row.status == "expired"
        assert row.updated_at == NOW
        assert row.soft_deleted_at is None
        assert row.deletion_reason is None
        assert result.confirmed_flagged == 1
        assert result.unconfirmed_deleted == 0

    def test_a_confirmed_row_without_a_status_is_flagged_too(self) -> None:
        legacy = _inference(_upc(), expires_in_days=-1, provenance=CONFIRMED, status=None)

        _sweep()

        assert _reload(legacy).status == "expired"

    def test_an_already_flagged_row_is_not_touched_again(self) -> None:
        upc = _upc()
        flagged = _inference(upc, expires_in_days=-10, provenance=CONFIRMED, status="expired")
        MemoryEntry.objects.filter(pk=flagged.pk).update(updated_at=NOW - timedelta(days=9))
        due = _inference(upc, expires_in_days=-1, provenance=CONFIRMED)

        result = _sweep()

        assert _reload(due).status == "expired"
        assert _reload(flagged).updated_at == NOW - timedelta(days=9)
        assert result.confirmed_flagged == 1


class TestWhatTheSweepNeverTouches:
    def test_an_explicit_fact_with_a_past_expiry_stays(self) -> None:
        upc = _upc()
        stated = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_EXPLICIT,
            provenance=MemoryEntry.PROVENANCE_USER_STATED,
            content={"key": "diet", "value": "vegan"},
        )
        MemoryEntry.objects.filter(pk=stated.pk).update(expires_at=NOW - timedelta(days=1))
        inferred = _inference(upc, expires_in_days=-1)

        _sweep()

        assert _reload(inferred).soft_deleted_at is not None
        stated = _reload(stated)
        assert stated.soft_deleted_at is None
        assert stated.status != "expired"

    def test_a_legacy_explicit_fact_without_provenance_stays(self) -> None:
        """Явные строки до обратного заполнения несут ``provenance IS NULL`` —
        ровно как неподтверждённый вывод. Различает их только ``source``."""
        upc = _upc()
        legacy = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_EXPLICIT,
            content={"key": "diet", "value": "vegan"},
        )
        MemoryEntry.objects.filter(pk=legacy.pk).update(
            provenance=None, status="active", expires_at=NOW - timedelta(days=1)
        )
        inferred = _inference(upc, expires_in_days=-1)

        _sweep()

        assert _reload(inferred).soft_deleted_at is not None
        assert _reload(legacy).soft_deleted_at is None

    def test_a_yellow_inference_is_left_to_the_zone_sweep(self) -> None:
        upc = _upc()
        yellow = _inference(upc, expires_in_days=-1)
        MemoryEntry.objects.filter(pk=yellow.pk).update(
            sensitivity_zone=MemoryEntry.SENSITIVITY_YELLOW, consent_at=NOW - timedelta(days=60)
        )
        green = _inference(upc, expires_in_days=-1)

        _sweep()

        assert _reload(green).soft_deleted_at is not None
        assert _reload(yellow).soft_deleted_at is None

    def test_an_unknown_provenance_is_not_guessed(self) -> None:
        upc = _upc()
        odd = _inference(upc, expires_in_days=-1, provenance="user_stated")
        plain = _inference(upc, expires_in_days=-1)

        _sweep()

        assert _reload(plain).soft_deleted_at is not None
        odd = _reload(odd)
        assert odd.soft_deleted_at is None
        assert odd.status == "active"

    def test_a_superseded_proposal_is_history_and_stays(self) -> None:
        upc = _upc()
        corrected = _inference(upc, expires_in_days=-1, status="superseded")
        plain = _inference(upc, expires_in_days=-1)

        result = _sweep()

        assert _reload(plain).soft_deleted_at is not None
        corrected = _reload(corrected)
        assert corrected.soft_deleted_at is None
        assert corrected.status == "superseded"
        assert result.unconfirmed_deleted == 1

    def test_forget_all_is_left_to_its_own_path(self) -> None:
        held = _inference(_upc(forgotten=True), expires_in_days=-1)
        ordinary = _inference(_upc(), expires_in_days=-1)

        result = _sweep()

        assert _reload(ordinary).soft_deleted_at is not None
        assert _reload(held).soft_deleted_at is None
        assert result.unconfirmed_deleted == 1

    def test_an_already_deleted_inference_keeps_its_reason(self) -> None:
        upc = _upc()
        gone = _inference(upc, expires_in_days=-1)
        earlier = NOW - timedelta(days=3)
        MemoryEntry.objects.filter(pk=gone.pk).update(
            delete_requested_at=earlier,
            soft_deleted_at=earlier,
            deletion_reason="user_request_miniapp",
            status="deleted",
        )
        live = _inference(upc, expires_in_days=-1)

        result = _sweep()

        assert _reload(live).deletion_reason == "ttl_purge"
        assert _reload(gone).deletion_reason == "user_request_miniapp"
        assert result.unconfirmed_deleted == 1


class TestRunShape:
    def test_the_batch_takes_the_oldest_expiry_first(self) -> None:
        upc = _upc()
        oldest = _inference(upc, expires_in_days=-30)
        middle = _inference(upc, expires_in_days=-20)
        newest = _inference(upc, expires_in_days=-10)

        result = _sweep(limit=2)

        assert _reload(newest).soft_deleted_at is None
        assert _reload(oldest).soft_deleted_at is not None
        assert _reload(middle).soft_deleted_at is not None
        assert result.unconfirmed_deleted == 2

    def test_a_second_run_changes_nothing(self) -> None:
        upc = _upc()
        _inference(upc, expires_in_days=-1)
        _inference(upc, expires_in_days=-1, provenance=CONFIRMED)

        first = _sweep()
        second = _sweep()

        assert first.as_summary() == {"unconfirmed_deleted": 1, "confirmed_flagged": 1, "users": 1}
        assert second.as_summary() == {"unconfirmed_deleted": 0, "confirmed_flagged": 0, "users": 0}

    def test_the_run_is_audited_by_counts(self) -> None:
        _inference(_upc(), expires_in_days=-1)
        _inference(_upc(), expires_in_days=-1, provenance=CONFIRMED)

        _sweep()

        row = AuditLog.all_tenants.get(action="memory.inferences_expired")
        assert row.payload == {
            "unconfirmed_deleted": 1,
            "confirmed_flagged": 1,
            "users": 2,
            "reason": "ttl_purge",
        }

    def test_an_idle_run_writes_no_audit_row(self) -> None:
        fresh = _inference(_upc(), expires_in_days=5)

        result = _sweep()

        assert _reload(fresh).status == "active"
        assert result.as_summary() == {"unconfirmed_deleted": 0, "confirmed_flagged": 0, "users": 0}
        assert AuditLog.all_tenants.filter(action="memory.inferences_expired").count() == 0


class TestTheSwitch:
    def test_the_switch_is_closed_by_default(self) -> None:
        assert settings.MEMORY_INFERENCE_TTL_SWEEP_ENABLED is False

    def test_the_task_is_inert_while_the_switch_is_closed(self) -> None:
        expired = _inference(_upc(), expires_in_days=-400)

        with override_settings(MEMORY_INFERENCE_TTL_SWEEP_ENABLED=False):
            summary = memory_inference_ttl_sweep()

        assert summary == {"mode": "disabled"}
        assert _reload(expired).soft_deleted_at is None

    def test_the_open_switch_runs_the_sweep(self) -> None:
        upc = _upc()
        expired = _inference(upc, expires_in_days=-400)
        fresh = _inference(upc, expires_in_days=400)

        with override_settings(MEMORY_INFERENCE_TTL_SWEEP_ENABLED=True):
            summary = memory_inference_ttl_sweep()

        assert summary["unconfirmed_deleted"] == 1
        assert _reload(fresh).soft_deleted_at is None
        assert _reload(expired).soft_deleted_at is not None

    def test_the_task_is_scheduled_nightly(self) -> None:
        entry = settings.CELERY_BEAT_SCHEDULE["identity_memory_inference_ttl_sweep"]
        assert entry["task"] == "apps.identity.tasks.memory_inference_ttl_sweep"
        assert entry["schedule"].hour == {2}
        assert entry["schedule"].minute == {50}
