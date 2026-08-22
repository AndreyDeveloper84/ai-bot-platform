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

    def test_ayla_marketplace_voice_importable(self) -> None:
        """#1026 — the tenant-less discovery reply consumes the frozen
        marketplace voice (apps/persona/voice.py since DRF-1061; it was
        apps/orchestrator/discovery.py when this test was written). Sibling
        of the already-consumed FORMULA_TELA_VOICE
        (apps/promptreg/voice_examples.py).
        """
        from ayla_ai_core import AYLA_MARKETPLACE_VOICE

        assert AYLA_MARKETPLACE_VOICE.assistant_name == "Ayla"

    def test_offline_voice_mirror_matches_frozen_constant(self) -> None:
        """The offline mirror of the marketplace voice must not drift from the
        frozen AYLA_MARKETPLACE_VOICE it copies (CR NIT, #1026).

        DRF-1061 moved that mirror out of apps/orchestrator/discovery.py into
        apps/persona/voice.py, where all three surfaces now take their name
        from one table; the guard follows the thing it guards.
        apps/persona/tests/test_voice.py holds the same invariant next to the
        code. This copy stays deliberately: `pytest tests/smoke/` fails in
        seconds, ahead of the ~30-minute `pytest apps/` step, and a library
        bump that silently diverges from the mirror is worth failing fast on
        (same reasoning as the eventbus fast-fail step in ci.yml).

        Runs only when the extra is installed (this class is skipif-gated).
        """
        from ayla_ai_core import AYLA_MARKETPLACE_VOICE

        from apps.persona.voice import _FROZEN_MIRROR

        assert _FROZEN_MIRROR == {
            "assistant_name": AYLA_MARKETPLACE_VOICE.assistant_name,
            "business_name": AYLA_MARKETPLACE_VOICE.business_name,
            "domain": AYLA_MARKETPLACE_VOICE.domain,
            "off_topic_redirect": AYLA_MARKETPLACE_VOICE.off_topic_redirect,
        }

    def test_package_version_pinned(self) -> None:
        """Pin: v0.9.0 SHA + [django] extra — memory block export."""
        import ayla_ai_core

        assert ayla_ai_core.__version__ == "0.9.0", (
            f"ayla-ai-core version drift: expected '0.9.0', got "
            f"{ayla_ai_core.__version__!r}. Check pyproject.toml [ai-core] "
            "extra + uv.lock. Bump procedure in pyproject.toml header."
        )

    def test_package_sha_pinned(self) -> None:
        """A9 SHA-divergence guard (PR #935 — 2026-06-01).

        Intra-repo invariant: the SHA installed by `pip` / `uv resolve`
        MUST match the SHA pinned in `pyproject.toml`. This guards
        against `uv.lock` ↔ `pyproject.toml` drift.

        SCOPE NOTE (adversarial CR H3): this is INTRA-repo only —
        cross-repo coordination (bot-platform ↔ Ayla djangoproject)
        is procedural per the bump checklist in `pyproject.toml`
        header. Boot-log grep across both containers is the
        operational verification surface. There is no CI gate that
        auto-checks Ayla's pin from this repo's CI.

        Implementation: read PEP 610 `direct_url.json` metadata,
        normalize the SHA (lowercase), compare against the SHA parsed
        from `pyproject.toml`. The regex accepts both `git+https://`
        and `git+ssh://` URL forms (private-repo CI uses ssh auth)
        and requires exactly one declaration (duplicates surface as
        explicit failure rather than silent first-match).
        """
        import json
        import re
        import warnings
        from importlib.metadata import distribution
        from pathlib import Path

        dist = distribution("ayla-ai-core")

        # Parse the canonical SHA pin out of pyproject.toml FIRST so the
        # adversarial CR M3 «PyPI install silently disables SHA guard»
        # attack surfaces clearly: if pyproject declares a git+ pin,
        # we MUST find a matching SHA in the install metadata. If
        # pyproject doesn't declare git+ (future PyPI migration), we
        # honour the skip path but warn loudly.
        repo_root = Path(__file__).resolve().parents[2]
        pyproject_text = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
        # Adversarial CR H1 — accept https OR ssh, hex case-insensitive,
        # require exactly ONE declaration to defend against stale-pin
        # commented-out lines silently winning regex search.
        matches = re.findall(
            r"ayla-ai-core\[django\]\s*@\s*git\+(?:https|ssh)://[^@\s]+@([a-fA-F0-9]{40})",
            pyproject_text,
        )
        if not matches:
            # No git+ pin in pyproject — installer used PyPI / wheel /
            # local path. Version guard (above) still catches major
            # drift. Skip the SHA gate but make it visible.
            payload_missing = dist.read_text("direct_url.json")
            warnings.warn(
                "ayla-ai-core SHA gate skipped: no `git+(https|ssh)://...@<40hex>` "
                "pin in pyproject.toml. Verify out-of-band. "
                f"direct_url.json present={payload_missing is not None}",
                stacklevel=2,
            )
            pytest.skip("pyproject pin is non-git — SHA gate not applicable")
        assert len(matches) == 1, (
            f"Expected exactly 1 `ayla-ai-core[django]` git pin in "
            f"pyproject.toml; found {len(matches)}. Duplicates / stale "
            "commented-out lines silently break the SHA gate."
        )
        pinned_sha = matches[0].lower()

        payload = dist.read_text("direct_url.json")
        if payload is None:
            pytest.fail(
                "ayla-ai-core pyproject declares a git pin but the install has "
                "no PEP 610 `direct_url.json` — installer resolved from a "
                "non-VCS source (PyPI / local wheel / editable). This violates "
                "the A9 SHA-pin invariant. Re-run `uv sync --frozen` to "
                "restore the git install."
            )
        installed_data = json.loads(payload)
        installed_sha = ((installed_data.get("vcs_info") or {}).get("commit_id", "") or "").lower()
        assert installed_sha, (
            "ayla-ai-core direct_url.json missing vcs_info.commit_id — "
            "expected git install with resolved SHA."
        )

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
