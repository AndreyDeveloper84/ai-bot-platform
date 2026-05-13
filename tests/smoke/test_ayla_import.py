"""ayla-ai-core import allow-list smoke (DRF-557 / Sprint 7 / K0).

When the `--extra ai-core` CI extra is enabled (K0), ayla-ai-core v0.6.0
(K12 / DRF-570) is installed. Sprint 7 / Decision 1 + Decision 15 lock
the platform to a **narrow allow-list** of imports from that package:

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
        """K12 (DRF-570) pins to v0.6.0 — drift indicates lockfile rot."""
        import ayla_ai_core

        version = getattr(ayla_ai_core, "__version__", None)
        # ayla-ai-core may not expose __version__ in 0.6.0; the real
        # check is the git ref pin in pyproject.toml. This is a soft
        # signal — only assert when the attribute exists.
        if version is not None:
            assert version.startswith("0.6"), (
                f"ayla-ai-core version drift: expected 0.6.x, got {version!r}. "
                "Check pyproject.toml [ai-core] extra + uv.lock."
            )
