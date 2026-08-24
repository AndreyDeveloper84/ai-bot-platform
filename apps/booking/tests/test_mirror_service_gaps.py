"""The counter behind «сколько их» (DRF-1103).

Before anyone decides whether to touch history on a live pilot, the size of
the hole has to be answerable. This pins that the command answers it, and —
just as importantly — that it only ever reads.
"""

from __future__ import annotations

import datetime as dt
from io import StringIO
from uuid import uuid4

import pytest
from django.core.management import call_command

from apps.booking.models import RemoteBookingProxy
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _proxy(tenant: Tenant, **overrides) -> RemoteBookingProxy:
    defaults = {
        "tenant": tenant,
        "start_at": dt.datetime(2026, 5, 22, 12, 0, tzinfo=dt.timezone.utc),
        "end_at": dt.datetime(2026, 5, 22, 13, 0, tzinfo=dt.timezone.utc),
        "status": RemoteBookingProxy.Status.CONFIRMED,
        "source": RemoteBookingProxy.Source.AUTOMATION,
        "service_id": None,
    }
    defaults.update(overrides)
    return RemoteBookingProxy.all_tenants.create(appointment_id=uuid4(), **defaults)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="t-gaps", name="Gaps")


def _run() -> str:
    out = StringIO()
    call_command("mirror_service_gaps", stdout=out)
    return out.getvalue()


def test_it_separates_the_rows_that_still_matter(tenant: Tenant) -> None:
    """A cancelled booking with no service is a row nobody opens again; a
    confirmed one is somebody walking in on Tuesday. The command must not
    report them as one number."""
    _proxy(tenant)  # live, no service — the DRF-1103 cohort
    _proxy(tenant, status=RemoteBookingProxy.Status.CANCELLED)  # dead, no service
    _proxy(tenant, service_id=uuid4(), source="mobile_app")  # fine

    output = _run()

    assert "mirror rows total:              3" in output
    assert "of which service_id IS NULL:  2" in output
    assert "still live (day board):     1" in output


def test_it_names_the_source_so_the_writer_is_identifiable(tenant: Tenant) -> None:
    """«automation» is the bot. Splitting by source is what turned this from
    «some rows are empty» into «every booking the bot made»."""
    _proxy(tenant)
    _proxy(tenant, source="mobile_app")

    output = _run()

    assert "automation" in output
    assert "mobile_app" in output


def test_it_writes_nothing(tenant: Tenant) -> None:
    """Read-only by contract. Backfilling history on a live pilot is a
    separate decision with a separate owner."""
    row = _proxy(tenant)

    _run()

    row.refresh_from_db()
    assert row.service_id is None
    assert RemoteBookingProxy.all_tenants.count() == 1
