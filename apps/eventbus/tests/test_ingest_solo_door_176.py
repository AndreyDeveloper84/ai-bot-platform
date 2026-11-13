"""§176 — a proven solo cabinet passes the pilot ingest without being listed.

Owner decision 13.11.2026: a solo master registers on their own, gets a fresh
tenant the operator never put into ``EVENT_INGEST_ALLOWED_TENANTS``, and their
``booking.created`` was rejected with ``tenant_not_allowed`` — the master was
never told about a new booking. The tenant dimension now also admits an
ACTIVE tenant whose slug recomputes from one of its owners' identities.

What must NOT change: the env-listed salon path, the event dimension,
deny-all on an unconfigured pilot, and the refusal of a slug that only looks
solo-shaped.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any
from unittest import mock

import pytest
from django.test import override_settings

from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.ingest_tenancy import (
    TenantAuthorizationError,
    assert_envelope_tenant_authorized,
)
from apps.identity.models import BotUser
from apps.identity.services.solo_onboarding import _solo_tenant_slug, is_proven_solo_tenant
from apps.tenancy.models import Tenant, TenantStaff

SALON = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
SOLO = "4b1e2c3d-5f6a-4b7c-8d9e-0f1a2b3c4d5e"
OWNER_ID = "9900176001"
STRANGER_ID = "9900176002"
AYLA_USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
PILOT_EVENTS = frozenset({"booking.created", "booking.cancelled"})


def _envelope(*, tenant_id: str, event_name: str = "booking.created") -> IngestEnvelope:
    return IngestEnvelope(
        event_id="01J9HXKM8Z2T4V6R8Q1P3D5175",  # pragma: allowlist secret
        event_name=event_name,
        event_version=1,
        occurred_at=dt.datetime(2026, 11, 13, 16, 28, tzinfo=dt.timezone.utc),
        tenant_id=tenant_id,
        user_id=AYLA_USER_ID,
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data={},
    )


def _pilot(**overrides: Any) -> Any:
    config: dict[str, Any] = {
        "EVENT_INGEST_ALLOWED_TENANTS": frozenset({SALON}),
        "EVENT_INGEST_ALLOWED_EVENTS": PILOT_EVENTS,
        "EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN": False,
    }
    config.update(overrides)
    return override_settings(**config)


def _cabinet(*, slug: str, owner_channel_user_id: str, active: bool = True) -> Tenant:
    """A tenant with one owner row, the way solo provisioning lays it out."""
    tenant = Tenant.objects.create(id=SOLO, slug=slug, name="Студия", is_active=active)
    owner = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=owner_channel_user_id
    )
    TenantStaff.all_tenants.create(
        tenant=tenant, bot_user=owner, role=TenantStaff.Role.OWNER, created_by=owner
    )
    return tenant


@pytest.fixture
def salon(db) -> Tenant:  # noqa: ANN001 — pytest-django fixture
    return Tenant.objects.create(id=SALON, slug="t176-salon", name="Салон")


@pytest.mark.django_db
class TestSoloDoorAdmits:
    def test_env_listed_salon_still_admitted(self, salon: Tenant) -> None:
        with _pilot():
            assert_envelope_tenant_authorized(_envelope(tenant_id=SALON))

    def test_proven_solo_base_slug_admitted(self, salon: Tenant) -> None:
        _cabinet(slug=_solo_tenant_slug("max", OWNER_ID), owner_channel_user_id=OWNER_ID)
        with _pilot():
            assert_envelope_tenant_authorized(_envelope(tenant_id=SOLO))

    def test_proven_solo_second_cabinet_admitted(self, salon: Tenant) -> None:
        """``{base}-2`` — re-registration after account deletion (the 13.11 case)."""
        _cabinet(slug=f"{_solo_tenant_slug('max', OWNER_ID)}-2", owner_channel_user_id=OWNER_ID)
        with _pilot():
            assert_envelope_tenant_authorized(_envelope(tenant_id=SOLO))

    def test_proven_solo_admitted_with_empty_tenant_list(self) -> None:
        """Events configured, tenant list empty — the solo door still opens."""
        _cabinet(slug=_solo_tenant_slug("max", OWNER_ID), owner_channel_user_id=OWNER_ID)
        with _pilot(EVENT_INGEST_ALLOWED_TENANTS=frozenset()):
            assert_envelope_tenant_authorized(_envelope(tenant_id=SOLO))

    def test_deactivated_owner_row_still_proves(self, salon: Tenant) -> None:
        """Origin, not today's authority — same rule as ``list_solo_tenant_slugs``."""
        tenant = _cabinet(slug=_solo_tenant_slug("max", OWNER_ID), owner_channel_user_id=OWNER_ID)
        TenantStaff.all_tenants.filter(tenant=tenant).update(
            deactivated_at=dt.datetime(2026, 11, 13, tzinfo=dt.timezone.utc)
        )
        assert is_proven_solo_tenant(tenant)
        with _pilot():
            assert_envelope_tenant_authorized(_envelope(tenant_id=SOLO))

    def test_admit_path_is_named_in_the_audit_line(
        self, salon: Tenant, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="apps.eventbus.ingest_tenancy")
        _cabinet(slug=_solo_tenant_slug("max", OWNER_ID), owner_channel_user_id=OWNER_ID)
        with _pilot():
            assert_envelope_tenant_authorized(_envelope(tenant_id=SOLO))
            assert_envelope_tenant_authorized(_envelope(tenant_id=SALON))
        lines = [
            r.getMessage() for r in caplog.records if "tenant_verify_accepted" in r.getMessage()
        ]
        assert any(f"tenant_id={SOLO}" in x and "admitted_by=solo_proof" in x for x in lines)
        assert any(f"tenant_id={SALON}" in x and "admitted_by=allowlist" in x for x in lines)
        # Identifiers only: the owner's channel id never reaches the log.
        assert not any(OWNER_ID in r.getMessage() for r in caplog.records)


@pytest.mark.django_db
class TestSoloDoorRefuses:
    def _rejected(self, tenant_id: str, **settings_overrides: Any) -> str:
        with _pilot(**settings_overrides), pytest.raises(TenantAuthorizationError) as exc:
            assert_envelope_tenant_authorized(_envelope(tenant_id=tenant_id))
        return str(exc.value)

    def test_solo_shaped_slug_of_another_identity_refused(self, salon: Tenant) -> None:
        """The prefix proves nothing: the slug recomputes from a stranger, not the owner."""
        _cabinet(slug=_solo_tenant_slug("max", STRANGER_ID), owner_channel_user_id=OWNER_ID)
        assert "tenant_not_allowed" in self._rejected(SOLO)

    def test_ownerless_solo_shaped_tenant_refused(self, salon: Tenant) -> None:
        Tenant.objects.create(id=SOLO, slug=_solo_tenant_slug("max", OWNER_ID), name="Студия")
        assert "tenant_not_allowed" in self._rejected(SOLO)

    def test_unlisted_salon_refused(self, salon: Tenant) -> None:
        Tenant.objects.create(id=SOLO, slug="another-salon", name="Другой салон")
        assert "tenant_not_allowed" in self._rejected(SOLO)

    def test_closed_solo_cabinet_refused(self, salon: Tenant) -> None:
        _cabinet(
            slug=_solo_tenant_slug("max", OWNER_ID), owner_channel_user_id=OWNER_ID, active=False
        )
        assert "tenant_not_allowed" in self._rejected(SOLO)

    def test_unknown_tenant_refused(self, salon: Tenant) -> None:
        assert "tenant_not_allowed" in self._rejected(SOLO)

    def test_event_outside_allowlist_refused_for_solo(self, salon: Tenant) -> None:
        _cabinet(slug=_solo_tenant_slug("max", OWNER_ID), owner_channel_user_id=OWNER_ID)
        with _pilot(), pytest.raises(TenantAuthorizationError) as exc:
            assert_envelope_tenant_authorized(
                _envelope(tenant_id=SOLO, event_name="appointment.rescheduled")
            )
        assert "event_not_allowed" in str(exc.value)

    def test_unconfigured_pilot_stays_deny_all(self) -> None:
        _cabinet(slug=_solo_tenant_slug("max", OWNER_ID), owner_channel_user_id=OWNER_ID)
        reason = self._rejected(
            SOLO,
            EVENT_INGEST_ALLOWED_TENANTS=frozenset(),
            EVENT_INGEST_ALLOWED_EVENTS=frozenset(),
        )
        assert "relationship_unavailable" in reason

    def test_non_canonical_tenant_id_never_reaches_the_db(self, salon: Tenant) -> None:
        with mock.patch("apps.identity.services.solo_onboarding.is_proven_solo_tenant") as proof:
            assert "tenant_not_allowed" in self._rejected(f" {SOLO} ")
            assert "tenant_not_allowed" in self._rejected(SOLO.replace("-", ""))
        proof.assert_not_called()

    def test_db_failure_fails_closed(self, salon: Tenant) -> None:
        _cabinet(slug=_solo_tenant_slug("max", OWNER_ID), owner_channel_user_id=OWNER_ID)
        with mock.patch(
            "apps.identity.services.solo_onboarding.is_proven_solo_tenant",
            side_effect=RuntimeError("db down"),
        ):
            assert "tenant_lookup_error" in self._rejected(SOLO)
