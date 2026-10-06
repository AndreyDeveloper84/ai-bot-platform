"""Срок жёлтой и красной памяти: «использование продлевает до предела» (DRF-2774).

Каркас без чисел владельца. Узлы держат две половины:

* **как есть сейчас** — таблица категорий без сроков, ни одна категория не
  утверждена, дверь ничего не продлевает, свип прежний;
* **как будет после слова владельца** — политика категории утверждается в
  узле тестовыми числами (продление 30, предел 100 — НЕ значения владельца), и
  проверяется сама механика: продлевает, упирается в предел, не укорачивает,
  не воскрешает, уступает удалению и отзыву, не трогает исключённое.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from apps.identity.models import MemoryEntry, RedZoneAccessLog, UserPersonalContext
from apps.identity.services import memory_term
from apps.identity.services.memory_deleter import soft_delete_expired_entries
from apps.identity.services.memory_term import (
    EXTEND_BY_CONFIRMATION_ONLY,
    EXTEND_BY_USE,
    NON_USE_SURFACES,
    TERM_POLICIES,
    record_memory_use,
)

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

GREEN = MemoryEntry.SENSITIVITY_GREEN
YELLOW = MemoryEntry.SENSITIVITY_YELLOW
RED = MemoryEntry.SENSITIVITY_RED

#: Тестовые числа механики — не сроки владельца.
EXT = 30
CAP = 100


def _upc(*, forgotten: bool = False, deletion_requested: bool = False) -> UserPersonalContext:
    return UserPersonalContext.objects.create(
        user_id=uuid.uuid4(),
        forget_all_requested_at=NOW - timedelta(days=1) if forgotten else None,
        deletion_requested_at=NOW - timedelta(days=1) if deletion_requested else None,
    )


def _entry(
    upc: UserPersonalContext,
    zone: str,
    kind: str,
    *,
    age_days: int,
    expires_in_days: int,
    ttl_days: int | None = 365,
    consent_scope: str | None = None,
    tombstoned: bool = False,
    delete_requested: bool = False,
) -> MemoryEntry:
    created = NOW - timedelta(days=age_days)
    entry = MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=zone,
        kind=kind,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        consent_at=None if zone == GREEN else created,
        ttl_days=ttl_days,
        content={"key": "k", "value": "v"},
    )
    MemoryEntry.objects.filter(pk=entry.pk).update(
        created_at=created,
        last_used_at=created,
        consent_at=None if zone == GREEN else created,
        expires_at=NOW + timedelta(days=expires_in_days),
        consent_scope=consent_scope,
        soft_deleted_at=NOW - timedelta(hours=1) if tombstoned else None,
        delete_requested_at=(
            NOW - timedelta(hours=1) if (tombstoned or delete_requested) else None
        ),
        deletion_reason="user_delete" if (tombstoned or delete_requested) else None,
    )
    entry.refresh_from_db()
    return entry


@pytest.fixture
def consent():
    """Согласие зоны есть; узел видит, о какой зоне спросили."""
    with patch("apps.consent.services.has_memory_consent", return_value=True) as mocked:
        yield mocked


@pytest.fixture
def approved(monkeypatch):
    """Утвердить политику категории тестовыми числами."""

    def _approve(zone: str, kind: str) -> None:
        key = (zone, kind)
        monkeypatch.setitem(
            TERM_POLICIES,
            key,
            dataclasses.replace(
                TERM_POLICIES[key], initial_days=365, extension_days=EXT, hard_cap_days=CAP
            ),
        )

    return _approve


def _use(*entries: MemoryEntry, surface: str = "answer_cited") -> int:
    return record_memory_use(entries, surface=surface, now=NOW)


def _expiry(entry: MemoryEntry) -> datetime:
    entry.refresh_from_db()
    assert entry.expires_at is not None
    return entry.expires_at


class TestTheTableAsItStandsToday:
    def test_every_category_waits_for_the_owner_numbers(self) -> None:
        assert len(TERM_POLICIES) == 8
        for policy in TERM_POLICIES.values():
            assert policy.initial_days is None
            assert policy.extension_days is None
            assert policy.hard_cap_days is None
            assert policy.approved is False
        assert memory_term.approved_categories() == []

    def test_mental_health_and_special_categories_extend_only_by_confirmation(self) -> None:
        assert TERM_POLICIES[(RED, "symptom")].extend_by == EXTEND_BY_CONFIRMATION_ONLY
        assert TERM_POLICIES[(RED, "other")].extend_by == EXTEND_BY_CONFIRMATION_ONLY
        assert TERM_POLICIES[(RED, "contraindication")].extend_by == EXTEND_BY_USE
        assert TERM_POLICIES[(YELLOW, "relationship")].extend_by == EXTEND_BY_USE

    def test_a_category_outside_the_table_is_never_extended(self) -> None:
        assert memory_term.policy_for(YELLOW, "symptom").extend_by == "none"
        assert memory_term.policy_for(GREEN, "preference").extend_by == "none"

    def test_an_empty_policy_extends_nothing(self, consent) -> None:
        entry = _entry(_upc(), YELLOW, "relationship", age_days=10, expires_in_days=5)
        before = _expiry(entry)

        assert _use(entry) == 0

        assert _expiry(entry) == before
        assert entry.last_used_count == 0


class TestOnlyAUseSurfaceCounts:
    @pytest.mark.parametrize("surface", sorted(NON_USE_SURFACES))
    def test_a_non_use_surface_extends_nothing(self, surface, consent, approved) -> None:
        approved(YELLOW, "relationship")
        entry = _entry(_upc(), YELLOW, "relationship", age_days=10, expires_in_days=5)

        assert _use(entry, surface=surface) == 0

        assert _expiry(entry) == NOW + timedelta(days=5)

    def test_the_non_use_list_names_context_screen_export_sweep_admin(self) -> None:
        assert NON_USE_SURFACES == {
            "prompt_context",
            "memory_screen",
            "export_152fz",
            "sweep",
            "admin",
        }
        assert memory_term.USE_SURFACES == {"answer_cited", "recommendation_reason"}

    def test_an_unknown_surface_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="unknown memory use surface"):
            record_memory_use([], surface="read", now=NOW)

    def test_a_screening_answer_is_not_memory(self) -> None:
        from apps.orchestrator.body_care.screening_answer import ScreeningAnswer

        with pytest.raises(TypeError, match="ScreeningAnswer"):
            # Нарочно чужой тип: дверь должна отказать, а не молча продлить.
            record_memory_use(
                [ScreeningAnswer.__new__(ScreeningAnswer)],  # type: ignore[list-item]
                surface="answer_cited",
            )


class TestTheExtensionOnceApproved:
    def test_use_extends_to_now_plus_extension(self, consent, approved) -> None:
        approved(YELLOW, "relationship")
        entry = _entry(_upc(), YELLOW, "relationship", age_days=10, expires_in_days=5)

        assert _use(entry, surface="recommendation_reason") == 1

        assert _expiry(entry) == NOW + timedelta(days=EXT)
        assert entry.last_used_at == NOW
        assert entry.last_used_count == 1
        consent.assert_called_once_with(entry.user_id, YELLOW)

    def test_the_extension_stops_at_the_hard_cap_from_creation(self, consent, approved) -> None:
        approved(YELLOW, "relationship")
        entry = _entry(_upc(), YELLOW, "relationship", age_days=90, expires_in_days=5)

        assert _use(entry) == 1

        assert _expiry(entry) == entry.created_at + timedelta(days=CAP)
        assert _expiry(entry) == NOW + timedelta(days=10)

    def test_use_never_shortens_a_longer_term(self, consent, approved) -> None:
        approved(YELLOW, "relationship")
        entry = _entry(_upc(), YELLOW, "relationship", age_days=10, expires_in_days=200)

        assert _use(entry) == 1

        assert _expiry(entry) == NOW + timedelta(days=200)
        assert entry.last_used_count == 1

    def test_an_expired_row_is_not_resurrected(self, consent, approved) -> None:
        approved(YELLOW, "relationship")
        entry = _entry(_upc(), YELLOW, "relationship", age_days=10, expires_in_days=-1)

        assert _use(entry) == 0

        assert _expiry(entry) == NOW - timedelta(days=1)
        assert entry.last_used_count == 0

    def test_a_red_use_is_logged_as_use(self, consent, approved) -> None:
        approved(RED, "contraindication")
        upc = _upc()
        entry = _entry(upc, RED, "contraindication", age_days=10, expires_in_days=5)

        assert _use(entry) == 1

        assert _expiry(entry) == NOW + timedelta(days=EXT)
        log = RedZoneAccessLog.objects.get(memory_entry_id=entry.id)
        assert log.access_type == "use"
        assert log.user_id == upc.user_id
        assert log.accessor_role == "system_job"
        assert log.accessor_principal.startswith("memory_term:")

    def test_a_yellow_use_writes_no_red_log(self, consent, approved) -> None:
        approved(YELLOW, "relationship")
        entry = _entry(_upc(), YELLOW, "relationship", age_days=10, expires_in_days=5)

        assert _use(entry) == 1
        assert RedZoneAccessLog.objects.filter(memory_entry_id=entry.id).count() == 0


class TestDeletionAndWithdrawalWin:
    def test_a_tombstoned_row_is_untouched(self, consent, approved) -> None:
        approved(YELLOW, "relationship")
        live = _entry(_upc(), YELLOW, "relationship", age_days=10, expires_in_days=5)
        dead = _entry(
            _upc(), YELLOW, "relationship", age_days=10, expires_in_days=5, tombstoned=True
        )

        assert _use(live, dead) == 1

        assert _expiry(dead) == NOW + timedelta(days=5)
        assert dead.last_used_count == 0

    def test_a_row_with_a_delete_request_is_untouched(self, consent, approved) -> None:
        approved(YELLOW, "relationship")
        entry = _entry(
            _upc(), YELLOW, "relationship", age_days=10, expires_in_days=5, delete_requested=True
        )

        assert _use(entry) == 0
        assert _expiry(entry) == NOW + timedelta(days=5)

    @pytest.mark.parametrize("hold", ["forgotten", "deletion_requested"])
    def test_a_person_who_asked_to_erase_is_untouched(self, hold, consent, approved) -> None:
        approved(YELLOW, "relationship")
        entry = _entry(_upc(**{hold: True}), YELLOW, "relationship", age_days=10, expires_in_days=5)

        assert _use(entry) == 0
        assert _expiry(entry) == NOW + timedelta(days=5)

    def test_without_zone_consent_nothing_is_extended(self, approved) -> None:
        approved(YELLOW, "relationship")
        entry = _entry(_upc(), YELLOW, "relationship", age_days=10, expires_in_days=5)

        with patch("apps.consent.services.has_memory_consent", return_value=False) as mocked:
            assert _use(entry) == 0

        mocked.assert_called_once_with(entry.user_id, YELLOW)
        assert _expiry(entry) == NOW + timedelta(days=5)


class TestWhatTheDoorNeverExtends:
    @pytest.mark.parametrize("kind", ["symptom", "other"])
    def test_confirmation_only_categories_ignore_use(self, kind, consent, approved) -> None:
        approved(RED, kind)
        entry = _entry(_upc(), RED, kind, age_days=10, expires_in_days=5)

        assert _use(entry) == 0
        assert _expiry(entry) == NOW + timedelta(days=5)
        assert RedZoneAccessLog.objects.filter(memory_entry_id=entry.id).count() == 0

    def test_green_is_not_extended(self, consent, monkeypatch) -> None:
        # Даже с утверждённой политикой «use» — зелёная зона срока не продлевает.
        monkeypatch.setitem(
            TERM_POLICIES,
            (GREEN, "preference"),
            dataclasses.replace(
                TERM_POLICIES[(YELLOW, "preference")],
                initial_days=365,
                extension_days=EXT,
                hard_cap_days=CAP,
            ),
        )
        entry = _entry(_upc(), GREEN, "preference", age_days=10, expires_in_days=5)

        assert _use(entry) == 0
        assert _expiry(entry) == NOW + timedelta(days=5)

    def test_an_f4_inference_keeps_its_absolute_term(self, consent, approved) -> None:
        approved(YELLOW, "lifestyle")
        f4 = _entry(
            _upc(),
            YELLOW,
            "lifestyle",
            age_days=10,
            expires_in_days=5,
            consent_scope="preference_inference",
        )
        booked = _entry(_upc(), YELLOW, "lifestyle", age_days=10, expires_in_days=5)

        assert _use(f4, booked) == 1

        assert _expiry(booked) == NOW + timedelta(days=EXT)
        assert _expiry(f4) == NOW + timedelta(days=5)


class TestTheSweepFollowsApproval:
    def test_unapproved_category_keeps_the_intersection(self) -> None:
        upc = _upc()
        # Дата прошла, но последнее использование свежее — окно держит строку.
        entry = _entry(upc, YELLOW, "relationship", age_days=400, expires_in_days=-1)
        MemoryEntry.objects.filter(pk=entry.pk).update(last_used_at=NOW - timedelta(days=5))

        result = soft_delete_expired_entries(now=NOW)

        entry.refresh_from_db()
        assert result.purged_yellow == 0
        assert entry.soft_deleted_at is None

    def test_approved_category_is_judged_by_its_single_expiry(self, approved) -> None:
        approved(YELLOW, "relationship")
        upc = _upc()
        entry = _entry(upc, YELLOW, "relationship", age_days=400, expires_in_days=-1)
        MemoryEntry.objects.filter(pk=entry.pk).update(last_used_at=NOW - timedelta(days=5))
        other = _entry(upc, YELLOW, "financial", age_days=400, expires_in_days=-1)
        MemoryEntry.objects.filter(pk=other.pk).update(last_used_at=NOW - timedelta(days=5))

        result = soft_delete_expired_entries(now=NOW)

        entry.refresh_from_db()
        other.refresh_from_db()
        assert entry.soft_deleted_at == NOW
        assert entry.deletion_reason == "ttl_purge"
        assert other.soft_deleted_at is None
        assert result.purged_yellow == 1

    def test_an_f4_row_in_an_approved_category_keeps_the_intersection(self, approved) -> None:
        approved(YELLOW, "lifestyle")
        upc = _upc()
        booked = _entry(upc, YELLOW, "lifestyle", age_days=400, expires_in_days=-1)
        f4 = _entry(
            upc,
            YELLOW,
            "lifestyle",
            age_days=400,
            expires_in_days=-1,
            consent_scope="preference_inference",
        )
        MemoryEntry.objects.filter(pk__in=[booked.pk, f4.pk]).update(
            last_used_at=NOW - timedelta(days=5)
        )

        result = soft_delete_expired_entries(now=NOW)

        booked.refresh_from_db()
        f4.refresh_from_db()
        assert booked.soft_deleted_at == NOW
        assert f4.soft_deleted_at is None
        assert result.purged_yellow == 1
