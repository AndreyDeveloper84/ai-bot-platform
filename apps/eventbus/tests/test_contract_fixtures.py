"""Contract-fixture guards (Gamma A10).

Three jobs:

1. **Drift guard** — every fixture's sha256 matches ``MANIFEST.sha256``.
   The Ayla repo mirrors the same files + manifest and runs the
   equivalent assertion; if either copy is edited without regenerating
   the manifest, that repo's CI fails. This is the cross-repo anchor
   that kills the codex P0-1 ``payment.confirmed`` / ``payment.captured``
   divergence.

2. **Schema guard** — every *event* fixture parses cleanly through the
   production ``parse_envelope`` and its ``event_name`` matches the file
   name. If someone changes a fixture into a shape the consumer can't
   parse, this fails — exactly the "CI fails if fixture schema breaks
   parsing" acceptance criterion.

3. **Consumer reference** — the ``booking.created`` fixture is fed
   through the real ``handle_booking_created`` consumer, proving the
   canonical bytes drive a real consumer path (not a hand-rolled dict).
"""

from __future__ import annotations

from uuid import UUID

import pytest

from apps.eventbus.ingest_envelope import ALLOWED_EVENT_NAMES, parse_envelope
from tests.fixtures.contracts import (
    ALL_FIXTURES,
    EVENT_FIXTURES,
    compute_manifest,
    load_contract,
    read_manifest,
)

# Canonical IDs baked into the fixtures (taxonomy §2.1 / §3.1).
FIXTURE_TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
FIXTURE_USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
FIXTURE_APPOINTMENT_ID = "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"


class TestManifestIntegrity:
    """Drift guard — no DB, runs everywhere CI runs."""

    def test_manifest_matches_fixture_bytes(self) -> None:
        stored = read_manifest()
        computed = compute_manifest()
        assert stored == computed, (
            "Contract fixture(s) changed without regenerating the manifest. "
            "Run: python -m tests.fixtures.contracts --write-manifest"
        )

    def test_manifest_covers_every_fixture(self) -> None:
        assert set(read_manifest()) == set(ALL_FIXTURES)


class TestEventFixturesParse:
    """Schema guard — each event fixture survives production parsing."""

    @pytest.mark.parametrize("name", EVENT_FIXTURES)
    def test_event_fixture_parses(self, name: str) -> None:
        env = parse_envelope(load_contract(name))
        assert env.event_version == 1
        # File is named "<event_name>.v<version>.json".
        expected_event = name.rsplit(".v", 1)[0]
        assert env.event_name == expected_event
        assert env.event_name in ALLOWED_EVENT_NAMES

    @pytest.mark.parametrize("name", EVENT_FIXTURES)
    def test_event_fixture_has_non_null_tenant(self, name: str) -> None:
        # All booking/payment events MUST carry a tenant (§2.2).
        assert load_contract(name)["tenant_id"] is not None


class TestRecommendationsRequestShape:
    """The bot -> Ayla request body must stay within the fields Ayla's
    ``RecommendationsRequestSerializer`` accepts (lat / lon / goal). A
    stray field here means the bot would send something Ayla silently
    drops — the request-side flavour of contract drift.
    """

    def test_only_known_request_fields(self) -> None:
        body = load_contract("recommendations.request.json")
        assert set(body) <= {"lat", "lon", "goal"}

    def test_types_match_serializer(self) -> None:
        body = load_contract("recommendations.request.json")
        assert isinstance(body.get("lat"), (int, float))
        assert isinstance(body.get("lon"), (int, float))
        assert isinstance(body.get("goal"), str)


@pytest.mark.django_db
class TestBookingCreatedFixtureConsumer:
    """Consumer reference — the canonical fixture drives the real
    ``booking.*`` consumer, end to end through ``parse_envelope``.
    """

    @pytest.fixture(autouse=True)
    def _fail_open(self, settings) -> None:
        # Pre-#246 tenant-verify bridge (Round-3 NEW-5), same as
        # test_booking_consumer.py.
        settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True

    @pytest.fixture
    def tenant(self):
        from apps.tenancy.models import Tenant

        return Tenant.objects.create(
            id=FIXTURE_TENANT_ID, slug="t-contract", name="Contract fixture tenant"
        )

    @pytest.fixture
    def bot_user_linked(self, tenant):
        from apps.identity.models import BotUser

        return BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="9100",
            chat_id="chat-9100",
            ayla_user_id=FIXTURE_USER_ID,
        )

    def test_fixture_drives_booking_consumer(self, tenant, bot_user_linked) -> None:
        from apps.booking.models import RemoteBookingProxy
        from apps.eventbus.consumers.booking import handle_booking_created

        env = parse_envelope(load_contract("booking.created.v1.json"))
        handle_booking_created(env)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(FIXTURE_APPOINTMENT_ID))
        assert str(proxy.tenant_id) == FIXTURE_TENANT_ID
        assert proxy.bot_user_id == bot_user_linked.id
        assert proxy.status == "confirmed"
        assert proxy.source == "mobile_app"
        assert proxy.last_synced_event_id == env.event_id
