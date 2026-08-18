"""Tests for tools/lint/import_boundaries.py — ADR-0009 G-series guard (#1001).

Mirrors the test contract of `test_red_zone_guard.py`: prove BOTH what the
guard catches (every import shape, incl. the adversarial set S5 flagged:
aliasing, importlib hops, TYPE_CHECKING blocks, relative imports,
from-package-import-submodule) AND what it deliberately doesn't, plus the
baseline mechanics (accepted debt passes; new debt fails; stale baseline
fails) and a regression that pins BASELINE to the real `apps/` tree.
"""

from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# `tools/` is not a package (no __init__.py) — import via path injection,
# same pattern as test_red_zone_guard.py.
sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "lint"))
import import_boundaries as ib  # type: ignore[import-not-found]  # noqa: E402


# A synthetic contract used by most unit tests so they don't depend on the
# real G-series wording: "apps/api/ must not import apps.secret(.*)".
_C = ib.Contract(
    id="T1-api-no-secret",
    issue="#test",
    source_prefixes=("apps/api/",),
    forbidden_modules=("apps.secret",),
    message="api must not import secret.",
)
_CONTRACTS = (_C,)
_EMPTY = frozenset()  # type: frozenset[ib.BaselineKey]


def _write(root: Path, rel: str, source: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dedent(source), encoding="utf-8")
    return p


def _scan(root: Path, baseline: frozenset[ib.BaselineKey] = _EMPTY) -> list[ib.Violation]:
    return ib.scan_paths([root / "apps"], root, contracts=_CONTRACTS, baseline=baseline)


# ── Import shapes that MUST be caught ─────────────────────────────────


class TestDetectsForbiddenEdges:
    @pytest.mark.parametrize(
        "stmt",
        [
            "from apps.secret import thing",
            "from apps.secret import (a, b)",
            "import apps.secret",
            "import apps.secret as s",
            "from apps.secret.sub import thing",
            "from apps.secret import sub as aliased",
            "from apps import secret",  # from-package-import-submodule
        ],
    )
    def test_static_import_shapes(self, tmp_path, stmt: str) -> None:
        _write(tmp_path, "apps/api/views.py", stmt + "\n")
        v = _scan(tmp_path)
        assert len(v) == 1, f"{stmt!r} not caught"
        assert "T1-api-no-secret" in v[0].message

    def test_type_checking_block_is_counted(self, tmp_path) -> None:
        _write(
            tmp_path,
            "apps/api/views.py",
            """
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                from apps.secret import Thing
            """,
        )
        v = _scan(tmp_path)
        assert len(v) == 1, "import hidden in TYPE_CHECKING must still be flagged"

    @pytest.mark.parametrize(
        "call",
        [
            "importlib.import_module('apps.secret')",
            "importlib.import_module('apps.secret.sub')",
            "import_module('apps.secret')",
            "__import__('apps.secret')",
        ],
    )
    def test_dynamic_import_literal(self, tmp_path, call: str) -> None:
        _write(tmp_path, "apps/api/views.py", f"import importlib\nx = {call}\n")
        v = _scan(tmp_path)
        assert len(v) == 1, f"{call!r} not caught"

    def test_relative_import_resolved_to_absolute(self, tmp_path) -> None:
        # File apps/api/sub/mod.py, package apps.api.sub; `from ...secret`
        # (level 3) resolves to apps.secret.
        _write(tmp_path, "apps/api/sub/mod.py", "from ...secret import thing\n")
        v = _scan(tmp_path)
        assert len(v) == 1, "relative import not resolved/caught"


# ── Things that MUST NOT fire ─────────────────────────────────────────


class TestAllowsLegitImports:
    def test_sibling_prefix_not_matched(self, tmp_path) -> None:
        # apps.secretstuff shares a string prefix but is NOT a submodule.
        _write(tmp_path, "apps/api/views.py", "from apps.secretstuff import x\n")
        assert _scan(tmp_path) == []

    def test_unrelated_import_clean(self, tmp_path) -> None:
        _write(tmp_path, "apps/api/views.py", "from apps.public import x\n")
        assert _scan(tmp_path) == []

    def test_forbidden_import_from_non_source_dir_clean(self, tmp_path) -> None:
        # The booking app itself may import its own secret — only apps/api/ is
        # the constrained source.
        _write(tmp_path, "apps/booking/svc.py", "from apps.secret import x\n")
        assert _scan(tmp_path) == []

    @pytest.mark.parametrize(
        "rel",
        [
            "apps/api/tests/test_x.py",
            "apps/api/migrations/0001_init.py",
            "apps/api/conftest.py",
            "apps/api/views_test.py",
        ],
    )
    def test_test_and_migration_files_skipped(self, tmp_path, rel: str) -> None:
        _write(tmp_path, rel, "from apps.secret import x\n")
        assert _scan(tmp_path) == []


# ── Baseline mechanics ────────────────────────────────────────────────


class TestBaseline:
    def test_baselined_crossing_passes(self, tmp_path) -> None:
        _write(tmp_path, "apps/api/views.py", "from apps.secret import x\n")
        baseline = frozenset({("T1-api-no-secret", "apps/api/views.py", "<module>", "apps.secret")})
        assert _scan(tmp_path, baseline) == []

    def test_same_crossing_without_baseline_fails(self, tmp_path) -> None:
        _write(tmp_path, "apps/api/views.py", "from apps.secret import x\n")
        assert len(_scan(tmp_path)) == 1

    def test_new_target_in_baselined_file_still_fails(self, tmp_path) -> None:
        # File baselined for apps.secret; a NEW edge to apps.secret2 must
        # still fire (granularity is per forbidden-root, not per-file).
        c = ib.Contract(
            id="T2",
            issue="#t",
            source_prefixes=("apps/api/",),
            forbidden_modules=("apps.secret", "apps.secret2"),
            message="m",
        )
        _write(
            tmp_path, "apps/api/views.py", "from apps.secret import a\nfrom apps.secret2 import b\n"
        )
        baseline = frozenset({("T2", "apps/api/views.py", "<module>", "apps.secret")})
        v = ib.scan_paths([tmp_path / "apps"], tmp_path, contracts=(c,), baseline=baseline)
        assert len(v) == 1
        assert "apps.secret2" in v[0].message

    def test_stale_baseline_entry_reported(self, tmp_path) -> None:
        # Baseline claims a crossing that the (clean) file no longer makes.
        _write(tmp_path, "apps/api/views.py", "from apps.public import x\n")
        baseline = frozenset({("T1-api-no-secret", "apps/api/views.py", "<module>", "apps.secret")})
        v = _scan(tmp_path, baseline)
        assert len(v) == 1
        assert "STALE BASELINE" in v[0].message

    def test_partial_scan_does_not_report_unscanned_baseline_as_stale(self, tmp_path) -> None:
        # The baselined file (apps/api/views.py) exists and still crosses, but
        # we scan only a DIFFERENT subtree (apps/other/). A baseline entry for
        # a file outside the scanned paths must NOT be reported stale —
        # otherwise a per-subtree run would demand deleting live baseline
        # entries and silently weaken enforcement.
        _write(tmp_path, "apps/api/views.py", "from apps.secret import x\n")
        _write(tmp_path, "apps/other/mod.py", "from apps.public import ok\n")
        baseline = frozenset({("T1-api-no-secret", "apps/api/views.py", "<module>", "apps.secret")})
        v = ib.scan_paths(
            [tmp_path / "apps" / "other"], tmp_path, contracts=_CONTRACTS, baseline=baseline
        )
        assert v == []


# ── Qualname granularity (DRF-1157) ───────────────────────────────────
#
# The hole this closes: the baseline key used to be (contract, file,
# root), so ONE accepted crossing in a file silenced EVERY other crossing
# to the same root anywhere else in that file. apps/miniapp_api/views.py
# imports BookingRequest from four different functions; a single line
# covered all four, including the one G9 was written to surface.


class TestQualnameGranularity:
    _TWO_SITES = """
        def legit():
            from apps.secret import ok
            return ok

        def sneaky():
            from apps.secret import bad
            return bad
        """

    def test_module_level_import_keyed_as_module(self, tmp_path) -> None:
        _write(tmp_path, "apps/api/views.py", "from apps.secret import x\n")
        v = _scan(tmp_path)
        assert v[0].key == ("T1-api-no-secret", "apps/api/views.py", "<module>", "apps.secret")

    def test_function_local_import_keyed_by_function(self, tmp_path) -> None:
        _write(tmp_path, "apps/api/views.py", "def f():\n    from apps.secret import x\n")
        v = _scan(tmp_path)
        assert v[0].key == ("T1-api-no-secret", "apps/api/views.py", "f", "apps.secret")

    def test_nested_scopes_are_dotted(self, tmp_path) -> None:
        _write(
            tmp_path,
            "apps/api/views.py",
            """
            class C:
                async def m(self):
                    def inner():
                        from apps.secret import x
                    return inner
            """,
        )
        v = _scan(tmp_path)
        assert v[0].key[2] == "C.m.inner"

    def test_baselining_one_call_site_does_not_cover_its_neighbour(self, tmp_path) -> None:
        """THE regression for DRF-1157 — this is what file granularity hid."""
        _write(tmp_path, "apps/api/views.py", self._TWO_SITES)
        baseline = frozenset({("T1-api-no-secret", "apps/api/views.py", "legit", "apps.secret")})
        v = _scan(tmp_path, baseline)
        assert len(v) == 1, "the un-baselined sibling call site must still fail"
        assert "sneaky" in v[0].message
        assert v[0].key == ("T1-api-no-secret", "apps/api/views.py", "sneaky", "apps.secret")

    def test_both_call_sites_baselined_passes(self, tmp_path) -> None:
        _write(tmp_path, "apps/api/views.py", self._TWO_SITES)
        baseline = frozenset(
            {
                ("T1-api-no-secret", "apps/api/views.py", "legit", "apps.secret"),
                ("T1-api-no-secret", "apps/api/views.py", "sneaky", "apps.secret"),
            }
        )
        assert _scan(tmp_path, baseline) == []

    def test_moving_a_baselined_import_to_a_new_function_fails(self, tmp_path) -> None:
        # A crossing that migrates to a different scope is NEW debt at the
        # new site and STALE at the old one — both must be reported, so the
        # baseline cannot be dodged by relocating the import.
        _write(tmp_path, "apps/api/views.py", "def renamed():\n    from apps.secret import x\n")
        baseline = frozenset({("T1-api-no-secret", "apps/api/views.py", "old", "apps.secret")})
        v = _scan(tmp_path, baseline)
        messages = " | ".join(x.message for x in v)
        assert len(v) == 2, messages
        assert "STALE BASELINE" in messages
        assert "renamed" in messages


# ── Integration: the real repo ────────────────────────────────────────


class TestRealReposClean:
    def test_apps_clean_under_real_baseline(self) -> None:
        """The CI gate: scanning the real apps/ with the shipped CONTRACTS +
        BASELINE yields zero violations (no new debt, no stale baseline)."""
        v = ib.scan_paths([_PROJECT_ROOT / "apps"], _PROJECT_ROOT)
        assert v == [], "\n".join(x.format() for x in v)

    def test_baseline_matches_reality(self) -> None:
        """Regression: with an EMPTY baseline, the crossings the guard finds
        in apps/ are EXACTLY the shipped BASELINE — so the baseline neither
        hides a new violation nor carries a stale entry.

        Keys come off ``Violation.key`` rather than being re-parsed out of
        the message text: since DRF-1157 the key carries the qualname, which
        the human-readable message renders as prose."""
        found = ib.scan_paths([_PROJECT_ROOT / "apps"], _PROJECT_ROOT, baseline=frozenset())
        observed = {
            v.key
            for v in found
            if v.key is not None and v.key[0] != ib.CATALOG_CROSS_TENANT_CONTRACT_ID
        }
        assert observed == set(ib.BASELINE)

    def test_catalog_baseline_matches_reality(self) -> None:
        """Same regression for the MKT1 cross-tenant catalog rule: with an
        empty catalog baseline, the flagged files are EXACTLY the shipped
        CATALOG_CROSS_TENANT_BASELINE."""
        found = ib.scan_paths([_PROJECT_ROOT / "apps"], _PROJECT_ROOT, catalog_baseline=frozenset())
        observed = {
            v.key
            for v in found
            if v.key is not None and v.key[0] == ib.CATALOG_CROSS_TENANT_CONTRACT_ID
        }
        assert observed == set(ib.CATALOG_CROSS_TENANT_BASELINE)

    def test_catalog_rule_stays_file_granular(self) -> None:
        """MKT1 reports one violation per file by design — its keys must keep
        the synthetic ``<file>`` qualname, not an enclosing scope. Only the
        import-edge contracts got the DRF-1157 split."""
        assert all(k[2] == ib.FILE_QUALNAME for k in ib.CATALOG_CROSS_TENANT_BASELINE)


# ── MKT1: cross-tenant catalog-read rule (#1018) ──────────────────────


class TestCatalogCrossTenantRule:
    def _scan_cat(self, root, baseline=_EMPTY):
        # Only the catalog rule matters here; pass no import contracts.
        return ib.scan_paths([root / "apps"], root, contracts=(), catalog_baseline=baseline)

    @pytest.mark.parametrize(
        "stmt",
        [
            "CatalogMaster.all_tenants.filter(x=1)",
            "CatalogService.all_tenants.all()",
            "qs = CatalogMaster.all_tenants.select_for_update().get(pk=1)",
        ],
    )
    def test_catalog_all_tenants_flagged_outside_marketplace(self, tmp_path, stmt) -> None:
        _write(tmp_path, "apps/foo/views.py", stmt + "\n")
        v = self._scan_cat(tmp_path)
        assert len(v) == 1
        assert ib.CATALOG_CROSS_TENANT_CONTRACT_ID in v[0].message

    def test_allowed_inside_marketplace(self, tmp_path) -> None:
        _write(tmp_path, "apps/marketplace/discovery.py", "CatalogMaster.all_tenants.all()\n")
        assert self._scan_cat(tmp_path) == []

    def test_baselined_file_passes(self, tmp_path) -> None:
        _write(tmp_path, "apps/foo/views.py", "CatalogMaster.all_tenants.all()\n")
        baseline = frozenset(
            {
                (
                    ib.CATALOG_CROSS_TENANT_CONTRACT_ID,
                    "apps/foo/views.py",
                    ib.FILE_QUALNAME,
                    ib._CATALOG_ROOT,
                )
            }
        )
        assert self._scan_cat(tmp_path, baseline) == []

    def test_non_catalog_all_tenants_ignored(self, tmp_path) -> None:
        # `.all_tenants` on a non-catalog model is not this rule's concern.
        _write(tmp_path, "apps/foo/views.py", "BotUser.all_tenants.filter(x=1)\n")
        assert self._scan_cat(tmp_path) == []

    def test_objects_manager_not_flagged(self, tmp_path) -> None:
        _write(tmp_path, "apps/foo/views.py", "CatalogMaster.objects.all()\n")
        assert self._scan_cat(tmp_path) == []

    def test_stale_catalog_baseline_reported(self, tmp_path) -> None:
        _write(tmp_path, "apps/foo/views.py", "CatalogMaster.objects.all()\n")
        baseline = frozenset(
            {
                (
                    ib.CATALOG_CROSS_TENANT_CONTRACT_ID,
                    "apps/foo/views.py",
                    ib.FILE_QUALNAME,
                    ib._CATALOG_ROOT,
                )
            }
        )
        v = self._scan_cat(tmp_path, baseline)
        assert len(v) == 1
        assert "STALE BASELINE" in v[0].message


# (`_parse_contract_and_root` used to reconstruct the baseline key by
# scraping the violation message. DRF-1157 put the key on `Violation.key`
# instead — the message is prose for humans, the key is data for tests.)
