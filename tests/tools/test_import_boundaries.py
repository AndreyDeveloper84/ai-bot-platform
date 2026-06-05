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
        baseline = frozenset({("T1-api-no-secret", "apps/api/views.py", "apps.secret")})
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
        baseline = frozenset({("T2", "apps/api/views.py", "apps.secret")})
        v = ib.scan_paths([tmp_path / "apps"], tmp_path, contracts=(c,), baseline=baseline)
        assert len(v) == 1
        assert "apps.secret2" in v[0].message

    def test_stale_baseline_entry_reported(self, tmp_path) -> None:
        # Baseline claims a crossing that the (clean) file no longer makes.
        _write(tmp_path, "apps/api/views.py", "from apps.public import x\n")
        baseline = frozenset({("T1-api-no-secret", "apps/api/views.py", "apps.secret")})
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
        baseline = frozenset({("T1-api-no-secret", "apps/api/views.py", "apps.secret")})
        v = ib.scan_paths(
            [tmp_path / "apps" / "other"], tmp_path, contracts=_CONTRACTS, baseline=baseline
        )
        assert v == []


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
        hides a new violation nor carries a stale entry."""
        found = ib.scan_paths([_PROJECT_ROOT / "apps"], _PROJECT_ROOT, baseline=frozenset())
        observed = {
            (msg_id, v.file.resolve().relative_to(_PROJECT_ROOT).as_posix(), root)
            for v in found
            for msg_id, root in [_parse_contract_and_root(v.message)]
            if msg_id is not None
        }
        assert observed == set(ib.BASELINE)

    def test_catalog_baseline_matches_reality(self) -> None:
        """Same regression for the MKT1 cross-tenant catalog rule: with an
        empty catalog baseline, the flagged files are EXACTLY the shipped
        CATALOG_CROSS_TENANT_BASELINE."""
        found = ib.scan_paths([_PROJECT_ROOT / "apps"], _PROJECT_ROOT, catalog_baseline=frozenset())
        observed = {
            (
                ib.CATALOG_CROSS_TENANT_CONTRACT_ID,
                v.file.resolve().relative_to(_PROJECT_ROOT).as_posix(),
                ib._CATALOG_ROOT,
            )
            for v in found
            if ib.CATALOG_CROSS_TENANT_CONTRACT_ID in v.message
        }
        assert observed == set(ib.CATALOG_CROSS_TENANT_BASELINE)


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
            {(ib.CATALOG_CROSS_TENANT_CONTRACT_ID, "apps/foo/views.py", ib._CATALOG_ROOT)}
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
            {(ib.CATALOG_CROSS_TENANT_CONTRACT_ID, "apps/foo/views.py", ib._CATALOG_ROOT)}
        )
        v = self._scan_cat(tmp_path, baseline)
        assert len(v) == 1
        assert "STALE BASELINE" in v[0].message


def _parse_contract_and_root(message: str) -> tuple[str | None, str | None]:
    """Pull (contract_id, imported-root) back out of a violation message.

    Message shape: `[<id>] imports '<module>' — ...`. The root is the
    forbidden module the import fell under, recomputed from the contract.
    """
    if not message.startswith("[") or "imports '" not in message:
        return None, None
    contract_id = message[1 : message.index("]")]
    mod_start = message.index("imports '") + len("imports '")
    module = message[mod_start : message.index("'", mod_start)]
    contract = next((c for c in ib.CONTRACTS if c.id == contract_id), None)
    if contract is None:
        return None, None
    return contract_id, ib._matched_root(module, contract.forbidden_modules)
