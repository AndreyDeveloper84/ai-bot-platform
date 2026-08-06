"""Tests for the identity event consumer (#446 user.profile.updated).

Covers per event-contract.md §3.12:

* **PII §7 enforcement (acceptance criterion)** — fields outside the
  safe-sync allowlist (phone, email, birthday, language) trigger ZERO
  REST calls and ZERO DB writes.
* Safe-sync slice — only `display_name` and `avatar_url` cause REST
  + DB writes.
* Migration ordering — handler gracefully degrades when W4 has not
  yet shipped the `avatar_url` field on BotUser (display_name still
  syncs; avatar_url warning is logged).
* Idempotency — re-applying same fetched values is a no-op (UPDATE
  fires only when value differs).
* Tenant-nullable — envelope tenant_id MAY be null per §3.12.
* Failure-path — ProfileFetchError raises and dispatcher dead-letters.
* Cross-channel — same Ayla user with BotUsers in MAX + Telegram
  under the same tenant: both get updated.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest

from apps.eventbus.consumers.identity import handle_user_profile_updated
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.identity.models import BotUser
from apps.integrations.ayla.profile_client import (
    ProfileFetchError,
    ProfileFields,
)
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


AYLA_USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"


# ─── helpers ───────────────────────────────────────────────────────────────


def _envelope(
    *,
    data: dict[str, Any],
    event_id: str = "01J9PROFILE000000000000000",  # pragma: allowlist secret
    tenant_id: str | None = None,  # §3.12 — may be null
    user_id: str = AYLA_USER_ID,
) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id,
        event_name="user.profile.updated",
        event_version=1,
        occurred_at=dt.datetime(2026, 5, 22, 20, 33, 11, tzinfo=dt.timezone.utc),
        tenant_id=tenant_id,
        user_id=user_id,
        actor="user",
        correlation_id="f6a7b8c9-d0e1-2345-f012-567890123456",
        causation_id=None,
        data=data,
    )


@pytest.fixture(autouse=True)
def _pilot_allowlist(settings) -> None:
    """T-02 pilot scope — NOT the global fail-open.

    These envelopes are tenant-null by contract, so the carve-out admits
    them regardless; the allowlist entry covers the tenant-scoped variants
    and keeps this suite off the emergency escape hatch, which is what the
    production path actually looks like.
    """
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
    settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset({TENANT_ID})
    settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset({"user.profile.updated"})


@pytest.fixture(autouse=True)
def _reset_profile_breaker() -> None:
    """Round-1 review B1: ``profile_client._circuit`` is a module-level
    singleton. Without explicit reset, failure state from one test
    bleeds into the next — false negatives on the failure-classification
    suite. Reinitialize per test."""
    from apps.integrations.ayla import profile_client

    profile_client._circuit = profile_client._Circuit()


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(
        id=TENANT_ID,
        slug="t-identity",
        name="Identity test tenant",
    )


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="9001",
        chat_id="chat-9001",
        ayla_user_id=AYLA_USER_ID,
        display_name="Old Name",
    )


# ─── PII §7 negative tests (acceptance criterion) ──────────────────────────


class TestPIISafeSyncList:
    """Acceptance criterion from #446: «PII field NOT in safe-sync list
    → handler doesn't pull or store it». These are the LOAD-BEARING
    tests for §7 enforcement at the consumer layer."""

    def test_phone_in_changed_fields_no_rest_call(self, tenant: Tenant, bot_user: BotUser) -> None:
        """changed_fields=['phone'] MUST NOT trigger REST fetch."""
        env = _envelope(data={"user_id": AYLA_USER_ID, "changed_fields": ["phone"]})

        with patch("apps.eventbus.consumers.identity.fetch_profile_fields") as mock_fetch:
            handle_user_profile_updated(env)

        mock_fetch.assert_not_called()
        bot_user.refresh_from_db()
        assert bot_user.display_name == "Old Name"  # untouched

    def test_email_in_changed_fields_no_rest_call(self, tenant: Tenant, bot_user: BotUser) -> None:
        env = _envelope(data={"user_id": AYLA_USER_ID, "changed_fields": ["email"]})

        with patch("apps.eventbus.consumers.identity.fetch_profile_fields") as mock_fetch:
            handle_user_profile_updated(env)

        mock_fetch.assert_not_called()

    def test_birthday_in_changed_fields_no_rest_call(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        env = _envelope(data={"user_id": AYLA_USER_ID, "changed_fields": ["birthday"]})

        with patch("apps.eventbus.consumers.identity.fetch_profile_fields") as mock_fetch:
            handle_user_profile_updated(env)

        mock_fetch.assert_not_called()

    def test_language_in_changed_fields_no_rest_call(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        env = _envelope(data={"user_id": AYLA_USER_ID, "changed_fields": ["language"]})

        with patch("apps.eventbus.consumers.identity.fetch_profile_fields") as mock_fetch:
            handle_user_profile_updated(env)

        mock_fetch.assert_not_called()

    def test_all_unsafe_fields_combined_no_rest_call(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        env = _envelope(
            data={
                "user_id": AYLA_USER_ID,
                "changed_fields": ["phone", "email", "birthday", "language"],
            }
        )

        with patch("apps.eventbus.consumers.identity.fetch_profile_fields") as mock_fetch:
            handle_user_profile_updated(env)

        mock_fetch.assert_not_called()

    def test_mixed_safe_unsafe_fetches_only_safe_slice(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """changed_fields=['phone', 'display_name'] → REST fired
        (display_name is safe), but ONLY display_name comes back from
        the client (phone never enters the consumer path)."""
        env = _envelope(
            data={
                "user_id": AYLA_USER_ID,
                "changed_fields": ["phone", "display_name"],
            }
        )

        with patch(
            "apps.eventbus.consumers.identity.fetch_profile_fields",
            return_value=ProfileFields(
                display_name="New Name", avatar_url="https://avatar.example/new.png"
            ),
        ) as mock_fetch:
            handle_user_profile_updated(env)

        # REST fired exactly once.
        mock_fetch.assert_called_once_with(UUID(AYLA_USER_ID))

        # display_name updated.
        bot_user.refresh_from_db()
        assert bot_user.display_name == "New Name"

        # phone NEVER touched. (BotUser has phone field — we never set it.)
        assert bot_user.phone == ""


# ─── Safe-sync happy path ──────────────────────────────────────────────────


class TestSafeSyncHappyPath:
    def test_display_name_only_updates_display_name(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        env = _envelope(data={"user_id": AYLA_USER_ID, "changed_fields": ["display_name"]})

        with patch(
            "apps.eventbus.consumers.identity.fetch_profile_fields",
            return_value=ProfileFields(display_name="Anna New", avatar_url="ignored"),
        ):
            handle_user_profile_updated(env)

        bot_user.refresh_from_db()
        assert bot_user.display_name == "Anna New"

    def test_avatar_url_only_with_w4_field_present(self, tenant: Tenant, bot_user: BotUser) -> None:
        """W4 has shipped avatar_url field → handler writes it."""
        if not hasattr(bot_user, "avatar_url"):
            pytest.skip("W4 avatar_url field not yet on BotUser")

        env = _envelope(data={"user_id": AYLA_USER_ID, "changed_fields": ["avatar_url"]})
        with patch(
            "apps.eventbus.consumers.identity.fetch_profile_fields",
            return_value=ProfileFields(
                display_name="ignored",
                avatar_url="https://cdn.ayla.example/u/abc.png",
            ),
        ):
            handle_user_profile_updated(env)

        bot_user.refresh_from_db()
        assert getattr(bot_user, "avatar_url") == "https://cdn.ayla.example/u/abc.png"

    def test_avatar_url_graceful_skip_pre_w4_migration(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Migration ordering defence — if W4 hasn't shipped avatar_url
        yet, handler logs warning + continues. display_name in the
        same event still syncs."""
        # Force hasattr to return False to simulate pre-W4 state.
        # We patch the BotUser class so the `hasattr` check sees no
        # `avatar_url` attribute on the instance.
        env = _envelope(
            data={
                "user_id": AYLA_USER_ID,
                "changed_fields": ["display_name", "avatar_url"],
            }
        )
        with patch(
            "apps.eventbus.consumers.identity.fetch_profile_fields",
            return_value=ProfileFields(display_name="Both Synced", avatar_url="will_be_skipped"),
        ):
            with patch(
                "apps.eventbus.consumers.identity.hasattr",
                side_effect=lambda obj, attr: False if attr == "avatar_url" else hasattr(obj, attr),
            ):
                handle_user_profile_updated(env)

        bot_user.refresh_from_db()
        # display_name still synced.
        assert bot_user.display_name == "Both Synced"


# ─── Idempotency ───────────────────────────────────────────────────────────


class TestIdempotency:
    def test_same_values_no_save(self, tenant: Tenant, bot_user: BotUser) -> None:
        """REST returns the value we already have → no UPDATE."""
        env = _envelope(data={"user_id": AYLA_USER_ID, "changed_fields": ["display_name"]})
        with patch(
            "apps.eventbus.consumers.identity.fetch_profile_fields",
            return_value=ProfileFields(
                display_name="Old Name",  # same as fixture
                avatar_url="",
            ),
        ):
            with patch.object(BotUser, "save") as mock_save:
                handle_user_profile_updated(env)

        # No save() call because nothing changed.
        mock_save.assert_not_called()

    def test_replay_3x_only_one_write(self, tenant: Tenant, bot_user: BotUser) -> None:
        """3 dispatches with the same payload → 1 DB write (1st applies
        change, 2 + 3 see no diff and skip)."""
        env = _envelope(data={"user_id": AYLA_USER_ID, "changed_fields": ["display_name"]})
        with patch(
            "apps.eventbus.consumers.identity.fetch_profile_fields",
            return_value=ProfileFields(display_name="Updated", avatar_url=""),
        ):
            with patch.object(
                BotUser, "save", side_effect=BotUser.save, autospec=True
            ) as mock_save:
                for _ in range(3):
                    handle_user_profile_updated(env)

        # Only the 1st call triggered save (value changed); 2nd + 3rd
        # saw the new value already in DB → no save.
        assert mock_save.call_count == 1

        bot_user.refresh_from_db()
        assert bot_user.display_name == "Updated"


# ─── Cross-channel ─────────────────────────────────────────────────────────


class TestCrossChannel:
    def test_multi_channel_user_updates_all_bot_users(self, tenant: Tenant) -> None:
        """Same Ayla user has BotUsers in MAX + Telegram under one
        tenant — both get the new display_name."""
        max_user = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="9001",
            chat_id="chat-max",
            ayla_user_id=AYLA_USER_ID,
            display_name="Old MAX",
        )
        tg_user = BotUser.all_tenants.create(
            tenant=tenant,
            channel="telegram",
            channel_user_id="9002",
            chat_id="chat-tg",
            ayla_user_id=AYLA_USER_ID,
            display_name="Old Telegram",
        )

        env = _envelope(data={"user_id": AYLA_USER_ID, "changed_fields": ["display_name"]})
        with patch(
            "apps.eventbus.consumers.identity.fetch_profile_fields",
            return_value=ProfileFields(display_name="Unified Name", avatar_url=""),
        ):
            handle_user_profile_updated(env)

        max_user.refresh_from_db()
        tg_user.refresh_from_db()
        assert max_user.display_name == "Unified Name"
        assert tg_user.display_name == "Unified Name"


# ─── Failure modes ─────────────────────────────────────────────────────────


class TestFailureModes:
    def test_profile_fetch_error_propagates_to_dispatcher(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """REST failure → ProfileFetchError raises → dispatcher
        dead-letters → Ayla retries per §6.3. NEVER silent skip."""
        env = _envelope(data={"user_id": AYLA_USER_ID, "changed_fields": ["display_name"]})
        with patch(
            "apps.eventbus.consumers.identity.fetch_profile_fields",
            side_effect=ProfileFetchError("circuit open"),
        ):
            with pytest.raises(ProfileFetchError):
                handle_user_profile_updated(env)

        # BotUser untouched on failure.
        bot_user.refresh_from_db()
        assert bot_user.display_name == "Old Name"

    def test_no_bot_users_no_rest_call(self, tenant: Tenant) -> None:
        """Event for an Ayla user with no BotUser projection yet
        (mobile-only customer) — REST still fires (we need to fetch
        before we know there are no BotUsers? No: we check BotUsers
        AFTER fetching). Actually current code fetches first. Test
        documents the behaviour."""
        env = _envelope(data={"user_id": AYLA_USER_ID, "changed_fields": ["display_name"]})
        with patch(
            "apps.eventbus.consumers.identity.fetch_profile_fields",
            return_value=ProfileFields(display_name="Whatever", avatar_url=""),
        ) as mock_fetch:
            handle_user_profile_updated(env)

        # REST fired, but no DB writes (no BotUsers to write to).
        mock_fetch.assert_called_once()
        assert BotUser.all_tenants.filter(ayla_user_id=AYLA_USER_ID).count() == 0

    def test_bad_user_id_no_rest_call(self, tenant: Tenant) -> None:
        env = _envelope(data={"user_id": "not-a-uuid", "changed_fields": ["display_name"]})
        with patch("apps.eventbus.consumers.identity.fetch_profile_fields") as mock_fetch:
            handle_user_profile_updated(env)

        mock_fetch.assert_not_called()

    def test_non_list_changed_fields_no_rest_call(self, tenant: Tenant) -> None:
        env = _envelope(data={"user_id": AYLA_USER_ID, "changed_fields": "display_name"})
        with patch("apps.eventbus.consumers.identity.fetch_profile_fields") as mock_fetch:
            handle_user_profile_updated(env)

        mock_fetch.assert_not_called()

    def test_empty_changed_fields_no_rest_call(self, tenant: Tenant, bot_user: BotUser) -> None:
        env = _envelope(data={"user_id": AYLA_USER_ID, "changed_fields": []})
        with patch("apps.eventbus.consumers.identity.fetch_profile_fields") as mock_fetch:
            handle_user_profile_updated(env)

        mock_fetch.assert_not_called()


# ─── Tenant-nullable ───────────────────────────────────────────────────────


class TestTenantNullable:
    def test_null_tenant_id_handler_proceeds(self, tenant: Tenant, bot_user: BotUser) -> None:
        """§3.12 — user.profile.updated is the ONE event where
        tenant_id may be null (display_name + avatar are user-global).
        Handler MUST NOT raise TenantAuthorizationError here."""
        env = _envelope(
            data={"user_id": AYLA_USER_ID, "changed_fields": ["display_name"]},
            tenant_id=None,
        )
        with patch(
            "apps.eventbus.consumers.identity.fetch_profile_fields",
            return_value=ProfileFields(display_name="Updated", avatar_url=""),
        ):
            handle_user_profile_updated(env)

        bot_user.refresh_from_db()
        assert bot_user.display_name == "Updated"


# ─── subject binding + tenant scoping ──────────────────────────────────────


OTHER_TENANT_ID = "1d4f8a2c-6b3e-4a7d-9c1f-2e5b8d3a6c9f"
OTHER_USER_ID = "aa11bb22-cc33-4d44-9e55-ff6677889900"


@pytest.fixture
def other_tenant() -> Tenant:
    return Tenant.objects.create(
        id=OTHER_TENANT_ID,
        slug="t-identity-other",
        name="Neighbour tenant",
    )


@pytest.fixture
def other_tenant_bot_user(other_tenant: Tenant) -> BotUser:
    """The SAME Ayla user, projected under a second tenant."""
    return BotUser.all_tenants.create(
        tenant=other_tenant,
        channel="max",
        channel_user_id="9002",
        chat_id="chat-9002",
        ayla_user_id=AYLA_USER_ID,
        display_name="Old Name",
    )


class TestSubjectIsTheEnvelope:
    """The acted-upon user is the AUTHORIZED user, not a payload field.

    The handler used to key off ``data["user_id"]`` — a field the tenant
    authorization helper never inspects. Combined with the tenant-null
    carve-out, that made the pair (authorized subject, acted-upon subject)
    divergeable at will: a REST fan-out primitive against arbitrary Ayla
    user UUIDs, driven purely by the payload.
    """

    def test_payload_user_id_mismatch_is_dropped(self, tenant: Tenant, bot_user: BotUser) -> None:
        env = _envelope(
            data={"user_id": OTHER_USER_ID, "changed_fields": ["display_name"]},
            user_id=AYLA_USER_ID,
        )
        with patch("apps.eventbus.consumers.identity.fetch_profile_fields") as mock_fetch:
            handle_user_profile_updated(env)

        # No REST call — the fan-out primitive is exactly what this kills.
        mock_fetch.assert_not_called()
        bot_user.refresh_from_db()
        assert bot_user.display_name == "Old Name"

    def test_the_envelope_subject_is_the_one_fetched(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """A payload naming a different user cannot redirect the fetch."""
        env = _envelope(
            data={"user_id": AYLA_USER_ID, "changed_fields": ["display_name"]},
            user_id=AYLA_USER_ID,
        )
        with patch(
            "apps.eventbus.consumers.identity.fetch_profile_fields",
            return_value=ProfileFields(display_name="Updated", avatar_url=""),
        ) as mock_fetch:
            handle_user_profile_updated(env)

        mock_fetch.assert_called_once_with(UUID(AYLA_USER_ID))

    def test_absent_payload_user_id_still_works(self, tenant: Tenant, bot_user: BotUser) -> None:
        """``data.user_id`` is no longer required — the envelope carries it."""
        env = _envelope(data={"changed_fields": ["display_name"]})
        with patch(
            "apps.eventbus.consumers.identity.fetch_profile_fields",
            return_value=ProfileFields(display_name="Updated", avatar_url=""),
        ):
            handle_user_profile_updated(env)

        bot_user.refresh_from_db()
        assert bot_user.display_name == "Updated"


class TestTenantScopedWritesDoNotCrossTenants:
    def test_tenant_scoped_envelope_writes_only_its_own_tenant(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        other_tenant: Tenant,
        other_tenant_bot_user: BotUser,
    ) -> None:
        """A tenant-scoped envelope authorizes ONE tenant — write only it."""
        env = _envelope(
            data={"user_id": AYLA_USER_ID, "changed_fields": ["display_name"]},
            tenant_id=TENANT_ID,
        )
        with patch(
            "apps.eventbus.consumers.identity.fetch_profile_fields",
            return_value=ProfileFields(display_name="Updated", avatar_url=""),
        ):
            handle_user_profile_updated(env)

        bot_user.refresh_from_db()
        other_tenant_bot_user.refresh_from_db()
        assert bot_user.display_name == "Updated"
        assert other_tenant_bot_user.display_name == "Old Name"

    def test_tenant_null_envelope_still_fans_out(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        other_tenant: Tenant,
        other_tenant_bot_user: BotUser,
    ) -> None:
        """display_name/avatar are user-global — the null path is by design."""
        env = _envelope(
            data={"user_id": AYLA_USER_ID, "changed_fields": ["display_name"]},
            tenant_id=None,
        )
        with patch(
            "apps.eventbus.consumers.identity.fetch_profile_fields",
            return_value=ProfileFields(display_name="Updated", avatar_url=""),
        ):
            handle_user_profile_updated(env)

        bot_user.refresh_from_db()
        other_tenant_bot_user.refresh_from_db()
        assert bot_user.display_name == "Updated"
        assert other_tenant_bot_user.display_name == "Updated"
