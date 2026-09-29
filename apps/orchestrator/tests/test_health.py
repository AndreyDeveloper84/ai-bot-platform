"""Pipeline health check tests (DRF-552 / Sprint 6 / G3)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from apps.orchestrator.health import (
    check_intent_router,
    check_skill_registry,
    pipeline_health,
)

# Sprint 8/G4 (DRF-735) added the audit_cleanup probe to pipeline_health() —
# it reads AuditLog, so every test in this module needs DB access.
pytestmark = pytest.mark.django_db


class TestCheckIntentRouterSaysWhatItDoesNotCheck:
    """DRF-1938: ``ok`` здесь — «не блокирует трафик», а не «LLM здорова».

    Breaker ``openai.complete`` стоит только на старом ``intent_router``
    (путь per-tenant). Путь консьержа (``apps.llm.router``) breaker не имеет
    ни у одного вендора, и проверка обязана это сказать: ``checked=False``,
    ``detail`` и ``vendor`` — всегда, при любом состоянии breaker.
    """

    @pytest.mark.parametrize("vendor", ["anthropic", "openai"])
    @pytest.mark.parametrize("state", ["CLOSED", "HALF_OPEN", None])
    def test_a_closed_legacy_breaker_is_not_a_verdict_on_the_llm(self, settings, vendor, state):
        from apps.orchestrator.llm.breaker import State

        settings.LLM_PROVIDER = vendor
        settings.SKILL_LLM_PROVIDER = {}
        value = getattr(State, state) if state else None
        with patch("apps.orchestrator.llm.breaker.get_state", return_value=value):
            r = check_intent_router()

        assert r["ok"] is True
        assert r["checked"] is False
        assert r["vendor"] == vendor
        assert r["detail"] == "llm_breaker_not_on_concierge_path"

    def test_anthropic_vendor_with_the_openai_breaker_is_never_a_bare_ok(self, settings):
        """Сторож подменой: прежний ответ «openai closed → ok» без оговорки — красный."""
        from apps.orchestrator.llm.breaker import State

        settings.LLM_PROVIDER = "anthropic"
        settings.SKILL_LLM_PROVIDER = {}
        with patch("apps.orchestrator.llm.breaker.get_state", return_value=State.CLOSED):
            r = check_intent_router()

        assert set(r) >= {"ok", "error", "duration_ms", "checked", "vendor", "detail"}
        assert not (r["ok"] is True and r["checked"] is not False)

    def test_the_check_never_calls_an_llm(self, settings, monkeypatch):
        settings.LLM_PROVIDER = "anthropic"

        def _no_llm(*_a, **_k):
            raise AssertionError("readyz не зовёт внешний API")

        monkeypatch.setattr("apps.llm.router.build_provider", _no_llm)
        monkeypatch.setattr("apps.llm.router.get_router", _no_llm)
        r = check_intent_router()

        assert r["checked"] is False


class TestCheckIntentRouter:
    def test_breaker_closed_returns_ok(self):
        from apps.orchestrator.llm.breaker import State

        with patch("apps.orchestrator.llm.breaker.get_state", return_value=State.CLOSED):
            r = check_intent_router()
        assert r["ok"] is True
        assert r["error"] is None
        assert isinstance(r["duration_ms"], int)

    def test_breaker_open_returns_fail(self):
        from apps.orchestrator.llm.breaker import State

        with patch("apps.orchestrator.llm.breaker.get_state", return_value=State.OPEN):
            r = check_intent_router()
        assert r["ok"] is False
        assert r["error"] == "openai_breaker_open"

    def test_breaker_half_open_treated_as_ok(self):
        from apps.orchestrator.llm.breaker import State

        with patch("apps.orchestrator.llm.breaker.get_state", return_value=State.HALF_OPEN):
            r = check_intent_router()
        # HALF_OPEN means the breaker is probing; readyz treats this as
        # recovering (not fully degraded). Only OPEN is a hard fail.
        assert r["ok"] is True

    def test_no_state_returns_ok(self):
        # Cold boot: breaker hasn't been created yet → get_state returns None.
        with patch("apps.orchestrator.llm.breaker.get_state", return_value=None):
            r = check_intent_router()
        assert r["ok"] is True

    def test_check_swallows_exceptions(self):
        with patch(
            "apps.orchestrator.llm.breaker.get_state",
            side_effect=RuntimeError("broken"),
        ):
            r = check_intent_router()
        assert r["ok"] is False
        assert "RuntimeError" in r["error"]


class TestCheckSkillRegistry:
    def test_faq_registered_returns_ok(self):
        r = check_skill_registry()
        assert r["ok"] is True
        assert r["error"] is None

    def test_no_faq_returns_fail(self):
        # Simulate empty registry — patch registered() to return non-FAQ list.
        from types import SimpleNamespace

        fake_skills = [SimpleNamespace(name="echo"), SimpleNamespace(name="handoff")]
        with patch("apps.skills.registry.registered", return_value=fake_skills):
            r = check_skill_registry()
        assert r["ok"] is False
        assert "faq_not_registered" in r["error"]

    def test_check_swallows_exceptions(self):
        with patch("apps.skills.registry.registered", side_effect=RuntimeError("kaboom")):
            r = check_skill_registry()
        assert r["ok"] is False
        assert "RuntimeError" in r["error"]


class TestPipelineHealth:
    def test_returns_both_components(self):
        results = pipeline_health()
        assert "intent_router" in results
        assert "skill_registry" in results

    def test_all_ok_under_normal_conditions(self):
        results = pipeline_health()
        # In a fresh test process, breaker isn't created → ok. FAQ is registered.
        assert all(r["ok"] for r in results.values()), results


class TestReadyzIntegration:
    """G3 wires pipeline_health into readyz response shape."""

    @pytest.mark.asyncio
    async def test_readyz_includes_pipeline_checks(self):
        from django.test import AsyncClient

        client = AsyncClient()
        response = await client.get("/readyz/")
        # Status 200 OR 503 depending on backing services; either way
        # the checks dict must include our pipeline component names.
        data = response.json()
        assert "checks" in data
        assert "intent_router" in data["checks"]
        assert "skill_registry" in data["checks"]
