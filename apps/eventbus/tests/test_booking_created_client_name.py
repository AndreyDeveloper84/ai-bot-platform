"""``booking.created`` blank-fills the client's name from Ayla.

### The defect

A client who booked a solo master from the global bot got a fresh
per-tenant ``BotUser`` with no name, and the master's schedule showed
«Гость» although Ayla knows the person's name (stand, 13.11.2026).

### Pinned here

* a blank linked row gets ``display_name`` from the Ayla profile;
* a row that already has a name is left alone and Ayla is not asked;
* an unlinked row (no ``ayla_user_id``) is not looked up;
* a failed fetch does not fail the event — the mirror row is still
  written — and is logged with ids only;
* nothing but ``display_name`` is written: phone stays as it was.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any
from unittest.mock import patch

import pytest

from apps.booking.models import RemoteBookingProxy
from apps.eventbus.consumers.booking import handle_booking_created
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.identity.models import BotUser
from apps.integrations.ayla.profile_client import ProfileFetchError, ProfileFields
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

FETCH = "apps.integrations.ayla.profile_client.fetch_profile_fields"

TENANT_ID = "4b6e2f1a-9c3d-4e5f-8a7b-1c2d3e4f5a6b"
AYLA_USER_ID = "a1b2c3d4-e5f6-4789-9abc-def012345679"
APPOINTMENT_ID = "c9d8e7f6-1a2b-4c3d-8e9f-0a1b2c3d4e5f"
NAME = "Тестовое Имя"


@pytest.fixture(autouse=True)
def _allowlist(settings) -> None:
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
    settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset({TENANT_ID})
    settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset({"booking.created"})
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.AYLA_INTERNAL_API_TOKEN = "test-token"  # pragma: allowlist secret


@pytest.fixture
def tenant() -> Tenant:
    obj, _ = Tenant.objects.get_or_create(
        id=TENANT_ID, defaults={"slug": "t-client-name", "name": "Client name tenant"}
    )
    return obj


def _make_user(tenant: Tenant, **fields: Any) -> BotUser:
    defaults: dict[str, Any] = {
        "tenant": tenant,
        "channel": "max",
        "channel_user_id": "7001",
        "chat_id": "chat-7001",
        "ayla_user_id": AYLA_USER_ID,
    }
    defaults.update(fields)
    return BotUser.all_tenants.create(**defaults)


def _envelope() -> IngestEnvelope:
    return IngestEnvelope(
        event_id="01J9HXKM8Z2T4V6R8Q1P3D5F7X",  # pragma: allowlist secret
        event_name="booking.created",
        event_version=1,
        occurred_at=dt.datetime(2026, 11, 13, 16, 28, tzinfo=dt.timezone.utc),
        tenant_id=TENANT_ID,
        user_id=AYLA_USER_ID,
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567891",
        causation_id=None,
        data={
            "appointment_id": APPOINTMENT_ID,
            "specialist_id": "7c2d8e1f-0a5c-4c3a-9e1b-4d52f8eb3a18",
            "service_id": "3d5f7e1c-8a2d-4e6f-b9c0-1d2e3f4a5b6d",
            "start_at": "2030-05-22T15:00:00+03:00",
            "end_at": "2030-05-22T16:00:00+03:00",
            "status": "pending_payment",
            "price_total": "2500.00",
            "source": "bot",
        },
    )


def _profile() -> ProfileFields:
    return ProfileFields(display_name=NAME, avatar_url="")


def test_blank_linked_row_gets_display_name(tenant: Tenant) -> None:
    user = _make_user(tenant)
    with patch(FETCH, return_value=_profile()) as fetch:
        handle_booking_created(_envelope())
    fetch.assert_called_once()
    assert fetch.call_args.kwargs["on_behalf_of"] == "bot:max:7001"
    user.refresh_from_db()
    assert user.display_name == NAME
    assert user.client_name == ""


@pytest.mark.parametrize(
    "fields",
    [{"display_name": "Канал"}, {"client_name": "Сама назвалась"}],
    ids=["display_name_set", "client_name_set"],
)
def test_named_row_is_untouched(tenant: Tenant, fields: dict[str, str]) -> None:
    user = _make_user(tenant, **fields)
    with patch(FETCH, return_value=_profile()) as fetch:
        handle_booking_created(_envelope())
    fetch.assert_not_called()
    user.refresh_from_db()
    assert user.display_name == fields.get("display_name", "")
    assert user.client_name == fields.get("client_name", "")


def test_unlinked_row_is_not_looked_up(tenant: Tenant) -> None:
    """No ``ayla_user_id`` → the consumer cannot resolve the row at all."""
    _make_user(tenant, ayla_user_id=None)
    with patch(FETCH, return_value=_profile()) as fetch:
        handle_booking_created(_envelope())
    fetch.assert_not_called()


def test_fetch_failure_does_not_fail_the_event(
    tenant: Tenant, caplog: pytest.LogCaptureFixture
) -> None:
    user = _make_user(tenant)
    with (
        caplog.at_level(logging.WARNING, logger="apps.eventbus.consumers.booking"),
        patch(FETCH, side_effect=ProfileFetchError("ayla.profile circuit open")),
    ):
        handle_booking_created(_envelope())
    assert RemoteBookingProxy.all_tenants.filter(appointment_id=APPOINTMENT_ID).exists()
    user.refresh_from_db()
    assert user.display_name == ""
    messages = [r.getMessage() for r in caplog.records]
    assert any("client_name_fetch_failed" in m and "ProfileFetchError" in m for m in messages)


def test_only_display_name_is_written(tenant: Tenant) -> None:
    user = _make_user(tenant, phone="")
    with patch(FETCH, return_value=_profile()):
        handle_booking_created(_envelope())
    user.refresh_from_db()
    assert user.phone == ""
    assert user.display_name == NAME


def test_unconfigured_ayla_is_not_asked(tenant: Tenant, settings) -> None:
    settings.AYLA_BASE_URL = ""
    user = _make_user(tenant)
    with patch(FETCH, return_value=_profile()) as fetch:
        handle_booking_created(_envelope())
    fetch.assert_not_called()
    user.refresh_from_db()
    assert user.display_name == ""


def test_blank_profile_name_writes_nothing(tenant: Tenant) -> None:
    user = _make_user(tenant)
    with patch(FETCH, return_value=ProfileFields(display_name="  ", avatar_url="")):
        handle_booking_created(_envelope())
    user.refresh_from_db()
    assert user.display_name == ""
