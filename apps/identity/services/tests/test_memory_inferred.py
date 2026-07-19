"""Tests for inferred green-memory persistence (S3-B) and the gated
personal-context service (memory_green consent gate, contract v1.0)."""

from __future__ import annotations

import uuid

import pytest

from apps.consent.models import ConsentRecord
from apps.identity.models import MemoryEntry
from apps.identity.services.memory_inferred import (
    InferredGreenFact,
    record_inferred_green_facts,
)
from apps.identity.services.personal_context import (
    GateStatus,
    get_ask_eligibility,
    get_declared_prefs,
    mark_asked,
    patch_declared_prefs,
    skip,
)
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db

CT = ConsentRecord.ConsentType


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="mem-inf", name="Mem Inf")


def _bot_user(tenant: Tenant, ayla_user_id, cuid: str = "u1"):
    from apps.identity.models import BotUser

    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=cuid, ayla_user_id=ayla_user_id
    )


def _grant(bu, ctype: str) -> None:
    ConsentRecord.all_tenants.create(
        tenant=bu.tenant,
        bot_user=bu,
        consent_type=ctype,
        granted=True,
        source="test",
    )


def _fact(kind: str = "diet", key: str = "diet_type", value: str = "vegan") -> InferredGreenFact:
    return InferredGreenFact(kind=kind, content={"key": key, "value": value})


class TestInferredWrite:
    def test_gate_closed_no_write(self, tenant) -> None:
        bu = _bot_user(tenant, uuid.uuid4())
        assert record_inferred_green_facts(bu, [_fact()]) == 0
        assert MemoryEntry.objects.count() == 0

    def test_write_with_personal_data_consent(self, tenant) -> None:
        uid = uuid.uuid4()
        bu = _bot_user(tenant, uid)
        _grant(bu, CT.PERSONAL_DATA)  # green basis per ADR-0011 §11

        assert record_inferred_green_facts(bu, [_fact()]) == 1

        entry = MemoryEntry.objects.get()
        assert entry.source == MemoryEntry.SOURCE_INFERRED
        assert entry.sensitivity_zone == MemoryEntry.SENSITIVITY_GREEN
        assert entry.last_inferred_at is not None  # CHECK 1
        assert entry.content == {"key": "diet_type", "value": "vegan"}

    def test_dedup_same_value(self, tenant) -> None:
        uid = uuid.uuid4()
        bu = _bot_user(tenant, uid)
        _grant(bu, CT.PERSONAL_DATA)

        assert record_inferred_green_facts(bu, [_fact()]) == 1
        assert record_inferred_green_facts(bu, [_fact()]) == 0  # re-inferred same value
        assert MemoryEntry.objects.count() == 1

    def test_changed_value_writes_new_entry(self, tenant) -> None:
        uid = uuid.uuid4()
        bu = _bot_user(tenant, uid)
        _grant(bu, CT.PERSONAL_DATA)

        assert record_inferred_green_facts(bu, [_fact()]) == 1
        assert record_inferred_green_facts(bu, [_fact(value="keto")]) == 1
        assert MemoryEntry.objects.count() == 2

    def test_no_ayla_user_id_no_write(self, tenant) -> None:
        bu = _bot_user(tenant, None)
        _grant(bu, CT.PERSONAL_DATA)
        assert record_inferred_green_facts(bu, [_fact()]) == 0
        assert MemoryEntry.objects.count() == 0


class _StubPCClient:
    """PersonalContextHttpClient stand-in recording calls."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.closed = False

    def get_context(self, *, ayla_user_id: str):
        self.calls.append(("get", ayla_user_id))
        from apps.integrations.ayla.personal_context_client import DeclaredContext

        return DeclaredContext(ayla_user_id=ayla_user_id, context={"diet_type": "vegan"})

    def patch_context(self, *, ayla_user_id: str, updates: list):
        self.calls.append(("patch", ayla_user_id, updates))
        from apps.integrations.ayla.personal_context_client import DeclaredContext

        return DeclaredContext(ayla_user_id=ayla_user_id, context={})

    def get_ask_eligibility(self, *, ayla_user_id: str):
        self.calls.append(("ask", ayla_user_id))
        from apps.integrations.ayla.personal_context_client import AskEligibility

        return AskEligibility(should_ask=True, field="diet_type", prompt_hint="?")

    def mark_asked(self, *, ayla_user_id: str, field: str):
        self.calls.append(("mark", ayla_user_id, field))

    def skip(self, *, ayla_user_id: str, field: str):
        self.calls.append(("skip", ayla_user_id, field))
        return 2

    def close(self) -> None:
        self.closed = True


class TestPersonalContextGate:
    def test_blocked_without_memory_green(self, tenant) -> None:
        bu = _bot_user(tenant, uuid.uuid4())
        client = _StubPCClient()

        result = get_declared_prefs(bu, client=client)  # type: ignore[arg-type]

        assert result.status is GateStatus.BLOCKED_CONSENT
        assert client.calls == []  # gate fires BEFORE the wire

    def test_blocked_without_ayla_user_id(self, tenant) -> None:
        bu = _bot_user(tenant, None)
        result = get_declared_prefs(bu, client=_StubPCClient())  # type: ignore[arg-type]
        assert result.status is GateStatus.BLOCKED_CONSENT

    def test_get_ok_with_memory_green(self, tenant) -> None:
        uid = uuid.uuid4()
        bu = _bot_user(tenant, uid)
        _grant(bu, CT.MEMORY_GREEN)
        client = _StubPCClient()

        result = get_declared_prefs(bu, client=client)  # type: ignore[arg-type]

        assert result.status is GateStatus.OK
        assert result.context is not None
        assert result.context.context == {"diet_type": "vegan"}
        assert client.calls == [("get", str(uid))]

    def test_personal_data_alone_does_not_open_gate(self, tenant) -> None:
        """The two consent bases are distinct: PERSONAL_DATA covers local
        MemoryEntry writes; Ayla declared-prefs calls need memory_green."""
        uid = uuid.uuid4()
        bu = _bot_user(tenant, uid)
        _grant(bu, CT.PERSONAL_DATA)

        result = get_declared_prefs(bu, client=_StubPCClient())  # type: ignore[arg-type]

        assert result.status is GateStatus.BLOCKED_CONSENT

    def test_cross_tenant_grant_covers(self, tenant) -> None:
        """MEMORY_CONSENT_SPEC §8.1: a grant in ANY tenant opens the gate."""
        uid = uuid.uuid4()
        bu = _bot_user(tenant, uid, cuid="u1")
        other = Tenant.objects.create(slug="mem-inf-b", name="B")
        bu2 = _bot_user(other, uid, cuid="u2")
        _grant(bu2, CT.MEMORY_GREEN)  # granted in the OTHER tenant

        result = get_declared_prefs(bu, client=_StubPCClient())  # type: ignore[arg-type]

        assert result.status is GateStatus.OK

    def test_patch_ok(self, tenant) -> None:
        uid = uuid.uuid4()
        bu = _bot_user(tenant, uid)
        _grant(bu, CT.MEMORY_GREEN)
        client = _StubPCClient()

        updates = [{"field": "diet_type", "value": "vegan"}]
        result = patch_declared_prefs(bu, updates, client=client)  # type: ignore[arg-type]

        assert result.status is GateStatus.OK
        assert client.calls == [("patch", str(uid), updates)]

    def test_ask_mark_skip_ok(self, tenant) -> None:
        uid = uuid.uuid4()
        bu = _bot_user(tenant, uid)
        _grant(bu, CT.MEMORY_GREEN)
        client = _StubPCClient()

        ask = get_ask_eligibility(bu, client=client)  # type: ignore[arg-type]
        assert ask.status is GateStatus.OK
        assert ask.eligibility is not None and ask.eligibility.field == "diet_type"

        assert mark_asked(bu, "diet_type", client=client).status is GateStatus.OK  # type: ignore[arg-type]

        skip_result = skip(bu, "diet_type", client=client)  # type: ignore[arg-type]
        assert skip_result.status is GateStatus.OK
        assert skip_result.skip_count == 2

    def test_upstream_error_mapped(self, tenant) -> None:
        from apps.integrations.ayla.personal_context_client import (
            PersonalContextTransportError,
        )

        uid = uuid.uuid4()
        bu = _bot_user(tenant, uid)
        _grant(bu, CT.MEMORY_GREEN)

        class _Failing(_StubPCClient):
            def get_context(self, *, ayla_user_id: str):
                raise PersonalContextTransportError("http_500")

        result = get_declared_prefs(bu, client=_Failing())  # type: ignore[arg-type]
        assert result.status is GateStatus.ERROR
