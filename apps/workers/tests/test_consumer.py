"""Tests for worker consumer + TenantAwareTask base (DRF-425 / C3).

Uses the same `_FakeRedis` stub pattern as test_streams.py — extended
with XREADGROUP / XACK / PEL behaviour to exercise the consumer loop.
G1 (DRF-432) runs the same pipeline end-to-end against real Redis.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from apps.events.models import Event
from apps.ingress import streams
from apps.tenancy.context import current_tenant, current_trace_id
from apps.tenancy.models import Tenant
from apps.workers import consumer
from apps.workers.base import TenantAwareTask
from apps.workers.registry import clear_registry, register

pytestmark = pytest.mark.django_db


class _FakeStreamRedis:
    """Stub supporting XADD + XREADGROUP + XACK + PEL."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.groups: dict[str, set[str]] = {}
        self.pel: dict[tuple[str, str], dict[str, dict[str, str]]] = {}
        # group_offset[(stream, group)] = next index to read
        self.group_offset: dict[tuple[str, str], int] = {}
        self._counter = 0

    def xgroup_create(self, name: str, groupname: str, id: str, mkstream: bool):  # noqa: A002
        import redis

        existing = self.groups.setdefault(name, set())
        if groupname in existing:
            raise redis.ResponseError("BUSYGROUP")
        existing.add(groupname)
        self.streams.setdefault(name, [])
        self.group_offset[(name, groupname)] = 0

    def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self._counter += 1
        entry_id = f"1700000000-{self._counter}"
        self.streams.setdefault(stream, []).append((entry_id, dict(fields)))
        return entry_id

    def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[str, str],  # noqa: A002 — match redis-py signature
        count: int = 10,
        block: int = 0,
    ):
        out = []
        for stream_name in streams:
            entries = self.streams.get(stream_name, [])
            offset = self.group_offset.get((stream_name, groupname), 0)
            batch = entries[offset : offset + count]
            if not batch:
                continue
            self.group_offset[(stream_name, groupname)] = offset + len(batch)
            # Track PEL: each entry is now pending for this consumer.
            for entry_id, fields in batch:
                self.pel.setdefault((stream_name, groupname), {})[entry_id] = dict(fields)
            out.append((stream_name, batch))
        return out

    def xack(self, stream: str, groupname: str, entry_id: str) -> int:
        pel = self.pel.get((stream, groupname), {})
        if entry_id in pel:
            del pel[entry_id]
            return 1
        return 0

    def xrange(self, stream: str) -> list[tuple[str, dict[str, str]]]:
        return list(self.streams.get(stream, []))


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeStreamRedis()
    monkeypatch.setattr(streams, "_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


# ---------------------------------------------------------------------------
# Test handlers
# ---------------------------------------------------------------------------


class _Captured:
    """Mutable bucket for handler observations."""

    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self.tenants_seen: list[Any] = []
        self.traces_seen: list[Any] = []
        self.fail_count = 0


# ---------------------------------------------------------------------------
# Consumer happy path
# ---------------------------------------------------------------------------


class TestConsumeOnce:
    def test_dispatches_to_registered_handler(self, fake_redis, settings):
        cap = _Captured()

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):
                cap.calls.append(payload)

        # Enqueue one entry.
        streams.enqueue("max", {"hello": "world"})
        processed = consumer.consume_once(streams=["ingress:max"])
        assert processed == 1
        assert len(cap.calls) == 1
        assert cap.calls[0] == {"hello": "world"}

    def test_xack_called_on_success(self, fake_redis):
        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):
                pass

        streams.enqueue("max", {})
        consumer.consume_once(streams=["ingress:max"])
        # PEL should be empty — the entry was XACK'd.
        assert fake_redis.pel.get(("ingress:max", "consumers"), {}) == {}


class TestTenantContextRestoration:
    def test_tenant_set_during_handler(self, fake_redis, settings):
        t = Tenant.objects.create(slug="ten-h", name="H")
        cap = _Captured()

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):
                cap.tenants_seen.append(current_tenant())
                cap.traces_seen.append(current_trace_id())

        # Build a payload with explicit tenant_id; pretend the ingress
        # already resolved it and put it on the journal row.
        fake_redis.xgroup_create("ingress:max", "consumers", "$", True)
        fake_redis.xadd(
            "ingress:max",
            {
                "data": json.dumps({"x": 1}),
                "trace_id": "trace-A",
                "resolved_tenant_id": str(t.id),
            },
        )
        consumer.consume_once(streams=["ingress:max"])

        assert len(cap.tenants_seen) == 1
        assert cap.tenants_seen[0].id == t.id
        assert cap.traces_seen[0] == "trace-A"

        # After the call returns, the outer context is clean.
        assert current_tenant() is None
        assert current_trace_id() is None

    def test_unknown_tenant_id_routes_to_none(self, fake_redis):
        cap = _Captured()

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):
                cap.tenants_seen.append(current_tenant())

        fake_redis.xgroup_create("ingress:max", "consumers", "$", True)
        fake_redis.xadd(
            "ingress:max",
            {
                "data": json.dumps({}),
                "trace_id": "trace-B",
                "resolved_tenant_id": "00000000-0000-4000-8000-000000000000",
            },
        )
        consumer.consume_once(streams=["ingress:max"])
        # Unknown UUID resolves to None; handler still runs.
        assert cap.tenants_seen == [None]


class TestHandlerFailure:
    def test_no_xack_on_handler_exception(self, fake_redis):
        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):
                raise RuntimeError("boom")

        streams.enqueue("max", {})
        consumer.consume_once(streams=["ingress:max"])
        # PEL still holds the entry — consumer didn't XACK.
        pel = fake_redis.pel.get(("ingress:max", "consumers"), {})
        assert len(pel) == 1

    def test_failed_handler_emits_event(self, fake_redis):
        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):
                raise RuntimeError("intentional fail")

        streams.enqueue("max", {})
        consumer.consume_once(streams=["ingress:max"])
        failed = Event.objects.filter(event_type="worker.handler_failed")
        assert failed.count() == 1
        assert failed.first().payload["error_class"] == "RuntimeError"

    def test_consumer_continues_after_one_failure(self, fake_redis):
        cap = _Captured()

        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):
                if payload.get("fail"):
                    cap.fail_count += 1
                    raise RuntimeError("intentional fail")
                cap.calls.append(payload)

        streams.enqueue("max", {"fail": True})
        streams.enqueue("max", {"good": True})
        processed = consumer.consume_once(streams=["ingress:max"])
        assert processed == 1  # only the good one was XACK'd
        assert cap.fail_count == 1
        assert cap.calls == [{"good": True}]


class TestEventsEmitted:
    def test_emits_consumed_started_completed(self, fake_redis):
        @register("ingress:max")
        class _H(TenantAwareTask):
            def handle(self, payload):
                pass

        streams.enqueue("max", {})
        consumer.consume_once(streams=["ingress:max"])

        kinds = set(Event.objects.values_list("event_type", flat=True))
        assert "worker.consumed" in kinds
        assert "worker.handler_started" in kinds
        assert "worker.handler_completed" in kinds


class TestNoHandler:
    def test_no_handler_skips_entry(self, fake_redis):
        fake_redis.xgroup_create("ingress:max", "consumers", "$", True)
        fake_redis.xadd("ingress:max", {"data": "{}", "trace_id": "x"})
        processed = consumer.consume_once(streams=["ingress:max"])
        # No handler registered → skipped (not XACK'd, not processed).
        assert processed == 0


class TestEmptyRegistry:
    def test_no_streams_returns_zero(self, fake_redis):
        processed = consumer.consume_once()
        assert processed == 0


# ---------------------------------------------------------------------------
# Tenancy retro B4 — requires_tenant tag + STRICT_TENANT_REFUSE flag
# ---------------------------------------------------------------------------


class TestRetroB4RequiresTenantTag:
    """Pre-fix workers silently entered ``tenant_scope(None)`` for every
    handler when ``resolved_tenant_id`` was empty/invalid. Reads
    returned empty + warn but handlers proceeded on phantom-tenant
    context. Post-fix the ``requires_tenant`` ClassVar + ``STRICT_TENANT_REFUSE``
    settings flag let strict-tenant handlers DLQ entries cleanly OR
    log-only during the rollout soak.
    """

    def test_default_requires_tenant_is_true(self):
        # The default conservative tag — every handler is presumed to
        # need a tenant unless it explicitly opts out.
        class _DefaultHandler(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        assert _DefaultHandler.requires_tenant is True

    def test_log_only_mode_proceeds_with_none_tenant(self, fake_redis, settings, caplog):
        # STRICT_TENANT_REFUSE=False (default) → handler runs against
        # tenant_scope(None), ERROR log captures the violation.
        settings.STRICT_TENANT_REFUSE = False
        captured: dict[str, Any] = {}

        @register("ingress:b4-log-only")
        class _StrictHandler(TenantAwareTask):
            # requires_tenant inherited as True.
            def handle(self, payload):  # noqa: ANN001
                captured["tenant"] = current_tenant()
                captured["ran"] = True

        try:
            fake_redis.xadd(
                "ingress:b4-log-only",
                {"data": json.dumps({}), "trace_id": "t-b4-log"},
            )
            with caplog.at_level("ERROR", logger="apps.workers.base"):
                processed = consumer.consume_once(streams=["ingress:b4-log-only"])

            assert processed == 1
            assert captured.get("ran") is True
            assert captured.get("tenant") is None
            assert any(
                "tenant_required_missing" in rec.message and "log-only mode" in rec.message
                for rec in caplog.records
            ), "expected ERROR log in log-only mode"
        finally:
            clear_registry()

    def test_strict_mode_dlqs_handler_with_required_tenant_missing(
        self, fake_redis, settings, caplog
    ):
        # STRICT_TENANT_REFUSE=True → handler refuses to dispatch,
        # raises TenantRequiredButMissing. The consumer treats this
        # like any other handler exception (no XACK; PEL retains for
        # DLQ retry policy).
        from apps.workers.base import TenantRequiredButMissing

        settings.STRICT_TENANT_REFUSE = True
        ran = {"handle_called": False}

        @register("ingress:b4-strict")
        class _StrictHandler(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                ran["handle_called"] = True

        try:
            fake_redis.xadd(
                "ingress:b4-strict",
                {"data": json.dumps({}), "trace_id": "t-b4-strict"},
            )
            with caplog.at_level("ERROR", logger="apps.workers.base"):
                # consumer.consume_once swallows handler exceptions
                # (no XACK; entry stays in PEL). The raise is
                # contained — test verifies handle was NOT called.
                consumer.consume_once(streams=["ingress:b4-strict"])

            # Handler MUST NOT have been invoked.
            assert ran["handle_called"] is False
            # DLQ-side log is present.
            assert any(
                "tenant_required_missing" in rec.message and "DLQ" in rec.message
                for rec in caplog.records
            ), "expected DLQ ERROR log"
            # And the exception class name shows up on the
            # worker.handler_failed event payload.
            assert TenantRequiredButMissing.__name__
        finally:
            clear_registry()

    def test_tenant_optional_handler_logs_info(self, fake_redis, settings, caplog):
        # requires_tenant=False handler runs with tenant_scope(None)
        # cleanly — INFO log, no ERROR.
        settings.STRICT_TENANT_REFUSE = True  # even strict mode allows opt-out
        ran = {"ran": False}

        @register("ingress:b4-optional")
        class _SystemHandler(TenantAwareTask):
            # System-tier handler: declares itself tenant-optional.
            requires_tenant = False

            def handle(self, payload):  # noqa: ANN001
                ran["ran"] = True

        try:
            fake_redis.xadd(
                "ingress:b4-optional",
                {"data": json.dumps({}), "trace_id": "t-b4-opt"},
            )
            with caplog.at_level("INFO", logger="apps.workers.base"):
                processed = consumer.consume_once(streams=["ingress:b4-optional"])

            assert processed == 1
            assert ran["ran"] is True
            assert any("tenantless_handler" in rec.message for rec in caplog.records), (
                "expected INFO-level tenantless_handler log"
            )
            # No ERROR-level tenant_required logs.
            assert not any(
                rec.levelname == "ERROR" and "tenant_required" in rec.message
                for rec in caplog.records
            )
        finally:
            clear_registry()

    def test_strict_mode_with_tenant_present_proceeds_normally(self, fake_redis, settings, db):
        # Negative regression: strict mode must NOT affect handlers
        # whose entry carries a valid tenant.
        settings.STRICT_TENANT_REFUSE = True
        captured: dict[str, Any] = {}

        tenant = Tenant.objects.create(slug="b4-ok", name="B4-OK")

        @register("ingress:b4-strict-ok")
        class _StrictHandler(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                captured["tenant_id"] = current_tenant().id if current_tenant() else None

        try:
            fake_redis.xadd(
                "ingress:b4-strict-ok",
                {
                    "data": json.dumps({}),
                    "trace_id": "t-b4-ok",
                    "resolved_tenant_id": str(tenant.id),
                },
            )
            processed = consumer.consume_once(streams=["ingress:b4-strict-ok"])

            assert processed == 1
            assert captured.get("tenant_id") == tenant.id
        finally:
            clear_registry()

    def test_tenant_optional_handler_with_tenant_present_proceeds_normally(
        self, fake_redis, settings, db, caplog
    ):
        # Completes the 2x2 enforcement matrix (closes reviewer Y3
        # missing test cell): requires_tenant=False handler that
        # happens to receive a tenant-tagged entry must silently
        # proceed — no INFO «tenantless» log, no ERROR. Handler sees
        # the real tenant via current_tenant() and dispatches normally.
        settings.STRICT_TENANT_REFUSE = True  # irrelevant when tenant present
        captured: dict[str, Any] = {}

        tenant = Tenant.objects.create(slug="b4-opt-ok", name="B4-Opt-OK")

        @register("ingress:b4-opt-ok")
        class _SystemHandler(TenantAwareTask):
            requires_tenant = False

            def handle(self, payload):  # noqa: ANN001
                captured["tenant_id"] = current_tenant().id if current_tenant() else None

        try:
            fake_redis.xadd(
                "ingress:b4-opt-ok",
                {
                    "data": json.dumps({}),
                    "trace_id": "t-b4-opt-ok",
                    "resolved_tenant_id": str(tenant.id),
                },
            )
            with caplog.at_level("INFO", logger="apps.workers.base"):
                processed = consumer.consume_once(streams=["ingress:b4-opt-ok"])

            assert processed == 1
            assert captured.get("tenant_id") == tenant.id
            # No tenantless / tenant_required logs — handler dispatched
            # cleanly with real tenant in scope.
            assert not any(
                "tenantless_handler" in rec.message or "tenant_required_missing" in rec.message
                for rec in caplog.records
            ), "expected no tenant-status logs when tenant is present"
        finally:
            clear_registry()


# ---------------------------------------------------------------------------
# Tenancy retro B4 follow-up — adversarial-pass blockers (PR #476 retro)
# ---------------------------------------------------------------------------


class TestRetroB4BlockersPreFlip:
    """Adversarial Code Reviewer pass on PR #476 found 4 blockers that
    the first-pass review missed. Each is fixed here pre-strict-mode-flip
    (earliest 2026-05-28). See ``docs/runbooks/strict-tenant-refuse-flip.md``.
    """

    # ---- B1: orphan emits inside trace_id_scope ----

    def test_b1_strict_mode_emit_carries_trace_id_via_contextvar(self, settings, monkeypatch):
        """B1: ``emit(worker.tenant_required_missing, ...)`` in strict mode
        must fire INSIDE ``trace_id_scope`` so the audit row carries the
        right trace_id. Pre-fix: emit fired before trace_id_scope enter,
        so a direct ``TenantAwareTask()`` call (bypassing consumer.py's
        outer scope) emitted orphan events with no trace correlation.
        """

        from apps.workers import base as base_mod
        from apps.workers.base import TenantRequiredButMissing

        settings.STRICT_TENANT_REFUSE = True
        captured_trace: dict[str, str | None] = {}

        real_emit = base_mod.emit

        def spy_emit(event_type: str, *, payload: dict[str, Any] | None = None) -> None:
            if event_type == "worker.tenant_required_missing":
                captured_trace["trace_id"] = current_trace_id()
            return real_emit(event_type, payload=payload)

        monkeypatch.setattr(base_mod, "emit", spy_emit)

        class _SpyHandler(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        with pytest.raises(TenantRequiredButMissing):
            _SpyHandler()({"data": "{}", "trace_id": "trace-b1-strict", "resolved_tenant_id": ""})

        assert captured_trace.get("trace_id") == "trace-b1-strict", (
            "B1: emit must fire inside trace_id_scope so the row carries trace_id"
        )

    def test_b1_log_only_mode_emit_carries_trace_id_via_contextvar(self, settings, monkeypatch):
        """B1: same invariant for log-only mode — emit must be trace-correlated."""

        from apps.workers import base as base_mod

        settings.STRICT_TENANT_REFUSE = False
        captured_trace: dict[str, str | None] = {}

        real_emit = base_mod.emit

        def spy_emit(event_type: str, *, payload: dict[str, Any] | None = None) -> None:
            if event_type == "worker.tenant_required_missing":
                captured_trace["trace_id"] = current_trace_id()
            return real_emit(event_type, payload=payload)

        monkeypatch.setattr(base_mod, "emit", spy_emit)

        class _SpyHandler(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        _SpyHandler()({"data": "{}", "trace_id": "trace-b1-logonly", "resolved_tenant_id": ""})

        assert captured_trace.get("trace_id") == "trace-b1-logonly", (
            "B1: log-only emit must also fire inside trace_id_scope"
        )

    # ---- B2: __init_subclass__ MRO bypass guard ----

    def test_b2_mixin_with_requires_tenant_attr_rejected_at_class_creation(self):
        """B2: a mixin sibling of TenantAwareTask that declares
        ``requires_tenant = False`` MUST not silently override the
        subclass's resolved value. Class creation raises TypeError.
        """

        class _BadSystemMixin:
            requires_tenant = False  # would silently shadow TenantAwareTask's True

        with pytest.raises(TypeError, match="requires_tenant"):

            class _Compromised(_BadSystemMixin, TenantAwareTask):
                def handle(self, payload):  # noqa: ANN001
                    pass

    def test_b2_explicit_optout_on_subclass_itself_is_allowed(self):
        """B2: opt-out via direct subclass declaration is allowed — the
        check is about ancestors silently shadowing the default, not
        about forbidding opt-out entirely.
        """

        class _SystemHandler(TenantAwareTask):
            # requires_tenant=False: documented opt-out — system-tier.
            requires_tenant = False

            def handle(self, payload):  # noqa: ANN001
                pass

        assert _SystemHandler.requires_tenant is False

    def test_b2_default_subclass_inherits_true(self):
        """B2: a subclass that doesn't touch requires_tenant resolves
        to TenantAwareTask's True default without any MRO surprise.
        """

        class _Default(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        assert _Default.requires_tenant is True

    def test_b2_mixin_without_requires_tenant_does_not_trigger_check(self):
        """B2: bare mixins (no ``requires_tenant`` in __dict__) coexist
        cleanly — the guard only fires when an ancestor between cls and
        TenantAwareTask shadows the attribute.
        """

        class _LoggingMixin:
            def log_thing(self) -> None:
                pass

        class _OK(_LoggingMixin, TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        assert _OK.requires_tenant is True

    # ---- B3: documented restart-required ----

    def test_b3_strict_flag_read_site_documents_restart_required(self):
        """B3: ``STRICT_TENANT_REFUSE`` is imported from os.environ once at
        config load (config/settings/base.py). The per-message read in
        base.py looks live but returns the frozen value. The flag-read
        site MUST carry an explicit «restart required» note so anyone
        reading the code doesn't assume hot-reload semantics. The
        operator runbook is the authoritative reference.
        """

        from apps.workers import base as base_mod
        import inspect

        src = inspect.getsource(base_mod)
        # Look for the keyword «restart» near the STRICT_TENANT_REFUSE
        # read site — proxy for the contract being documented.
        assert "STRICT_TENANT_REFUSE" in src
        assert "restart" in src.lower(), (
            "B3: base.py must document worker-restart-required semantics "
            "near the STRICT_TENANT_REFUSE read site "
            "(see docs/runbooks/strict-tenant-refuse-flip.md)"
        )

    def test_b3_runbook_file_exists_and_mentions_restart_and_xautoclaim(self):
        """B3 + B4: the operator runbook lives at the documented path
        and covers (a) the worker-restart-required flip and (b) the
        absence of XAUTOCLAIM-based DLQ — both contracts that the code
        comments cross-reference.
        """

        from pathlib import Path

        runbook = (
            Path(__file__).resolve().parents[3]
            / "docs"
            / "runbooks"
            / ("strict-tenant-refuse-flip.md")
        )
        assert runbook.exists(), f"missing runbook: {runbook}"
        text = runbook.read_text(encoding="utf-8")
        assert "restart" in text.lower(), "runbook must cover the restart step"
        assert "XAUTOCLAIM" in text or "PEL" in text, (
            "runbook must document the PEL retention contract (no auto-DLQ)"
        )

    # ---- B4: PEL retention is documented as the contract, not «DLQ» ----

    def test_b4_exception_docstring_does_not_overclaim_dlq(self):
        """B4: ``TenantRequiredButMissing`` docstring must NOT promise DLQ
        semantics — there is no XAUTOCLAIM reaper. The entry stays in
        the PEL until manual operator intervention. Documenting the
        contract accurately prevents downstream callers from relying on
        retry mechanics that don't exist.
        """

        from apps.workers import base as base_mod
        import inspect

        exc_doc = inspect.getdoc(base_mod.TenantRequiredButMissing) or ""
        # The forbidden phrasing — the old docstring said «DLQ the entry».
        # «DLQ» can appear in a cross-reference («no DLQ infra yet») but
        # must not be used as a positive claim about current behaviour.
        assert "DLQ the entry" not in exc_doc, (
            "B4: exception docstring claimed DLQ semantics — not implemented"
        )
        # The accurate replacement: PEL retention.
        assert "PEL" in exc_doc, "B4: docstring must reference the PEL retention contract"

    def test_b4_call_docstring_does_not_overclaim_dlq(self):
        """B4: same fix on ``TenantAwareTask.__call__`` docstring."""

        from apps.workers import base as base_mod
        import inspect

        call_doc = inspect.getdoc(base_mod.TenantAwareTask.__call__) or ""
        assert "DLQ the" not in call_doc, "B4: __call__ docstring must not promise DLQ behaviour"
        assert "PEL" in call_doc, "B4: __call__ docstring must describe PEL retention accurately"

    def test_b4_strict_mode_entry_remains_in_pel_no_autoclaim(self, fake_redis, settings):
        """B4: regression — confirm that the strict-mode DLQ behaviour we
        documented is what the code does. After a TenantRequiredButMissing
        raise, the consumer must NOT XACK; the entry must remain in the
        PEL. (XAUTOCLAIM reaping is out of scope for this PR.)
        """

        settings.STRICT_TENANT_REFUSE = True

        @register("ingress:b4-pel")
        class _StrictHandler(TenantAwareTask):
            def handle(self, payload):  # noqa: ANN001
                pass

        entry_id = fake_redis.xadd(
            "ingress:b4-pel",
            {"data": json.dumps({}), "trace_id": "t-b4-pel", "resolved_tenant_id": ""},
        )

        consumer.consume_once(streams=["ingress:b4-pel"])

        pel = fake_redis.pel.get(("ingress:b4-pel", "consumers"), {})
        assert entry_id in pel, (
            "B4: strict-mode refuse must leave the entry in the PEL (no auto-DLQ)"
        )
