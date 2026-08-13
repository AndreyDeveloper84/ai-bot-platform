"""Memory × identity-resolution (DRF-1035, owner §15 row 10 + AC-6).

Two properties are locked here, and the second matters as much as the first:

1. A user Ayla has never resolved CAN now accumulate persistent memory — the
   write path establishes the link itself.
2. Resolution happens only when there is genuinely something to persist.
   «привет» must not mint a permanent Ayla subject (owner ruling J-O3,
   identity-on-first-dependent-action).
"""

from __future__ import annotations

import uuid

import pytest

from apps.consent.services import record_global_consent
from apps.identity.models import MemoryEntry
from apps.identity.services import resolve_or_create_global_bot_user
from apps.identity.services.memory_reader import read_personal_context
from apps.integrations.ayla.identity_client import IdentityResolveError, ResolvedIdentity
from apps.orchestrator.memory.personal_context import record_explicit_green_facts

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def resolver(monkeypatch: pytest.MonkeyPatch):
    """Stub the identity read-back and count how often it is called."""
    calls: list[str] = []
    state = {"uuid": uuid.uuid4(), "error": None}

    def _fake(external_user_id: str) -> ResolvedIdentity:
        calls.append(external_user_id)
        if state["error"] is not None:
            raise state["error"]
        return ResolvedIdentity(ayla_user_id=state["uuid"], is_proxy=True)

    monkeypatch.setattr(
        "apps.integrations.ayla.identity_client.resolve_identity", _fake, raising=True
    )
    return type("R", (), {"calls": calls, "state": state})()


def _unlinked_user(uid: str, settings):
    settings.STRICT_TENANT_SCOPE = "strict"
    bot_user = resolve_or_create_global_bot_user(channel="max", channel_user_id=uid)
    assert bot_user.ayla_user_id is None
    record_global_consent(bot_user, source="welcome")
    return bot_user


class TestMemoryEstablishesIdentity:
    def test_unlinked_user_can_now_store_memory(self, settings, resolver) -> None:
        """Pre-DRF-1035 this returned 0 forever: no writer, no memory, ever."""
        bot_user = _unlinked_user("drf1035-mem-1", settings)

        assert record_explicit_green_facts(bot_user, "кстати, я веган") == 1

        bot_user.refresh_from_db()
        assert bot_user.ayla_user_id == resolver.state["uuid"]
        assert MemoryEntry.objects.filter(user_id=resolver.state["uuid"]).exists()

    def test_memory_is_readable_in_a_later_session(self, settings, resolver) -> None:
        # AC-6: session 1 writes, session 2 reads the same subject.
        bot_user = _unlinked_user("drf1035-mem-2", settings)
        record_explicit_green_facts(bot_user, "я веган")

        reloaded = resolve_or_create_global_bot_user(channel="max", channel_user_id="drf1035-mem-2")
        view = read_personal_context(reloaded.ayla_user_id)
        assert any(f.content.get("value") == "vegan" for f in view.green_facts)

    def test_second_write_reuses_the_link(self, settings, resolver) -> None:
        bot_user = _unlinked_user("drf1035-mem-3", settings)
        record_explicit_green_facts(bot_user, "я веган")
        record_explicit_green_facts(bot_user, "я вегетарианка")

        assert len(resolver.calls) == 1  # resolved once, then cache hits


class TestMinimalSufficientIdentity:
    """J-O3: no permanent identity for a turn that stores nothing."""

    def test_greeting_does_not_resolve_identity(self, settings, resolver) -> None:
        bot_user = _unlinked_user("drf1035-mem-4", settings)

        assert record_explicit_green_facts(bot_user, "привет") == 0

        assert resolver.calls == []  # nothing extracted → nothing to key
        bot_user.refresh_from_db()
        assert bot_user.ayla_user_id is None

    def test_no_consent_does_not_resolve_identity(self, settings) -> None:
        # Consent is checked before extraction and before resolution, so a
        # user who never consented never gets an identity minted for memory.
        settings.STRICT_TENANT_SCOPE = "strict"
        bot_user = resolve_or_create_global_bot_user(channel="max", channel_user_id="drf1035-mem-5")

        # No stub installed: a resolve attempt would raise on the real HTTP
        # path, so reaching zero without an error IS the assertion.
        assert record_explicit_green_facts(bot_user, "я веган") == 0
        bot_user.refresh_from_db()
        assert bot_user.ayla_user_id is None


class TestDegradation:
    def test_unresolvable_identity_drops_the_fact_without_raising(self, settings, resolver) -> None:
        bot_user = _unlinked_user("drf1035-mem-6", settings)
        resolver.state["error"] = IdentityResolveError("network: ConnectError")

        assert record_explicit_green_facts(bot_user, "я веган") == 0

        assert MemoryEntry.objects.count() == 0
        bot_user.refresh_from_db()
        assert bot_user.ayla_user_id is None
