"""W0-B3 — security-critical bot settings: declaration + validation.

Pins the declaration semantics of the four security-critical settings
formalized in ``config/settings/base.py`` and the conditional fail-fast
validation in ``config/settings/production.py``:

* ``EVENT_INGEST_HMAC_SECRET`` — required in strict production because
  the ingest endpoint is unconditionally routed (config/urls.py) and
  consumer families register at app-ready (apps/eventbus/apps.py);
  absence outside production must not block startup.
* ``EVENT_INGEST_TRUSTED_PROXY_DEPTH`` — integer, default 0, negative
  and non-integer values rejected at configuration load.
* ``LLM_PROVIDER`` — canonical default ``"openai"``, case/whitespace
  normalized, unsupported values rejected in strict production only.
* ``ANTHROPIC_API_KEY`` — required in strict production only when a
  configured provider path (LLM_PROVIDER or SKILL_LLM_PROVIDER)
  selects anthropic.

Production is loaded via ``importlib`` against a monkey-patched
``os.environ`` so the side-effect-on-import doesn't pollute the test
runner's own (local) settings — same pattern as
``tests/smoke/test_catalog_settings.py``. No test performs network
calls.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from types import ModuleType

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test.utils import override_settings


@pytest.fixture
def _restore_production_module() -> Iterator[None]:
    """Reload `config.settings.production` cleanly after each scenario.

    The module's import-time side effect (`raise ImproperlyConfigured`)
    is exactly what we're testing — but we mustn't leave a half-imported
    module in `sys.modules` for the next test.
    """
    saved = sys.modules.pop("config.settings.production", None)
    yield
    if saved is not None:
        sys.modules["config.settings.production"] = saved
    else:
        sys.modules.pop("config.settings.production", None)


@pytest.fixture
def _production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Satisfy the pre-existing production fail-fast vars.

    Only the W0-B3 variables under test are left to each scenario.
    """
    monkeypatch.setenv("MYSITE_CATALOG_SERVICE_TOKEN", "catalog-token")  # noqa: S105
    monkeypatch.setenv("SENTRY_DSN", "https://public@sentry.example.com/1")
    monkeypatch.setenv("CHROMA_AUTH_TOKEN", "chroma-token-abc")  # noqa: S105
    monkeypatch.setenv("MYSITE_WEBHOOK_HMAC_SECRET", "hmac-secret-abc")  # noqa: S105


def _reload_base_snapshot(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Reload base settings and snapshot the W0-B3 values.

    ``importlib.reload`` mutates the module in place, and the
    ``finally`` restore-reload (with the patched env removed) would
    overwrite the values under test — so they are copied out BEFORE
    the restore. A base-module reload that raises (exactly what some
    scenarios test) leaves a half-executed module object behind; the
    ``finally`` reload puts it back into a sane state either way.
    """
    import config.settings.base as base_settings

    try:
        module = importlib.reload(base_settings)
        return {
            "EVENT_INGEST_HMAC_SECRET": module.EVENT_INGEST_HMAC_SECRET,
            "EVENT_INGEST_TRUSTED_PROXY_DEPTH": module.EVENT_INGEST_TRUSTED_PROXY_DEPTH,
            "LLM_PROVIDER": module.LLM_PROVIDER,
        }
    finally:
        monkeypatch.delenv("EVENT_INGEST_TRUSTED_PROXY_DEPTH", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("EVENT_INGEST_HMAC_SECRET", raising=False)
        importlib.reload(base_settings)


def _import_production() -> ModuleType:
    return importlib.import_module("config.settings.production")


class TestDeclarations:
    """All four settings are declared centrally in base settings."""

    def test_event_ingest_hmac_secret_declared_empty_default(self) -> None:
        assert hasattr(settings, "EVENT_INGEST_HMAC_SECRET")

    def test_event_ingest_trusted_proxy_depth_declared(self) -> None:
        assert hasattr(settings, "EVENT_INGEST_TRUSTED_PROXY_DEPTH")

    def test_llm_provider_declared(self) -> None:
        assert hasattr(settings, "LLM_PROVIDER")

    def test_anthropic_api_key_declared_empty_default(self) -> None:
        assert hasattr(settings, "ANTHROPIC_API_KEY")

    def test_skill_llm_provider_declared_empty(self) -> None:
        # Tier-2 router map declared centrally so production validation
        # can inspect every configured provider path.
        assert hasattr(settings, "SKILL_LLM_PROVIDER")


class TestEventIngestHmacSecret:
    def test_absent_outside_production_does_not_block_startup(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Feature-disabled / non-production boot: no secret configured,
        # settings load must succeed and default to empty.
        monkeypatch.delenv("EVENT_INGEST_HMAC_SECRET", raising=False)
        snapshot = _reload_base_snapshot(monkeypatch)
        assert snapshot["EVENT_INGEST_HMAC_SECRET"] == ""

    def test_missing_secret_fails_in_production(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _production_env: None,
        _restore_production_module: None,
    ) -> None:
        monkeypatch.delenv("EVENT_INGEST_HMAC_SECRET", raising=False)
        with pytest.raises(ImproperlyConfigured) as exc_info:
            _import_production()
        assert "EVENT_INGEST_HMAC_SECRET" in str(exc_info.value)

    def test_empty_secret_fails_in_production(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _production_env: None,
        _restore_production_module: None,
    ) -> None:
        monkeypatch.setenv("EVENT_INGEST_HMAC_SECRET", "")
        with pytest.raises(ImproperlyConfigured) as exc_info:
            _import_production()
        assert "EVENT_INGEST_HMAC_SECRET" in str(exc_info.value)

    def test_configured_secret_passes_in_production(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _production_env: None,
        _restore_production_module: None,
    ) -> None:
        secret = "ingest-hmac-secret-abc"  # noqa: S105  # pragma: allowlist secret
        monkeypatch.setenv("EVENT_INGEST_HMAC_SECRET", secret)
        module = _import_production()
        assert module.EVENT_INGEST_HMAC_SECRET == secret


class TestTrustedProxyDepth:
    def test_default_is_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EVENT_INGEST_TRUSTED_PROXY_DEPTH", raising=False)
        snapshot = _reload_base_snapshot(monkeypatch)
        assert snapshot["EVENT_INGEST_TRUSTED_PROXY_DEPTH"] == 0

    def test_positive_integer_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EVENT_INGEST_TRUSTED_PROXY_DEPTH", "3")
        snapshot = _reload_base_snapshot(monkeypatch)
        assert snapshot["EVENT_INGEST_TRUSTED_PROXY_DEPTH"] == 3

    def test_negative_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EVENT_INGEST_TRUSTED_PROXY_DEPTH", "-1")
        with pytest.raises(ImproperlyConfigured) as exc_info:
            _reload_base_snapshot(monkeypatch)
        assert "EVENT_INGEST_TRUSTED_PROXY_DEPTH" in str(exc_info.value)

    def test_non_integer_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EVENT_INGEST_TRUSTED_PROXY_DEPTH", "deep")
        with pytest.raises(ImproperlyConfigured) as exc_info:
            _reload_base_snapshot(monkeypatch)
        assert "EVENT_INGEST_TRUSTED_PROXY_DEPTH" in str(exc_info.value)


class TestLlmProvider:
    def test_default_remains_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        snapshot = _reload_base_snapshot(monkeypatch)
        assert snapshot["LLM_PROVIDER"] == "openai"

    def test_case_and_whitespace_normalized(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "  AnThRoPic ")
        snapshot = _reload_base_snapshot(monkeypatch)
        assert snapshot["LLM_PROVIDER"] == "anthropic"

    def test_empty_env_falls_back_to_openai(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Matches the router's own ``or "openai"`` fallback.
        monkeypatch.setenv("LLM_PROVIDER", "")
        snapshot = _reload_base_snapshot(monkeypatch)
        assert snapshot["LLM_PROVIDER"] == "openai"

    def test_unsupported_provider_allowed_outside_production(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Non-production keeps the router's warn-and-fallback-to-openai
        # policy — settings load must NOT fail here.
        monkeypatch.setenv("LLM_PROVIDER", "unknown_vendor")
        snapshot = _reload_base_snapshot(monkeypatch)
        assert snapshot["LLM_PROVIDER"] == "unknown_vendor"

    def test_unsupported_provider_rejected_in_production(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _production_env: None,
        _restore_production_module: None,
    ) -> None:
        monkeypatch.setenv("EVENT_INGEST_HMAC_SECRET", "ingest-hmac-secret-abc")  # noqa: S105
        monkeypatch.setenv("LLM_PROVIDER", "unknown_vendor")
        with pytest.raises(ImproperlyConfigured) as exc_info:
            _import_production()
        assert "LLM_PROVIDER" in str(exc_info.value)


class TestAnthropicApiKey:
    def test_openai_route_does_not_require_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _production_env: None,
        _restore_production_module: None,
    ) -> None:
        monkeypatch.setenv("EVENT_INGEST_HMAC_SECRET", "ingest-hmac-secret-abc")  # noqa: S105
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        module = _import_production()
        assert module.ANTHROPIC_API_KEY == ""

    def test_anthropic_route_without_key_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _production_env: None,
        _restore_production_module: None,
    ) -> None:
        monkeypatch.setenv("EVENT_INGEST_HMAC_SECRET", "ingest-hmac-secret-abc")  # noqa: S105
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ImproperlyConfigured) as exc_info:
            _import_production()
        assert "ANTHROPIC_API_KEY" in str(exc_info.value)

    def test_anthropic_route_with_key_passes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _production_env: None,
        _restore_production_module: None,
    ) -> None:
        monkeypatch.setenv("EVENT_INGEST_HMAC_SECRET", "ingest-hmac-secret-abc")  # noqa: S105
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        key = "sk-ant-test-fixture"  # noqa: S105  # pragma: allowlist secret
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
        module = _import_production()
        assert module.ANTHROPIC_API_KEY == key

    def test_skill_override_route_without_key_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _production_env: None,
        _restore_production_module: None,
    ) -> None:
        # Tier-2 path: org default stays openai, one skill pins
        # anthropic — the key is still required.
        monkeypatch.setenv("EVENT_INGEST_HMAC_SECRET", "ingest-hmac-secret-abc")  # noqa: S105
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(
            "config.settings.base.SKILL_LLM_PROVIDER",
            {"intent": "anthropic"},
        )
        with pytest.raises(ImproperlyConfigured) as exc_info:
            _import_production()
        assert "ANTHROPIC_API_KEY" in str(exc_info.value)


class TestNoFeatureActivation:
    """Declaration alone activates nothing."""

    def test_empty_secret_keeps_ingest_fail_closed(self) -> None:
        # Declaring EVENT_INGEST_HMAC_SECRET does not open the
        # endpoint: with an empty secret the verifier still rejects
        # every delivery (fail-closed), exactly as before.
        from apps.eventbus.ingest_security import (
            REASON_NO_SECRET,
            verify_signature,
        )

        result = verify_signature(
            body=b"{}",
            signature_header="sha256=deadbeef",
            timestamp_header="0",
            secret="",
        )
        assert result.ok is False
        assert result.reason == REASON_NO_SECRET

    def test_declaration_does_not_route_to_anthropic(self) -> None:
        # With the declared defaults the router still resolves the
        # org-wide openai route; no Anthropic client is constructed.
        from apps.llm.router import LLMRouter

        with override_settings(LLM_PROVIDER="openai", SKILL_LLM_PROVIDER={}):
            candidate, source = LLMRouter()._resolve_candidate(None, skill="")
        assert candidate == "openai"
        assert source == "org_default"
