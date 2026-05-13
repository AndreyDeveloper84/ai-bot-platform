"""ayla-ai-core import allow-list smoke (DRF-557, DRF-618 / Sprint 7 / K0 + A2).

When the `--extra ai-core` CI extra is enabled (K0), ayla-ai-core v0.7.0
(A2 / DRF-618, SHA pin) is installed. Sprint 7 / Decision 1 + Decision 15
lock the platform to a **narrow allow-list** of imports from that package:

  * ``ayla_ai_core.prompts.BrandVoiceConfig``
  * ``ayla_ai_core.prompts.Example``
  * ``ayla_ai_core.context.SpecialistContext``
  * ``ayla_ai_core.tools.ActionType``

Forbidden (covered by Sprint 7 / F-track unit tests, NOT here):

  * ``ayla_ai_core.prompts.render_system_prompt`` — booking-domain
    template hardcodes ``masters_summary`` slot. FAQ skill builds its
    own template via :mod:`apps.skills.faq.prompts` (Decision 15).
  * ``ayla_ai_core.tool_handlers`` — Phase 2.3 booking-flow handlers.
    Platform reimplements per skill under :mod:`apps.tools`.

This smoke test enforces only the positive allow-list (imports succeed).
The negative direction is locked by F-track linter tests in
``apps/skills/faq/tests/`` once F2 (DRF-589) lands.

### Behaviour without the extra

If the package is not installed (CI without ``--extra ai-core``, or
local ``uv sync`` without the extra), the test SKIPS rather than fails
— platform smoke must still pass for contributors who don't touch AI.
"""

from __future__ import annotations

import importlib

import pytest


def _ayla_installed() -> bool:
    try:
        importlib.import_module("ayla_ai_core")
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _ayla_installed(),
    reason="ayla-ai-core not installed (sync with `--extra ai-core` to enable)",
)


class TestAylaAllowList:
    def test_brand_voice_config_importable(self) -> None:
        from ayla_ai_core.prompts import BrandVoiceConfig

        assert BrandVoiceConfig is not None

    def test_example_importable(self) -> None:
        from ayla_ai_core.prompts import Example

        assert Example is not None

    def test_specialist_context_importable(self) -> None:
        from ayla_ai_core.context import SpecialistContext

        assert SpecialistContext is not None

    def test_action_type_importable(self) -> None:
        from ayla_ai_core.tools import ActionType

        assert ActionType is not None

    def test_package_version_pinned(self) -> None:
        """Pin: v0.7.3 SHA (DRF-681/682/683/684) — drift = lockfile rot."""
        import ayla_ai_core

        assert ayla_ai_core.__version__ == "0.7.3", (
            f"ayla-ai-core version drift: expected '0.7.3', got "
            f"{ayla_ai_core.__version__!r}. Check pyproject.toml [ai-core] "
            "extra + uv.lock. Bump procedure in pyproject.toml header."
        )

    def test_render_system_prompt_escapes_braces_by_default(self) -> None:
        """B4 layer 2 (ayla v0.7.0) — protects consumers bypassing the adapter.

        `escape_for_format=True` is the default in ayla 0.7+. Even if a
        future caller forgets to route through `apps.orchestrator.ayla_adapter`,
        injected `{...}` in user-controlled fields cannot trigger a
        `KeyError` or template substitution inside ayla's `.format()` call.
        """
        from datetime import date

        from ayla_ai_core.context import build_specialist_context_from_candidates
        from ayla_ai_core.prompts import BrandVoiceConfig, render_system_prompt

        voice = BrandVoiceConfig(
            assistant_name="Alina",
            business_name="Test Beauty",
            business_address="Penza, Test str. 1",
            domain="beauty_salon",
            off_topic_redirect="—",
        )
        ctx = build_specialist_context_from_candidates([], tenant_id="smoke-test")
        rendered = render_system_prompt(
            today=date(2026, 5, 19),
            client_name="{evil}",  # pre-v0.7.0 → KeyError
            bookings_count=0,
            specialist_context=ctx,
            voice_config=voice,
        )
        assert "{evil}" in rendered, (
            "ayla v0.7.0 default `escape_for_format=True` should preserve "
            "literal braces in user-controlled fields."
        )
