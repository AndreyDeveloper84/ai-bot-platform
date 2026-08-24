#!/usr/bin/env python3
"""Fail when a personal field appears with nobody declared to own it.

The memory audit of 2026-08-24 (`REPORT_AYLA_MEMORY_VS_KB.md`) returned
**C — MULTIPLE COMPETING MEMORY SYSTEMS**: two semantic stores, plus the
transcript, plus a computed profile, plus bookings, plus nutrition. Seven
P0s. Not one of those stores was ever designed as «another memory». They
grew **one field at a time**, and every single field was reasonable where
it was added.

Section 5 of that audit is the sentence this guard exists for:

    There is no explicit conflict policy for city, price, favorite master
    or goal across bot and backend stores.

Nobody decided that. It is what a pile of individually-sensible additions
adds up to when no one is required to say, at the moment of adding, *who
owns this*. Without a guard the seventh store grows exactly as quietly as
the first six did.

# What counts as a personal field

    A personal field is a named slot whose value (a) outlives the turn
    that produced it, (b) is keyed to one person rather than to an event,
    and (c) is read back to shape what Ayla says or offers to that person
    later.

Each clause does work, and the criterion is deliberately NOT «a column on
a user model»:

* **(a) outlives the turn** — a Redis journey key or a 900-second booking
  continuation is not memory; it is the current turn holding its place.
* **(b) keyed to a person, not an event** — this is the discriminator
  that keeps the guard from swallowing the whole domain.
  ``BookingRequest.starts_at`` is keyed to a *booking*: the row IS the
  event, and the person is a foreign key hanging off it. A profile row is
  the other shape — **one row per person, ever** — and that shape is what
  makes a store accumulate into a memory. Mechanically: the person is the
  row's identity (a ``OneToOneField`` to the person model, or a
  ``user_id`` primary key), not one of its attributes.
* **(c) read back to shape the answer** — ``BotUser.chat_id`` is keyed to
  the person and permanent, but it only decides *where* a message is
  delivered, never *what* it says. That is plumbing, and plumbing that
  trips a lint gets the lint switched off.

Clause (b) also has a second shape in this repo, which is why the guard
has two surfaces. Inside ``MemoryEntry`` the *column* is one opaque
``content`` blob; the personal fields are the **keys inside it**
(``diet``, ``price_range``, …). A column-only scan would see one field
where there are five, and would stay silent on the sixth.

# The two surfaces

**A. Person-keyed profile models.** Every field of every model whose
primary identity is a person: the person model itself, anything with a
``OneToOneField`` to it, anything with a ``user_id`` UUID primary key.
Today that is six models across three apps — deliberately not just
``apps/identity``, because «personal» is a property of the slot, not of
the directory it lives in.

**B. Memory keys.** Every string literal written as ``content["key"]`` on
a ``MemoryEntry``, plus every key named in
``memory_key_policy._KEY_CARDINALITY`` — the read-side registry that
already exists. The two are cross-checked against each other: a key that
is written but has no cardinality can contradict itself inside one
prompt (that is what DRF-1260 fixed), and a cardinality for a key nobody
writes is a rule nobody can trip.

# What each field must declare

The registry is :mod:`apps.identity.personal_fields`. Every discovered
site must appear in it exactly once, either as a
:class:`~apps.identity.personal_fields.PersonalField` — origin, owner,
whether it crosses salons, and why — or in ``NOT_PERSONAL`` with the
reason it is plumbing. A field with no entry fails the build **by name**.

Two of those columns are machine-checked, not merely stated:

* ``crosses_salons`` is checked against what the code actually does. A
  store with no ``tenant`` foreign key is cross-tenant, and green
  ``MemoryEntry`` rows are read by ``user_id`` alone
  (``apps/identity/services/memory_reader.py:100``) — so declaring
  ``crosses_salons=False`` on such a slot is a claim the guard refuses.
* the owner's ruling of 2026-08-24 (`docs/DECISIONS_MEMORY_PACKAGE.md`,
  decision 3) — **сказал сам — переходит; узнали о нём — нет** — is
  enforced as: a crossing slot must be ``USER_STATED`` or ``SYSTEM``, and
  must not be one of the slots the ruling names as never-crossing.

# The debt is named, not hidden

Slots that break those rules today are listed **individually** in
``POLICY_DEBT``, with the ruling each one contradicts. That list can only
shrink: an entry that stops violating fails this guard until the line is
deleted. Same ratchet as ``tools/lint/miniapp_style_contract.py``, and
the same reason — a baseline that says «58 known issues» in one line is a
way of not looking at them.

# KNOWN LIMITATIONS (explicitly NOT detected)

* **Keys inside ``BotUser.context``.** It is a JSON scratch bag; a new
  personalisation flag can be added there with no schema change and no
  literal this scanner can see. The field itself is therefore declared,
  and sits in ``POLICY_DEBT`` — the hole is the finding.
* **Memory keys built at runtime.** ``content={"key": key_var}`` is
  invisible here. The cross-check against ``_KEY_CARDINALITY`` catches
  the case where such a key is also given a read policy; a key that is
  written dynamically and never given one is not caught.
* **Staff-side slots.** ``MasterNotificationPrefs`` is one row per master
  forever and holds stated preferences, but its person is a
  ``CatalogMaster`` — a mirrored staff record, governed by the catalog
  contract rather than by client memory. Out of scope, on purpose.
* **Whether a declaration is true.** The guard checks that an owner is
  named and that the crossing claim matches the store's tenancy. It
  cannot tell that ``owner=BACKEND`` is the right answer.
* **The backend's own store.** ``users.UserPersonalContext`` lives in
  another repository. A field added there is invisible from here.

CLI usage::

    python tools/lint/personal_field_guard.py apps/

Exit codes: 0 clean, 1 violations, 2 bad invocation.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

#: The one model that IS a customer in this repo. Everything else becomes
#: person-keyed by pointing at it. Named rather than guessed: a structural
#: rule («any OneToOne target») would drag in staff and catalog mirrors,
#: and a wrong anchor makes the guard silent rather than noisy. The scan
#: fails loudly if this class is not found.
PERSON_MODEL = "BotUser"

#: The read-side key registry the memory reader already consults. Its keys
#: are a second, independent view of surface B — see the module docstring.
CARDINALITY_MODULE = Path("identity/services/memory_key_policy.py")
CARDINALITY_CONSTANT = "_KEY_CARDINALITY"

_FIELD_CALLS = frozenset({"ForeignKey", "OneToOneField", "ManyToManyField"})

MEMORY_KEY_PREFIX = "memory_key:"


class Declared(Protocol):
    """One entry in ``PERSONAL_FIELDS`` — see the registry for the columns."""

    site: str
    origin: str
    owner: str
    crosses_salons: bool
    why: str


class Registry(Protocol):
    """The shape ``apps/identity/personal_fields.py`` must keep.

    Stated here so the guard can be run against a substitute in tests, and
    so a rename over there fails as a type error rather than an attribute
    error at lint time.
    """

    PERSONAL_FIELDS: Sequence[Declared]
    NOT_PERSONAL: Mapping[str, str]
    POLICY_DEBT: Mapping[str, str]
    NEVER_CROSSES: frozenset[str]
    ORIGINS: frozenset[str]
    OWNERS: frozenset[str]


@dataclass(frozen=True)
class Site:
    """One discovered slot that must be declared in the registry."""

    key: str
    """Registry key: ``<app>.<Model>.<field>`` or ``memory_key:<key>``."""

    path: str
    """Repo-relative file the slot was found in."""

    line: int

    crosses_salons: bool
    """Derived from the store, NOT from the declaration."""


# ---------------------------------------------------------------------------
# Surface A — person-keyed profile models
# ---------------------------------------------------------------------------


def _call_name(node: ast.AST) -> str | None:
    """Name of the callable in ``x = Something(...)``, attribute or bare."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _is_field_call(node: ast.AST) -> bool:
    name = _call_name(node)
    return bool(name) and (name.endswith("Field") or name in _FIELD_CALLS)


def _kwarg(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _is_true(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _target_model(call: ast.Call) -> str | None:
    """The model a relation points at, from ``FK(Target)`` or ``FK("app.Target")``."""
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Name):
        return first.id
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value.rsplit(".", 1)[-1]
    return None


@dataclass(frozen=True)
class _Model:
    app: str
    name: str
    path: str
    fields: tuple[tuple[str, int, ast.Call], ...]

    def field_call(self, name: str) -> ast.Call | None:
        for field_name, _line, call in self.fields:
            if field_name == name:
                return call
        return None


def _models_in(path: Path, app: str) -> list[_Model]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[_Model] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        fields: list[tuple[str, int, ast.Call]] = []
        for stmt in node.body:
            target: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                target, value = stmt.targets[0], stmt.value
            elif isinstance(stmt, ast.AnnAssign):
                target, value = stmt.target, stmt.value
            if not isinstance(target, ast.Name) or value is None:
                continue
            if _is_field_call(value):
                assert isinstance(value, ast.Call)
                fields.append((target.id, stmt.lineno, value))
        if fields:
            found.append(
                _Model(app=app, name=node.name, path=path.as_posix(), fields=tuple(fields))
            )
    return found


def _is_person_keyed(model: _Model) -> bool:
    """One row per person, ever — see the module docstring, clause (b).

    The one-to-one must be **mandatory**. A nullable one is a *link*, not an
    identity: ``CatalogMaster.linked_bot_user`` says «this staff record may
    also sign in as that bot user», and the master row exists whether or not
    it does. Treating that as person-keyed would drag every catalog mirror
    field into a registry about client memory.
    """
    if model.name == PERSON_MODEL:
        return True
    for name, _line, call in model.fields:
        if _call_name(call) == "OneToOneField" and _target_model(call) == PERSON_MODEL:
            if not _is_true(_kwarg(call, "null")):
                return True
        if name == "user_id" and _is_true(_kwarg(call, "primary_key")):
            return True
    return False


def _crosses_salons(model: _Model) -> bool:
    """A store with no ``tenant`` FK follows the person between salons."""
    tenant = model.field_call("tenant")
    return not (tenant is not None and _call_name(tenant) == "ForeignKey")


def scan_models(apps_root: Path) -> list[Site]:
    """Every field of every person-keyed model under ``apps_root``."""
    sites: list[Site] = []
    anchor_seen = False
    for path in sorted(apps_root.rglob("models.py")):
        if "/tests/" in path.as_posix() or "/migrations/" in path.as_posix():
            continue
        app = path.relative_to(apps_root).parts[0]
        for model in _models_in(path, app):
            if model.name == PERSON_MODEL:
                anchor_seen = True
            if not _is_person_keyed(model):
                continue
            crosses = _crosses_salons(model)
            for name, line, _call in model.fields:
                sites.append(
                    Site(
                        key=f"{model.app}.{model.name}.{name}",
                        path=model.path,
                        line=line,
                        crosses_salons=crosses,
                    )
                )
    if not anchor_seen:
        raise LookupError(
            f"personal_field_guard: no model named `{PERSON_MODEL}` under {apps_root}. "
            "The anchor this guard hangs off has been renamed or moved — every "
            "person-keyed model became invisible and this lint silently stopped "
            "guarding anything. Point PERSON_MODEL at the new customer model; do "
            "not delete the check."
        )
    return sites


# ---------------------------------------------------------------------------
# Surface B — memory keys
# ---------------------------------------------------------------------------


def _dict_key_literal(node: ast.expr) -> str | None:
    """``{"key": "diet", ...}`` → ``"diet"``."""
    if not isinstance(node, ast.Dict):
        return None
    for k, v in zip(node.keys, node.values):
        if (
            isinstance(k, ast.Constant)
            and k.value == "key"
            and isinstance(v, ast.Constant)
            and isinstance(v.value, str)
            and v.value
        ):
            return v.value
    return None


def scan_memory_keys(apps_root: Path) -> list[Site]:
    """Keys written into ``MemoryEntry.content`` as literals."""
    sites: dict[str, Site] = {}
    for path in sorted(apps_root.rglob("*.py")):
        posix = path.as_posix()
        if "/tests/" in posix or "/migrations/" in posix or path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            found: list[tuple[str, int]] = []
            if isinstance(node, ast.Call):
                value = _kwarg(node, "content")
                if value is not None and (key := _dict_key_literal(value)):
                    found.append((key, node.lineno))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                named_content = any(isinstance(t, ast.Name) and t.id == "content" for t in targets)
                if named_content and node.value is not None:
                    if key := _dict_key_literal(node.value):
                        found.append((key, node.lineno))
            for key, line in found:
                sites.setdefault(
                    key,
                    # Green MemoryEntry rows are read by user_id alone
                    # (memory_reader.read_green_entries) — every memory key
                    # crosses salons today, whatever the writer intended.
                    Site(
                        key=f"{MEMORY_KEY_PREFIX}{key}",
                        path=posix,
                        line=line,
                        crosses_salons=True,
                    ),
                )
    return sorted(sites.values(), key=lambda s: s.key)


def read_cardinality_keys(apps_root: Path) -> set[str]:
    """Keys named in the read-side ``_KEY_CARDINALITY`` registry."""
    path = apps_root / CARDINALITY_MODULE
    if not path.is_file():
        raise LookupError(
            f"personal_field_guard: {path} is gone. It held {CARDINALITY_CONSTANT}, "
            "the read-side half of the memory-key registry; without it this guard "
            "sees only what the writers spell out as literals. Point "
            "CARDINALITY_MODULE at wherever the read policy lives now."
        )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target: ast.expr | None = None
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        else:
            continue
        if not (isinstance(target, ast.Name) and target.id == CARDINALITY_CONSTANT):
            continue
        if not isinstance(value, ast.Dict):
            continue
        return {
            k.value for k in value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
    raise LookupError(
        f"personal_field_guard: {CARDINALITY_CONSTANT} is no longer a dict literal "
        f"in {path}. This guard reads it out of the source on purpose — importing it "
        "would need Django. Restore the literal or teach this reader the new shape."
    )


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def scan(apps_root: Path) -> list[Site]:
    """Every slot that must carry a declaration, both surfaces."""
    return scan_models(apps_root) + scan_memory_keys(apps_root)


def check(sites: list[Site], registry: Registry, cardinality_keys: set[str]) -> list[str]:
    """Return one ``::error`` line per violation; empty when clean."""
    personal = {f.site: f for f in registry.PERSONAL_FIELDS}
    not_personal = dict(registry.NOT_PERSONAL)
    debt = dict(registry.POLICY_DEBT)
    never_crosses = set(registry.NEVER_CROSSES)
    origins = set(registry.ORIGINS)
    owners = set(registry.OWNERS)

    problems: list[str] = []
    declared = set(personal) | set(not_personal)
    by_key = {s.key: s for s in sites}

    both = sorted(set(personal) & set(not_personal))
    for key in both:
        problems.append(
            f"::error::`{key}` is declared BOTH personal and not-personal in "
            "apps/identity/personal_fields.py. One slot, one verdict."
        )

    for site in sorted(sites, key=lambda s: s.key):
        if site.key in declared:
            continue
        problems.append(
            f"::error file={site.path},line={site.line}::`{site.key}` is a new "
            "personal slot with nobody declared to own it. Add it to "
            "apps/identity/personal_fields.py — PERSONAL_FIELDS with origin, "
            "owner and crosses_salons, or NOT_PERSONAL with the reason it is "
            "plumbing. Six memory stores grew one silent field at a time; this "
            "is the step that was missing each of those times."
        )

    for key in sorted(declared - set(by_key)):
        problems.append(
            f"::error::`{key}` is declared in apps/identity/personal_fields.py but "
            "no longer exists in the code. A declaration nothing can trip hides the "
            "slot that replaced it — delete the entry."
        )

    for key, field in sorted(personal.items()):
        if field.origin not in origins:
            problems.append(f"::error::`{key}`: unknown origin `{field.origin}`.")
        if field.owner not in owners:
            problems.append(f"::error::`{key}`: unknown owner `{field.owner}`.")
        if len(field.why.strip()) < 40:
            problems.append(
                f"::error::`{key}`: `why` is {len(field.why.strip())} chars. A table "
                "of labels decays; a table of reasons is reviewable."
            )
        site = by_key.get(key)
        if site is None:
            continue
        if field.crosses_salons != site.crosses_salons:
            problems.append(
                f"::error file={site.path},line={site.line}::`{key}` declares "
                f"crosses_salons={field.crosses_salons}, but its store says "
                f"{site.crosses_salons}. A store with no `tenant` foreign key "
                "follows the person between salons, and green memory rows are read "
                "by user_id alone. Declaring otherwise does not make it so."
            )
            continue
        broken = _rules_broken(key, field, never_crosses)
        if broken and key not in debt:
            for reason in broken:
                problems.append(
                    f"::error file={site.path},line={site.line}::`{key}` {reason} "
                    "Fix it, or name it in POLICY_DEBT with the ruling it "
                    "contradicts — a debt list is read, a silent pass is not."
                )
        if not broken and key in debt:
            problems.append(
                f"::error::`{key}` is in POLICY_DEBT but breaks no rule any more. "
                "Delete the line — accepted debt only shrinks."
            )

    for key in sorted(set(debt) - set(personal)):
        problems.append(
            f"::error::POLICY_DEBT names `{key}`, which is not a declared personal "
            "field. Debt for a slot nobody declares is a note, not a ratchet."
        )

    written = {s.key.removeprefix(MEMORY_KEY_PREFIX) for s in sites if _is_memory_key(s.key)}
    for key in sorted(written - cardinality_keys):
        problems.append(
            f"::error::memory key `{key}` is written but has no entry in "
            "_KEY_CARDINALITY. Without one it defaults to single-valued by luck, "
            "not by decision — and two live rows for one key reach the prompt as a "
            "contradiction (DRF-1260)."
        )
    for key in sorted(cardinality_keys - written):
        problems.append(
            f"::error::memory key `{key}` has a cardinality but nothing writes it. "
            "A read policy for a key that never arrives is a rule nobody can trip."
        )

    return problems


def _is_memory_key(key: str) -> bool:
    return key.startswith(MEMORY_KEY_PREFIX)


def _rules_broken(key: str, field: Declared, never_crosses: set[str]) -> list[str]:
    """Every declared rule this slot breaks today, as reviewer-facing prose.

    Two rules, both from the owner's rulings of 2026-08-24:

    * decision 3 — «сказал сам — переходит; узнали о нём — нет», plus the
      slots the ruling names as never-crossing whatever their origin;
    * a slot may not stay ``UNCLASSIFIED`` silently: an unsorted bag is
      exactly how the six stores grew, so it is allowed only as named debt.
    """
    origin = field.origin
    crosses = field.crosses_salons
    broken: list[str] = []

    if crosses:
        if key in never_crosses:
            broken.append(
                "crosses salons, and the ruling of 2026-08-24 "
                "(docs/DECISIONS_MEMORY_PACKAGE.md, decision 3) names it as a "
                "salon's own observation that must never follow the person out."
            )
        elif origin not in ("USER_STATED", "SYSTEM"):
            broken.append(
                f"crosses salons with origin {origin} — the salon learned it, the "
                "person did not say it. Decision 3 of 2026-08-24: «сказал сам — "
                "переходит; узнали о нём — нет»."
            )

    if origin == "UNCLASSIFIED":
        broken.append(
            "is declared UNCLASSIFIED — it holds more than one kind of fact and "
            "nobody has taken it apart. An unsorted bag is how the six stores of "
            "the 24.08 audit grew."
        )

    return broken


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_registry(apps_root: Path) -> Registry:
    """Import the registry without pulling Django in (it is stdlib-only)."""
    import importlib.util

    path = apps_root / "identity" / "personal_fields.py"
    spec = importlib.util.spec_from_file_location("_ayla_personal_fields", path)
    if spec is None or spec.loader is None:  # pragma: no cover — unreachable in tree
        raise LookupError(f"personal_field_guard: cannot load registry at {path}")
    module = importlib.util.module_from_spec(spec)
    # `@dataclass` resolves annotations through ``sys.modules[cls.__module__]``,
    # so the module has to be registered before it executes.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module  # type: ignore[return-value]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: personal_field_guard.py <apps/>", file=sys.stderr)
        return 2

    apps_root = Path(argv[1])
    if not apps_root.is_dir():
        print(f"personal_field_guard: not a directory: {apps_root}", file=sys.stderr)
        return 2

    registry = _load_registry(apps_root)
    sites = scan(apps_root)
    problems = check(sites, registry, read_cardinality_keys(apps_root))

    for line in problems:
        print(line)

    if problems:
        print(
            f"\npersonal_field_guard: {len(problems)} violation(s) across "
            f"{len(sites)} personal slot(s). A personal field with no declared "
            "owner is how the seventh memory store starts.",
            file=sys.stderr,
        )
        return 1

    declared_personal = len(registry.PERSONAL_FIELDS)
    print(
        f"personal_field_guard: clean ({len(sites)} slots scanned, "
        f"{declared_personal} personal, {len(registry.POLICY_DEBT)} in debt)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
