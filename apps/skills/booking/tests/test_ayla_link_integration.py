"""Booking × identity-resolution integration (DRF-1035).

Owner §15 rows 8-9 and AC-2 / AC-12 on the bot side: a booking by a user
Ayla has never resolved must now succeed, and the ``client_id`` the bot puts
in the create body must be exactly the subject Ayla resolved from the header
— that equality is what makes Ayla's cross-check
(``appointments/internal_api.py:166-174``) pass instead of 403.

The pre-DRF-1035 behaviour is locked here too: when identity cannot be
resolved, ``create_record`` must still fail loudly with
``ayla_client_id_missing`` rather than send an empty ``client_id`` (which
Ayla would 403) or, worse, silently book against the wrong subject.
"""

from __future__ import annotations

import uuid

import pytest
from django.test import override_settings

from apps.identity.models import BotUser
from apps.integrations.ayla.identity_client import IdentityResolveError, ResolvedIdentity
from apps.integrations.yclients.client import YClientsAPIError
from apps.skills.booking.provider import AylaYClientsAdapter, get_booking_provider
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


AYLA_SETTINGS = {
    "BOOKING_VIA_AYLA_REST": True,
    "AYLA_BASE_URL": "https://ayla.test",
    "AYLA_INTERNAL_API_TOKEN": "t",
}


@pytest.fixture
def bot_user() -> BotUser:
    tenant = Tenant.objects.create(slug="drf1035-booking", name="DRF-1035 Booking")
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="260237491",
        chat_id="260237491",
        client_name="Андрей",
    )


@pytest.fixture
def resolved_uuid(monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    uid = uuid.uuid4()
    monkeypatch.setattr(
        "apps.integrations.ayla.identity_client.resolve_identity",
        lambda external_user_id: ResolvedIdentity(ayla_user_id=uid, is_proxy=True),
        raising=True,
    )
    return uid


@pytest.fixture
def failing_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(external_user_id: str) -> ResolvedIdentity:
        raise IdentityResolveError("network: ConnectError")

    monkeypatch.setattr(
        "apps.integrations.ayla.identity_client.resolve_identity", _boom, raising=True
    )


# ─── §15.8 booking after identity resolution ────────────────────────────────


@override_settings(**AYLA_SETTINGS)
def test_unlinked_user_gets_client_id_from_resolution(
    bot_user: BotUser, resolved_uuid: uuid.UUID
) -> None:
    """The DRF-1035 incident, inverted: this used to yield an empty client_id."""
    assert bot_user.ayla_user_id is None

    provider = get_booking_provider(bot_user=bot_user)

    assert isinstance(provider, AylaYClientsAdapter)
    assert provider._client_id == str(resolved_uuid)
    assert provider._external_user_id == "bot:max:260237491"


@override_settings(**AYLA_SETTINGS)
def test_resolution_is_persisted_for_later_capabilities(
    bot_user: BotUser, resolved_uuid: uuid.UUID
) -> None:
    # Booking is usually the first identity-dependent action; the link it
    # establishes is what later unblocks memory, consent reads and the
    # inbound eventbus consumers for this person.
    get_booking_provider(bot_user=bot_user)

    bot_user.refresh_from_db()
    assert bot_user.ayla_user_id == resolved_uuid


@override_settings(**AYLA_SETTINGS)
def test_already_linked_user_needs_no_resolution(bot_user: BotUser) -> None:
    known = uuid.uuid4()
    bot_user.ayla_user_id = known
    bot_user.save(update_fields=["ayla_user_id"])

    # No resolve_identity stub installed: a network call here would blow up
    # the test, which is exactly the assertion (AC-3).
    provider = get_booking_provider(bot_user=bot_user)

    assert provider._client_id == str(known)


# ─── §15.9 / AC-12 the cross-check contract ─────────────────────────────────


@override_settings(**AYLA_SETTINGS)
def test_body_client_id_equals_resolved_subject(
    bot_user: BotUser, resolved_uuid: uuid.UUID
) -> None:
    """What Ayla cross-checks: body.client_id == the header-resolved user.

    The bot sends the subject twice — once in ``X-External-User-ID`` (which
    Ayla resolves) and once as ``client_id`` in the body (which Ayla compares
    against the resolution). DRF-1035 does not weaken that check; it makes the
    two agree. Asserting the pairing here is what catches a future refactor
    that starts sourcing ``client_id`` from somewhere else.
    """
    provider = get_booking_provider(bot_user=bot_user)

    assert provider._client_id == str(resolved_uuid)
    assert provider._external_user_id == "bot:max:260237491"


# ─── graceful degradation is unchanged ──────────────────────────────────────


@override_settings(**AYLA_SETTINGS)
def test_unresolvable_identity_still_fails_loudly(bot_user: BotUser, failing_resolve: None) -> None:
    """Ayla down → the pre-DRF-1035 failure path, byte-for-byte.

    ``ayla_client_id_missing`` is what the booking skill turns into an
    AdminTask + operator notification (DRF-1029). Degrading to a *silent*
    failure — or to a create with an empty client_id — would be worse than
    the bug this ticket fixes.
    """
    provider = get_booking_provider(bot_user=bot_user)
    assert provider._client_id == ""

    with pytest.raises(YClientsAPIError, match="ayla_client_id_missing"):
        provider.create_record(
            staff_id="spec-1",
            services=["svc-1"],
            datetime="2026-08-20T10:00:00+03:00",
            client_phone="",
            client_name="Андрей",
        )

    bot_user.refresh_from_db()
    assert bot_user.ayla_user_id is None


@override_settings(**AYLA_SETTINGS)
def test_failed_resolution_is_retried_on_the_next_attempt(
    bot_user: BotUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    uid = uuid.uuid4()

    def _flaky(external_user_id: str) -> ResolvedIdentity:
        calls.append(external_user_id)
        if len(calls) == 1:
            raise IdentityResolveError("server: HTTP 502")
        return ResolvedIdentity(ayla_user_id=uid, is_proxy=True)

    monkeypatch.setattr(
        "apps.integrations.ayla.identity_client.resolve_identity", _flaky, raising=True
    )

    assert get_booking_provider(bot_user=bot_user)._client_id == ""
    assert get_booking_provider(bot_user=bot_user)._client_id == str(uid)
    assert len(calls) == 2
