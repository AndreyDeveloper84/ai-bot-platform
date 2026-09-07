"""Backfill of the ``memory_green`` welcome-S2 consent (migration 0003, DRF-1311).

Driven through the real ``MigrationExecutor`` (house pattern, cf.
``apps/identity/tests/test_memory_entry_step3_backfill.py``) — no stubbed
``apps`` registry, because the historical model's manager is NOT the runtime
tenant-scoped one and a hand-rolled fake would hide exactly that difference.

Data-only migration; schema is identical between 0002 and 0003, so rows are
inserted through the runtime models (``all_tenants``) and only the migration
itself is exercised.

Reading through the RUNTIME models is what forces the shape of these tests
(DRF-1554). ``identity/0021`` declares ``consent/0003`` a dependency, so
standing at ``consent/0002`` means identity is unapplied too — and with it any
column a later identity migration adds to ``BotUser``. Measured 07.09.2026
against a probe migration shaped like PR #1399's ``identity/0022``: with the
old fixture all six tests died inside ``_bot_user()`` on ``column ... of
relation "identity_botuser" does not exist``, before ever reaching the
migration they exist to test.

So the database is never *rested on* below the head of the graph. Rows are
created at head; ``rerun_backfill()`` takes the graph down and straight back
up as one indivisible pair with no statement of the test in between; the
assertions run at head again. The one place that must observe the reverse —
``test_reverse_removes_only_the_backfilled_rows`` — reads only
``consent_consentrecord`` while it is down, and restores in a ``finally``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone as tz

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant
from tests.support.migration_graph import restore_migration_head

pytestmark = pytest.mark.django_db(transaction=True)

_MIG_0002 = ("consent", "0002_alter_consentrecord_consent_type")

_WELCOME_S2_SOURCE = "global_onboarding:welcome_s2"
_BACKFILL_SOURCE = "backfill:drf1311:welcome_s2"
_TAPPED_AT = datetime(2026, 8, 20, 18, 41, 12, tzinfo=tz.utc)

CT = ConsentRecord.ConsentType


def _executor() -> MigrationExecutor:
    # Fresh executor per use — the loader caches applied-migration state.
    return MigrationExecutor(connection)


@pytest.fixture(autouse=True)
def _graph_intact():
    """Unconditional safety net: this worker leaves with a whole graph.

    pytest-django hands one database to an xdist worker for the entire
    session, so a test that dies mid-rollback would poison every test landing
    on that worker afterwards — in another app, in another PR, with an error
    naming neither (DRF-1551).
    """
    yield
    restore_migration_head()


@pytest.fixture
def rerun_backfill():
    """Run migration 0003 again over the rows the test has just created.

    Down and straight back up, with no statement of the test in between: the
    rollback is what makes the backfill runnable a second time, the restore is
    what keeps `identity` — and everything standing on it — applied for the
    rest of the test.

    `restore_migration_head()`, not `migrate([("consent", "0003_...")])`:
    walking consent forward on its own does not re-apply the apps that depend
    on it (DRF-1554).
    """

    def _rerun() -> None:
        try:
            _executor().migrate([_MIG_0002])
        finally:
            restore_migration_head()

    return _rerun


def _bot_user(slug: str, cuid: str) -> BotUser:
    tenant = Tenant.objects.create(slug=slug, name=slug)
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=cuid, ayla_user_id=uuid.uuid4()
    )


def _grant(bu: BotUser, ctype: str, *, source: str, version: str = "welcome-s2-v1"):
    row = ConsentRecord.all_tenants.create(
        tenant=bu.tenant,
        bot_user=bu,
        consent_type=ctype,
        granted=True,
        source=source,
        document_version=version,
    )
    ConsentRecord.all_tenants.filter(pk=row.pk).update(captured_at=_TAPPED_AT)
    return row


def _green_rows(bu: BotUser):
    return ConsentRecord.all_tenants.filter(
        bot_user=bu, consent_type=CT.MEMORY_GREEN, granted=True, withdrawn_at__isnull=True
    )


def _green_rows_at_0002(bu: BotUser) -> int:
    """The same count, read through the historical registry at 0002.

    For use while the graph is rolled back: the 0002-era model has exactly
    the columns that exist there, so this read cannot break the day someone
    adds a column to `consent_consentrecord` in a later migration.
    """
    historical = _executor().loader.project_state(_MIG_0002).apps
    return (
        historical.get_model("consent", "ConsentRecord")
        .objects.filter(
            bot_user_id=bu.pk,
            consent_type=CT.MEMORY_GREEN,
            granted=True,
            withdrawn_at__isnull=True,
        )
        .count()
    )


def test_welcome_s2_grant_gets_a_mirrored_memory_green_row(rerun_backfill) -> None:
    bu = _bot_user("bf-a", "a1")
    _grant(bu, CT.PERSONAL_DATA, source=_WELCOME_S2_SOURCE)

    rerun_backfill()

    row = _green_rows(bu).get()
    assert row.source == _BACKFILL_SOURCE  # provenance: derived, not re-tapped
    assert row.document_version == "welcome-s2-v1"  # the text actually shown
    assert row.captured_at == _TAPPED_AT  # WHEN it was actually accepted
    assert row.tenant_id == bu.tenant_id


def test_the_gate_the_backfill_exists_for_is_open_afterwards(rerun_backfill) -> None:
    """The point of the migration: ``has_memory_consent`` flips to True."""
    from apps.consent.services import has_memory_consent

    bu = _bot_user("bf-gate", "g1")
    _grant(bu, CT.PERSONAL_DATA, source=_WELCOME_S2_SOURCE)
    assert has_memory_consent(bu.ayla_user_id, "green") is False

    rerun_backfill()

    assert has_memory_consent(bu.ayla_user_id, "green") is True


def test_grant_from_another_flow_is_left_alone(rerun_backfill) -> None:
    """Only the welcome S2 disclosure covers memory — nothing else is widened."""
    bu = _bot_user("bf-b", "b1")
    _grant(bu, CT.PERSONAL_DATA, source="registration_form", version="privacy-v1.2")

    rerun_backfill()

    assert _green_rows(bu).count() == 0


def test_withdrawn_grant_is_not_resurrected(rerun_backfill) -> None:
    bu = _bot_user("bf-c", "c1")
    row = _grant(bu, CT.PERSONAL_DATA, source=_WELCOME_S2_SOURCE)
    ConsentRecord.all_tenants.filter(pk=row.pk).update(withdrawn_at=_TAPPED_AT)

    rerun_backfill()

    assert _green_rows(bu).count() == 0


def test_existing_memory_green_is_not_duplicated(rerun_backfill) -> None:
    bu = _bot_user("bf-d", "d1")
    _grant(bu, CT.PERSONAL_DATA, source=_WELCOME_S2_SOURCE)
    _grant(bu, CT.MEMORY_GREEN, source=_WELCOME_S2_SOURCE)

    rerun_backfill()

    assert _green_rows(bu).count() == 1
    assert _green_rows(bu).get().source == _WELCOME_S2_SOURCE  # the real tap wins


def test_reverse_removes_only_the_backfilled_rows(rerun_backfill) -> None:
    bu = _bot_user("bf-e", "e1")
    _grant(bu, CT.PERSONAL_DATA, source=_WELCOME_S2_SOURCE)
    real_tap = _bot_user("bf-f", "f1")
    _grant(real_tap, CT.MEMORY_GREEN, source=_WELCOME_S2_SOURCE)

    rerun_backfill()
    assert _green_rows(bu).count() == 1

    # The reverse IS the subject here, so this one observation has to be made
    # with the graph down — and it is made through the HISTORICAL registry at
    # 0002, not the runtime model. The file's runtime-model rule is about
    # SEEDING: a historical manager is not tenant-scoped, and seeding through
    # one would hide exactly the difference these tests exist to exercise.
    # Counting rows back does not depend on the manager, and the historical
    # model has precisely the columns that exist while 0003 is unapplied —
    # which is what makes this read safe against a future migration that adds
    # a column to `consent_consentrecord` (DRF-1554).
    _executor().migrate([_MIG_0002])
    try:
        assert _green_rows_at_0002(bu) == 0  # derived row dropped
        assert _green_rows_at_0002(real_tap) == 1  # genuine grant untouched
    finally:
        restore_migration_head()
