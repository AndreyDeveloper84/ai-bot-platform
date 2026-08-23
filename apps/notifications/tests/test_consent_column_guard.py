"""AST lint tests for tools/lint/consent_column_guard.py (DRF-1314).

# What this file tests

| Class | What |
|---|---|
| TestBlocksReads | Every denied pattern from the script docstring fires |
| TestAllowsWrites | Writes DON'T fire — recording consent is not the defect |
| TestAllowsNeighbouringFields | `food_scanner_consent_at` and friends DON'T fire |
| TestAllowlistPaths | The five sanctioned modules + tests + migrations DON'T fire |
| TestRealAppsAreClean | Integration: scanning the real `apps/` yields zero violations |
| TestCatchesTheHistoricalDefects | The pre-fix DRF-1314 and as-shipped DRF-1301 gates DO fire |
| TestKnownLimitations | Honest documentation of what this guard cannot see |

# Why these tests exist

The guard is a path allowlist over a name match, which is a blunt tool
chosen deliberately (the legitimate and the illegitimate read are the
same expression — see the script docstring). A blunt tool earns its
place only if its edges are pinned down, so both halves are asserted:
what it catches, and what it lets through.

Placed under ``apps/notifications/`` rather than ``tools/`` because the
gate this guard redirects people to lives here, and because ``testpaths``
collects ``apps/``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

# Project root — `apps/` and `pyproject.toml` both live here, which is
# what `_detect_repo_root` looks for.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Import the lint module via path injection because `tools/` is not a
# package (no `__init__.py`). Same convention as
# apps/identity/tests/test_red_zone_guard.py.
sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "lint"))
import consent_column_guard  # type: ignore[import-not-found]  # noqa: E402


def _write_py(tmp_path: Path, name: str, source: str) -> Path:
    """Write ``source`` to ``tmp_path/name`` and return the path."""
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dedent(source), encoding="utf-8")
    return p


def _scan(tmp_path: Path, source: str, name: str = "surface.py"):
    return consent_column_guard.scan_file(_write_py(tmp_path, name, source), repo_root=tmp_path)


# ───────────────────────────────────────────────────────────────────────
# DENIED
# ───────────────────────────────────────────────────────────────────────


class TestBlocksReads:
    def test_attribute_read(self, tmp_path) -> None:
        violations = _scan(tmp_path, "if bot_user.consent_at is None:\n    pass\n")
        assert len(violations) == 1
        assert "consent_blocker" in violations[0].message

    def test_getattr_read(self, tmp_path) -> None:
        violations = _scan(tmp_path, 'x = getattr(bot_user, "consent_at", None)\n')
        assert len(violations) == 1

    def test_the_exact_shape_three_surfaces_shipped(self, tmp_path) -> None:
        """The line this guard exists for, verbatim from DRF-1285."""
        violations = _scan(
            tmp_path,
            """
            def check_common(bot_user):
                if getattr(bot_user, "consent_at", None) is None:
                    return "no_consent"
                return None
            """,
        )
        assert len(violations) == 1
        assert violations[0].lineno == 3

    def test_orm_filter_lookup(self, tmp_path) -> None:
        violations = _scan(tmp_path, "BotUser.all_tenants.filter(consent_at__isnull=False)\n")
        assert len(violations) == 1

    def test_orm_exclude_lookup(self, tmp_path) -> None:
        violations = _scan(tmp_path, "BotUser.all_tenants.exclude(consent_at=None)\n")
        assert len(violations) == 1

    def test_related_lookup(self, tmp_path) -> None:
        violations = _scan(
            tmp_path, "Reminder.objects.filter(bot_user__consent_at__isnull=False)\n"
        )
        assert len(violations) == 1

    def test_q_object(self, tmp_path) -> None:
        violations = _scan(tmp_path, "q = Q(consent_at__isnull=True)\n")
        assert len(violations) == 1

    def test_projection(self, tmp_path) -> None:
        violations = _scan(tmp_path, 'BotUser.objects.values_list("consent_at", flat=True)\n')
        assert len(violations) == 1

    def test_only(self, tmp_path) -> None:
        violations = _scan(tmp_path, 'BotUser.objects.only("consent_at")\n')
        assert len(violations) == 1

    def test_every_read_in_a_file_is_reported_not_just_the_first(self, tmp_path) -> None:
        """A second copy in the same module must not hide behind the first."""
        violations = _scan(
            tmp_path,
            """
            a = bot_user.consent_at
            b = getattr(other, "consent_at", None)
            """,
        )
        assert len(violations) == 2


# ───────────────────────────────────────────────────────────────────────
# ALLOWED
# ───────────────────────────────────────────────────────────────────────


class TestAllowsWrites:
    """Recording consent is not the defect, and DRF-1314 does not touch it.

    ``withdraw()`` leaving the column set is a deliberate form (soft
    delete on a live row, spec §4). A guard that fought the write would
    be fighting the specification instead of the readers.
    """

    def test_attribute_assignment(self, tmp_path) -> None:
        assert _scan(tmp_path, "bot_user.consent_at = timezone.now()\n") == []

    def test_save_update_fields(self, tmp_path) -> None:
        assert _scan(tmp_path, 'bot_user.save(update_fields=["consent_at"])\n') == []

    def test_queryset_update(self, tmp_path) -> None:
        assert _scan(tmp_path, "BotUser.all_tenants.filter(pk=1).update(consent_at=None)\n") == []

    def test_model_construction(self, tmp_path) -> None:
        """How `MemoryEntry` rows are built — a different model's column."""
        assert _scan(tmp_path, "MemoryEntry.objects.create(consent_at=now)\n") == []

    def test_write_entry_kwarg(self, tmp_path) -> None:
        assert _scan(tmp_path, "write_entry(consent_at=now, zone='yellow')\n") == []


class TestAllowsNeighbouringFields:
    def test_food_scanner_consent_at_attribute(self, tmp_path) -> None:
        """A different column, with no ConsentRecord behind it.

        ``ConsentRecord.ConsentType`` has no food-scanner member, so
        there is no second source to reconcile the column against and no
        withdrawal that could leave it stale. Reading it directly is
        correct, which is exactly what makes the two look alike and be
        different.
        """
        assert _scan(tmp_path, "x = bot_user.food_scanner_consent_at\n") == []

    def test_food_scanner_consent_at_lookup(self, tmp_path) -> None:
        assert _scan(tmp_path, "BotUser.objects.filter(food_scanner_consent_at=None)\n") == []

    def test_unrelated_attribute(self, tmp_path) -> None:
        assert _scan(tmp_path, "x = bot_user.welcomed_at\n") == []


class TestAllowlistPaths:
    """The sanctioned readers, named one by one so removing one is loud."""

    def test_the_shared_gate_may_read(self, tmp_path) -> None:
        assert (
            _scan(
                tmp_path,
                'if getattr(bot_user, "consent_at", None) is None:\n    return "no_consent"\n',
                name="apps/notifications/proactive.py",
            )
            == []
        )

    def test_consent_services_may_read(self, tmp_path) -> None:
        assert (
            _scan(
                tmp_path,
                'if getattr(bot_user, "consent_at", None) is None:\n    pass\n',
                name="apps/consent/services.py",
            )
            == []
        )

    def test_welcome_skill_may_read(self, tmp_path) -> None:
        assert (
            _scan(
                tmp_path,
                'if getattr(bot_user, "consent_at", None) is None:\n    pass\n',
                name="apps/skills/welcome/skill.py",
            )
            == []
        )

    def test_the_operator_census_may_read(self, tmp_path) -> None:
        assert (
            _scan(
                tmp_path,
                "BotUser.all_tenants.filter(consent_at__isnull=False).count()\n",
                name="apps/bookings/management/commands/post_visit_followup_dryrun.py",
            )
            == []
        )

    def test_tests_may_read(self, tmp_path) -> None:
        assert (
            _scan(
                tmp_path,
                "assert bot_user.consent_at is not None\n",
                name="apps/whatever/tests/test_thing.py",
            )
            == []
        )

    def test_migrations_may_read(self, tmp_path) -> None:
        assert (
            _scan(
                tmp_path,
                "BotUser.objects.filter(consent_at__isnull=True)\n",
                name="apps/identity/migrations/0099_backfill.py",
            )
            == []
        )

    def test_a_neighbour_of_an_allowlisted_file_may_not(self, tmp_path) -> None:
        """The allowlist is per file, not per directory."""
        violations = _scan(
            tmp_path,
            'x = getattr(bot_user, "consent_at", None)\n',
            name="apps/notifications/other.py",
        )
        assert len(violations) == 1


# ───────────────────────────────────────────────────────────────────────
# INTEGRATION
# ───────────────────────────────────────────────────────────────────────


class TestRealAppsAreClean:
    def test_apps_tree_has_no_violations(self) -> None:
        """The guard is green on `dev` + DRF-1314, so CI starts from zero."""
        violations = consent_column_guard.scan_directory(
            _PROJECT_ROOT / "apps", repo_root=_PROJECT_ROOT
        )
        assert violations == [], "\n".join(v.format() for v in violations)

    def test_the_fixed_nutrition_gate_is_clean(self) -> None:
        """The module DRF-1314 fixed no longer reads the column at all."""
        violations = consent_column_guard.scan_file(
            _PROJECT_ROOT / "apps" / "nutrition_proactive" / "selection.py",
            repo_root=_PROJECT_ROOT,
        )
        assert violations == []


class TestCatchesTheHistoricalDefects:
    """Reconstructions of the code that actually shipped.

    Not the real files — the real ones are fixed. These are the exact
    gate bodies as they stood, so the guard's value is asserted against
    history rather than asserted by hand-wave.
    """

    def test_the_nutrition_gate_as_it_shipped(self, tmp_path) -> None:
        """DRF-1285/1314 — the live one, behind a feature flag."""
        violations = _scan(
            tmp_path,
            """
            def check_common(bot_user):
                if getattr(bot_user, "proactive_messages_opt_out", False):
                    return "proactive_opt_out"
                if getattr(bot_user, "deleted_at", None) is not None:
                    return "deleted"
                if not (getattr(bot_user, "chat_id", "") or "").strip():
                    return "no_chat_id"
                if getattr(bot_user, "consent_at", None) is None:
                    return "no_consent"
                if getattr(bot_user, "food_scanner_consent_at", None) is None:
                    return "no_food_consent"
                return None
            """,
            name="apps/nutrition_proactive/selection.py",
        )
        assert len(violations) == 1

    def test_the_followup_gate_as_drf_1301_shipped_it(self, tmp_path) -> None:
        """A correct gate — and still a second copy of the column read.

        DRF-1301's fix read both the column and the record, so it was
        never wrong. It was a *duplicate*, and the duplicate is what
        DRF-1307 had to extract and DRF-1314 had to chase. This guard
        would have made that duplicate loud on the day it landed.
        """
        violations = _scan(
            tmp_path,
            """
            def _consent_blocker(bot_user):
                if getattr(bot_user, "proactive_messages_opt_out", False):
                    return "opt_out"
                if getattr(bot_user, "deleted_at", None) is not None:
                    return "deleted"
                if getattr(bot_user, "consent_at", None) is None:
                    return "no_consent"
                return None
            """,
            name="apps/bookings/followups.py",
        )
        assert len(violations) == 1


# ───────────────────────────────────────────────────────────────────────
# HONEST GAPS
# ───────────────────────────────────────────────────────────────────────


class TestKnownLimitations:
    """What this guard does NOT see. Asserted so nobody over-trusts it."""

    def test_it_cannot_see_a_surface_with_no_gate_at_all(self, tmp_path) -> None:
        """The biggest gap, and the one that matters most.

        DRF-1301 and DRF-1307 were not column-only gates — they had **no
        consent check whatsoever**, so there is no read to ban and this
        guard is silent. It defends against the third disease shape (a
        gate that trusts the column), not against the first two (no
        gate). Catching those needs a different check: every surface
        that writes first must import the shared gate. Raised for the
        owner in docs/REPORT_DRF1314.md, not built here.
        """
        assert (
            _scan(
                tmp_path,
                """
                def plan():
                    for bot_user in BotUser.all_tenants.all():
                        send_message(chat_id=bot_user.chat_id, text="hi")
                """,
            )
            == []
        )

    def test_it_cannot_see_an_indirected_field_name(self, tmp_path) -> None:
        assert _scan(tmp_path, 'field = "consent_at"\nx = getattr(bot_user, field)\n') == []

    def test_it_cannot_see_a_splatted_lookup(self, tmp_path) -> None:
        assert _scan(tmp_path, 'BotUser.objects.filter(**{"consent_at__isnull": False})\n') == []

    def test_it_cannot_see_raw_sql(self, tmp_path) -> None:
        assert (
            _scan(
                tmp_path,
                'BotUser.objects.raw("SELECT * FROM identity_botuser '
                'WHERE consent_at IS NOT NULL")\n',
            )
            == []
        )

    def test_it_cannot_tell_memory_entry_from_bot_user(self, tmp_path) -> None:
        """A *read* of `MemoryEntry.consent_at` would be a false positive.

        There is no such read in `apps/` today — every site is a write —
        so the guard costs nothing now. When one appears the fix is one
        more allowlist entry, not a cleverer matcher: an AST cannot
        resolve which model an attribute belongs to.
        """
        violations = _scan(tmp_path, "zone_ok = memory_entry.consent_at is not None\n")
        assert len(violations) == 1, "documented false positive, not a bug"
