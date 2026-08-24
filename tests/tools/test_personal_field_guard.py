"""Tests for tools/lint/personal_field_guard.py — the DRF-1365 guard.

Three kinds of test here, and the difference matters.

**Mechanics** build a throwaway ``apps/`` tree and prove the guard bites
where it must: a new field on a person-keyed model with no declaration
fails *by name*; a slot that crosses salons carrying something the salon
observed fails against the ruling of 2026-08-24; a declaration that lies
about crossing fails against the store's own tenancy.

**Silence** is tested just as hard. A lint that cries wolf gets switched
off, and this one scans models by the thousand: a field on an event table
(``BookingRequest``-shaped: its own primary key, the person hanging off
it as a foreign key), a nullable link to a person, a manager attribute
and a declared plumbing column must all pass without a word.

**The real tree** is pinned last. The defect this guard exists for is not
"the guard has a bug" — it is "somebody added a personal field and
nothing said so". A guard green on its own fixtures while ``apps/``
drifts would be the failure it was built to prevent.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# `tools/` is not a package (no __init__.py) — import via path injection,
# same pattern as test_import_boundaries.py.
sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "lint"))
import personal_field_guard as guard  # type: ignore[import-not-found]  # noqa: E402


# --------------------------------------------------------------------------
# Fixture tree
# --------------------------------------------------------------------------

#: The person model plus one event table and one nullable link — the two
#: shapes the guard must stay silent about.
_BASE_MODELS = '''
from django.db import models


class BotUser(models.Model):
    id = models.UUIDField(primary_key=True)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    chat_id = models.CharField(max_length=64)
    objects = models.Manager()


class BookingRequest(models.Model):
    """An EVENT row: its own pk, the person as an attribute."""

    id = models.UUIDField(primary_key=True)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    bot_user = models.ForeignKey(BotUser, on_delete=models.CASCADE)
    starts_at = models.DateTimeField()


class CatalogMaster(models.Model):
    """A staff mirror that MAY link to a bot user. Not person-keyed."""

    id = models.UUIDField(primary_key=True)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    linked_bot_user = models.OneToOneField(
        BotUser, on_delete=models.SET_NULL, null=True, blank=True
    )
    bio = models.TextField()
'''

_CARDINALITY = """
_KEY_CARDINALITY: dict[str, str] = {
    "diet": "single",
}
"""


@dataclass(frozen=True)
class _Field:
    site: str
    origin: str
    owner: str
    crosses_salons: bool
    why: str


def _registry(
    *,
    personal: tuple[_Field, ...] = (),
    not_personal: dict[str, str] | None = None,
    debt: dict[str, str] | None = None,
    never_crosses: frozenset[str] = frozenset(),
) -> SimpleNamespace:
    return SimpleNamespace(
        PERSONAL_FIELDS=personal,
        NOT_PERSONAL=not_personal or {},
        POLICY_DEBT=debt or {},
        NEVER_CROSSES=never_crosses,
        ORIGINS=guard_origins(),
        OWNERS=frozenset({"BACKEND", "BOT"}),
    )


def guard_origins() -> frozenset[str]:
    return frozenset(
        {
            "USER_STATED",
            "OBSERVED",
            "TRANSACTIONAL",
            "DERIVED",
            "INFERRED",
            "SYSTEM",
            "UNCLASSIFIED",
        }
    )


_WHY = "A sentence long enough to clear the reviewability floor the guard enforces."


@pytest.fixture
def apps_root(tmp_path: Path) -> Path:
    """A minimal `apps/` tree: identity models + the cardinality registry."""
    root = tmp_path / "apps"
    (root / "identity" / "services").mkdir(parents=True)
    (root / "identity" / "models.py").write_text(_BASE_MODELS, encoding="utf-8")
    (root / "identity" / "services" / "memory_key_policy.py").write_text(
        _CARDINALITY, encoding="utf-8"
    )
    return root


def _append(apps_root: Path, snippet: str) -> None:
    path = apps_root / "identity" / "models.py"
    path.write_text(path.read_text(encoding="utf-8") + snippet, encoding="utf-8")


def _keys(apps_root: Path) -> list[str]:
    return sorted(site.key for site in guard.scan(apps_root))


# --------------------------------------------------------------------------
# Which slots the guard looks at — clause (b) of the criterion
# --------------------------------------------------------------------------


class TestWhatCountsAsPersonKeyed:
    """One row per person, ever — not «has a person on it somewhere»."""

    def test_the_person_model_itself_is_scanned(self, apps_root: Path) -> None:
        assert "identity.BotUser.chat_id" in _keys(apps_root)

    def test_an_event_table_is_not_scanned(self, apps_root: Path) -> None:
        """``BookingRequest`` is keyed to a booking; the person hangs off it.

        This is the whole reason the criterion is «the person is the row's
        identity» and not «the row mentions a person». Without it the guard
        would demand a declaration for every column of the booking domain,
        and would be deleted within a week.
        """
        assert not any(k.startswith("identity.BookingRequest.") for k in _keys(apps_root))

    def test_a_nullable_link_to_a_person_is_not_scanned(self, apps_root: Path) -> None:
        """A staff mirror that MAY sign in as a bot user is still staff."""
        assert not any(k.startswith("identity.CatalogMaster.") for k in _keys(apps_root))

    def test_a_mandatory_one_to_one_is_scanned(self, apps_root: Path) -> None:
        _append(
            apps_root,
            """

class Profile(models.Model):
    bot_user = models.OneToOneField(BotUser, on_delete=models.CASCADE, primary_key=True)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    tone = models.CharField(max_length=16)
""",
        )
        assert "identity.Profile.tone" in _keys(apps_root)

    def test_a_user_id_primary_key_is_scanned(self, apps_root: Path) -> None:
        _append(
            apps_root,
            """

class Cross(models.Model):
    user_id = models.UUIDField(primary_key=True)
    summary = models.TextField()
""",
        )
        assert "identity.Cross.summary" in _keys(apps_root)

    def test_a_manager_is_not_a_field(self, apps_root: Path) -> None:
        assert "identity.BotUser.objects" not in _keys(apps_root)

    def test_a_missing_anchor_fails_loudly(self, tmp_path: Path) -> None:
        """A renamed person model must break the guard, not silence it."""
        root = tmp_path / "apps"
        (root / "identity" / "services").mkdir(parents=True)
        (root / "identity" / "models.py").write_text(
            "from django.db import models\n\n\n"
            "class Whatever(models.Model):\n    x = models.TextField()\n",
            encoding="utf-8",
        )
        (root / "identity" / "services" / "memory_key_policy.py").write_text(
            _CARDINALITY, encoding="utf-8"
        )
        with pytest.raises(LookupError, match="no model named"):
            guard.scan(root)


class TestMemoryKeys:
    """Surface B: the personal fields inside one opaque column."""

    def test_a_written_key_is_discovered(self, apps_root: Path) -> None:
        (apps_root / "persona").mkdir()
        (apps_root / "persona" / "extract.py").write_text(
            'record(content={"key": "diet", "value": v})\n', encoding="utf-8"
        )
        assert "memory_key:diet" in _keys(apps_root)

    def test_a_content_variable_is_discovered(self, apps_root: Path) -> None:
        (apps_root / "persona").mkdir()
        (apps_root / "persona" / "extract.py").write_text(
            'content = {"key": "diet", "currency": "RUB"}\n', encoding="utf-8"
        )
        assert "memory_key:diet" in _keys(apps_root)

    def test_test_files_are_not_a_source_of_keys(self, apps_root: Path) -> None:
        (apps_root / "persona" / "tests").mkdir(parents=True)
        (apps_root / "persona" / "tests" / "test_x.py").write_text(
            'e = make(content={"key": "made_up_by_a_fixture"})\n', encoding="utf-8"
        )
        assert "memory_key:made_up_by_a_fixture" not in _keys(apps_root)

    def test_the_cardinality_registry_is_read_from_source(self, apps_root: Path) -> None:
        assert guard.read_cardinality_keys(apps_root) == {"diet"}

    def test_a_vanished_cardinality_registry_fails_loudly(self, apps_root: Path) -> None:
        (apps_root / "identity" / "services" / "memory_key_policy.py").unlink()
        with pytest.raises(LookupError, match="is gone"):
            guard.read_cardinality_keys(apps_root)


# --------------------------------------------------------------------------
# The guard bites
# --------------------------------------------------------------------------


class TestItBites:
    def test_an_undeclared_slot_is_named(self, apps_root: Path) -> None:
        sites = guard.scan(apps_root)
        problems = guard.check(sites, _registry(), {"diet"})

        assert any("identity.BotUser.chat_id" in p for p in problems), problems

    def test_a_crossing_slot_the_salon_observed_is_refused(self, apps_root: Path) -> None:
        _append(
            apps_root,
            """

class Cross(models.Model):
    user_id = models.UUIDField(primary_key=True)
    skin_type = models.CharField(max_length=32)
""",
        )
        registry = _registry(
            personal=(_Field("identity.Cross.skin_type", "OBSERVED", "BOT", True, _WHY),),
            not_personal={
                "identity.BotUser.id": "pk",
                "identity.BotUser.tenant": "scoping",
                "identity.BotUser.chat_id": "routing",
                "identity.Cross.user_id": "pk",
            },
        )
        problems = guard.check(guard.scan(apps_root), registry, {"diet"})

        assert any("skin_type" in p and "crosses salons" in p for p in problems), problems

    def test_a_never_crossing_slot_is_refused_even_when_user_stated(self, apps_root: Path) -> None:
        """«сказал сам» does not override the ruling's own exception list."""
        _append(
            apps_root,
            """

class Cross(models.Model):
    user_id = models.UUIDField(primary_key=True)
    fave = models.CharField(max_length=32)
""",
        )
        registry = _registry(
            personal=(_Field("identity.Cross.fave", "USER_STATED", "BOT", True, _WHY),),
            not_personal={
                "identity.BotUser.id": "pk",
                "identity.BotUser.tenant": "scoping",
                "identity.BotUser.chat_id": "routing",
                "identity.Cross.user_id": "pk",
            },
            never_crosses=frozenset({"identity.Cross.fave"}),
        )
        problems = guard.check(guard.scan(apps_root), registry, {"diet"})

        assert any("fave" in p and "never follow" in p for p in problems), problems

    def test_a_crossing_claim_that_contradicts_the_store_is_refused(self, apps_root: Path) -> None:
        """Declaring «this does not travel» does not stop it travelling."""
        _append(
            apps_root,
            """

class Cross(models.Model):
    user_id = models.UUIDField(primary_key=True)
    tone = models.CharField(max_length=16)
""",
        )
        registry = _registry(
            personal=(_Field("identity.Cross.tone", "USER_STATED", "BOT", False, _WHY),),
            not_personal={
                "identity.BotUser.id": "pk",
                "identity.BotUser.tenant": "scoping",
                "identity.BotUser.chat_id": "routing",
                "identity.Cross.user_id": "pk",
            },
        )
        problems = guard.check(guard.scan(apps_root), registry, {"diet"})

        assert any("its store says True" in p for p in problems), problems

    def test_an_unclassified_slot_must_be_named_as_debt(self, apps_root: Path) -> None:
        registry = _registry(
            personal=(_Field("identity.BotUser.chat_id", "UNCLASSIFIED", "BOT", False, _WHY),),
            not_personal={"identity.BotUser.id": "pk", "identity.BotUser.tenant": "scoping"},
        )
        problems = guard.check(guard.scan(apps_root), registry, {"diet"})

        assert any("UNCLASSIFIED" in p for p in problems), problems

    def test_a_declaration_that_outlives_its_field_is_refused(self, apps_root: Path) -> None:
        registry = _registry(
            not_personal={
                "identity.BotUser.id": "pk",
                "identity.BotUser.tenant": "scoping",
                "identity.BotUser.chat_id": "routing",
                "identity.BotUser.long_gone": "was plumbing once",
            },
        )
        problems = guard.check(guard.scan(apps_root), registry, {"diet"})

        assert any("long_gone" in p and "no longer exists" in p for p in problems), problems

    def test_debt_that_no_longer_violates_must_be_deleted(self, apps_root: Path) -> None:
        registry = _registry(
            personal=(_Field("identity.BotUser.chat_id", "USER_STATED", "BOT", False, _WHY),),
            not_personal={"identity.BotUser.id": "pk", "identity.BotUser.tenant": "scoping"},
            debt={"identity.BotUser.chat_id": "settled long ago"},
        )
        problems = guard.check(guard.scan(apps_root), registry, {"diet"})

        assert any("breaks no rule any more" in p for p in problems), problems

    def test_a_reason_too_short_to_review_is_refused(self, apps_root: Path) -> None:
        registry = _registry(
            personal=(_Field("identity.BotUser.chat_id", "USER_STATED", "BOT", False, "meh"),),
            not_personal={"identity.BotUser.id": "pk", "identity.BotUser.tenant": "scoping"},
        )
        problems = guard.check(guard.scan(apps_root), registry, {"diet"})

        assert any("`why` is 3 chars" in p for p in problems), problems

    def test_a_written_key_with_no_cardinality_is_refused(self, apps_root: Path) -> None:
        (apps_root / "persona").mkdir()
        (apps_root / "persona" / "extract.py").write_text(
            'record(content={"key": "goal", "value": v})\n', encoding="utf-8"
        )
        registry = _registry(
            personal=(_Field("memory_key:goal", "USER_STATED", "BACKEND", True, _WHY),),
            not_personal={
                "identity.BotUser.id": "pk",
                "identity.BotUser.tenant": "scoping",
                "identity.BotUser.chat_id": "routing",
            },
        )
        problems = guard.check(guard.scan(apps_root), registry, {"diet"})

        assert any("`goal` is written but has no entry" in p for p in problems), problems


# --------------------------------------------------------------------------
# The guard stays silent
# --------------------------------------------------------------------------


class TestItStaysSilent:
    """A false positive is worse than no lint — it gets the lint removed."""

    def _clean_registry(self) -> SimpleNamespace:
        return _registry(
            not_personal={
                "identity.BotUser.id": "Row identity.",
                "identity.BotUser.tenant": "Scoping.",
                "identity.BotUser.chat_id": "Routing — decides where, never what.",
            },
        )

    def test_declared_plumbing_passes(self, apps_root: Path) -> None:
        assert guard.check(guard.scan(apps_root), self._clean_registry(), set()) == []

    def test_a_new_field_on_an_event_table_says_nothing(self, apps_root: Path) -> None:
        _append(
            apps_root,
            """

class Payment(models.Model):
    id = models.UUIDField(primary_key=True)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    bot_user = models.ForeignKey(BotUser, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
""",
        )
        assert guard.check(guard.scan(apps_root), self._clean_registry(), set()) == []

    def test_a_renamed_service_field_on_a_person_model_costs_one_line(
        self, apps_root: Path
    ) -> None:
        """Plumbing that changes shape is a one-line registry edit, not a fight."""
        _append(
            apps_root,
            """

class Prefs(models.Model):
    bot_user = models.OneToOneField(BotUser, on_delete=models.CASCADE, primary_key=True)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    updated_at = models.DateTimeField(auto_now=True)
""",
        )
        registry = self._clean_registry()
        registry.NOT_PERSONAL = dict(registry.NOT_PERSONAL) | {
            "identity.Prefs.bot_user": "Row identity.",
            "identity.Prefs.tenant": "Scoping.",
            "identity.Prefs.updated_at": "Row bookkeeping.",
        }
        assert guard.check(guard.scan(apps_root), registry, set()) == []


# --------------------------------------------------------------------------
# The real tree
# --------------------------------------------------------------------------


class TestTheRealTree:
    """What the guard is actually for: `apps/` as it stands right now."""

    def test_the_repository_is_clean(self) -> None:
        assert guard.main(["personal_field_guard.py", str(_PROJECT_ROOT / "apps")]) == 0

    def test_the_scan_is_not_vacuous(self) -> None:
        """Guard the guard: an empty scan would make everything above pass."""
        sites = guard.scan(_PROJECT_ROOT / "apps")
        models = {s.key.rsplit(".", 1)[0] for s in sites if not s.key.startswith("memory_key:")}
        memory_keys = {s.key for s in sites if s.key.startswith("memory_key:")}

        assert len(models) >= 5, sorted(models)
        assert len(memory_keys) >= 5, sorted(memory_keys)

    def test_deleting_the_registry_fails_loudly(self, tmp_path: Path) -> None:
        """Removing the declarations must break the guard, not satisfy it."""
        (tmp_path / "identity").mkdir(parents=True)
        with pytest.raises(LookupError, match="the registry is gone"):
            guard._load_registry(tmp_path)

    def test_every_debt_entry_says_which_ruling_it_breaks(self) -> None:
        """Named debt, not a one-line baseline — that is the whole point."""
        registry = guard._load_registry(_PROJECT_ROOT / "apps")
        assert registry.POLICY_DEBT, "debt list emptied without a commit that fixed anything?"
        for site, reason in registry.POLICY_DEBT.items():
            assert len(reason.strip()) >= 120, site
