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
        """Pin: v0.8.1 SHA + [django] extra — additive RELEASING/LTS docs + drift gate."""
        import ayla_ai_core

        assert ayla_ai_core.__version__ == "0.8.1", (
            f"ayla-ai-core version drift: expected '0.8.1', got "
            f"{ayla_ai_core.__version__!r}. Check pyproject.toml [ai-core] "
            "extra + uv.lock. Bump procedure in pyproject.toml header."
        )

    def test_package_sha_pinned(self) -> None:
        """A9 SHA-divergence guard (PR follow-up to maintainability roadmap
        Block A9 — 2026-06-01): the SHA installed по `pip` / `uv resolve`
        MUST match the SHA в `pyproject.toml`.

        This guards against:
        - Local `uv.lock` drift from `pyproject.toml` (e.g. ran
          `uv lock --upgrade-package ayla-ai-core` without updating the
          pyproject pin).
        - Cross-repo coordination failures: Ayla djangoproject's
          `requirements.txt` and bot-platform's `pyproject.toml` MUST
          pin the SAME SHA in production. This test fails the local
          repo if its own pin drifts; ops process documented in
          pyproject.toml header keeps both repos aligned.

        Implementation: read PEP 610 `direct_url.json` metadata which
        captures the install-time @SHA suffix.
        """
        import json
        import re
        from importlib.metadata import distribution
        from pathlib import Path

        dist = distribution("ayla-ai-core")
        payload = dist.read_text("direct_url.json")
        if payload is None:
            pytest.skip(
                "ayla-ai-core was not installed from a direct URL — "
                "PEP 610 metadata missing; cannot verify SHA pin."
            )
        installed_data = json.loads(payload)
        installed_sha = (installed_data.get("vcs_info") or {}).get("commit_id", "")
        assert installed_sha, (
            "ayla-ai-core direct_url.json missing vcs_info.commit_id — "
            "expected git install with resolved SHA."
        )

        # Parse the canonical SHA pin out of pyproject.toml so the
        # assertion stays in sync с the actual dependency declaration.
        repo_root = Path(__file__).resolve().parents[2]
        pyproject_text = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(
            r"ayla-ai-core\[django\]\s*@\s*git\+https://[^@]+@([a-f0-9]{40})",
            pyproject_text,
        )
        assert match, (
            "Could not locate `ayla-ai-core[django] @ git+...@<SHA>` pin in "
            "pyproject.toml. The A9 SHA guard depends on this exact shape."
        )
        pinned_sha = match.group(1)
        assert installed_sha == pinned_sha, (
            f"ayla-ai-core SHA drift: installed={installed_sha!r}, "
            f"pyproject.toml pin={pinned_sha!r}. Re-run "
            "`uv lock --upgrade-package ayla-ai-core` and `uv sync --frozen` "
            "to converge. Bump procedure in pyproject.toml header."
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
