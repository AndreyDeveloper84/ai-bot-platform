"""Tests for the catalog event consumer (#444).

Covers ``handle_service_updated`` per event-contract.md §3.10:

* Cache version bumps on every event.
* is_active toggles when in changed_fields (inferred from previous_values).
* No-op when mirror row absent (Ayla service not yet synced).
* Idempotency replay-3× — counter bumps exactly once when called via
  the dispatcher (IngestDedupe blocks replays). Handler-direct
  invocation 3× DOES bump 3× because handler-level idempotency lives
  at the dispatcher layer for this event family (mirror updates are
  cheap and additive — strict double-side-effect risk doesn't apply
  like for loyalty fan-out in payment.*).
* Tenant-verify mandate (A3) — null tenant_id raises.
* PII rule §7 — only IDs + booleans in payload, nothing to leak.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID, uuid4

import pytest

from apps.catalog.models import CatalogService
from apps.eventbus.consumers.catalog import (
    _infer_new_is_active,
    handle_service_updated,
)
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
AYLA_SERVICE_ID = "3d5f7e1c-8a2d-4e6f-b9c0-1d2e3f4a5b6c"
ADMIN_USER_ID = "e3f4a5b6-c7d8-4e9f-0a1b-2c3d4e5f6a7b"


# ─── helpers ───────────────────────────────────────────────────────────────


def _envelope(
    *,
    data: dict[str, Any],
    event_id: str = "01J9SERVICE000000000000000",  # pragma: allowlist secret
    tenant_id: str | None = TENANT_ID,
) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id,
        event_name="service.updated",
        event_version=1,
        occurred_at=dt.datetime(2026, 5, 22, 11, 8, 25, tzinfo=dt.timezone.utc),
        tenant_id=tenant_id,
        user_id=ADMIN_USER_ID,
        actor="admin",
        correlation_id="d4e5f6a7-b8c9-0123-def0-345678901234",
        causation_id=None,
        data=data,
    )


def _service_updated_data(
    *,
    service_id: str = AYLA_SERVICE_ID,
    changed_fields: list[str] | None = None,
    previous_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "service_id": service_id,
        "changed_fields": changed_fields if changed_fields is not None else ["price"],
        "previous_values": previous_values if previous_values is not None else {"price": "1800.00"},
    }


@pytest.fixture(autouse=True)
def _enable_tenant_verify_fail_open(settings) -> None:
    """Round-3 NEW-5 bridge — pre-#246 transition mode."""
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(
        id=TENANT_ID,
        slug="t-catalog",
        name="Catalog test tenant",
    )


@pytest.fixture
def service_mirror(tenant: Tenant) -> CatalogService:
    """A mirror row already provisioned for the Ayla service."""
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=42,  # legacy mysite id (coexists)
        ayla_service_id=UUID(AYLA_SERVICE_ID),
        external_updated_at=dt.datetime(2026, 5, 20, 10, 0, tzinfo=dt.timezone.utc),
        slug="haircut",
        name="Стрижка",
        price_from="1800.00",
        duration_min=60,
        is_active=True,
        cache_version=0,
    )


# ─── _infer_new_is_active helper ───────────────────────────────────────────


class TestInferNewIsActive:
    def test_previous_true_returns_false(self) -> None:
        assert _infer_new_is_active({"is_active": True}) is False

    def test_previous_false_returns_true(self) -> None:
        assert _infer_new_is_active({"is_active": False}) is True

    def test_missing_key_returns_none(self) -> None:
        assert _infer_new_is_active({"price": "1800.00"}) is None

    def test_non_bool_returns_none_defensive(self) -> None:
        """Contract encodes booleans as JSON true/false. A string
        value implies contract drift — refuse to guess."""
        assert _infer_new_is_active({"is_active": "true"}) is None

    def test_empty_dict_returns_none(self) -> None:
        assert _infer_new_is_active({}) is None

    @pytest.mark.parametrize(
        "value",
        [
            None,  # JSON null
            0,  # int falsy — must NOT be treated as bool(False)
            1,  # int truthy — must NOT be treated as bool(True)
            [],  # list
            {},  # nested dict
            "false",  # string «false»
            "True",  # capitalized string
            42,  # arbitrary int
            1.0,  # float
        ],
    )
    def test_non_bool_edge_cases_return_none(self, value: Any) -> None:
        """Round-1 F3: ``isinstance(x, bool)`` correctly rejects int 0/1
        because ``bool`` is a subclass of ``int`` (not the reverse).
        Pin this across many edge cases so a future refactor doesn't
        widen to ``bool(prev)`` which would coerce ints + strings."""
        assert _infer_new_is_active({"is_active": value}) is None


# ─── Round-1 N2: is_active in changed_fields but previous_values empty ─────


class TestIsActiveWithoutPreviousValue:
    """Round-1 N2: ``changed_fields=["is_active"]`` but
    ``previous_values={}`` — handler must bump cache_version without
    flipping is_active (no value to infer from)."""

    def test_is_active_listed_but_missing_previous_value(
        self, tenant: Tenant, service_mirror: CatalogService
    ) -> None:
        env = _envelope(
            data=_service_updated_data(
                changed_fields=["is_active"],
                previous_values={},  # no is_active key
            )
        )
        handle_service_updated(env)

        service_mirror.refresh_from_db()
        assert service_mirror.cache_version == 1
        assert service_mirror.is_active is True  # unchanged

    def test_is_active_listed_with_non_bool_previous(
        self, tenant: Tenant, service_mirror: CatalogService
    ) -> None:
        env = _envelope(
            data=_service_updated_data(
                changed_fields=["is_active"],
                previous_values={"is_active": "yes"},  # string, not bool
            )
        )
        handle_service_updated(env)

        service_mirror.refresh_from_db()
        assert service_mirror.cache_version == 1
        assert service_mirror.is_active is True  # unchanged — fail-safe


# ─── handle_service_updated ────────────────────────────────────────────────


class TestServiceUpdatedHappyPath:
    def test_bumps_cache_version_on_price_change(
        self, tenant: Tenant, service_mirror: CatalogService
    ) -> None:
        env = _envelope(
            data=_service_updated_data(
                changed_fields=["price"],
                previous_values={"price": "1800.00"},
            )
        )
        handle_service_updated(env)

        service_mirror.refresh_from_db()
        assert service_mirror.cache_version == 1
        # Canonical fields untouched — contract forbids proactive refetch.
        assert service_mirror.name == "Стрижка"
        assert str(service_mirror.price_from) == "1800.00"
        assert service_mirror.is_active is True

    def test_bumps_cache_version_on_duration_change(
        self, tenant: Tenant, service_mirror: CatalogService
    ) -> None:
        env = _envelope(
            data=_service_updated_data(
                changed_fields=["duration"],
                previous_values={"duration": "60"},
            )
        )
        handle_service_updated(env)

        service_mirror.refresh_from_db()
        assert service_mirror.cache_version == 1
        # Canonical duration_min NOT updated — new value not in payload.
        assert service_mirror.duration_min == 60

    def test_flips_is_active_to_false_when_in_changed_fields(
        self, tenant: Tenant, service_mirror: CatalogService
    ) -> None:
        env = _envelope(
            data=_service_updated_data(
                changed_fields=["is_active"],
                previous_values={"is_active": True},
            )
        )
        handle_service_updated(env)

        service_mirror.refresh_from_db()
        assert service_mirror.cache_version == 1
        assert service_mirror.is_active is False  # flipped from True

    def test_flips_is_active_to_true_when_previously_false(
        self, tenant: Tenant, service_mirror: CatalogService
    ) -> None:
        service_mirror.is_active = False
        service_mirror.save(update_fields=["is_active"])

        env = _envelope(
            data=_service_updated_data(
                changed_fields=["is_active"],
                previous_values={"is_active": False},
            )
        )
        handle_service_updated(env)

        service_mirror.refresh_from_db()
        assert service_mirror.is_active is True  # flipped from False

    def test_multiple_changed_fields_one_bump(
        self, tenant: Tenant, service_mirror: CatalogService
    ) -> None:
        """changed_fields = [price, duration] — both bump the cache
        once per event (not per field)."""
        env = _envelope(
            data=_service_updated_data(
                changed_fields=["price", "duration"],
                previous_values={"price": "1800.00", "duration": "60"},
            )
        )
        handle_service_updated(env)

        service_mirror.refresh_from_db()
        assert service_mirror.cache_version == 1


# ─── No mirror row ─────────────────────────────────────────────────────────


class TestServiceUpdatedNoMirror:
    def test_no_mirror_row_logs_and_returns(self, tenant: Tenant) -> None:
        """Ayla emitted service.updated for a service we haven't
        mirrored yet (sync race). Handler logs + no-op. No stub
        created — we don't have canonical fields in the payload."""
        env = _envelope(data=_service_updated_data())
        handle_service_updated(env)

        # No row created.
        assert CatalogService.all_tenants.filter(ayla_service_id=UUID(AYLA_SERVICE_ID)).count() == 0

    def test_unknown_service_id_does_not_touch_other_services(
        self, tenant: Tenant, service_mirror: CatalogService
    ) -> None:
        """Event for an unknown Ayla service must NOT bump cache_version
        on the mirror row for a DIFFERENT service."""
        other_uuid = str(uuid4())
        env = _envelope(data=_service_updated_data(service_id=other_uuid))
        handle_service_updated(env)

        service_mirror.refresh_from_db()
        assert service_mirror.cache_version == 0  # unchanged


# ─── Repeated handler calls (handler-direct, NOT through dispatcher) ───────


class TestHandlerDirectReplay:
    """Direct handler invocation 3× DOES bump 3× — handler-level
    idempotency for service.updated lives at the dispatcher layer
    (IngestDedupe). Mirror cache_version bumps are idempotent in
    the «converges to right value» sense: 3 bumps = version 3, still
    a valid cache-key change, no double-money side-effects.

    This is intentionally different from payment.captured /
    payment.refunded which have PaymentTerminalDedupe at the handler
    level — those events trigger loyalty fan-out where a duplicate
    emit causes real billing miscompute. service.updated has no such
    fan-out; replay is safe.

    The dispatcher-level idempotency contract is tested in
    ``tests/contracts/test_event_idempotency.py``.
    """

    def test_direct_3x_calls_increment_version_3_times(
        self, tenant: Tenant, service_mirror: CatalogService
    ) -> None:
        env = _envelope(data=_service_updated_data())
        for _ in range(3):
            handle_service_updated(env)

        service_mirror.refresh_from_db()
        assert service_mirror.cache_version == 3


# ─── Tenant-verify mandate (A3) ────────────────────────────────────────────


class TestTenantVerifyMandate:
    def test_raises_when_tenant_id_null(self) -> None:
        from apps.eventbus.ingest_tenancy import TenantAuthorizationError

        env = _envelope(data=_service_updated_data(), tenant_id=None)
        with pytest.raises(TenantAuthorizationError):
            handle_service_updated(env)


# ─── Malformed payload defence ─────────────────────────────────────────────


class TestMalformedPayloadDefence:
    def test_missing_service_id_logs_and_returns(self, tenant: Tenant) -> None:
        env = _envelope(data={"changed_fields": ["price"]})
        # No raise — handler logs warning + returns.
        handle_service_updated(env)

    def test_non_uuid_service_id_logs_and_returns(self, tenant: Tenant) -> None:
        env = _envelope(data={"service_id": "not-a-uuid", "changed_fields": []})
        handle_service_updated(env)

    def test_empty_changed_fields_still_bumps_cache(
        self, tenant: Tenant, service_mirror: CatalogService
    ) -> None:
        """changed_fields=[] is technically contract-valid (publisher
        may emit it as a «touch» signal). Handler bumps cache anyway
        — the event itself is the «something changed» signal."""
        env = _envelope(data={"service_id": AYLA_SERVICE_ID, "changed_fields": []})
        handle_service_updated(env)

        service_mirror.refresh_from_db()
        assert service_mirror.cache_version == 1


# ─── Cross-tenant isolation ────────────────────────────────────────────────


class TestCrossTenantIsolation:
    """Round-1 F1 fix: the handler now filters by ``tenant_id``
    explicitly. Cross-tenant collision on ``ayla_service_id`` (test
    leak, future shared-catalog feature, malicious publisher) MUST
    NOT mutate the other tenant's mirror row."""

    def test_event_for_other_tenant_does_not_touch_this_tenants_mirror(
        self, tenant: Tenant, service_mirror: CatalogService
    ) -> None:
        """Two mirrors with the SAME ``ayla_service_id`` under
        different tenants. An event tagged ``tenant_id=other_tenant``
        MUST bump only the other tenant's row, leaving this tenant's
        mirror untouched."""
        other_tenant = Tenant.objects.create(
            id="11111111-2222-3333-4444-555555555555",
            slug="t-other",
            name="Other tenant",
        )
        other_mirror = CatalogService.all_tenants.create(
            tenant=other_tenant,
            external_id=42,
            ayla_service_id=UUID(AYLA_SERVICE_ID),  # same UUID, different tenant
            external_updated_at=dt.datetime(2026, 5, 20, 10, 0, tzinfo=dt.timezone.utc),
            slug="haircut",
            name="Стрижка",
            cache_version=0,
        )

        # Event tagged with the OTHER tenant.
        env = _envelope(
            data=_service_updated_data(),
            tenant_id=str(other_tenant.id),
        )
        handle_service_updated(env)

        service_mirror.refresh_from_db()
        other_mirror.refresh_from_db()
        # This tenant's mirror UNTOUCHED — F1 fix proof.
        assert service_mirror.cache_version == 0
        # Other tenant's mirror bumped as expected.
        assert other_mirror.cache_version == 1

    def test_event_for_this_tenant_does_not_touch_other_tenants_mirror(
        self, tenant: Tenant, service_mirror: CatalogService
    ) -> None:
        """Symmetric: our tenant's event must not bump the other
        tenant's mirror even when both share an ``ayla_service_id``."""
        other_tenant = Tenant.objects.create(
            id="22222222-3333-4444-5555-666666666666",
            slug="t-other-sym",
            name="Other tenant symmetric",
        )
        other_mirror = CatalogService.all_tenants.create(
            tenant=other_tenant,
            external_id=43,
            ayla_service_id=UUID(AYLA_SERVICE_ID),
            external_updated_at=dt.datetime(2026, 5, 20, 10, 0, tzinfo=dt.timezone.utc),
            slug="haircut",
            name="Стрижка",
            cache_version=0,
        )

        env = _envelope(data=_service_updated_data(), tenant_id=TENANT_ID)
        handle_service_updated(env)

        service_mirror.refresh_from_db()
        other_mirror.refresh_from_db()
        assert service_mirror.cache_version == 1  # ours bumped
        assert other_mirror.cache_version == 0  # other untouched


# ─── Payload shape defence (Round-1 F2) ────────────────────────────────────


class TestPayloadShapeDefence:
    """Round-1 F2: publisher contract drift or malicious payload can
    send non-list ``changed_fields`` or non-dict ``previous_values``.
    Handler MUST defend rather than crash into DLQ retry loop."""

    def test_non_list_changed_fields_treated_as_empty(
        self, tenant: Tenant, service_mirror: CatalogService
    ) -> None:
        env = _envelope(
            data={
                "service_id": AYLA_SERVICE_ID,
                "changed_fields": "not-a-list",  # publisher drift
                "previous_values": {},
            }
        )
        # Must not raise — handler logs + treats as empty list.
        handle_service_updated(env)

        service_mirror.refresh_from_db()
        # cache_version still bumps (event itself is a «touch»).
        assert service_mirror.cache_version == 1
        # is_active untouched (changed_fields treated as empty).
        assert service_mirror.is_active is True

    def test_non_dict_previous_values_treated_as_empty(
        self, tenant: Tenant, service_mirror: CatalogService
    ) -> None:
        env = _envelope(
            data={
                "service_id": AYLA_SERVICE_ID,
                "changed_fields": ["is_active"],
                "previous_values": 42,  # publisher drift
            }
        )
        handle_service_updated(env)

        service_mirror.refresh_from_db()
        assert service_mirror.cache_version == 1
        # is_active inference fails (no key) → unchanged.
        assert service_mirror.is_active is True

    def test_non_string_changed_field_entries_filtered_out(
        self, tenant: Tenant, service_mirror: CatalogService
    ) -> None:
        """Mixed list: some valid strings, some non-string entries.
        Non-strings get dropped; the valid ones still process."""
        env = _envelope(
            data={
                "service_id": AYLA_SERVICE_ID,
                "changed_fields": ["price", 42, None, {"nested": "object"}],
                "previous_values": {"price": "1800.00"},
            }
        )
        handle_service_updated(env)

        service_mirror.refresh_from_db()
        assert service_mirror.cache_version == 1

    def test_substring_false_positive_blocked(
        self, tenant: Tenant, service_mirror: CatalogService
    ) -> None:
        """Round-1 F2 substring attack: `"is_active" in "is_active_xyz"`
        returns True for strings, which would flip is_active on a
        malformed payload. The list-filter prevents this — the string
        becomes the whole list element, not a substring container."""
        env = _envelope(
            data={
                "service_id": AYLA_SERVICE_ID,
                "changed_fields": "is_active_xyz",  # NOT a list, fail-closed to []
                "previous_values": {"is_active": True},
            }
        )
        handle_service_updated(env)

        service_mirror.refresh_from_db()
        # is_active NOT flipped — changed_fields was sanitized to [].
        assert service_mirror.is_active is True
