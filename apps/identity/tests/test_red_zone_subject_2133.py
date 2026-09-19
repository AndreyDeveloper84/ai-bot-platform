"""RedZoneReader — путь субъекта данных (DRF-2133, экран «Что Ayla помнит»).

``list_live_for_subject`` и ``soft_delete_for_subject`` — те же три слоя,
что у ``read()``: GUC внутри одного ``atomic()``, владение по построению,
лог доступа в той же транзакции, ``RESET`` на каждом выходе. Здесь —
инварианты, проверяемые на любой СУБД, и Postgres-only проверки GUC как у
``TestGucResetAfterAccessor``.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.identity.models import MemoryEntry, RedZoneAccessLog, UserPersonalContext
from apps.identity.services.red_zone_reader import RedZoneReader

pytestmark = pytest.mark.django_db

_PG_ONLY = pytest.mark.skipif(
    connection.vendor != "postgresql", reason="GUC mechanism is Postgres-only."
)
_GUC_NAME = "ayla.red_zone_access_context"


@pytest.fixture
def upc():
    return UserPersonalContext.objects.create(user_id=uuid.uuid4())


def _red(upc, **overrides):
    kwargs = dict(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_RED,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        consent_at=timezone.now(),
        content={"key": "allergy", "value": "nuts"},
    )
    kwargs.update(overrides)
    return MemoryEntry.objects.create(**kwargs)


def _green(upc):
    return MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        content={"key": "diet", "value": "vegan"},
    )


def _list(user_id, **kw):
    return RedZoneReader.list_live_for_subject(
        user_id=user_id,
        accessor_role=RedZoneAccessLog.ACCESSOR_DATA_SUBJECT,
        request_id=kw.pop("request_id", uuid.uuid4()),
        purpose="miniapp_memory_screen",
        **kw,
    )


def _delete(entry_id, user_id, **kw):
    return RedZoneReader.soft_delete_for_subject(
        entry_id=entry_id,
        user_id=user_id,
        accessor_role=RedZoneAccessLog.ACCESSOR_DATA_SUBJECT,
        request_id=kw.pop("request_id", uuid.uuid4()),
        purpose="miniapp_memory_forget",
        reason=MemoryEntry.DELETION_REASON_USER_REQUEST_MINIAPP,
        **kw,
    )


def _guc_value() -> str | None:
    with connection.cursor() as cur:
        cur.execute(f"SELECT current_setting('{_GUC_NAME}', true)")
        return cur.fetchone()[0]


def _patched_guc(calls: list[str]):
    return (
        patch.object(RedZoneReader, "_set_guc", side_effect=lambda rid: calls.append("set")),
        patch.object(RedZoneReader, "_reset_guc", side_effect=lambda: calls.append("reset")),
    )


class TestListLiveForSubject:
    def test_returns_only_the_subjects_live_red_rows_with_one_log_each(self, upc):
        mine = _red(upc)
        now = timezone.now()
        _red(
            upc,
            soft_deleted_at=now,
            delete_requested_at=now,
            deletion_reason=MemoryEntry.DELETION_REASON_USER_DELETE,
            status=MemoryEntry.STATUS_DELETED,
        )
        other = UserPersonalContext.objects.create(user_id=uuid.uuid4())
        _red(other)
        # A green row of the same person is not this reader's business.
        _green(upc)
        rid = uuid.uuid4()

        rows = _list(upc.user_id, request_id=rid)

        assert [r.id for r in rows] == [mine.id]
        logs = RedZoneAccessLog.objects.filter(user_id=upc.user_id)
        assert logs.count() == 1
        log = logs.get()
        assert log.memory_entry_id == mine.id
        assert log.accessor_role == RedZoneAccessLog.ACCESSOR_DATA_SUBJECT
        assert log.access_type == RedZoneAccessLog.ACCESS_READ
        assert log.request_id == rid
        assert log.purpose == "miniapp_memory_screen"

    def test_empty_result_writes_no_log(self, upc):
        assert _list(upc.user_id) == []
        assert RedZoneAccessLog.objects.count() == 0

    def test_ops_admin_without_principal_is_refused(self, upc):
        with pytest.raises(ValueError):
            RedZoneReader.list_live_for_subject(
                user_id=upc.user_id,
                accessor_role=RedZoneAccessLog.ACCESSOR_OPS_ADMIN,
                request_id=uuid.uuid4(),
                purpose="x",
            )

    def test_guc_set_inside_atomic_and_reset_on_success(self, upc):
        """Порядок слоёв как у read(): SET → SELECT → лог, RESET в finally."""
        _red(upc)
        calls: list[str] = []
        set_guc, reset_guc = _patched_guc(calls)
        with set_guc, reset_guc:
            rows = _list(upc.user_id)
        assert len(rows) == 1
        assert calls == ["set", "reset"]

    def test_reset_runs_when_the_select_fails(self, upc):
        calls: list[str] = []
        set_guc, reset_guc = _patched_guc(calls)
        with (
            set_guc,
            reset_guc,
            patch(
                "apps.identity.services.red_zone_reader.MemoryEntry.objects.filter",
                side_effect=IntegrityError("simulated"),
            ),
            pytest.raises(IntegrityError),
        ):
            _list(upc.user_id)
        assert calls == ["set", "reset"]
        assert RedZoneAccessLog.objects.count() == 0

    @_PG_ONLY
    def test_guc_is_clear_after_list_on_postgres(self, upc):
        _red(upc)
        with CaptureQueriesContext(connection) as ctx:
            _list(upc.user_id)
        executed = [q["sql"] or "" for q in ctx.captured_queries]
        assert any("set_config" in sql and _GUC_NAME in sql for sql in executed)
        assert any("RESET " + _GUC_NAME in sql for sql in executed)
        assert _guc_value() in ("", None)


class TestSoftDeleteForSubject:
    def test_own_live_red_row_is_tombstoned_with_a_delete_log(self, upc):
        entry = _red(upc)
        rid = uuid.uuid4()

        assert _delete(entry.id, upc.user_id, request_id=rid) is True

        entry.refresh_from_db()
        assert entry.soft_deleted_at is not None
        assert entry.delete_requested_at is not None
        assert entry.status == MemoryEntry.STATUS_DELETED
        assert entry.deletion_reason == MemoryEntry.DELETION_REASON_USER_REQUEST_MINIAPP
        log = RedZoneAccessLog.objects.get(memory_entry_id=entry.id)
        assert log.access_type == RedZoneAccessLog.ACCESS_DELETE
        assert log.accessor_role == RedZoneAccessLog.ACCESSOR_DATA_SUBJECT
        assert log.request_id == rid
        assert _list(upc.user_id) == []

    def test_foreign_row_is_untouched_and_unlogged(self, upc):
        other = UserPersonalContext.objects.create(user_id=uuid.uuid4())
        foreign = _red(other)

        assert _delete(foreign.id, upc.user_id) is False

        foreign.refresh_from_db()
        assert foreign.soft_deleted_at is None
        assert RedZoneAccessLog.objects.count() == 0

    def test_green_row_is_not_this_paths_business(self, upc):
        green = _green(upc)
        assert _delete(green.id, upc.user_id) is False
        green.refresh_from_db()
        assert green.soft_deleted_at is None

    def test_idempotent(self, upc):
        entry = _red(upc)
        assert _delete(entry.id, upc.user_id) is True
        assert _delete(entry.id, upc.user_id) is False
        assert RedZoneAccessLog.objects.filter(memory_entry_id=entry.id).count() == 1

    def test_guc_set_and_reset_around_the_update(self, upc):
        entry = _red(upc)
        calls: list[str] = []
        set_guc, reset_guc = _patched_guc(calls)
        with set_guc, reset_guc:
            assert _delete(entry.id, upc.user_id) is True
        assert calls == ["set", "reset"]

    @_PG_ONLY
    def test_guc_is_clear_after_delete_on_postgres(self, upc):
        entry = _red(upc)
        with CaptureQueriesContext(connection) as ctx:
            _delete(entry.id, upc.user_id)
        executed = [q["sql"] or "" for q in ctx.captured_queries]
        assert any("RESET " + _GUC_NAME in sql for sql in executed)
        assert _guc_value() in ("", None)
