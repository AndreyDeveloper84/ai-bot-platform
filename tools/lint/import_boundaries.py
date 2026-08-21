"""ADR-0009 G-series module-boundary AST guard (#1001 — Option B).

Enforces the G1–G10 architecture contracts (ADR-0009 module boundaries,
Codex integration audit) as **source-scoped forbidden import edges**.

# Why an AST linter and not ruff TID251

`ruff`'s `flake8-tidy-imports.banned-api` (TID251) bans a module
*globally* — for every importer. The G-series contracts are different:
they forbid an edge only from a **specific source package**. Example:
`apps/miniapp_api/` must not import `apps.booking.services.create`, but
the booking app itself obviously may. TID251 cannot express
"forbidden only from package X"; this guard can.

Division of labour (do not duplicate):
  - **ruff TID251** (`pyproject.toml`) — global bans: `legacy_notifications`
    static import from `apps/*` (the G1.2 contract). Stays in ruff.
  - **this guard** — source→target edges TID251 can't express (G2.1,
    G5.1, G6.2) + the cross-repo `psycopg2` ban (ADR-0009 rule 2).
  - **Option C** (Code Reviewer checklist) — contracts not cheaply
    AST-expressible.
  - **G9 (this guard, DRF-1109)** — originally filed as "not cheaply
    AST-expressible" (a runtime-semantics divergence, not an edge). The
    2026-08-15 architecture review showed a WEAK edge form catches both
    known instances anyway: BookingRequest is apps/booking/'s model: an
    import of it from anywhere else risks reading a store that
    BOOKING_VIA_AYLA_REST=ON stopped writing to (dual-source
    divergence). The guard can't see the flag check — it flags the
    import itself — so every existing crossing needs a BASELINE entry
    (legitimate flag-gated reads and un-migrated debt alike); new
    crossings must justify themselves the same way.

# What this guard detects

For every production `.py` file under a contract's `source_prefixes`,
it flags an import whose target matches the contract's
`forbidden_modules` (exact dotted module OR a submodule of it), unless
the `(contract_id, file, qualname, forbidden_root)` quadruple is in
`BASELINE`.

Alongside the import-edge contracts it runs three rules that are not
edges at all, each keyed the same way and baselined the same way:

  - **MKT1** (#1018) — cross-tenant catalog read (`CatalogMaster
    .all_tenants`) outside `apps/marketplace/`.
  - **DRF-1130** — `select_related()` in the same queryset chain as
    `select_for_update()`. On Postgres a nullable FK joins LEFT OUTER
    and the lock is refused outright; the guard cannot see nullability,
    so the combination is banned and the verified-NOT NULL sites are
    baselined.
  - **DRF-1158** — a builtin `hash()` value flowing into a stored sink
    (keyword argument, attribute assignment, string-keyed dict entry).
    `hash()` is salt-randomised per process. This is the ONE rule here
    that also scans test files: its defect lived in a fixture.

# The rule behind all three (and behind this file)

A checkable invariant does not get to live in a comment. `apps/skills/
booking/tools.py:2364` carried, in as many words, «FOOT-GUN: do NOT add
``.select_related(...)`` here». It did not protect the line forty rows
above it in its own file, and it did not protect two sites in other
modules. DRF-1130 shipped to production and only went red once the full
suite was switched on. A comment addresses whoever happens to read it;
a rule addresses everyone who does not.

The same rule applies to what this file itself writes down: see
"Reading a BASELINE entry" at `BASELINE_STATUSES` below — the verdict
behind an accepted crossing is a required annotation with a closed
vocabulary and a test, not a paragraph above a frozenset.

# Why `qualname` is part of the baseline key (DRF-1157)

The key used to be the `(contract_id, file, forbidden_root)` triple —
i.e. **file**-granular. That is a hole in the sieve, and it was hiding
exactly the defect G9 was written to catch:
`apps/miniapp_api/views.py` imports `apps.booking.models.BookingRequest`
from FOUR different function bodies. One of them (`_bookings_list`) is
the legitimate flag-gated read; another (`_collect_occupied`) reads the
booking store with no `BOOKING_VIA_AYLA_REST` check at all — the slots
defect. A single file-granular baseline line covered all four, so the
rule reported green over the crossing it existed to flag.

Function-local imports are the normal shape in this repo (Django app
loading order), so the enclosing scope is available for free from the
AST: every import node is recorded with the dotted qualname of the
`class`/`def` nesting it sits in, or `<module>` for a top-level import.
Baselining one call site therefore no longer baselines its neighbours,
and moving a forbidden import into a new function fails CI as new debt.

The MKT1 catalog rule below deliberately stays file-granular (it reports
one violation per file by design); its entries carry the synthetic
qualname `<file>` so both rules can share the key type and the
stale-entry machinery.

Import shapes detected (the adversarial set #1001 / S5 asked for):
  - `import a.b.c`            / `import a.b.c as x`
  - `from a.b import c`       (target `a.b` AND `a.b.c`)
  - `from a.b import (c, d)`  (multi-line)
  - `from a.b.c import name`  / `... import name as y`
  - relative imports `from ..x import y` — resolved to absolute via the
    file's package path
  - imports inside `if TYPE_CHECKING:` blocks — **counted** (a real
    boundary crossing hidden behind TYPE_CHECKING is still a crossing)
  - `importlib.import_module("a.b.c")` / `__import__("a.b.c")` with a
    string-literal argument — the documented dynamic escape hatch is
    NOT a blind spot

# KNOWN LIMITATIONS (explicitly NOT detected)

Per the same tech-lead direction as `red_zone_guard.py` («do not chase
infinite obtuse patterns»):
  - **Runtime-variable module names**:
    `mod = cfg["m"]; importlib.import_module(mod)` — the name is not a
    source literal.
  - **`getattr`-style attribute walking** off an already-imported
    parent package.
  - **Re-export laundering**: importing a forbidden module via a third
    module that re-exports it (the edge to the *re-exporter* is what's
    visible; add a contract for it if it becomes a pattern).
These are accepted gaps — the guard is the cheap first line; Code
Review (Option C) + the runtime tenant/idempotency guards are the rest.

# Baseline (the file `.importlinter.baseline` never existed — #1001)

The G-series tickets referenced `.importlinter.baseline` /
`.importlinter.passing` files that were never created (roadmap item
A11 was skipped). `BASELINE` below is the real, in-code replacement:
the set of **currently-accepted** crossings on `origin/dev`, each tied
to its tracking issue. CI fails on any crossing NOT in the baseline
(new debt) and on any baseline entry that no longer matches (stale —
forces the line to be deleted when the site is migrated, ratcheting
the debt down).

# CLI usage

  python tools/lint/import_boundaries.py apps/

Exits 0 when clean. Exits 1 with one line per violation:
`file:line:col: [contract] message`.

  python tools/lint/import_boundaries.py --baseline-report

Prints every accepted crossing grouped by its verdict — LIVE-DEFECT
first, UNTRIAGED next, then the sites somebody has proved safe. Ask this
before filing a bug off a baseline line (DRF-1159).
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# ── Contract registry ────────────────────────────────────────────────


@dataclass(frozen=True)
class Contract:
    """A forbidden-import-edge contract.

    A file under any of ``source_prefixes`` (repo-relative, POSIX, with
    trailing slash) may not import any module that equals — or is a
    submodule of — any entry in ``forbidden_modules``.
    """

    id: str
    issue: str
    source_prefixes: tuple[str, ...]
    forbidden_modules: tuple[str, ...]
    message: str
    # Repo-relative prefixes that are exempt even though they match
    # ``source_prefixes`` — for contracts whose restricted source is
    # "everywhere except X" rather than a positive list of source dirs
    # (e.g. G9: everywhere except the module that owns the model).
    exclude_prefixes: tuple[str, ...] = ()
    # True when a baseline entry for this contract is a HUMAN VERDICT
    # rather than a record of visible debt — i.e. the guard bans a
    # construct it cannot fully judge (G9 cannot see the feature flag;
    # DRF-1130 cannot see column nullability). Every baseline entry for
    # such a rule MUST carry a ``BaselineNote`` (enforced by
    # tests/tools/test_import_boundaries.py). See "Reading a BASELINE
    # entry" in the module docstring (DRF-1159).
    triage_note_required: bool = False


CONTRACTS: tuple[Contract, ...] = (
    Contract(
        id="G2.1-skills-no-yclients",
        issue="#928",
        source_prefixes=("apps/skills/",),
        forbidden_modules=("apps.integrations.yclients",),
        message=(
            "skills must not import YClients directly — booking state must go "
            "through Ayla's canonical gate via REST (ADR-0009 rule 5)."
        ),
    ),
    Contract(
        id="G5.1-api-no-booking-mutators",
        issue="#925/#968",
        source_prefixes=("apps/miniapp_api/", "apps/admin_api/"),
        forbidden_modules=(
            "apps.booking.services.create",
            "apps.booking.services.reschedule",
            "apps.booking.services.transitions",
            "apps.booking.services.feedback",
        ),
        message=(
            "client/admin API surfaces must not DB-write booking via the "
            "canonical mutators — call Ayla REST; the event consumer "
            "reconciles the projection (ADR-0009 rule 5)."
        ),
    ),
    Contract(
        id="G6.2-eventbus-consumer-narrow",
        issue="#927",
        source_prefixes=("apps/eventbus/consumers/",),
        forbidden_modules=("apps.skills", "apps.channels"),
        message=(
            "eventbus consumers must not invoke skills/channels in-process — "
            "enqueue a Celery task keyed on event_id so the outbound "
            "side-effect stays exactly-once (ADR-0009 rule 7, idempotency)."
        ),
    ),
    Contract(
        id="ADR9.2-no-cross-repo-psycopg2",
        issue="ADR-0009 rule 2",
        source_prefixes=("apps/",),
        forbidden_modules=("psycopg2",),
        message=(
            "apps/ must not import psycopg2 directly — no cross-repo DB "
            "access (ADR-0009 rule 2). Use the Django ORM or Ayla REST."
        ),
    ),
    Contract(
        id="G9-booking-request-outside-owner",
        issue="DRF-1109",
        source_prefixes=("apps/",),
        exclude_prefixes=("apps/booking/",),
        forbidden_modules=("apps.booking.models.BookingRequest",),
        triage_note_required=True,
        message=(
            "BookingRequest is owned by apps/booking/ — reading it elsewhere "
            "risks dual-source divergence once BOOKING_VIA_AYLA_REST=ON stops "
            "writing to it. THIS GUARD SEES ONE FRAME: it reads the function "
            "the import sits in and nothing else, so a BOOKING_VIA_AYLA_REST "
            "check in the CALLING function is invisible to it and a flagged "
            "site may be perfectly safe (DRF-1159). A BASELINE entry here is "
            "therefore a human verdict, not a bug report — it MUST carry a "
            "BaselineNote saying which verdict (PROVEN-ELSEWHERE / BY-DESIGN / "
            "LIVE-DEFECT / UNTRIAGED). If this is a legitimate flag-gated or "
            "historical read, add it to BASELINE with that note and a tracking "
            "issue; if not, route through apps/booking/ or Ayla REST."
        ),
    ),
)

# ── Baseline: accepted crossings on origin/dev, each tied to its issue ─
# (contract_id, file POSIX relpath, qualname, forbidden_module_root)
#
# `qualname` is the dotted class/def nesting the import sits in, or
# `<module>` for a top-level import. See the module docstring (DRF-1157)
# for why file granularity was not enough.
#
# WHY EACH LINE IS HERE lives in BASELINE_NOTES, not in the prose
# between these entries (DRF-1159). The comments below are a summary and
# may drift; the annotation is checked by a test and cannot. For G9 in
# particular, read the note before treating an entry as a bug — its
# whole point is that the guard cannot see the frame that makes a site
# safe. `--baseline-report` prints all of it, grouped by verdict.
BaselineKey = tuple[str, str, str, str]

# Synthetic qualname for an import at module level.
MODULE_QUALNAME = "<module>"
# Synthetic qualname for rules that are file-granular by design (MKT1).
FILE_QUALNAME = "<file>"

BASELINE: frozenset[BaselineKey] = frozenset(
    {
        # -- G2.1 - skills -> YClients (#928; Phase 2.2 reroute via Ayla REST) --
        # provider.py is the strangler seam: behind BOOKING_VIA_AYLA_REST=OFF
        # (default) it returns the unchanged YClients client; flag-ON routes
        # through Ayla REST. Retired together with skill.py/tools.py when #928
        # completes the cutover. DRF-1157 split the single file-level entry for
        # provider.py into its two real sites (module header + the factory).
        (
            "G2.1-skills-no-yclients",
            "apps/skills/booking/provider.py",
            "<module>",
            "apps.integrations.yclients",
        ),
        (
            "G2.1-skills-no-yclients",
            "apps/skills/booking/provider.py",
            "get_booking_provider",
            "apps.integrations.yclients",
        ),
        (
            "G2.1-skills-no-yclients",
            "apps/skills/booking/skill.py",
            "<module>",
            "apps.integrations.yclients",
        ),
        (
            "G2.1-skills-no-yclients",
            "apps/skills/booking/tools.py",
            "<module>",
            "apps.integrations.yclients",
        ),
        # -- G5.1 - API surfaces -> booking mutators (#925 create; #968 rest) --
        # DRF-1157: apps/miniapp_api/views.py used to carry three file-level
        # entries (create / transitions / feedback). The `transitions` one alone
        # covered SIX distinct view bodies; they are enumerated now so migrating
        # one cancel/reschedule endpoint at a time ratchets the debt down,
        # instead of leaving one line that never has to move.
        (
            "G5.1-api-no-booking-mutators",
            "apps/miniapp_api/views.py",
            "create_booking",
            "apps.booking.services.create",
        ),
        (
            "G5.1-api-no-booking-mutators",
            "apps/miniapp_api/views.py",
            "submit_feedback",
            "apps.booking.services.feedback",
        ),
        (
            "G5.1-api-no-booking-mutators",
            "apps/miniapp_api/views.py",
            "_booking_to_dict",
            "apps.booking.services.transitions",
        ),
        (
            "G5.1-api-no-booking-mutators",
            "apps/miniapp_api/views.py",
            "booking_cancel_request",
            "apps.booking.services.transitions",
        ),
        (
            "G5.1-api-no-booking-mutators",
            "apps/miniapp_api/views.py",
            "booking_cancel_confirm",
            "apps.booking.services.transitions",
        ),
        (
            "G5.1-api-no-booking-mutators",
            "apps/miniapp_api/views.py",
            "booking_cancel_undo",
            "apps.booking.services.transitions",
        ),
        (
            "G5.1-api-no-booking-mutators",
            "apps/miniapp_api/views.py",
            "booking_reschedule_request",
            "apps.booking.services.transitions",
        ),
        (
            "G5.1-api-no-booking-mutators",
            "apps/miniapp_api/views.py",
            "booking_reschedule_confirm",
            "apps.booking.services.transitions",
        ),
        (
            "G5.1-api-no-booking-mutators",
            "apps/admin_api/services/master_deactivation.py",
            "<module>",
            "apps.booking.services.transitions",
        ),
        # -- G6.2 - eventbus consumer -> skill in-process (#927; fix = Celery) --
        (
            "G6.2-eventbus-consumer-narrow",
            "apps/eventbus/consumers/payment.py",
            "handle_payment_failed._dispatch_skill",
            "apps.skills",
        ),
        # -- G9 - BookingRequest read outside apps/booking/ (DRF-1109) --------
        # 22 accepted crossings across the same 18 files as before. DRF-1157
        # raised the entry count from 18 by splitting three multi-site files
        # into their real call sites. No NEW file appeared: the file-level key
        # was hiding unknown call sites inside known files, not unknown files.
        #
        # (a) Confirmed flag-gated / dual-path by design - the file references
        #     BOOKING_VIA_AYLA_REST at or above the read.
        (
            "G9-booking-request-outside-owner",
            "apps/miniapp_api/views.py",
            "bookings_list",
            "apps.booking.models.BookingRequest",
        ),
        (
            "G9-booking-request-outside-owner",
            "apps/integrations/yclients/webhooks.py",
            "<module>",
            "apps.booking.models.BookingRequest",
        ),
        (
            "G9-booking-request-outside-owner",
            "apps/skills/booking/tools.py",
            "<module>",
            "apps.booking.models.BookingRequest",
        ),
        # (a2) DRF-1157 - the three OTHER miniapp_api/views.py sites the old
        #      file-level key silenced along with `bookings_list`. Each is a
        #      separate decision now:
        #      * `_collect_occupied` - the local slot computation. Its CALLER
        #        (`slots`) has gated it behind BOOKING_VIA_AYLA_REST since
        #        DRF-1062 (commit 0860183, 2026-08-15): flag-ON returns Ayla
        #        slots and never reaches this helper. Flag-gated in effect, but
        #        the gate lives one frame up where this AST guard cannot see
        #        it - hence a baseline line, not a fix.
        #      * `_get_booking_owned` - a helper called from BOTH flag-guarded
        #        endpoints (booking_cancel_*/booking_reschedule_* return early
        #        on flag-ON) and unguarded ones. Untriaged, follow-up ticket.
        #      * `customer_recent_activity` - documented as a read of the local
        #        mirror populated by the Ayla booking-event consumer (ADR-0009
        #        cached-canonical-state read). Untriaged.
        (
            "G9-booking-request-outside-owner",
            "apps/miniapp_api/views.py",
            "_collect_occupied",
            "apps.booking.models.BookingRequest",
        ),
        (
            "G9-booking-request-outside-owner",
            "apps/miniapp_api/views.py",
            "_get_booking_owned",
            "apps.booking.models.BookingRequest",
        ),
        (
            "G9-booking-request-outside-owner",
            "apps/miniapp_api/views.py",
            "customer_recent_activity",
            "apps.booking.models.BookingRequest",
        ),
        # (b) Known LIVE defect, not legitimate - the periodic completion scan
        #     reads BookingRequest with NO flag check at all (arch review S2,
        #     A2). This is the DRF-1108 instance the rule is required to catch;
        #     left unfixed here (out of scope, IMPL_BRIEF_MECHANIZATION.md S4)
        #     but explicitly called out - do NOT read this line as legitimate.
        (
            "G9-booking-request-outside-owner",
            "apps/bookings/tasks.py",
            "detect_completed_bookings",
            "apps.booking.models.BookingRequest",
        ),
        # (c) Untriaged - zero BOOKING_VIA_AYLA_REST references in the file.
        #     Neither confirmed-safe nor confirmed-broken; surfaced by this
        #     contract for the first time (DRF-1109, 2026-08-15). Candidates
        #     for a follow-up ticket per file/surface, not fixed here. Includes
        #     master_api's booking-facing master surfaces (dashboard/schedule/
        #     customers/conversations), the same class of risk as A1 (Mini App
        #     slots) if BOOKING_VIA_AYLA_REST is ON on the pilot - value
        #     UNKNOWN, not read this session.
        (
            "G9-booking-request-outside-owner",
            "apps/admin_api/services/master_deactivation.py",
            "<module>",
            "apps.booking.models.BookingRequest",
        ),
        (
            "G9-booking-request-outside-owner",
            "apps/bookings/followups.py",
            "_b11_blocked_statuses_frozen_at_pr_time",
            "apps.booking.models.BookingRequest",
        ),
        (
            "G9-booking-request-outside-owner",
            "apps/bookings/recheck.py",
            "_recheck_booking_state",
            "apps.booking.models.BookingRequest",
        ),
        (
            "G9-booking-request-outside-owner",
            "apps/bookings/reminders_factory.py",
            "<module>",
            "apps.booking.models.BookingRequest",
        ),
        (
            "G9-booking-request-outside-owner",
            "apps/eventbus/signals.py",
            "<module>",
            "apps.booking.models.BookingRequest",
        ),
        (
            "G9-booking-request-outside-owner",
            "apps/integrations/yclients/tasks.py",
            "push_booking_to_yclients",
            "apps.booking.models.BookingRequest",
        ),
        (
            "G9-booking-request-outside-owner",
            "apps/loyalty/services.py",
            "<module>",
            "apps.booking.models.BookingRequest",
        ),
        # DRF-1157 split: credit and revoke are separate paths; migrating one
        # must not silently keep the other baselined.
        (
            "G9-booking-request-outside-owner",
            "apps/loyalty/subscribers.py",
            "LoyaltySubscriber._credit_visit",
            "apps.booking.models.BookingRequest",
        ),
        (
            "G9-booking-request-outside-owner",
            "apps/loyalty/subscribers.py",
            "LoyaltySubscriber._revoke_visit",
            "apps.booking.models.BookingRequest",
        ),
        (
            "G9-booking-request-outside-owner",
            "apps/master_api/services/conversation_detail.py",
            "<module>",
            "apps.booking.models.BookingRequest",
        ),
        (
            "G9-booking-request-outside-owner",
            "apps/master_api/services/conversations.py",
            "<module>",
            "apps.booking.models.BookingRequest",
        ),
        (
            "G9-booking-request-outside-owner",
            "apps/master_api/services/customers.py",
            "<module>",
            "apps.booking.models.BookingRequest",
        ),
        # dashboard.py and schedule.py stood here until DRF-1085 (869285c,
        # 205f2dd) moved both surfaces onto the RemoteBookingProxy mirror
        # and the BookingRequest import left the files entirely. The
        # ratchet then demanded the entries be deleted — debt paid, line
        # removed. This is the baseline working as designed, not a
        # relaxation: a stale entry is a lie about the shape of the code.
        (
            "G9-booking-request-outside-owner",
            "apps/master_api/tasks.py",
            "auto_generate_draft_for_inbound",
            "apps.booking.models.BookingRequest",
        ),
    }
)

# Directory/file name fragments that are out of scope.
#
# Two different kinds of "out of scope" live here:
#   * GENERATED — machine-written or non-source; no rule ever looks.
#   * TEST      — out of scope for the PRODUCTION-boundary rules (the
#     G-series tickets count prod sites separately from test sites), but
#     IN scope for DRF-1158, whose defect lived in a fixture.
_GENERATED_DIR_PARTS = frozenset({"migrations", "__pycache__"})
_TEST_DIR_PARTS = frozenset({"tests"})
_SKIP_DIR_PARTS = _GENERATED_DIR_PARTS | _TEST_DIR_PARTS


# ── Cross-tenant catalog-read rule (MKT1, #1018) ─────────────────────
#
# Distinct from the import-edge CONTRACTS above: this is an ORM-access
# rule (same posture as tools/lint/red_zone_guard.py), because the thing
# to police — a cross-tenant catalog read — is the `.all_tenants` manager
# access on the catalog mirror models, NOT a module import. An import-edge
# contract can't see it: everyone legitimately imports CatalogMaster for
# tenant-scoped (`.objects`) reads.
#
# Invariant (ADR-0009 tenant isolation + EPIC #1014): cross-tenant catalog
# discovery is the SOLE sanctioned `all_tenants` carve-out and lives only
# in `apps/marketplace/`. Anywhere else, `CatalogMaster.all_tenants` /
# `CatalogService.all_tenants` is a violation unless pinned in
# CATALOG_CROSS_TENANT_BASELINE (the legitimate pre-existing sites that
# scope by explicit tenant_id — onboarding, admin, event consumers, etc).
#
# Detection is literal model-name + manager-name (`CatalogMaster.all_tenants`),
# same pragmatic limit as red_zone_guard: aliased model refs or querysets
# stashed in a variable are not chased — the tenant ContextVar + audit
# hooks (apps/tenancy) are the deeper lines of defence.
CATALOG_CROSS_TENANT_CONTRACT_ID = "MKT1-catalog-cross-tenant-read-only-marketplace"
CATALOG_CROSS_TENANT_ISSUE = "#1018"
CATALOG_CROSS_TENANT_MODELS = frozenset({"CatalogMaster", "CatalogService"})
CATALOG_CROSS_TENANT_MANAGER = "all_tenants"
# The one place a cross-tenant catalog read is allowed.
MARKETPLACE_PREFIX = "apps/marketplace/"
# Synthetic "root" used in BaselineKey for the catalog rule so it shares
# the import-edge baseline/stale machinery.
_CATALOG_ROOT = "<catalog.all_tenants>"

# Accepted pre-existing cross-tenant catalog-read sites on origin/dev
# (one entry per file). New sites outside apps/marketplace/ fail CI; a
# stale entry (the `.all_tenants` use was removed/migrated) also fails,
# ratcheting the surface down. Generated by running the rule with an
# empty baseline against apps/ (same method as the import-edge BASELINE).
CATALOG_CROSS_TENANT_BASELINE: frozenset[BaselineKey] = frozenset(
    (CATALOG_CROSS_TENANT_CONTRACT_ID, _f, FILE_QUALNAME, _CATALOG_ROOT)
    for _f in (
        # admin / master surfaces — explicit tenant_id scoping, admin authority
        "apps/admin_api/services/availability.py",
        "apps/admin_api/services/master_deactivation.py",
        "apps/admin_api/views.py",
        "apps/admin_api/views_invite.py",
        "apps/master_api/auth.py",
        "apps/master_api/management/commands/create_test_master_invite.py",
        "apps/master_api/management/commands/print_master_dev_env.py",
        "apps/master_api/services/catalog.py",
        "apps/master_api/services/dashboard.py",
        "apps/master_api/services/schedule.py",
        # DRF-1085 — the visit read layer extracted out of dashboard.py /
        # schedule.py, both already listed above. Same posture, not a new
        # surface: every query filters on an explicit `tenant_id` taken
        # from the master being viewed, and nothing here is reachable
        # cross-tenant. `.objects` is unavailable because the service is
        # also called outside a request (tests, and any future job), where
        # no tenant ContextVar is set.
        "apps/master_api/services/visit_source.py",
        # DRF-1061 — invite redemption links a person to the master named
        # by the invite. Reads exactly one row, by primary key, filtered on
        # the invite's own tenant_id; there is no discovery here and no way
        # to reach another tenant's catalog. `.objects` is unavailable
        # because redemption also runs from a management command and from
        # the stream consumer, where no tenant ContextVar is set.
        "apps/identity/services/staff_invites.py",
        # DRF-1227 — the mirror of redemption: clears the link on the one
        # master row this person is attached to, filtered on the tenant the
        # caller named. Same posture as staff_invites.py above, and `.objects`
        # is unavailable for the same reason — revocation must also work from
        # a command, where no tenant ContextVar is set.
        "apps/identity/services/staff_revoke.py",
        # DRF-1061 — operator command listing and picking a master to invite.
        # Every query is filtered on the --tenant the operator named, and it
        # runs at a terminal with no request and therefore no tenant
        # ContextVar. Same posture as the other management commands above.
        "apps/identity/management/commands/issue_staff_invite.py",
        "apps/master_api/tasks.py",
        "apps/master_api/views.py",
        # booking write paths (S1) — explicit-id reads before canonical write
        "apps/booking/services/create.py",
        "apps/booking/services/transitions.py",
        # catalog sync / seed (mirror write path + dev bootstrap)
        "apps/catalog/management/commands/seed_dev_formula_tela.py",
        # event consumers — payload carries explicit tenant_id (not context)
        "apps/eventbus/consumers/catalog.py",
        "apps/eventbus/consumers/schedule.py",
        # identity onboarding / role resolution (S3) — tenant creation flow
        "apps/identity/services/role_resolver.py",
        "apps/identity/services/solo_onboarding.py",
        # skills — tenant resolved before the call (tracked by G2.1 #928 too)
        "apps/skills/booking/skill.py",
        "apps/skills/booking/tools.py",
        "apps/skills/payment_failed/skill.py",
    )
)


# -- Shape rules: banned CODE SHAPES, not import edges -----------------
#
# The G-series contracts above ban an *edge* - a fact fully visible in
# the file being parsed. The two rules below ban a *shape* whose actual
# defect is NOT visible in the file:
#
#   * DRF-1130 - whether a ``select_related()``d FK is nullable lives in
#     a model that may sit in another app (and, via ``a__b`` spans, in
#     several).
#   * DRF-1158 - where a ``hash()`` value ends up (a column, an
#     idempotency key, a cross-run comparison) is a property of the
#     other process, not of this expression.
#
# So each bans the construct and accepts the known-safe uses through a
# baseline. That trade is deliberate: a false positive costs one
# annotated baseline line; a false negative costs an unconditional
# production failure (DRF-1130) or a test that is green on SQLite and
# red on Postgres depending on PYTHONHASHSEED (DRF-1158).
#
# Corollary - and this is the DRF-1159 lesson: because the guard cannot
# judge the shape on its own, a baseline entry for a shape rule is a
# HUMAN VERDICT. It must say which verdict. Hence
# ``triage_note_required=True`` and BASELINE_NOTES below.


@dataclass(frozen=True)
class ShapeRule:
    """A banned code shape (ORM/stdlib call), keyed like a contract.

    ``root`` is the synthetic "forbidden root" token that goes into the
    :data:`BaselineKey` quadruple, so shape rules share the baseline and
    stale-entry machinery with the import-edge contracts unchanged.
    """

    id: str
    issue: str
    root: str
    message: str
    # Shape rules ban a proxy for the defect, never the defect itself -
    # so every accepted site is somebody's judgement call. Always True
    # today; kept explicit so a future purely-mechanical shape rule can
    # opt out honestly rather than by omission.
    triage_note_required: bool = True


# -- DRF-1130: select_related() under select_for_update() --------------
#
# ``select_related`` on a NULLABLE FK emits a LEFT OUTER JOIN, and
# Postgres refuses to lock through it:
#
#     FOR UPDATE cannot be applied to the nullable side of an outer join
#
# The refusal is unconditional - the query cannot succeed on any data.
# SQLite (the local default) does not enforce it, so the whole suite
# stayed green; production never hit it because BOOKING_VIA_AYLA_REST
# routed around the path. The defect was armed, not firing.
#
# WHY THE COMBINATION IS BANNED OUTRIGHT rather than "banned when the FK
# is nullable": this guard parses ONE file. Nullability is declared on
# the model - frequently in another app, sometimes on an abstract base,
# and for ``select_related("a__b")`` on a model two hops away. Resolving
# it would mean carrying a model registry inside a lint script, i.e.
# teaching a syntax tool the DB schema, and getting it silently wrong on
# every dynamic/abstract/swappable case. The asymmetry decides it:
# ``select_related`` is a query-count optimisation and NEVER a
# correctness one, so a false positive costs one join plus one annotated
# baseline line, while a false negative costs a production 500 that no
# local test run can reproduce. Ban the shape; baseline the
# verified-NOT NULL uses.
ROW_LOCK_JOIN_RULE = ShapeRule(
    id="DRF1130-no-join-under-row-lock",
    issue="DRF-1130",
    root="<select_for_update+select_related>",
    message=(
        "one queryset both locks rows and joins. A nullable FK joins LEFT "
        'OUTER and Postgres then refuses the lock outright: "FOR UPDATE '
        'cannot be applied to the nullable side of an outer join". The '
        "refusal is unconditional - no data makes it succeed. "
        "SQLite does not enforce this, so a "
        "green local suite proves nothing. This guard reads one file and "
        "cannot know whether the joined field is NOT NULL, so the combination "
        "is banned outright: drop the `select_related` (it is a query-count "
        "optimisation, never a correctness one), or - if every joined field is "
        "verified NOT NULL - add a BASELINE entry with a PROVEN-ELSEWHERE "
        "note naming the fields and the model file that declares them"
    ),
)

# -- DRF-1158: builtin hash() flowing into a stored value --------------
#
# ``hash()`` is salt-randomised per process (``PYTHONHASHSEED``): the
# same input gives a different number in the next run, in another worker
# and on the other side of a fork. Anything that outlives the process -
# a column, an idempotency key, a fixture id compared across runs - must
# use a digest instead.
#
# The real instance: ``hash(str(x)) & 0xFFFFFFFF`` written into an
# ``IntegerField``. ``0xFFFFFFFF`` is 4 294 967 295; Postgres ``integer``
# tops out at 2 147 483 647, so roughly half of all seeds overflowed.
# The test failed or passed by coin flip, and never failed at all on
# SQLite (which does not check integer width).
#
# WHAT IS DETECTED - ``hash(...)`` whose value flows, within the
# expression, into a named sink: a keyword argument
# (``Model.objects.create(external_id=...)``, and every fixture/factory/
# payload helper that forwards one), an attribute assignment
# (``obj.field = ...``), or a string-keyed dict entry (payloads and
# idempotency keys). Counting every keyword argument is a deliberately
# wide net: ``hash()`` appears a handful of times in this repo and every
# one of them was a value on its way somewhere it should not have gone.
#
# WHAT IS NOT - a bare ``hash(x)`` bound to a local, ``__hash__`` bodies
# (the one place the builtin is the correct idiom) and ``hash()`` inside
# a ``lambda`` (an in-process sort key never leaves the process).
# Cross-run COMPARISON of two hashes is not AST-visible at all and is
# not chased.
#
# This rule runs on TEST files as well as production ones - unlike every
# other rule in this file - because the defect it is named after lived
# in a fixture, and a flaky fixture is exactly the thing nobody bisects.
HASH_SINK_RULE = ShapeRule(
    id="DRF1158-no-builtin-hash-into-stored-value",
    issue="DRF-1158",
    root="<hash()-into-stored-value>",
    message=(
        "receives a builtin `hash()` value. `hash()` is salt-randomised per "
        "process (PYTHONHASHSEED), so the value changes between runs and "
        "workers and must never reach a column, an idempotency key, or a "
        "comparison that spans processes. The 32-bit form also overflows "
        "Postgres `integer` about half the time - and never on SQLite, which "
        "is why the test only flickered. Use a digest: "
        "int.from_bytes(hashlib.sha256(str(v).encode()).digest()[:4], 'big') "
        "& 0x7FFFFFFF"
    ),
)

SHAPE_RULES: tuple[ShapeRule, ...] = (ROW_LOCK_JOIN_RULE, HASH_SINK_RULE)


# -- Reading a BASELINE entry: the annotation vocabulary (DRF-1159) ----
#
# A baseline line says "the guard fires here and CI stays green". It
# does NOT say why, and twice in a row that gap got filled by guessing:
# both an architecture review and the main working window read the
# `_collect_occupied` G9 entry as a live defect. It is not one - the
# BOOKING_VIA_AYLA_REST gate sits in its CALLER (`slots`), one frame
# above anything this guard can see.
#
# The fix is not another comment. Prose above a 60-line frozenset is
# precisely the artefact that already failed here: the same class of
# failure as the `# FOOT-GUN: do NOT add select_related` comment, which
# protected neither the next line of its own file nor two sites in other
# modules. So the verdict becomes DATA next to the key, in a required
# field with a closed vocabulary, and a test fails the build when a note
# is missing, carries an unknown status, or is orphaned.
#
#   BY-DESIGN       The site is correct and stays. The entry retires
#                   only when the design changes.
#   PROVEN-ELSEWHERE
#                   The site is safe, but the proof lives OUTSIDE the
#                   frame this key names - a flag check in the CALLER, a
#                   NOT NULL declared on a model in another app. NOT a
#                   defect, and the exact line a reader is most likely to
#                   misread (that is the whole of DRF-1159). The note
#                   MUST say where the proof lives, by file and name, so
#                   the claim is checkable in one hop.
#   LIVE-DEFECT     A real instance of the defect the rule names: wrong
#                   as written, whether or not it happens to be firing
#                   today. Accepted only because fixing it was out of
#                   scope for the change that added the line. The note
#                   must say how it fails.
#   UNTRIAGED       Nobody has decided yet. Read it as neither safe nor
#                   broken. This is the honest status, and the only one
#                   that stays honest when nobody has looked.
BASELINE_STATUSES: frozenset[str] = frozenset(
    {"BY-DESIGN", "PROVEN-ELSEWHERE", "LIVE-DEFECT", "UNTRIAGED"}
)


@dataclass(frozen=True)
class BaselineNote:
    """The human verdict behind one baseline entry."""

    status: str
    text: str


def _note_required_ids() -> frozenset[str]:
    """Contract/rule ids whose baseline entries MUST carry a note.

    Derived from the registries rather than hand-listed, so a new
    note-required rule cannot forget to join.
    """
    return frozenset(
        [c.id for c in CONTRACTS if c.triage_note_required]
        + [r.id for r in SHAPE_RULES if r.triage_note_required]
    )


NOTE_REQUIRED_CONTRACT_IDS: frozenset[str] = _note_required_ids()


# -- DRF-1130 baseline: verified-safe lock+join sites on origin/dev ----
#
# Four sites, all PROVEN-ELSEWHERE: every joined FK is declared without
# ``null=True``, so the join is INNER and Postgres has nothing to refuse.
# "Elsewhere" is literal - the declaration sits in an app none of these
# files import from, which is precisely why the rule does not try to
# work it out for itself.
#
# The two sites this rule was written for are NOT here: they were fixed
# in 586317b (apps/booking/services/reschedule.py,
# apps/skills/booking/tools.py) before the rule existed. Their absence
# is the ratchet working - a fixed site leaves no line behind.
ROW_LOCK_JOIN_BASELINE: frozenset[BaselineKey] = frozenset(
    {
        (
            "DRF1130-no-join-under-row-lock",
            "apps/admin_api/services/availability.py",
            "approve_availability_request",
            "<select_for_update+select_related>",
        ),
        (
            "DRF1130-no-join-under-row-lock",
            "apps/admin_api/services/availability.py",
            "reject_availability_request",
            "<select_for_update+select_related>",
        ),
        (
            "DRF1130-no-join-under-row-lock",
            "apps/eventbus/consumers/payment.py",
            "handle_payment_failed",
            "<select_for_update+select_related>",
        ),
        (
            "DRF1130-no-join-under-row-lock",
            "apps/identity/services/staff_invites.py",
            "redeem_staff_invite",
            "<select_for_update+select_related>",
        ),
    }
)


# -- DRF-1158 baseline: accepted hash()-into-sink sites on origin/dev --
#
# Four sites, all LIVE-DEFECT, all in fixtures - which is the point: the
# rule scans test files because that is where its defect lived, and a
# per-process-random fixture id is the specific failure mode nobody
# bisects (it moves between runs, and SQLite never complains). None are
# fixed here; this window is the sieve, not the cleanup. Each entry says
# how it fails so the follow-up can be scoped without re-reading the
# code.
HASH_SINK_BASELINE: frozenset[BaselineKey] = frozenset(
    {
        (
            "DRF1158-no-builtin-hash-into-stored-value",
            "apps/catalog/services/tests/test_linking.py",
            "_legacy_row",
            "<hash()-into-stored-value>",
        ),
        (
            "DRF1158-no-builtin-hash-into-stored-value",
            "apps/eventbus/tests/test_reviews_consumer.py",
            "TestMalformedPayload.test_invalid_rating_no_dedupe_row",
            "<hash()-into-stored-value>",
        ),
        (
            "DRF1158-no-builtin-hash-into-stored-value",
            "apps/kb/tests/test_webhooks.py",
            "TestSchemaTypeMapping.test_schema_type_maps_to_doc_type",
            "<hash()-into-stored-value>",
        ),
        (
            "DRF1158-no-builtin-hash-into-stored-value",
            "apps/skills/faq/tests/test_skill.py",
            "TestMatches.test_imperative_phrasings_match",
            "<hash()-into-stored-value>",
        ),
    }
)


# -- The verdicts behind the entries above (DRF-1159) ------------------
#
# Required for every entry of a contract/rule in
# NOTE_REQUIRED_CONTRACT_IDS; optional (but welcome) elsewhere. Enforced
# by tests/tools/test_import_boundaries.py::TestBaselineAnnotations:
# missing note, unknown status and orphaned note all fail the build.
BASELINE_NOTES: dict[BaselineKey, BaselineNote] = {
    # -- G9: every entry is a triage verdict, never a bug report ------
    # A G9 hit means 'this file reads apps/booking/'s model'. Whether
    # that is safe depends on a BOOKING_VIA_AYLA_REST check the guard
    # cannot see - it may sit in the caller. Hence a status per entry.
    (
        "G9-booking-request-outside-owner",
        "apps/miniapp_api/views.py",
        "bookings_list",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "BY-DESIGN",
        "Dual-path read: the BOOKING_VIA_AYLA_REST branch is in this same function, above the read. Retires with the flag.",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/miniapp_api/views.py",
        "_collect_occupied",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "PROVEN-ELSEWHERE",
        "THE DRF-1159 line. Read twice as a live defect; it is not one. The gate is in the CALLER, apps/miniapp_api/views.py::slots, which since DRF-1062 (0860183) returns Ayla slots on flag-ON and never reaches this helper. This guard sees one frame and cannot see that. Do not file a bug off this line: check slots() first.",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/miniapp_api/views.py",
        "_get_booking_owned",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "UNTRIAGED",
        "Helper called from BOTH flag-guarded endpoints (booking_cancel_*/booking_reschedule_* return early on flag-ON) and unguarded ones. Nobody has decided which callers matter. Follow-up ticket.",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/miniapp_api/views.py",
        "customer_recent_activity",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "UNTRIAGED",
        "Documented in-file as a read of the local mirror the Ayla booking-event consumer populates (ADR-0009 cached-canonical-state). The documentation has not been verified against the consumer.",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/integrations/yclients/webhooks.py",
        "<module>",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "BY-DESIGN",
        "The YClients webhook is the writer of the local store; the file checks BOOKING_VIA_AYLA_REST at the read. Retires with the flag (#928).",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/skills/booking/tools.py",
        "<module>",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "BY-DESIGN",
        "Strangler seam: flag-OFF keeps the local write path, flag-ON routes to Ayla REST. The file checks the flag. Retires with #928.",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/bookings/tasks.py",
        "detect_completed_bookings",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "LIVE-DEFECT",
        "The periodic completion scan reads BookingRequest with NO flag check anywhere in the file - on flag-ON it scans a store that stopped being written. This is the DRF-1108 instance G9 exists to catch. Unfixed here on purpose (IMPL_BRIEF_MECHANIZATION.md S4).",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/admin_api/services/master_deactivation.py",
        "<module>",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "UNTRIAGED",
        "Zero BOOKING_VIA_AYLA_REST references in the file (DRF-1109 sweep, 2026-08-15). Neither confirmed safe nor confirmed broken - nobody has looked at this surface since the contract first surfaced it.",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/bookings/followups.py",
        "_b11_blocked_statuses_frozen_at_pr_time",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "UNTRIAGED",
        "Zero BOOKING_VIA_AYLA_REST references in the file (DRF-1109 sweep, 2026-08-15). Neither confirmed safe nor confirmed broken - nobody has looked at this surface since the contract first surfaced it.",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/bookings/recheck.py",
        "_recheck_booking_state",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "UNTRIAGED",
        "Zero BOOKING_VIA_AYLA_REST references in the file (DRF-1109 sweep, 2026-08-15). Neither confirmed safe nor confirmed broken - nobody has looked at this surface since the contract first surfaced it.",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/bookings/reminders_factory.py",
        "<module>",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "UNTRIAGED",
        "Zero BOOKING_VIA_AYLA_REST references in the file (DRF-1109 sweep, 2026-08-15). Neither confirmed safe nor confirmed broken - nobody has looked at this surface since the contract first surfaced it.",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/eventbus/signals.py",
        "<module>",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "UNTRIAGED",
        "Zero BOOKING_VIA_AYLA_REST references in the file (DRF-1109 sweep, 2026-08-15). Neither confirmed safe nor confirmed broken - nobody has looked at this surface since the contract first surfaced it.",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/integrations/yclients/tasks.py",
        "push_booking_to_yclients",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "UNTRIAGED",
        "Zero BOOKING_VIA_AYLA_REST references in the file (DRF-1109 sweep, 2026-08-15). Neither confirmed safe nor confirmed broken - nobody has looked at this surface since the contract first surfaced it.",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/loyalty/services.py",
        "<module>",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "UNTRIAGED",
        "Zero BOOKING_VIA_AYLA_REST references in the file (DRF-1109 sweep, 2026-08-15). Neither confirmed safe nor confirmed broken - nobody has looked at this surface since the contract first surfaced it.",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/loyalty/subscribers.py",
        "LoyaltySubscriber._credit_visit",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "UNTRIAGED",
        "Zero BOOKING_VIA_AYLA_REST references in the file (DRF-1109 sweep, 2026-08-15). Neither confirmed safe nor confirmed broken - nobody has looked at this surface since the contract first surfaced it.",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/loyalty/subscribers.py",
        "LoyaltySubscriber._revoke_visit",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "UNTRIAGED",
        "Zero BOOKING_VIA_AYLA_REST references in the file (DRF-1109 sweep, 2026-08-15). Neither confirmed safe nor confirmed broken - nobody has looked at this surface since the contract first surfaced it.",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/master_api/services/conversation_detail.py",
        "<module>",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "UNTRIAGED",
        "Zero BOOKING_VIA_AYLA_REST references in the file (DRF-1109 sweep, 2026-08-15). Neither confirmed safe nor confirmed broken - nobody has looked at this surface since the contract first surfaced it.",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/master_api/services/conversations.py",
        "<module>",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "UNTRIAGED",
        "Zero BOOKING_VIA_AYLA_REST references in the file (DRF-1109 sweep, 2026-08-15). Neither confirmed safe nor confirmed broken - nobody has looked at this surface since the contract first surfaced it.",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/master_api/services/customers.py",
        "<module>",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "UNTRIAGED",
        "Zero BOOKING_VIA_AYLA_REST references in the file (DRF-1109 sweep, 2026-08-15). Neither confirmed safe nor confirmed broken - nobody has looked at this surface since the contract first surfaced it.",
    ),
    (
        "G9-booking-request-outside-owner",
        "apps/master_api/tasks.py",
        "auto_generate_draft_for_inbound",
        "apps.booking.models.BookingRequest",
    ): BaselineNote(
        "UNTRIAGED",
        "Zero BOOKING_VIA_AYLA_REST references in the file (DRF-1109 sweep, 2026-08-15). Neither confirmed safe nor confirmed broken - nobody has looked at this surface since the contract first surfaced it.",
    ),
    # -- DRF-1130: the joined FK is NOT NULL, declared elsewhere ------
    (
        "DRF1130-no-join-under-row-lock",
        "apps/admin_api/services/availability.py",
        "approve_availability_request",
        "<select_for_update+select_related>",
    ): BaselineNote(
        "PROVEN-ELSEWHERE",
        "Joins ScheduleChangeRequest.master and .tenant. Both are declared without null=True in apps/scheduling/models.py (ScheduleChangeRequest, the tenant/master FKs) - INNER JOIN, nothing for FOR UPDATE to refuse. The declaration is in apps/scheduling/, a file this one never opens, which is exactly the knowledge the rule refuses to guess at.",
    ),
    (
        "DRF1130-no-join-under-row-lock",
        "apps/admin_api/services/availability.py",
        "reject_availability_request",
        "<select_for_update+select_related>",
    ): BaselineNote(
        "PROVEN-ELSEWHERE",
        "Same chain and same proof as approve_availability_request: ScheduleChangeRequest.master/.tenant are NOT NULL in apps/scheduling/models.py. Listed separately because the ratchet is per-function - migrating approve must not silently keep reject green.",
    ),
    (
        "DRF1130-no-join-under-row-lock",
        "apps/eventbus/consumers/payment.py",
        "handle_payment_failed",
        "<select_for_update+select_related>",
    ): BaselineNote(
        "PROVEN-ELSEWHERE",
        "Joins Conversation.bot_user, declared without null=True in apps/conversations/models.py (on_delete=PROTECT, Sprint 2.5 H1) - INNER JOIN. The join is load-bearing here: the post-commit payload is built from the locked row and must not re-query.",
    ),
    (
        "DRF1130-no-join-under-row-lock",
        "apps/identity/services/staff_invites.py",
        "redeem_staff_invite",
        "<select_for_update+select_related>",
    ): BaselineNote(
        "PROVEN-ELSEWHERE",
        "Joins StaffInvite.tenant, declared without null=True in apps/tenancy/models.py - INNER JOIN. DRF-1160 already dropped the nullable catalog_master join from this same chain; what is left is the NOT NULL half, and the in-file comment says so.",
    ),
    # -- DRF-1158: randomised hash() in a fixture, all still live -----
    (
        "DRF1158-no-builtin-hash-into-stored-value",
        "apps/catalog/services/tests/test_linking.py",
        "_legacy_row",
        "<hash()-into-stored-value>",
    ): BaselineNote(
        "LIVE-DEFECT",
        "external_id=abs(hash(slug)) % 10_000_000 into CatalogService.external_id, an IntegerField with unique_together (tenant, external_id). In range, so it does not overflow - but the value is a different number every process, so a collision between two fixtures is a per-seed coin flip. apps/skills/payment_failed/tests/test_skill.py already carries the fix to copy (_stable_external_id, a truncated sha256).",
    ),
    (
        "DRF1158-no-builtin-hash-into-stored-value",
        "apps/eventbus/tests/test_reviews_consumer.py",
        "TestMalformedPayload.test_invalid_rating_no_dedupe_row",
        "<hash()-into-stored-value>",
    ): BaselineNote(
        "LIVE-DEFECT",
        "hash() built into an event_id - i.e. into an IDEMPOTENCY KEY, the one place a value must be identical across processes by definition. Passes today only because each run starts from an empty dedupe table.",
    ),
    (
        "DRF1158-no-builtin-hash-into-stored-value",
        "apps/kb/tests/test_webhooks.py",
        "TestSchemaTypeMapping.test_schema_type_maps_to_doc_type",
        "<hash()-into-stored-value>",
    ): BaselineNote(
        "LIVE-DEFECT",
        "knowledge_doc_id=100 + hash(schema_type) % 1000 into a KbDocument id. Four parametrised schema_types drawn from 1000 slots with a per-process salt: the collision is rare, seed-dependent, and would read as a flake.",
    ),
    (
        "DRF1158-no-builtin-hash-into-stored-value",
        "apps/skills/faq/tests/test_skill.py",
        "TestMatches.test_imperative_phrasings_match",
        "<hash()-into-stored-value>",
    ): BaselineNote(
        "LIVE-DEFECT",
        "channel_user_id=f'imp-{hash(text) & 0xFFFF:x}' - 16 bits of a salt-randomised hash used as an identity. Two phrasings colliding collapses two BotUsers into one on some seeds and not others. Note the irony: apps/skills/faq/tools.py documents this exact hazard for its cache key and its own test fixture ignores it.",
    ),
}


# Every accepted entry this module ships, for the report + the tests.
ALL_BASELINES: frozenset[BaselineKey] = frozenset(
    BASELINE | CATALOG_CROSS_TENANT_BASELINE | ROW_LOCK_JOIN_BASELINE | HASH_SINK_BASELINE
)


def baseline_report() -> list[str]:
    """Every baseline entry grouped by verdict, most alarming first.

    This is the answer to "which of these lines is a real bug?" — the
    question that got guessed at twice (DRF-1159). Run:

        python tools/lint/import_boundaries.py --baseline-report
    """
    order = ["LIVE-DEFECT", "UNTRIAGED", "PROVEN-ELSEWHERE", "BY-DESIGN", "UNANNOTATED"]
    buckets: dict[str, list[str]] = {status: [] for status in order}
    for key in sorted(ALL_BASELINES):
        contract_id, rel_posix, qualname, root = key
        note = BASELINE_NOTES.get(key)
        status = note.status if note else "UNANNOTATED"
        scope = "" if qualname in (MODULE_QUALNAME, FILE_QUALNAME) else f" ({qualname})"
        text = f"  {rel_posix}{scope}  [{contract_id} -> {root}]"
        if note:
            text += f"\n      {note.text}"
        buckets.setdefault(status, []).append(text)

    lines: list[str] = []
    for status in order:
        entries = buckets.get(status) or []
        lines.append(f"{status} ({len(entries)})")
        lines.extend(entries or ["  -"])
        lines.append("")
    return lines


@dataclass(frozen=True)
class Violation:
    file: Path
    lineno: int
    col_offset: int
    message: str
    # The baseline key this violation would be silenced by. Carried so
    # callers (notably the baseline-matches-reality regression test) can
    # reconstruct the exact key without re-parsing the message text.
    key: BaselineKey | None = None

    def format(self) -> str:
        return f"{self.file}:{self.lineno}:{self.col_offset}: {self.message}"


@dataclass(frozen=True)
class ImportEdge:
    """A single imported module reference found in a source file."""

    module: str
    lineno: int
    col_offset: int
    # Dotted class/def nesting the reference sits in, `<module>` if
    # top-level. Part of the baseline key (DRF-1157).
    qualname: str = MODULE_QUALNAME


class _ScopedVisitor(ast.NodeVisitor):
    """Base visitor that tracks the enclosing ``class``/``def`` nesting.

    Every rule in this file keys its baseline on the qualname of the
    scope a hit sits in (DRF-1157), so the scope stack is shared rather
    than re-implemented per rule.
    """

    def __init__(self) -> None:
        self._scope: list[str] = []

    @property
    def qualname(self) -> str:
        return ".".join(self._scope) if self._scope else MODULE_QUALNAME

    def _visit_scope(self, node: ast.AST, name: str) -> None:
        self._scope.append(name)
        try:
            self.generic_visit(node)
        finally:
            self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scope(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scope(node, node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scope(node, node.name)


class _ImportCollector(_ScopedVisitor):
    """Collect every statically-resolvable imported module in a file.

    ``package_parts`` is the dotted package the file lives in, used to
    resolve relative imports to absolute module paths.

    Each edge also records the qualname of the enclosing ``class``/``def``
    scope, so the baseline can pin a single call site instead of a whole
    file (DRF-1157). Function-local imports are the dominant shape for
    the G-series crossings in this repo, which is exactly why file
    granularity hid `_collect_occupied` behind its own module's
    legitimate flag-gated read.
    """

    def __init__(self, package_parts: tuple[str, ...]) -> None:
        super().__init__()
        self.package_parts = package_parts
        self.edges: list[ImportEdge] = []

    # `import a.b.c` / `import a.b.c as x`
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._add(alias.name, node)
        self.generic_visit(node)

    # `from a.b import c` / `from . import c` / `from ..a import (c, d)`
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = self._resolve_base(node.module, node.level)
        if base is not None:
            # The package/module imported FROM is itself an edge…
            if node.module is not None or node.level > 0:
                self._add(base, node)
            # …and each name may itself be a submodule (`from a.b import c`
            # where a.b.c is a module). Recording both is safe: the
            # contract match is a prefix test, so a non-module name like a
            # function simply won't match any forbidden ROOT it isn't under.
            for alias in node.names:
                if alias.name != "*":
                    self._add(f"{base}.{alias.name}", node)
        self.generic_visit(node)

    # `importlib.import_module("a.b")` / `__import__("a.b")`
    def visit_Call(self, node: ast.Call) -> None:
        if self._is_dynamic_import(node.func) and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                self._add(first.value, node)
        self.generic_visit(node)

    def _resolve_base(self, module: str | None, level: int) -> str | None:
        if level == 0:
            return module
        # Relative: level 1 = current package, 2 = parent, etc.
        drop = level - 1
        if drop >= len(self.package_parts):
            return None  # climbs to/above the top package — Python would ImportError
        base_parts = self.package_parts[: len(self.package_parts) - drop]
        tail = module.split(".") if module else []
        resolved = ".".join((*base_parts, *tail))
        return resolved or None

    @staticmethod
    def _is_dynamic_import(func: ast.AST) -> bool:
        # `importlib.import_module(...)`  (Attribute) or a bare
        # `import_module(...)` / `__import__(...)` (Name).
        if isinstance(func, ast.Attribute):
            return func.attr == "import_module"
        if isinstance(func, ast.Name):
            return func.id in {"import_module", "__import__"}
        return False

    def _add(self, module: str, node: ast.AST) -> None:
        self.edges.append(
            ImportEdge(
                module=module,
                lineno=getattr(node, "lineno", 0),
                col_offset=getattr(node, "col_offset", 0),
                qualname=self.qualname,
            )
        )


def _package_parts(rel_posix: str) -> tuple[str, ...]:
    """Dotted package parts for a repo-relative POSIX file path.

    `apps/skills/booking/skill.py`     -> ('apps','skills','booking')
    `apps/skills/booking/__init__.py`  -> ('apps','skills','booking')
    """
    # The file's package is its containing directory — drop the filename.
    # (For __init__.py the filename is the package marker, so dropping it
    # yields the same package dir; no special case needed.)
    return tuple(rel_posix.split("/")[:-1])


def _matched_root(module: str, forbidden_modules: tuple[str, ...]) -> str | None:
    """Return the forbidden root `module` falls under, or None.

    Matches exact dotted module OR a submodule (`root` or `root.<sub>`),
    never a sibling sharing a prefix (`apps.skills` != `apps.skillsfoo`).
    """
    for root in forbidden_modules:
        if module == root or module.startswith(root + "."):
            return root
    return None


def _is_generated(rel_posix: str) -> bool:
    """Never scanned by any rule."""
    return any(p in _GENERATED_DIR_PARTS for p in rel_posix.split("/"))


def _is_test_file(rel_posix: str) -> bool:
    parts = rel_posix.split("/")
    if any(p in _TEST_DIR_PARTS for p in parts):
        return True
    name = parts[-1]
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def _is_skipped(rel_posix: str) -> bool:
    """Out of scope for the production-boundary rules (everything but
    DRF-1158)."""
    return _is_generated(rel_posix) or _is_test_file(rel_posix)


def _chain_method_names(call: ast.Call) -> list[tuple[str, ast.Attribute]]:
    """Method names in ``call``'s receiver spine, outermost first.

    ``M.objects.select_for_update().select_related("x").get(pk=1)`` ->
    ``[("get", …), ("select_related", …), ("select_for_update", …)]``.
    Walks only through chained *calls*; a non-call link (a bare
    attribute, a subscript, a name) ends the walk — a queryset stashed
    in a local and re-chained later is a documented blind spot, same
    pragmatic limit as ``red_zone_guard``.
    """
    out: list[tuple[str, ast.Attribute]] = []
    current: ast.AST = call
    while isinstance(current, ast.Call):
        func = current.func
        if not isinstance(func, ast.Attribute):
            break
        out.append((func.attr, func))
        current = func.value
    return out


def _find_hash_calls(node: ast.AST) -> list[ast.Call]:
    """Every builtin ``hash(...)`` call in ``node``'s subtree.

    Does not descend into a ``lambda``: an in-process sort key
    (``sorted(xs, key=lambda v: hash(v))``) never leaves the process and
    is not what DRF-1158 is about.
    """
    found: list[ast.Call] = []
    stack: list[ast.AST] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Lambda):
            continue
        if (
            isinstance(current, ast.Call)
            and isinstance(current.func, ast.Name)
            and current.func.id == "hash"
        ):
            found.append(current)
        stack.extend(ast.iter_child_nodes(current))
    return found


class _RowLockJoinVisitor(_ScopedVisitor):
    """Record chains that combine ``select_for_update`` + ``select_related``.

    Order-independent (either method may come first) and blind to what
    sits between them (``.filter()``, ``.only()``, ``.get()``): what
    matters is that both land in ONE queryset, because that is what makes
    Postgres put a lock and an outer join in the same statement.

    ``prefetch_related`` is deliberately NOT matched — it issues a second
    query instead of a join, so it never widens the FOR UPDATE scope.
    """

    def __init__(self) -> None:
        super().__init__()
        self.hits: list[ImportEdge] = []
        self._seen: set[tuple[int, int]] = set()

    def visit_Call(self, node: ast.Call) -> None:
        chain = _chain_method_names(node)
        names = {name for name, _ in chain}
        if "select_for_update" in names and "select_related" in names:
            lock = next(attr for name, attr in chain if name == "select_for_update")
            joined = next(attr for name, attr in chain if name == "select_related")
            position = (lock.lineno, lock.col_offset)
            # Every enclosing call in the same chain sees both names;
            # report the lock site once.
            if position not in self._seen:
                self._seen.add(position)
                self.hits.append(
                    ImportEdge(
                        module=f"select_for_update() + select_related() (line {joined.lineno})",
                        lineno=lock.lineno,
                        col_offset=lock.col_offset,
                        qualname=self.qualname,
                    )
                )
        self.generic_visit(node)


class _HashSinkVisitor(_ScopedVisitor):
    """Record builtin ``hash()`` values flowing into a stored/named sink.

    Sinks: a keyword argument, an attribute assignment target, a
    string-keyed dict entry. See the DRF-1158 block above for why the
    keyword-argument net is cast this wide and what is deliberately left
    out.
    """

    def __init__(self) -> None:
        super().__init__()
        self.hits: list[ImportEdge] = []
        self._seen: set[tuple[int, int]] = set()

    def _record(self, value: ast.AST, sink: str) -> None:
        for call in _find_hash_calls(value):
            position = (call.lineno, call.col_offset)
            if position in self._seen:
                continue
            self._seen.add(position)
            self.hits.append(
                ImportEdge(
                    module=sink,
                    lineno=call.lineno,
                    col_offset=call.col_offset,
                    qualname=self.qualname,
                )
            )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # `def __hash__(self): return hash(self._key)` is the ONE place
        # the builtin is the right answer — the value never outlives the
        # dict that asked for it.
        if node.name == "__hash__":
            return
        super().visit_FunctionDef(node)

    def visit_keyword(self, node: ast.keyword) -> None:
        if node.arg is not None:  # `**kwargs` carries no field name
            self._record(node.value, f"keyword argument `{node.arg}=`")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if any(isinstance(t, ast.Attribute) for t in node.targets):
            field = next(t for t in node.targets if isinstance(t, ast.Attribute))
            self._record(node.value, f"attribute assignment `.{field.attr} =`")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Attribute) and node.value is not None:
            self._record(node.value, f"attribute assignment `.{node.target.attr} =`")
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Attribute):
            self._record(node.value, f"attribute assignment `.{node.target.attr} =`")
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                self._record(value, f"dict entry `{key.value}`")
        self.generic_visit(node)


class _CatalogAllTenantsVisitor(ast.NodeVisitor):
    """Record `<Model>.all_tenants` access where Model is a catalog mirror.

    Matches the literal attribute chain ``CatalogMaster.all_tenants`` /
    ``CatalogService.all_tenants`` (the value is a bare ``Name``). The
    ``.filter(...)``/``.create(...)`` tail is irrelevant — any use of the
    cross-tenant manager on a catalog model is the boundary crossing.
    """

    def __init__(self, models: frozenset[str], manager: str) -> None:
        self.models = models
        self.manager = manager
        self.hits: list[ImportEdge] = []  # reuse ImportEdge as (label, line, col)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            node.attr == self.manager
            and isinstance(node.value, ast.Name)
            and node.value.id in self.models
        ):
            self.hits.append(
                ImportEdge(
                    module=f"{node.value.id}.{self.manager}",
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                )
            )
        self.generic_visit(node)


def evaluate_file(
    file_path: Path,
    repo_root: Path,
    *,
    contracts: tuple[Contract, ...] = CONTRACTS,
    baseline: frozenset[BaselineKey] = BASELINE,
    catalog_baseline: frozenset[BaselineKey] = CATALOG_CROSS_TENANT_BASELINE,
    catalog_models: frozenset[str] = CATALOG_CROSS_TENANT_MODELS,
    marketplace_prefix: str = MARKETPLACE_PREFIX,
    row_lock_baseline: frozenset[BaselineKey] = ROW_LOCK_JOIN_BASELINE,
    hash_baseline: frozenset[BaselineKey] = HASH_SINK_BASELINE,
) -> tuple[list[Violation], set[BaselineKey]]:
    """Evaluate one file against every rule in this module.

    Import-edge contracts (G-series), the cross-tenant catalog read
    (MKT1) and the row-lock join (DRF-1130) describe the PRODUCTION
    boundary and skip test files. The hash sink rule (DRF-1158) does
    not: its defect lived in a fixture.

    Returns ``(violations, satisfied_baseline_keys)`` — the second
    element lets the caller detect stale baseline entries across all
    four baselines.
    """
    try:
        rel_posix = file_path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return [], set()

    if _is_generated(rel_posix):
        return [], set()
    # Test files are out of scope for the production-boundary rules but
    # IN scope for DRF-1158 — see _SKIP_DIR_PARTS.
    production_scope = not _is_test_file(rel_posix)

    violations: list[Violation] = []
    satisfied: set[BaselineKey] = set()

    applicable = (
        [
            c
            for c in contracts
            if any(rel_posix.startswith(p) for p in c.source_prefixes)
            and not any(rel_posix.startswith(e) for e in c.exclude_prefixes)
        ]
        if production_scope
        else []
    )
    # The catalog rule applies everywhere EXCEPT the sanctioned carve-out.
    catalog_applies = production_scope and not rel_posix.startswith(marketplace_prefix)
    row_lock_applies = production_scope
    # No early return: DRF-1158 applies to every non-generated file, so
    # every such file is parsed.

    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    except (OSError, SyntaxError):
        # Broken syntax / unreadable is ruff's job, not ours.
        return [], set()

    # De-dupe identical (contract, root, scope) crossings on the same line.
    seen: set[tuple[str, str, str, int]] = set()

    # ── Import-edge contracts (G-series) ──────────────────────────────
    if applicable:
        collector = _ImportCollector(_package_parts(rel_posix))
        collector.visit(tree)
        for edge in collector.edges:
            for contract in applicable:
                root = _matched_root(edge.module, contract.forbidden_modules)
                if root is None:
                    continue
                key: BaselineKey = (contract.id, rel_posix, edge.qualname, root)
                if key in baseline:
                    satisfied.add(key)
                    continue
                dedupe = (contract.id, root, edge.qualname, edge.lineno)
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                where = (
                    "at module level"
                    if edge.qualname == MODULE_QUALNAME
                    else f"in {edge.qualname}()"
                )
                violations.append(
                    Violation(
                        file=file_path,
                        lineno=edge.lineno,
                        col_offset=edge.col_offset,
                        message=(
                            f"[{contract.id}] imports {edge.module!r} {where} — "
                            f"{contract.message} (tracked {contract.issue})"
                        ),
                        key=key,
                    )
                )

    # ── Cross-tenant catalog-read rule (MKT1) ─────────────────────────
    if catalog_applies:
        cat_visitor = _CatalogAllTenantsVisitor(catalog_models, CATALOG_CROSS_TENANT_MANAGER)
        cat_visitor.visit(tree)
        # This rule is file-granular BY DESIGN (one violation per file),
        # so its key carries the synthetic FILE_QUALNAME rather than the
        # enclosing scope. Only the import-edge contracts got the
        # qualname split (DRF-1157).
        cat_key: BaselineKey = (
            CATALOG_CROSS_TENANT_CONTRACT_ID,
            rel_posix,
            FILE_QUALNAME,
            _CATALOG_ROOT,
        )
        if cat_visitor.hits and cat_key in catalog_baseline:
            satisfied.add(cat_key)
        else:
            # One violation per file is enough — point at the first hit.
            for hit in cat_visitor.hits[:1]:
                violations.append(
                    Violation(
                        file=file_path,
                        lineno=hit.lineno,
                        col_offset=hit.col_offset,
                        message=(
                            f"[{CATALOG_CROSS_TENANT_CONTRACT_ID}] {hit.module} — cross-tenant "
                            "catalog read outside apps/marketplace/. Route discovery through "
                            "apps.marketplace.discovery (the sole sanctioned all_tenants carve-out, "
                            f"tracked {CATALOG_CROSS_TENANT_ISSUE})."
                        ),
                        key=cat_key,
                    )
                )

    # ── Shape rules (DRF-1130 row-lock join, DRF-1158 hash sink) ──────
    def _run_shape_rule(
        rule: ShapeRule,
        visitor: _ScopedVisitor,
        rule_baseline: frozenset[BaselineKey],
    ) -> None:
        visitor.visit(tree)
        for hit in visitor.hits:  # type: ignore[attr-defined]
            key: BaselineKey = (rule.id, rel_posix, hit.qualname, rule.root)
            if key in rule_baseline:
                satisfied.add(key)
                continue
            where = "at module level" if hit.qualname == MODULE_QUALNAME else f"in {hit.qualname}()"
            violations.append(
                Violation(
                    file=file_path,
                    lineno=hit.lineno,
                    col_offset=hit.col_offset,
                    message=(
                        f"[{rule.id}] {hit.module} {where} — {rule.message} (tracked {rule.issue})"
                    ),
                    key=key,
                )
            )

    if row_lock_applies:
        _run_shape_rule(ROW_LOCK_JOIN_RULE, _RowLockJoinVisitor(), row_lock_baseline)
    # DRF-1158 runs on production AND test files, deliberately.
    _run_shape_rule(HASH_SINK_RULE, _HashSinkVisitor(), hash_baseline)

    return violations, satisfied


def scan_paths(
    paths: list[Path],
    repo_root: Path,
    *,
    contracts: tuple[Contract, ...] = CONTRACTS,
    baseline: frozenset[BaselineKey] = BASELINE,
    catalog_baseline: frozenset[BaselineKey] = CATALOG_CROSS_TENANT_BASELINE,
    row_lock_baseline: frozenset[BaselineKey] = ROW_LOCK_JOIN_BASELINE,
    hash_baseline: frozenset[BaselineKey] = HASH_SINK_BASELINE,
) -> list[Violation]:
    """Scan files/directories, returning new-edge / cross-tenant-read
    violations + stale-baseline violations (a baseline entry that no longer
    matches any crossing), across both the import-edge ``baseline`` and the
    catalog ``catalog_baseline``.

    Stale-baseline reporting is restricted to baselined files that were
    actually visited in this scan: on a partial scan (e.g. one subtree) a
    baselined file outside the scanned paths is simply unknown, NOT stale —
    otherwise a per-subtree invocation would spuriously demand deletion of
    live baseline entries and silently weaken enforcement.
    """
    violations: list[Violation] = []
    satisfied: set[BaselineKey] = set()
    scanned_rel: set[str] = set()

    for path in paths:
        files = [path] if path.is_file() else sorted(path.rglob("*.py"))
        for py_file in files:
            try:
                scanned_rel.add(py_file.resolve().relative_to(repo_root.resolve()).as_posix())
            except ValueError:
                pass
            v, s = evaluate_file(
                py_file,
                repo_root,
                contracts=contracts,
                baseline=baseline,
                catalog_baseline=catalog_baseline,
                row_lock_baseline=row_lock_baseline,
                hash_baseline=hash_baseline,
            )
            violations.extend(v)
            satisfied |= s

    all_baselines = baseline | catalog_baseline | row_lock_baseline | hash_baseline
    for key in sorted(all_baselines - satisfied):
        contract_id, rel_posix, qualname, root = key
        if rel_posix not in scanned_rel:
            continue  # file outside the scanned paths — can't judge staleness
        scope = "" if qualname in (MODULE_QUALNAME, FILE_QUALNAME) else f" ({qualname})"
        # The verdict travels with the entry: whoever deletes the line
        # gets told what it claimed, instead of inferring it from prose
        # sixty lines above the frozenset (DRF-1159).
        note = BASELINE_NOTES.get(key)
        verdict = f" It was annotated {note.status}: {note.text}" if note else ""
        violations.append(
            Violation(
                file=repo_root / rel_posix,
                lineno=0,
                col_offset=0,
                message=(
                    f"[{contract_id}] STALE BASELINE — {rel_posix}{scope} no longer matches "
                    f"{root!r}. The debt was fixed/migrated (or the enclosing function was "
                    "renamed/moved); delete or update this line in the baseline in "
                    f"tools/lint/import_boundaries.py.{verdict}"
                ),
                key=key,
            )
        )
    return violations


def _detect_repo_root(start: Path) -> Path:
    """Walk upward from `start` to the dir holding `apps/` + `pyproject.toml`."""
    current = start.resolve() if start.is_absolute() else (Path.cwd() / start).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "apps").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    return current


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: import_boundaries.py <path> [<path> ...]\n"
            "       import_boundaries.py --baseline-report",
            file=sys.stderr,
        )
        return 2

    if argv[1] == "--baseline-report":
        for line in baseline_report():
            print(line)
        return 0

    repo_root = _detect_repo_root(Path(argv[1]))
    targets: list[Path] = []
    for arg in argv[1:]:
        target = Path(arg)
        if not target.exists():
            print(f"import_boundaries: path does not exist: {target}", file=sys.stderr)
            continue
        targets.append(target)

    violations = scan_paths(targets, repo_root)
    if not violations:
        return 0

    for v in violations:
        print(v.format())
    print(
        f"\nimport_boundaries: {len(violations)} boundary violation(s) detected. "
        "See tools/lint/import_boundaries.py for the G-series contracts.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
