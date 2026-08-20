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


# ── DRF-1130: select_related() under select_for_update() ──────────────
#
# The defect this rule replaces was a COMMENT. `apps/skills/booking/
# tools.py` said, in as many words, "FOOT-GUN: do NOT add
# .select_related(...) here" — and forty lines above it, in the same
# file, somebody did. So the tests below are about the two properties a
# comment does not have: it fires on a site nobody was reading, and it
# fires on a site somebody moved.


def _scan_row_lock(root: Path, baseline: frozenset = _EMPTY) -> list[ib.Violation]:
    """Scan with ONLY the DRF-1130 rule live."""
    return ib.scan_paths(
        [root / "apps"],
        root,
        contracts=(),
        catalog_baseline=_EMPTY,
        row_lock_baseline=baseline,
        hash_baseline=_EMPTY,
    )


class TestRowLockJoinRule:
    @pytest.mark.parametrize(
        "expr",
        [
            # the canonical shape, and the shape of both fixed defects
            'M.all_tenants.select_for_update().select_related("m").get(pk=1)',
            # order reversed — the SQL is identical, so the rule must be too
            'M.all_tenants.select_related("m").select_for_update().get(pk=1)',
            # filters/slicers between the two halves
            'M.all_tenants.select_for_update().filter(a=1).select_related("m").first()',
            'M.all_tenants.select_for_update().only("id").select_related("m", "t").get(pk=1)',
            # select_for_update carrying arguments
            'M.all_tenants.select_for_update(of=("self",)).select_related("m").get(pk=1)',
            'M.all_tenants.select_for_update(skip_locked=True).select_related("m").first()',
            # multi-hop join spec — the case a nullability resolver would
            # have to chase across two models
            'M.all_tenants.select_for_update().select_related("m__tenant").get(pk=1)',
        ],
    )
    def test_lock_plus_join_is_flagged(self, tmp_path, expr: str) -> None:
        _write(tmp_path, "apps/svc/mod.py", f"x = {expr}\n")
        v = _scan_row_lock(tmp_path)
        assert len(v) == 1, f"{expr!r} not caught"
        assert "DRF1130-no-join-under-row-lock" in v[0].message

    def test_multiline_chain_is_flagged(self, tmp_path) -> None:
        # The real sites are all wrapped like this; the rule must not
        # depend on the two calls sharing a line.
        _write(
            tmp_path,
            "apps/svc/mod.py",
            """
            def approve():
                req = (
                    ScheduleChangeRequest.all_tenants.select_for_update()
                    .select_related("master", "tenant")
                    .get(id=1)
                )
                return req
            """,
        )
        v = _scan_row_lock(tmp_path)
        assert len(v) == 1
        assert v[0].key == (
            "DRF1130-no-join-under-row-lock",
            "apps/svc/mod.py",
            "approve",
            "<select_for_update+select_related>",
        )

    def test_reported_once_per_chain(self, tmp_path) -> None:
        # Every enclosing call in the chain sees both method names; the
        # lock site must still be reported exactly once.
        _write(
            tmp_path,
            "apps/svc/mod.py",
            'x = M.all_tenants.select_for_update().select_related("m").filter(a=1).first()\n',
        )
        assert len(_scan_row_lock(tmp_path)) == 1

    def test_two_chains_in_one_function_report_separately(self, tmp_path) -> None:
        _write(
            tmp_path,
            "apps/svc/mod.py",
            """
            def f():
                a = M.all_tenants.select_for_update().select_related("m").get(pk=1)
                b = N.all_tenants.select_for_update().select_related("t").get(pk=2)
                return a, b
            """,
        )
        assert len(_scan_row_lock(tmp_path)) == 2


class TestRowLockJoinFalsePositives:
    """A rule that cries wolf gets baselined into silence. These are the
    shapes it must stay quiet about."""

    @pytest.mark.parametrize(
        "src_code",
        [
            # lock alone — the whole point of the DRF-1130 fix
            "x = M.all_tenants.select_for_update().get(pk=1)\n",
            'x = M.all_tenants.select_for_update(of=("self",)).get(pk=1)\n',
            # join alone, no lock in sight
            'x = M.all_tenants.select_related("m").filter(a=1)\n',
            # prefetch_related issues a SECOND query — no join, so it can
            # never widen the FOR UPDATE scope
            'x = M.all_tenants.select_for_update().prefetch_related("items").get(pk=1)\n',
            # only/defer/annotate near a lock are fine
            'x = M.all_tenants.select_for_update().only("id").get(pk=1)\n',
            # two INDEPENDENT chains, one locking, one joining
            "a = M.all_tenants.select_for_update().get(pk=1)\n"
            'b = M.all_tenants.select_related("m").all()\n',
            # a lock and a join on different querysets in one expression
            "x = (M.all_tenants.select_for_update().get(pk=1), "
            'N.all_tenants.select_related("m").first())\n',
        ],
    )
    def test_safe_shapes_are_silent(self, tmp_path, src_code: str) -> None:
        _write(tmp_path, "apps/svc/mod.py", src_code)
        assert _scan_row_lock(tmp_path) == []

    def test_test_files_are_out_of_scope(self, tmp_path) -> None:
        # DRF-1130 describes the PRODUCTION boundary: a test that builds
        # the shape on purpose (to assert Postgres rejects it, say) is not
        # the target. DRF-1158 is the one rule that crosses into tests.
        _write(
            tmp_path,
            "apps/svc/tests/test_mod.py",
            'x = M.all_tenants.select_for_update().select_related("m").get(pk=1)\n',
        )
        assert _scan_row_lock(tmp_path) == []

    def test_queryset_split_across_statements_is_a_known_blind_spot(self, tmp_path) -> None:
        # Documented limitation, pinned so a future reader knows it is a
        # decision and not an oversight: the rule walks ONE call chain.
        _write(
            tmp_path,
            "apps/svc/mod.py",
            """
            def f():
                qs = M.all_tenants.select_for_update()
                return qs.select_related("m").get(pk=1)
            """,
        )
        assert _scan_row_lock(tmp_path) == []


class TestRowLockJoinBaseline:
    _TWO_SITES = """
        def approve():
            return M.all_tenants.select_for_update().select_related("m").get(pk=1)

        def reject():
            return M.all_tenants.select_for_update().select_related("m").get(pk=2)
        """

    def _key(self, qualname: str) -> ib.BaselineKey:
        return (
            "DRF1130-no-join-under-row-lock",
            "apps/svc/mod.py",
            qualname,
            "<select_for_update+select_related>",
        )

    def test_baselined_site_passes(self, tmp_path) -> None:
        _write(tmp_path, "apps/svc/mod.py", self._TWO_SITES)
        both = frozenset({self._key("approve"), self._key("reject")})
        assert _scan_row_lock(tmp_path, both) == []

    def test_baselining_one_site_leaves_its_neighbour_red(self, tmp_path) -> None:
        # Same granularity guarantee as DRF-1157: the two availability.py
        # sites are separate lines precisely so migrating `approve` cannot
        # silently keep `reject` green.
        _write(tmp_path, "apps/svc/mod.py", self._TWO_SITES)
        v = _scan_row_lock(tmp_path, frozenset({self._key("approve")}))
        assert len(v) == 1
        assert v[0].key == self._key("reject")

    def test_stale_entry_reported_when_the_join_is_dropped(self, tmp_path) -> None:
        # The ratchet: DRF-1130 was fixed by DELETING the select_related,
        # and the baseline line then has to go too.
        _write(
            tmp_path,
            "apps/svc/mod.py",
            "def approve():\n    return M.all_tenants.select_for_update().get(pk=1)\n",
        )
        v = _scan_row_lock(tmp_path, frozenset({self._key("approve")}))
        assert len(v) == 1
        assert "STALE BASELINE" in v[0].message


# ── DRF-1158: builtin hash() into a stored value ──────────────────────


def _scan_hash(root: Path, baseline: frozenset = _EMPTY) -> list[ib.Violation]:
    """Scan with ONLY the DRF-1158 rule live."""
    return ib.scan_paths(
        [root / "apps"],
        root,
        contracts=(),
        catalog_baseline=_EMPTY,
        row_lock_baseline=_EMPTY,
        hash_baseline=baseline,
    )


class TestHashSinkRule:
    @pytest.mark.parametrize(
        "src_code",
        [
            # the literal DRF-1158 shape: 32-bit mask into an IntegerField
            "M.all_tenants.create(external_id=hash(str(slug)) & 0xFFFFFFFF)\n",
            # arithmetic around it changes nothing
            "M.objects.create(external_id=abs(hash(slug)) % 10_000_000)\n",
            "M.objects.create(external_id=100 + hash(k) % 1000)\n",
            # laundered through an f-string
            'M.objects.create(channel_user_id=f"imp-{hash(text) & 0xFFFF:x}")\n',
            # an idempotency key — the sink the brief calls out by name
            'consume(event_id=f"01J9{hash(payload) % 10**12:012d}ZZ")\n',
            # attribute assignment onto a model instance
            "obj.external_id = hash(slug) & 0xFFFFFFFF\n",
            "obj.external_id: int = hash(slug)\n",
            "obj.counter += hash(slug)\n",
            # a string-keyed dict entry: payloads and idempotency keys
            'payload = {"event_id": hash(x)}\n',
            # nested one level down in a fixture helper's kwargs
            "row = _legacy_row(tenant, external_id=abs(hash(slug)) % 1000)\n",
        ],
    )
    def test_hash_into_sink_is_flagged(self, tmp_path, src_code: str) -> None:
        _write(tmp_path, "apps/svc/mod.py", src_code)
        v = _scan_hash(tmp_path)
        assert len(v) == 1, f"{src_code!r} not caught"
        assert "DRF1158-no-builtin-hash-into-stored-value" in v[0].message

    def test_names_the_sink_in_the_message(self, tmp_path) -> None:
        _write(tmp_path, "apps/svc/mod.py", "M.objects.create(external_id=hash(x))\n")
        assert "`external_id=`" in _scan_hash(tmp_path)[0].message

    def test_fires_inside_test_files(self, tmp_path) -> None:
        """THE regression for DRF-1158: the defect lived in a fixture.

        Every other rule in this module stops at the production boundary.
        If this one did too, the flaky-on-Postgres, green-on-SQLite test
        that started the ticket would never have been caught."""
        _write(
            tmp_path,
            "apps/svc/tests/test_mod.py",
            "def _row():\n    return M.objects.create(external_id=hash('x') & 0xFFFFFFFF)\n",
        )
        v = _scan_hash(tmp_path)
        assert len(v) == 1
        assert v[0].key == (
            "DRF1158-no-builtin-hash-into-stored-value",
            "apps/svc/tests/test_mod.py",
            "_row",
            "<hash()-into-stored-value>",
        )

    def test_conftest_is_scanned_too(self, tmp_path) -> None:
        _write(tmp_path, "apps/svc/conftest.py", "M.objects.create(external_id=hash('x'))\n")
        assert len(_scan_hash(tmp_path)) == 1

    def test_reported_once_per_hash_call(self, tmp_path) -> None:
        # The call sits inside a dict inside a keyword argument: three
        # nested sinks, one defect.
        _write(tmp_path, "apps/svc/mod.py", 'M.objects.create(raw={"k": hash(x)})\n')
        assert len(_scan_hash(tmp_path)) == 1

    def test_two_hash_calls_in_one_sink_are_both_reported(self, tmp_path) -> None:
        _write(tmp_path, "apps/svc/mod.py", "M.objects.create(a=hash(x), b=hash(y))\n")
        assert len(_scan_hash(tmp_path)) == 2


class TestHashSinkFalsePositives:
    @pytest.mark.parametrize(
        "src_code",
        [
            # a bare local that never reaches a sink — out of scope by
            # design, not by accident (documented limitation)
            "v = hash(x)\n",
            "if hash(a) == hash(b):\n    pass\n",
            # a digest is the prescribed fix — it must not trip the rule
            "M.objects.create(external_id=int.from_bytes("
            'hashlib.sha256(s.encode()).digest()[:4], "big"))\n',
            # a method or module-level function that happens to be named
            # `hash` is not the builtin
            "M.objects.create(external_id=self.hash(x))\n",
            "M.objects.create(external_id=hashlib.md5(x).hexdigest())\n",
            # **kwargs spread carries no field name
            "M.objects.create(**extra)\n",
            # a non-string dict key is not a payload field
            "payload = {0: hash(x)}\n",
        ],
    )
    def test_safe_shapes_are_silent(self, tmp_path, src_code: str) -> None:
        _write(tmp_path, "apps/svc/mod.py", src_code)
        assert _scan_hash(tmp_path) == []

    def test_dunder_hash_body_is_allowed(self, tmp_path) -> None:
        # `def __hash__` is the one place the builtin is the right answer:
        # the value never outlives the dict that asked for it.
        _write(
            tmp_path,
            "apps/svc/mod.py",
            """
            class Key:
                def __hash__(self):
                    return hash(self._parts)
            """,
        )
        assert _scan_hash(tmp_path) == []

    def test_dunder_hash_delegating_through_a_call_is_allowed(self, tmp_path) -> None:
        _write(
            tmp_path,
            "apps/svc/mod.py",
            """
            class Key:
                def __hash__(self):
                    return combine(left=hash(self.a), right=hash(self.b))
            """,
        )
        assert _scan_hash(tmp_path) == []

    def test_lambda_sort_key_is_allowed(self, tmp_path) -> None:
        # An in-process sort key never leaves the process, so per-process
        # randomisation is harmless — and `key=` is a keyword argument,
        # so this is the false positive the lambda carve-out exists for.
        _write(tmp_path, "apps/svc/mod.py", "rows = sorted(xs, key=lambda v: hash(v))\n")
        assert _scan_hash(tmp_path) == []

    def test_migrations_are_never_scanned(self, tmp_path) -> None:
        _write(
            tmp_path,
            "apps/svc/migrations/0001_init.py",
            "M.objects.create(external_id=hash('x'))\n",
        )
        assert _scan_hash(tmp_path) == []


class TestHashSinkBaseline:
    _TWO_SITES = """
        def accepted():
            return M.objects.create(external_id=hash("a"))

        def fresh():
            return M.objects.create(external_id=hash("b"))
        """

    def _key(self, qualname: str) -> ib.BaselineKey:
        return (
            "DRF1158-no-builtin-hash-into-stored-value",
            "apps/svc/mod.py",
            qualname,
            "<hash()-into-stored-value>",
        )

    def test_baselined_site_passes(self, tmp_path) -> None:
        _write(tmp_path, "apps/svc/mod.py", self._TWO_SITES)
        both = frozenset({self._key("accepted"), self._key("fresh")})
        assert _scan_hash(tmp_path, both) == []

    def test_baselining_one_site_leaves_its_neighbour_red(self, tmp_path) -> None:
        _write(tmp_path, "apps/svc/mod.py", self._TWO_SITES)
        v = _scan_hash(tmp_path, frozenset({self._key("accepted")}))
        assert len(v) == 1
        assert v[0].key == self._key("fresh")

    def test_stale_entry_reported_when_the_hash_is_replaced(self, tmp_path) -> None:
        _write(
            tmp_path,
            "apps/svc/mod.py",
            "def accepted():\n    return M.objects.create(external_id=_stable_id('a'))\n",
        )
        v = _scan_hash(tmp_path, frozenset({self._key("accepted")}))
        assert len(v) == 1
        assert "STALE BASELINE" in v[0].message


# ── DRF-1159: the baseline has to say what it means ───────────────────
#
# `apps/miniapp_api/views.py::_collect_occupied` is baselined under G9
# and is NOT a defect — the BOOKING_VIA_AYLA_REST gate lives in its
# caller, one frame above anything an AST guard reading a single
# function can see. Both an architecture review and the main working
# window read that line as a live bug. Twice is a pattern, and the
# pattern was: the entry carried a key and no verdict, with the
# explanation in prose above a sixty-line frozenset.
#
# So the verdict is now a required, closed-vocabulary annotation next to
# the key — and these are the tests that keep it required.


class TestBaselineAnnotations:
    def test_every_verdict_rule_is_note_required(self) -> None:
        # Derived from the registries, not hand-listed: a rule that bans
        # a construct it cannot fully judge must opt IN to annotation.
        assert ib.NOTE_REQUIRED_CONTRACT_IDS == {
            "G9-booking-request-outside-owner",
            "DRF1130-no-join-under-row-lock",
            "DRF1158-no-builtin-hash-into-stored-value",
        }

    def test_every_required_entry_carries_a_note(self) -> None:
        need = {k for k in ib.ALL_BASELINES if k[0] in ib.NOTE_REQUIRED_CONTRACT_IDS}
        missing = need - set(ib.BASELINE_NOTES)
        assert not missing, (
            "baseline entries with no verdict — add a BaselineNote (see "
            "'Reading a BASELINE entry' in tools/lint/import_boundaries.py):\n"
            + "\n".join(str(k) for k in sorted(missing))
        )

    def test_no_orphan_notes(self) -> None:
        # A note whose entry is gone is the same lie as a stale baseline
        # line: it describes code that no longer exists.
        orphans = set(ib.BASELINE_NOTES) - set(ib.ALL_BASELINES)
        assert not orphans, "\n".join(str(k) for k in sorted(orphans))

    def test_statuses_come_from_the_closed_vocabulary(self) -> None:
        bad = {
            k: n.status
            for k, n in ib.BASELINE_NOTES.items()
            if n.status not in ib.BASELINE_STATUSES
        }
        assert not bad, bad

    def test_notes_actually_say_something(self) -> None:
        thin = {k for k, n in ib.BASELINE_NOTES.items() if len(n.text) < 40}
        assert not thin, f"a verdict of fewer than 40 chars explains nothing: {thin}"

    def test_the_collect_occupied_entry_is_not_readable_as_a_defect(self) -> None:
        """THE regression for DRF-1159.

        Whoever reads this entry next must be told, at the entry, that
        the gate is in the caller and where to look."""
        key = (
            "G9-booking-request-outside-owner",
            "apps/miniapp_api/views.py",
            "_collect_occupied",
            "apps.booking.models.BookingRequest",
        )
        note = ib.BASELINE_NOTES[key]
        assert note.status == "PROVEN-ELSEWHERE"
        assert "slots" in note.text, "the note must name the frame that holds the gate"
        assert "CALLER" in note.text.upper()

    def test_the_known_live_defect_is_labelled_as_one(self) -> None:
        key = (
            "G9-booking-request-outside-owner",
            "apps/bookings/tasks.py",
            "detect_completed_bookings",
            "apps.booking.models.BookingRequest",
        )
        assert ib.BASELINE_NOTES[key].status == "LIVE-DEFECT"

    def test_g9_contract_message_states_the_one_frame_limit(self) -> None:
        # The limitation belongs in the contract too, not only in the
        # entries: a NEW crossing gets the contract message, never the
        # notes of somebody else's entry.
        g9 = next(c for c in ib.CONTRACTS if c.id == "G9-booking-request-outside-owner")
        assert g9.triage_note_required is True
        assert "ONE FRAME" in g9.message.upper()
        assert "CALLING" in g9.message.upper()

    def test_stale_message_carries_the_verdict(self, tmp_path) -> None:
        # When the ratchet demands a line be deleted, the person deleting
        # it is told what the line claimed.
        _write(tmp_path, "apps/svc/mod.py", "x = 1\n")
        key = ("DRF1130-no-join-under-row-lock", "apps/svc/mod.py", "f", "<x>")
        notes_backup = dict(ib.BASELINE_NOTES)
        ib.BASELINE_NOTES[key] = ib.BaselineNote("PROVEN-ELSEWHERE", "the FK is NOT NULL")
        try:
            v = _scan_row_lock(tmp_path, frozenset({key}))
        finally:
            ib.BASELINE_NOTES.clear()
            ib.BASELINE_NOTES.update(notes_backup)
        assert len(v) == 1
        assert "STALE BASELINE" in v[0].message
        assert "PROVEN-ELSEWHERE" in v[0].message
        assert "the FK is NOT NULL" in v[0].message

    def test_baseline_report_leads_with_the_live_defects(self) -> None:
        report = ib.baseline_report()
        assert report[0].startswith("LIVE-DEFECT")
        statuses = [line.split(" (")[0] for line in report if line and not line.startswith(" ")]
        assert statuses[:2] == ["LIVE-DEFECT", "UNTRIAGED"]
        joined = "\n".join(report)
        assert "apps/bookings/tasks.py" in joined


# ── Integration: the two new rules against the real repo ──────────────


class TestNewRulesAgainstRealRepo:
    def test_row_lock_baseline_matches_reality(self) -> None:
        found = ib.scan_paths(
            [_PROJECT_ROOT / "apps"], _PROJECT_ROOT, row_lock_baseline=frozenset()
        )
        observed = {
            v.key for v in found if v.key is not None and v.key[0] == ib.ROW_LOCK_JOIN_RULE.id
        }
        assert observed == set(ib.ROW_LOCK_JOIN_BASELINE)

    def test_hash_baseline_matches_reality(self) -> None:
        found = ib.scan_paths([_PROJECT_ROOT / "apps"], _PROJECT_ROOT, hash_baseline=frozenset())
        observed = {v.key for v in found if v.key is not None and v.key[0] == ib.HASH_SINK_RULE.id}
        assert observed == set(ib.HASH_SINK_BASELINE)

    def test_the_two_fixed_drf1130_sites_left_no_baseline_line(self) -> None:
        """586317b fixed reschedule.py and tools.py by deleting the join.

        A fixed site leaves nothing behind — that is the difference
        between this rule and the comment it replaces."""
        files = {k[1] for k in ib.ROW_LOCK_JOIN_BASELINE}
        assert "apps/booking/services/reschedule.py" not in files
        assert "apps/skills/booking/tools.py" not in files

    def test_hash_rule_reaches_test_files_in_the_real_tree(self) -> None:
        # Not a tautology over the baseline: it pins the SCOPE decision.
        # Every accepted hash site is a fixture, which is the whole reason
        # this rule crosses the production boundary.
        assert ib.HASH_SINK_BASELINE
        assert all(ib._is_test_file(k[1]) for k in ib.HASH_SINK_BASELINE)
