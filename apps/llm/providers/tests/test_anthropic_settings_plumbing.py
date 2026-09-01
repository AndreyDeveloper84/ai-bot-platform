"""Anthropic env → settings → provider plumbing (DRF-1437).

### The defect these tests exist for

``AnthropicProvider.__init__`` has always read::

    self._api_key = api_key or getattr(settings, "ANTHROPIC_API_KEY", "") or ""
    self._proxy = (
        getattr(settings, "ANTHROPIC_PROXY", "")
        or getattr(settings, "OPENAI_PROXY", "")
        or ""
    )

…and ``config/settings/base.py`` declared neither name. Both ``getattr``
calls therefore took their default forever. The owner could put
``ANTHROPIC_API_KEY`` in ``.env``, restart every container, and change
nothing: the provider still got an empty key, and every Anthropic
request still left through the shared ``OPENAI_PROXY``.

That is the failure mode catalogued in
``docs/HANDOFF_MAIN_WINDOW.md`` §0-duodecies — a missing setting produces
not an error but a **plausible substitute**. Three engineers lost a day
to the Postgres/Redis instances of it. Nothing failed loudly; the wrong
thing simply worked.

### Why both halves of every assertion

A test that only checks "the key arrives when set" passes against an
implementation that hard-codes a key, reads the wrong env var, or falls
through to OpenAI's credentials. So each claim is asserted on the same
data from both sides:

  * key set → provider has it; key unset → provider has ``""``, and
    specifically NOT the OpenAI key sitting right next to it;
  * ``ANTHROPIC_PROXY`` set → provider uses it, with ``OPENAI_PROXY``
    simultaneously set to a DIFFERENT address, so "prefers its own" and
    "falls back to shared" cannot both be satisfied by one behaviour.

### On the proxy addresses below

They are ``*.invalid`` hostnames with no credentials in the URL — see
the secrets-gate incident where a test proxy address carrying
``user:password@`` failed the ``detect-secrets`` gate. Never put
credentials in a test URL, even a fake one.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from apps.llm.providers.anthropic_provider import AnthropicProvider

# Non-credential placeholders. No vendor prefix, no entropy — these must
# never look like a real key to a human or to detect-secrets.
PLACEHOLDER_ANTHROPIC_KEY = "unit-test-placeholder-anthropic"
PLACEHOLDER_OPENAI_KEY = "unit-test-placeholder-openai"

# Distinct addresses so "took its own" and "fell back to shared" can
# never be confused for one another. No userinfo component by design.
ANTHROPIC_PROXY_URL = "http://proxy-anthropic.invalid:3128"
SHARED_PROXY_URL = "http://proxy-shared.invalid:3128"


# ---------------------------------------------------------------------------
# Layer 1 — environment variable → Django setting
# ---------------------------------------------------------------------------


def _load_base_settings_with_env(env: dict[str, str | None]) -> Any:
    """Execute ``config/settings/base.py`` in a FRESH namespace under ``env``.

    Reading ``django.conf.settings`` cannot prove anything here: the
    pytest ``settings`` fixture will happily hand back an attribute the
    settings module never defined — which is precisely how this defect
    survived a green suite for months. So we run the declaration
    statements themselves and read what they produced.

    A fresh module object is used rather than ``importlib.reload`` so the
    live, already-configured settings module is left untouched.
    """
    saved = {key: os.environ.get(key) for key in env}
    try:
        for key, value in env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

        base_path = Path(__file__).resolve().parents[4] / "config" / "settings" / "base.py"
        spec = importlib.util.spec_from_file_location("_base_settings_probe", base_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["_base_settings_probe"] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop("_base_settings_probe", None)
        return module
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class TestEnvironmentReachesSettings:
    """The link that did not exist: ``.env`` → ``settings``."""

    def test_anthropic_api_key_is_read_from_environment(self) -> None:
        module = _load_base_settings_with_env({"ANTHROPIC_API_KEY": PLACEHOLDER_ANTHROPIC_KEY})
        assert module.ANTHROPIC_API_KEY == PLACEHOLDER_ANTHROPIC_KEY

    def test_anthropic_proxy_is_read_from_environment(self) -> None:
        module = _load_base_settings_with_env({"ANTHROPIC_PROXY": ANTHROPIC_PROXY_URL})
        assert module.ANTHROPIC_PROXY == ANTHROPIC_PROXY_URL

    def test_llm_provider_is_read_from_environment(self) -> None:
        module = _load_base_settings_with_env({"LLM_PROVIDER": "anthropic"})
        assert module.LLM_PROVIDER == "anthropic"

    def test_skill_llm_provider_parses_json_from_environment(self) -> None:
        module = _load_base_settings_with_env({"SKILL_LLM_PROVIDER": '{"intent": "anthropic"}'})
        assert module.SKILL_LLM_PROVIDER == {"intent": "anthropic"}

    # -- the guards --------------------------------------------------------

    def test_unset_key_yields_empty_string_not_a_substitute(self) -> None:
        """The §0-duodecies shape: absence must produce an empty value,
        never a plausible stand-in. If this ever returns the OpenAI key
        or a hard-coded default, the whole feature is a silent lie.
        """
        module = _load_base_settings_with_env(
            {"ANTHROPIC_API_KEY": None, "OPENAI_API_KEY": PLACEHOLDER_OPENAI_KEY}
        )
        assert module.ANTHROPIC_API_KEY == ""
        assert module.ANTHROPIC_API_KEY != module.OPENAI_API_KEY

    def test_unset_proxy_yields_empty_string_at_the_settings_layer(self) -> None:
        """The OPENAI_PROXY fallback belongs to the PROVIDER, not to the
        setting. Collapsing it here would make the two indistinguishable
        and remove the owner's ability to route Anthropic separately.
        """
        module = _load_base_settings_with_env(
            {"ANTHROPIC_PROXY": None, "OPENAI_PROXY": SHARED_PROXY_URL}
        )
        assert module.ANTHROPIC_PROXY == ""
        assert module.OPENAI_PROXY == SHARED_PROXY_URL

    def test_malformed_skill_map_degrades_to_empty_not_to_a_crash(self) -> None:
        """A typo in an env var must not take the bot down at boot."""
        with pytest.warns(UserWarning, match="SKILL_LLM_PROVIDER"):
            module = _load_base_settings_with_env({"SKILL_LLM_PROVIDER": "not json at all"})
        assert module.SKILL_LLM_PROVIDER == {}


# ---------------------------------------------------------------------------
# Layer 2 — Django setting → provider instance
# ---------------------------------------------------------------------------


class TestSettingsReachTheProvider:
    def test_configured_key_reaches_the_provider(self, settings: Any) -> None:
        settings.ANTHROPIC_API_KEY = PLACEHOLDER_ANTHROPIC_KEY
        assert AnthropicProvider()._api_key == PLACEHOLDER_ANTHROPIC_KEY

    def test_explicit_constructor_key_wins_over_settings(self, settings: Any) -> None:
        settings.ANTHROPIC_API_KEY = PLACEHOLDER_ANTHROPIC_KEY
        provider = AnthropicProvider(api_key="explicit-placeholder")
        assert provider._api_key == "explicit-placeholder"

    # -- the guard ---------------------------------------------------------

    def test_empty_key_stays_empty_and_never_borrows_openais(self, settings: Any) -> None:
        settings.ANTHROPIC_API_KEY = ""
        settings.OPENAI_API_KEY = PLACEHOLDER_OPENAI_KEY

        provider = AnthropicProvider()
        assert provider._api_key == ""
        assert provider._api_key != PLACEHOLDER_OPENAI_KEY


class TestProxySelection:
    """The owner's requirement: Anthropic traffic must be able to leave
    from a DIFFERENT address than the shared tunnel.

    Both directions are asserted against the same pair of addresses, so
    no single hard-coded behaviour can satisfy both.
    """

    def test_dedicated_proxy_is_preferred_over_the_shared_one(self, settings: Any) -> None:
        settings.ANTHROPIC_PROXY = ANTHROPIC_PROXY_URL
        settings.OPENAI_PROXY = SHARED_PROXY_URL

        assert AnthropicProvider()._proxy == ANTHROPIC_PROXY_URL

    def test_shared_proxy_is_used_when_no_dedicated_one_is_set(self, settings: Any) -> None:
        settings.ANTHROPIC_PROXY = ""
        settings.OPENAI_PROXY = SHARED_PROXY_URL

        assert AnthropicProvider()._proxy == SHARED_PROXY_URL

    def test_no_proxy_configured_means_direct(self, settings: Any) -> None:
        settings.ANTHROPIC_PROXY = ""
        settings.OPENAI_PROXY = ""

        assert AnthropicProvider()._proxy == ""

    def test_explicit_empty_constructor_proxy_disables_both_fallbacks(
        self, settings: Any
    ) -> None:
        """``proxy=""`` is an explicit "go direct", distinct from
        ``proxy=None`` meaning "decide from settings". The provider
        already distinguishes them; without this test a refactor to
        ``proxy or getattr(...)`` would silently re-enable the tunnel.
        """
        settings.ANTHROPIC_PROXY = ANTHROPIC_PROXY_URL
        settings.OPENAI_PROXY = SHARED_PROXY_URL

        assert AnthropicProvider(proxy="")._proxy == ""

    def test_dedicated_proxy_reaches_the_sdk_client(self, settings: Any) -> None:
        """End of the chain: the address does not just land on an
        attribute, it becomes the httpx transport the SDK actually uses.
        No request is made — only the client is constructed.
        """
        settings.ANTHROPIC_PROXY = ANTHROPIC_PROXY_URL
        settings.OPENAI_PROXY = SHARED_PROXY_URL

        provider = AnthropicProvider(api_key=PLACEHOLDER_ANTHROPIC_KEY)
        client = provider._get_client()

        http_client = getattr(client, "_client", None)
        assert http_client is not None, "SDK client should carry the injected httpx client"
        mounts = getattr(http_client, "_mounts", {})
        assert mounts, "a proxied httpx.AsyncClient mounts a proxy transport"

    def test_no_proxy_means_no_injected_http_client(self, settings: Any) -> None:
        """The guard for the test above: ``_mounts`` must be able to come
        back empty, or "assert mounts" proves nothing about the proxy.
        """
        settings.ANTHROPIC_PROXY = ""
        settings.OPENAI_PROXY = ""

        client = AnthropicProvider(api_key=PLACEHOLDER_ANTHROPIC_KEY)._get_client()
        http_client = getattr(client, "_client", None)
        assert not getattr(http_client, "_mounts", {})
