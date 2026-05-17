"""Pin the ``OPENAI_API_KEY`` Django setting wiring.

Without ``OPENAI_API_KEY`` declared in ``config/settings/base.py`` the
``OpenAIProvider`` silently constructs with an empty key (it reads via
``getattr(settings, "OPENAI_API_KEY", "")``) and every embedding / chat
call returns 401. The ingester's broad ``except`` then surfaces this as
``failed: N`` with no stack trace — exactly the failure mode that broke
the KB-RAG Sub-6 seed run (PR #136) until the key was patched in
manually.

These tests pin the wiring so a future settings refactor can't quietly
drop it.
"""

from __future__ import annotations

import os
from unittest import mock

from django.conf import settings
from django.test.utils import override_settings


class TestOpenAiApiKeyWired:
    def test_setting_is_declared(self):
        # The attribute must exist on the settings module — `getattr(...,
        # "OPENAI_API_KEY", "")` would mask the regression otherwise.
        assert hasattr(settings, "OPENAI_API_KEY"), (
            "OPENAI_API_KEY must be declared in config/settings/base.py. "
            "Without it, OpenAIProvider silently uses an empty key and all "
            "OpenAI calls return 401."
        )

    def test_setting_reads_from_env(self):
        # Re-import the settings module fresh under a patched env to confirm
        # the binding `OPENAI_API_KEY = os.environ.get(...)` actually reads
        # at module import time (not lazily, not from a hardcoded default).
        import importlib

        import config.settings.base as base_settings

        fixture_value = "sk-test-fixture"  # pragma: allowlist secret
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": fixture_value}, clear=False):
            reloaded = importlib.reload(base_settings)
            assert reloaded.OPENAI_API_KEY == fixture_value

        # Reload again without the env var to confirm the empty-string
        # default — settings module must not retain stale values across
        # subsequent test imports.
        env_without_key = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with mock.patch.dict(os.environ, env_without_key, clear=True):
            reloaded = importlib.reload(base_settings)
            assert reloaded.OPENAI_API_KEY == ""

        # Final reload to restore real env-derived value so this test
        # leaves no side-effect for downstream tests in the same process.
        importlib.reload(base_settings)

    def test_provider_picks_up_setting(self):
        # End-to-end wiring: OpenAIProvider() with no explicit api_key
        # must inherit settings.OPENAI_API_KEY.
        from apps.orchestrator.llm.openai_provider import OpenAIProvider

        wire_value = "sk-wire-test"  # pragma: allowlist secret
        with override_settings(OPENAI_API_KEY=wire_value):
            provider = OpenAIProvider()
            assert provider.api_key == wire_value, (
                "OpenAIProvider() must inherit OPENAI_API_KEY from Django "
                "settings when no api_key kwarg is passed."
            )
