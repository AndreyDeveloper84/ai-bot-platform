"""Backfill of the ``memory_green`` welcome-S2 consent (migration 0003, DRF-1311).

Driven through the real ``MigrationExecutor`` (house pattern, cf.
``apps/identity/tests/test_memory_entry_step3_backfill.py``) — no stubbed
``apps`` registry, because the historical model's manager is NOT the runtime
tenant-scoped one and a hand-rolled fake would hide exactly that difference.

Data-only migration; schema is identical between 0002 and 0003, so rows are
inserted through the runtime models (``all_tenants``) and only the migration
itself is exercised.
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

pytestmark = pytest.mark.django_db(transaction=True)

_MIG_0002 = ("consent", "0002_alter_consentrecord_consent_type")
_MIG_0003 = ("consent", "0003_backfill_memory_green_consent")

_WELCOME_S2_SOURCE = "global_onboarding:welcome_s2"
_BACKFILL_SOURCE = "backfill:drf1311:welcome_s2"
_TAPPED_AT = datetime(2026, 8, 20, 18, 41, 12, tzinfo=tz.utc)

CT = ConsentRecord.ConsentType


def _executor() -> MigrationExecutor:
    # Fresh executor per use — the loader caches applied-migration state.
    return MigrationExecutor(connection)


@pytest.fixture
def at_0002():
    """Roll the consent app back to 0002 and ALWAYS return to the head."""
    _executor().migrate([_MIG_0002])
    yield
    _executor().migrate([_MIG_0003])


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


def test_welcome_s2_grant_gets_a_mirrored_memory_green_row(at_0002) -> None:
    bu = _bot_user("bf-a", "a1")
    _grant(bu, CT.PERSONAL_DATA, source=_WELCOME_S2_SOURCE)

    _executor().migrate([_MIG_0003])

    row = _green_rows(bu).get()
    assert row.source == _BACKFILL_SOURCE  # provenance: derived, not re-tapped
    assert row.document_version == "welcome-s2-v1"  # the text actually shown
    assert row.captured_at == _TAPPED_AT  # WHEN it was actually accepted
    assert row.tenant_id == bu.tenant_id


def test_the_gate_the_backfill_exists_for_is_open_afterwards(at_0002) -> None:
    """The point of the migration: ``has_memory_consent`` flips to True."""
    from apps.consent.services import has_memory_consent

    bu = _bot_user("bf-gate", "g1")
    _grant(bu, CT.PERSONAL_DATA, source=_WELCOME_S2_SOURCE)
    assert has_memory_consent(bu.ayla_user_id, "green") is False

    _executor().migrate([_MIG_0003])

    assert has_memory_consent(bu.ayla_user_id, "green") is True


def test_grant_from_another_flow_is_left_alone(at_0002) -> None:
    """Only the welcome S2 disclosure covers memory — nothing else is widened."""
    bu = _bot_user("bf-b", "b1")
    _grant(bu, CT.PERSONAL_DATA, source="registration_form", version="privacy-v1.2")

    _executor().migrate([_MIG_0003])

    assert _green_rows(bu).count() == 0


def test_withdrawn_grant_is_not_resurrected(at_0002) -> None:
    bu = _bot_user("bf-c", "c1")
    row = _grant(bu, CT.PERSONAL_DATA, source=_WELCOME_S2_SOURCE)
    ConsentRecord.all_tenants.filter(pk=row.pk).update(withdrawn_at=_TAPPED_AT)

    _executor().migrate([_MIG_0003])

    assert _green_rows(bu).count() == 0


def test_existing_memory_green_is_not_duplicated(at_0002) -> None:
    bu = _bot_user("bf-d", "d1")
    _grant(bu, CT.PERSONAL_DATA, source=_WELCOME_S2_SOURCE)
    _grant(bu, CT.MEMORY_GREEN, source=_WELCOME_S2_SOURCE)

    _executor().migrate([_MIG_0003])

    assert _green_rows(bu).count() == 1
    assert _green_rows(bu).get().source == _WELCOME_S2_SOURCE  # the real tap wins


def test_reverse_removes_only_the_backfilled_rows(at_0002) -> None:
    bu = _bot_user("bf-e", "e1")
    _grant(bu, CT.PERSONAL_DATA, source=_WELCOME_S2_SOURCE)
    real_tap = _bot_user("bf-f", "f1")
    _grant(real_tap, CT.MEMORY_GREEN, source=_WELCOME_S2_SOURCE)

    _executor().migrate([_MIG_0003])
    assert _green_rows(bu).count() == 1

    _executor().migrate([_MIG_0002])

    assert _green_rows(bu).count() == 0  # derived row dropped
    assert _green_rows(real_tap).count() == 1  # genuine grant untouched
