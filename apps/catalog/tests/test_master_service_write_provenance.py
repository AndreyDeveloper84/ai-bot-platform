"""The unaudited write path is closed (DRF-975).

These tests are deliberately *not* "audit is written on the happy path". The
happy path already audited before this PR and 232 rows still landed with no
trace, because the writer was a hand-run script that never touched a view.
What has to be proven is the negative: **the bypass no longer works.**

Every test that exercises the bypass requests the
``no_master_service_provenance`` fixture, which tears down the ambient
suite-wide context from ``config.pytest_master_service_provenance``. Without
it the test would run inside the fixture context and pass for the wrong
reason -- the failure mode this file exists to catch.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone as dt_timezone

import pytest
from django.db.models.signals import post_delete, post_save, pre_save

from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.catalog.provenance import (
    MasterServiceSource,
    UnprovenancedMasterServiceWrite,
    current_master_service_write,
    master_service_write,
)
from apps.events.vocabulary import (
    MASTER_SERVICE_EDGE_CREATED,
    MASTER_SERVICE_EDGE_DELETED,
)
from apps.tenancy.models import Tenant


def _ts() -> datetime:
    return datetime(2026, 7, 22, 16, 14, tzinfo=dt_timezone.utc)


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="drf975", name="DRF-975 provenance")


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant, external_id=1, external_updated_at=_ts(), name="Master"
    )


@pytest.fixture
def service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=_ts(),
        slug="svc-1",
        name="Service",
    )


# ---------------------------------------------------------------------------
# 1. The bypass is closed. Each of these is a way a script on the host would
#    reach for the table; all five must refuse.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "write",
    [
        pytest.param(
            lambda t, m, s: MasterService.all_tenants.create(tenant=t, master=m, service=s),
            id="manager_create",
        ),
        pytest.param(
            lambda t, m, s: MasterService(tenant=t, master=m, service=s).save(),
            id="instance_save",
        ),
        pytest.param(
            lambda t, m, s: MasterService.all_tenants.get_or_create(
                tenant=t, master=m, service=s
            ),
            id="get_or_create",
        ),
        pytest.param(
            lambda t, m, s: MasterService.all_tenants.update_or_create(
                tenant=t, master=m, service=s, defaults={}
            ),
            id="update_or_create",
        ),
        pytest.param(
            lambda t, m, s: MasterService.all_tenants.bulk_create(
                [MasterService(tenant=t, master=m, service=s)]
            ),
            id="bulk_create",
        ),
    ],
)
def test_unprovenanced_write_is_refused(
    no_master_service_provenance, tenant, master, service, write
):
    """No provenance in scope means no row. This is the DRF-975 acceptance test.

    ``bulk_create`` is in the list on purpose: it sends no ``pre_save``, so it
    is guarded by a separate mechanism (``MasterServiceQuerySet.bulk_create``)
    and a regression there would otherwise be invisible. It is also the exact
    shape of the 2026-07-22 event -- 232 rows in one act.
    """

    with pytest.raises(UnprovenancedMasterServiceWrite):
        write(tenant, master, service)

    assert MasterService.all_tenants.count() == 0, (
        "the row was created despite the gate refusing -- the INSERT was not "
        "rolled back"
    )


def test_refusal_message_tells_the_operator_what_to_do(
    no_master_service_provenance, tenant, master, service
):
    """A gate an operator cannot get past *correctly* just gets worked around.

    The message must name the context manager and the incident, otherwise the
    next person on the host reaches for raw SQL -- which nothing here can stop.
    """

    with pytest.raises(UnprovenancedMasterServiceWrite) as exc:
        MasterService.all_tenants.create(tenant=tenant, master=master, service=service)

    text = str(exc.value)
    assert "master_service_write" in text
    assert "MasterServiceSource.MANUAL_SCRIPT" in text
    assert "DRF-975" in text
    assert "232" in text


def test_empty_bulk_create_is_not_refused(no_master_service_provenance, tenant):
    """Nothing written means nothing to attribute. Refusing here is pure friction."""

    assert MasterService.all_tenants.bulk_create([]) == []


def test_caller_cannot_forge_source(no_master_service_provenance, tenant, master, service):
    """A caller-supplied ``source=`` must not win over the context.

    If it did, a script could stamp ``catalog_sync`` on its own rows and
    vanish into the beat traffic -- strictly worse than NULL, because NULL at
    least reads as "unknown" instead of lying.
    """

    with master_service_write(MasterServiceSource.MANUAL_SCRIPT, reason="DRF-975 test"):
        edge = MasterService.all_tenants.create(
            tenant=tenant,
            master=master,
            service=service,
            source=MasterServiceSource.CATALOG_SYNC.value,
        )

    edge.refresh_from_db()
    assert edge.source == MasterServiceSource.MANUAL_SCRIPT.value


# ---------------------------------------------------------------------------
# 2. The provenanced path records who and when, on the row and in AuditLog.
# ---------------------------------------------------------------------------


def test_provenanced_write_stamps_row_and_audits(tenant, master, service):
    actor = uuid.uuid4()

    with master_service_write(
        MasterServiceSource.MANUAL_SCRIPT, actor_id=actor, reason="DRF-975: backfill"
    ):
        edge = MasterService.all_tenants.create(tenant=tenant, master=master, service=service)

    edge.refresh_from_db()
    assert edge.source == MasterServiceSource.MANUAL_SCRIPT.value
    assert edge.created_by_actor_id == actor
    assert edge.created_at is not None

    row = AuditLog.all_tenants.get(action=MASTER_SERVICE_EDGE_CREATED)
    assert row.target == "catalog.MasterService"
    assert row.target_id == edge.pk
    assert row.actor_id == actor
    assert row.payload["source"] == MasterServiceSource.MANUAL_SCRIPT.value
    assert row.payload["master_id"] == str(master.pk)
    assert row.payload["service_id"] == str(service.pk)
    assert row.payload["reason"] == "DRF-975: backfill"
    # The sync-ownership discriminator, captured at write time -- this is what
    # makes the audit row able to answer the DRF-967 question later.
    assert row.payload["ayla_specialist_service_id"] is None


def test_row_stamp_outlives_the_audit_row(tenant, master, service):
    """Why ``source`` is a column and not only an AuditLog payload key.

    AuditLog is swept at ``AUDIT_LOG_RETENTION_DAYS`` (default 90). Edges live
    for years. After the sweep the column is the only surviving answer to
    "who created this relation", so it must not depend on the audit row.
    """

    with master_service_write(MasterServiceSource.MM4_MATRIX, actor_id=uuid.uuid4()):
        edge = MasterService.all_tenants.create(tenant=tenant, master=master, service=service)

    AuditLog.all_tenants.all().delete()  # simulate the retention sweep

    edge.refresh_from_db()
    assert edge.source == MasterServiceSource.MM4_MATRIX.value
    assert edge.created_by_actor_id is not None


def test_bulk_create_audits_every_row(tenant, master, service):
    other = CatalogService.all_tenants.create(
        tenant=tenant, external_id=2, external_updated_at=_ts(), slug="svc-2", name="Second"
    )

    with master_service_write(MasterServiceSource.INVITE_SEED, actor_id=uuid.uuid4()):
        MasterService.all_tenants.bulk_create(
            [
                MasterService(tenant=tenant, master=master, service=service),
                MasterService(tenant=tenant, master=master, service=other),
            ]
        )

    rows = AuditLog.all_tenants.filter(action=MASTER_SERVICE_EDGE_CREATED)
    assert rows.count() == 2, "a bulk act must not collapse into one audit row"
    assert {r.payload["source"] for r in rows} == {MasterServiceSource.INVITE_SEED.value}
    assert (
        MasterService.all_tenants.filter(source=MasterServiceSource.INVITE_SEED.value).count()
        == 2
    )


def test_nested_context_innermost_wins(tenant, master, service):
    """Production writers stamp their own source even under an ambient one.

    This is what makes the suite-wide TEST_FIXTURE context safe: asserting a
    specific source in a per-writer test is a real assertion about that
    writer, not about the ambient fallback.
    """

    with master_service_write(MasterServiceSource.TEST_FIXTURE):
        with master_service_write(MasterServiceSource.MM4_MATRIX):
            edge = MasterService.all_tenants.create(
                tenant=tenant, master=master, service=service
            )
        assert current_master_service_write().source is MasterServiceSource.TEST_FIXTURE

    assert edge.source == MasterServiceSource.MM4_MATRIX.value


def test_update_of_existing_row_needs_no_context(
    no_master_service_provenance, tenant, master, service
):
    """Only INSERT is gated -- see ``apps.catalog.signals``.

    Sync refreshing ``resolved_requires_health_check`` on an existing edge is
    not what manufactures unattributable data, and gating it would force a
    context onto every read-modify-write path for a field unrelated to
    authorship. The original author is recorded and is not being changed.
    """

    with master_service_write(MasterServiceSource.CATALOG_SYNC):
        edge = MasterService.all_tenants.create(tenant=tenant, master=master, service=service)

    edge.resolved_requires_health_check = True
    edge.save(update_fields=["resolved_requires_health_check", "updated_at"])

    edge.refresh_from_db()
    assert edge.resolved_requires_health_check is True
    assert edge.source == MasterServiceSource.CATALOG_SYNC.value


# ---------------------------------------------------------------------------
# 3. Deletion is traced, never gated. A CASCADE must not be able to fail on a
#    forensic concern.
# ---------------------------------------------------------------------------


def test_delete_is_audited_with_its_source(tenant, master, service):
    with master_service_write(MasterServiceSource.MM4_MATRIX, actor_id=uuid.uuid4()):
        edge = MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
        edge_id = edge.pk
        edge.delete()

    row = AuditLog.all_tenants.get(action=MASTER_SERVICE_EDGE_DELETED)
    assert row.target_id == edge_id
    assert row.payload["deleted_under_source"] == MasterServiceSource.MM4_MATRIX.value


def test_cascade_delete_does_not_raise(no_master_service_provenance, tenant, master, service):
    """Deleting the master takes its edges with it, with no context in scope.

    ``MasterService`` is a CASCADE child of Tenant / CatalogMaster /
    CatalogService. If ``post_delete`` gated the way ``pre_save`` does, master
    deactivation and every test teardown would fail.
    """

    with master_service_write(MasterServiceSource.CATALOG_SYNC):
        MasterService.all_tenants.create(tenant=tenant, master=master, service=service)

    master.delete()

    assert MasterService.all_tenants.count() == 0
    row = AuditLog.all_tenants.get(action=MASTER_SERVICE_EDGE_DELETED)
    assert row.payload["deleted_under_source"] is None, "a CASCADE must be visible as one"


# ---------------------------------------------------------------------------
# 4. The gate is actually installed, and it is scoped.
# ---------------------------------------------------------------------------


def test_receivers_are_connected():
    """A gate that silently fails to register is worse than none.

    ``CatalogConfig.ready()`` importing ``apps.catalog.signals`` is what
    connects these. An import dropped in a refactor would leave every
    behavioural test above still passing on the ambient context, with the gate
    simply gone.
    """

    for signal, uid in (
        (pre_save, "drf975_ms_provenance"),
        (post_save, "drf975_ms_audit_created"),
        (post_delete, "drf975_ms_audit_deleted"),
    ):
        # Django stores receivers as (lookup_key, receiver, is_async) and the
        # lookup key is (dispatch_uid_or_id, sender_id) -- unpack positionally
        # so a future arity change fails loudly here rather than silently
        # making this assertion vacuous.
        uids = [entry[0][0] for entry in signal.receivers]
        assert uid in uids, f"{uid} not connected"


def test_unrelated_model_writes_are_unaffected(no_master_service_provenance, db):
    """The receivers are sender-scoped, so no other model pays for this."""

    t = Tenant.objects.create(slug="unaffected", name="Unaffected")
    CatalogService.all_tenants.create(
        tenant=t, external_id=9, external_updated_at=_ts(), slug="s9", name="S9"
    )
    CatalogMaster.all_tenants.create(
        tenant=t, external_id=9, external_updated_at=_ts(), name="M9"
    )


# ---------------------------------------------------------------------------
# 5. Guard-rails on the context API itself.
# ---------------------------------------------------------------------------


def test_manual_script_requires_a_reason():
    with pytest.raises(ValueError, match="requires a reason"):
        with master_service_write(MasterServiceSource.MANUAL_SCRIPT):
            pass


def test_source_must_be_an_enum_member():
    with pytest.raises(TypeError, match="MasterServiceSource"):
        with master_service_write("catalog_sync"):  # type: ignore[arg-type]
            pass


def test_context_is_reset_even_on_exception():
    with pytest.raises(RuntimeError):
        with master_service_write(MasterServiceSource.CATALOG_SYNC):
            raise RuntimeError("boom")
    # The ambient suite fixture must be back, not the one we just left.
    assert current_master_service_write().source is MasterServiceSource.TEST_FIXTURE
