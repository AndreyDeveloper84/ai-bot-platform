"""Tests for :func:`apps.identity.services.ayla_link.ensure_ayla_link` (DRF-1035).

Covers the owner's §15 matrix rows 1-7, 14, 15: new MAX user, already-linked
user, resolver returns an existing proxy, resolver creates a new one, repeat
call, parallel resolution, backend unavailable, multi-tenant fan-out, no
duplicate identities.

The HTTP leg is stubbed at :func:`apps.integrations.ayla.identity_client.resolve_identity`
— the wire contract itself is locked by ``test_contract_route_table.py``, so
duplicating it here would test the mock, not the code.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from apps.identity.models import BotUser
from apps.identity.services.ayla_link import ensure_ayla_link
from apps.integrations.ayla.identity_client import IdentityResolveError, ResolvedIdentity
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


CHANNEL = "max"
CHANNEL_USER_ID = "260237491"  # the «Мой Парк» account from the DRF-1035 incident


@pytest.fixture
def stub_resolve(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Patch the HTTP leg; return a recorder so tests can assert call counts."""

    calls: list[str] = []
    state: dict[str, Any] = {"uuid": uuid.uuid4(), "is_proxy": True, "error": None}

    def _fake(external_user_id: str) -> ResolvedIdentity:
        calls.append(external_user_id)
        if state["error"] is not None:
            raise state["error"]
        return ResolvedIdentity(ayla_user_id=state["uuid"], is_proxy=state["is_proxy"])

    # Patch where it is looked up: ensure_ayla_link imports lazily from the
    # client module, so patching the module attribute is what takes effect.
    monkeypatch.setattr(
        "apps.integrations.ayla.identity_client.resolve_identity", _fake, raising=True
    )
    return type("Stub", (), {"calls": calls, "state": state})()


@pytest.fixture
def global_user() -> BotUser:
    from apps.identity.services import resolve_or_create_global_bot_user

    return resolve_or_create_global_bot_user(channel=CHANNEL, channel_user_id=CHANNEL_USER_ID)


# ─── §15.1 new MAX user, no ayla_user_id ────────────────────────────────────


def test_new_user_resolves_and_persists(global_user: BotUser, stub_resolve: Any) -> None:
    assert global_user.ayla_user_id is None

    resolved = ensure_ayla_link(global_user, trigger="booking")

    assert resolved == stub_resolve.state["uuid"]
    assert len(stub_resolve.calls) == 1
    # The external id is the deterministic bot-side form — never a raw channel id.
    assert stub_resolve.calls[0] == f"bot:{CHANNEL}:{CHANNEL_USER_ID}"

    global_user.refresh_from_db()
    assert global_user.ayla_user_id == stub_resolve.state["uuid"]


def test_in_memory_instance_is_updated_without_refetch(
    global_user: BotUser, stub_resolve: Any
) -> None:
    # The caller holds this object for the rest of the turn; it must see the
    # link without a refresh_from_db.
    ensure_ayla_link(global_user, trigger="booking")
    assert global_user.ayla_user_id == stub_resolve.state["uuid"]


# ─── §15.2 existing linked BotUser → no network ─────────────────────────────


def test_linked_user_short_circuits(global_user: BotUser, stub_resolve: Any) -> None:
    known = uuid.uuid4()
    global_user.ayla_user_id = known
    global_user.save(update_fields=["ayla_user_id"])

    resolved = ensure_ayla_link(global_user, trigger="booking")

    assert resolved == known
    assert stub_resolve.calls == []  # AC-3: no redundant resolve


# ─── §15.3 / §15.4 existing vs newly created proxy upstream ─────────────────


def test_is_proxy_false_is_accepted(global_user: BotUser, stub_resolve: Any) -> None:
    # After bind_external_identity Ayla resolves a REAL account. The bot must
    # persist whatever id it is told, not only proxy ids.
    stub_resolve.state["is_proxy"] = False
    resolved = ensure_ayla_link(global_user, trigger="booking")

    global_user.refresh_from_db()
    assert global_user.ayla_user_id == resolved


# ─── §15.5 repeat / §15.15 no duplicate identity ────────────────────────────


def test_repeat_call_resolves_once(global_user: BotUser, stub_resolve: Any) -> None:
    first = ensure_ayla_link(global_user, trigger="booking")
    second = ensure_ayla_link(global_user, trigger="booking")
    third = ensure_ayla_link(global_user, trigger="memory_write")

    assert first == second == third
    assert len(stub_resolve.calls) == 1  # AC-4: one resolve, then cache hits


# ─── §15.6 parallel resolution ──────────────────────────────────────────────


def test_parallel_resolution_converges_on_one_identity(
    global_user: BotUser, stub_resolve: Any
) -> None:
    """Two concurrent turns for the same person.

    Simulated by resolving twice from two independently-loaded instances of
    the same row — the shape a second worker would see, since neither holds
    the other's in-memory state. Ayla is deterministic (username is UNIQUE,
    so `get_or_create` converges), so both must land on ONE id and the row
    must never end up with a second value.
    """
    other = BotUser.all_tenants.get(pk=global_user.pk)

    a = ensure_ayla_link(global_user, trigger="booking")
    b = ensure_ayla_link(other, trigger="memory_write")

    assert a == b
    global_user.refresh_from_db()
    assert global_user.ayla_user_id == a
    assert (
        BotUser.all_tenants.filter(channel=CHANNEL, channel_user_id=CHANNEL_USER_ID)
        .values_list("ayla_user_id", flat=True)
        .distinct()
        .count()
        == 1
    )


# ─── §15.7 backend unavailable ──────────────────────────────────────────────


def test_backend_unavailable_returns_none_and_does_not_raise(
    global_user: BotUser, stub_resolve: Any
) -> None:
    stub_resolve.state["error"] = IdentityResolveError("network: ReadTimeout")

    assert ensure_ayla_link(global_user, trigger="booking") is None

    global_user.refresh_from_db()
    assert global_user.ayla_user_id is None  # nothing written on failure


def test_unexpected_exception_is_contained(global_user: BotUser, stub_resolve: Any) -> None:
    # Identity resolution must never abort a user's turn, whatever breaks.
    stub_resolve.state["error"] = RuntimeError("boom")

    assert ensure_ayla_link(global_user, trigger="booking") is None


def test_retry_after_failure_succeeds(global_user: BotUser, stub_resolve: Any) -> None:
    stub_resolve.state["error"] = IdentityResolveError("server: HTTP 502")
    assert ensure_ayla_link(global_user, trigger="booking") is None

    stub_resolve.state["error"] = None
    assert ensure_ayla_link(global_user, trigger="booking") == stub_resolve.state["uuid"]


# ─── §15.14 multi-tenant BotUser rows ───────────────────────────────────────


def test_fan_out_writes_every_shell_of_the_pair(
    global_user: BotUser, stub_resolve: Any, settings: Any
) -> None:
    """One person, two shells (sentinel + pilot tenant) → one canonical id.

    This is the property `privacy._resolve_person_link` depends on: writing
    only the requesting row would make the person look unlinked from the
    other shell.
    """
    tenant = Tenant.objects.create(slug="pilot-salon", name="Pilot Salon")
    pilot_shell = BotUser.all_tenants.create(
        tenant=tenant, channel=CHANNEL, channel_user_id=CHANNEL_USER_ID
    )
    assert pilot_shell.ayla_user_id is None

    ensure_ayla_link(global_user, trigger="booking")

    pilot_shell.refresh_from_db()
    global_user.refresh_from_db()
    assert pilot_shell.ayla_user_id == stub_resolve.state["uuid"]
    assert global_user.ayla_user_id == stub_resolve.state["uuid"]


def test_fan_out_does_not_touch_other_people(global_user: BotUser, stub_resolve: Any) -> None:
    from apps.identity.services import resolve_or_create_global_bot_user

    other_person = resolve_or_create_global_bot_user(channel=CHANNEL, channel_user_id="999000111")

    ensure_ayla_link(global_user, trigger="booking")

    other_person.refresh_from_db()
    assert other_person.ayla_user_id is None


# ─── never overwrite / conflict visibility ──────────────────────────────────


def test_existing_value_on_sibling_shell_is_never_overwritten(
    global_user: BotUser, stub_resolve: Any
) -> None:
    """A disagreeing stored id must survive untouched.

    `privacy._resolve_person_link` fail-closes on 2+ distinct ids, so this
    module must surface a conflict rather than silently reconcile it — and
    must never be the thing that destroys the pre-existing value.
    """
    tenant = Tenant.objects.create(slug="pilot-salon-2", name="Pilot Salon 2")
    stranger_id = uuid.uuid4()
    pilot_shell = BotUser.all_tenants.create(
        tenant=tenant,
        channel=CHANNEL,
        channel_user_id=CHANNEL_USER_ID,
        ayla_user_id=stranger_id,
    )

    ensure_ayla_link(global_user, trigger="booking")

    pilot_shell.refresh_from_db()
    assert pilot_shell.ayla_user_id == stranger_id  # untouched
