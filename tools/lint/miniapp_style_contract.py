#!/usr/bin/env python3
"""Fail when a Mini App screen renders a class name no stylesheet defines.

DRF-1066. On 2026-08-14 a person went through the booking funnel and the
payment block came out as one run-on line::

    Оплатить на местеНаличными или картой в салоне

Nothing was wrong with the markup and nothing was wrong with the CSS. The
five ``customer-confirm__payment*`` class names were written in
``CustomerBookingConfirmScreen.tsx`` and never given a rule, so both
``<span>`` elements stayed inline and the label ran straight into its
hint. A class name that does not exist in a stylesheet is silent: React
renders it, the browser ignores it, every test passes, and the defect is
visible only to a person looking at the screen.

Measuring the tree for that shape on 2026-08-24 found the same thing in
**21 files, 58 class names** -- so this is a class of defect, not an
incident. Those 58 are frozen in ``BASELINE`` below rather than fixed
here: some are genuinely inert containers, others are real and need a
designer's eye, and quietly "fixing" 58 layouts inside a bug-fix change
would be worse than naming them.

# Why this is a lint and not a front-end test

``apps/miniapp`` runs vitest with ``css: false``, so a stylesheet
imported inside a test comes back empty and any assertion over it passes
vacuously. jsdom does not lay text out either, so even with CSS enabled a
DOM test could not see two spans running together. The check has to read
the two files as text, which is what this does.

# What it detects

For every non-test ``.tsx`` under ``apps/miniapp/src``, every class name
appearing in a **static** ``className="..."`` literal must appear as
``.<name>`` somewhere in ``apps/miniapp/src/styles/``.

Deliberately NOT detected: class names built at runtime
(``className={cx(...)}``, template literals). Those are invisible to a
text scan, and pretending otherwise would make this guard's silence mean
more than it does.

Also not detected: a rule that exists but is wrong. This guard answers
"does this name resolve to anything at all", which is exactly the
question the DRF-1066 defect failed.

# Second check: bottom tab bars must fit on one row

A bottom tab bar is a fixed-position CSS grid with a declared column
count. Render more tabs into it than it has columns and the extras wrap
onto a second row -- and because the bar is pinned to the bottom, that
row grows upward over the screen's content.

``AdminTabBar`` did exactly this: five tabs rendered into
``.master-tabbar``, a four-column grid sized for ``MasterTabBar``'s
four. The wrapped row is visible on all five owner screenshots of
2026-08-25.

This lives here for the same reason as the check above: vitest cannot
see the stylesheet, and jsdom does not lay a grid out even when it can.
So it is read as text -- count the tab entries the component declares,
read ``repeat(N, 1fr)`` off the class its ``<nav>`` renders, and require
the two numbers to be equal.

# The baseline ratchets down

A name in ``BASELINE`` that is no longer unstyled is itself a failure:
the entry has to be deleted when the rule is written, so accepted debt
can only shrink. Same mechanic as ``tools/lint/import_boundaries.py``.

Usage::

    python tools/lint/miniapp_style_contract.py apps/miniapp

Exit codes: 0 clean, 1 violations, 2 bad invocation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CLASS_ATTR = re.compile(r'className="([^"{}]+)"')

# `<nav ... className="a b">` -- the tab bar's own element, tolerating
# either attribute order and a className pushed onto its own line.
NAV_CLASS_ATTR = re.compile(r'<nav\b[^>]*?className="([^"{}]+)"', re.DOTALL)
# One entry of a `TabSpec[]` literal: the `to: "/admin/day"` route.
# Not anchored to line start -- some specs are written on one line.
# `to: string` in the interface declaration has no quote and so does
# not match; the lookbehind keeps property-suffix hits out.
TAB_ENTRY = re.compile(r'(?<![A-Za-z0-9_$.])to:\s*"')
# A single-class rule declaring an explicit, repeated column count.
GRID_RULE = re.compile(
    r"\.([A-Za-z0-9_-]+)\s*\{[^}]*?grid-template-columns:\s*"
    r"repeat\(\s*(\d+)\s*,",
    re.DOTALL,
)

# Components whose `<nav>` is a tab-bar grid. The check is exactly as
# wide as this tuple -- add a bar here when you add one.
TABBAR_COMPONENTS = (
    "src/components/AdminTabBar.tsx",
    "src/components/MasterTabBar.tsx",
)

STYLES_DIR = Path("src/styles")
SOURCE_DIR = Path("src")


# Accepted debt, measured on origin/dev at 647d70c (2026-08-24).
# "<file relative to apps/miniapp>::<class>". Delete an entry when the
# rule is written -- a stale entry fails this guard on purpose.
BASELINE: frozenset[str] = frozenset(
    {
        "src/components/MasterCard.tsx::master-card__body",
        "src/components/Snackbar.tsx::snackbar",
        "src/components/SurfaceSwitch.tsx::surface-switch",
        "src/screens/CustomerBookingDetailScreen.tsx::modal__sheet",
        "src/screens/CustomerBookingSuccessScreen.tsx::customer-success__payment-note",
        "src/screens/CustomerCardsScreen.tsx::profile-cards__brand",
        "src/screens/CustomerCardsScreen.tsx::profile-cards__consent",
        "src/screens/CustomerCardsScreen.tsx::profile-cards__item",
        "src/screens/CustomerCardsScreen.tsx::profile-cards__last4",
        "src/screens/CustomerCardsScreen.tsx::profile-cards__list",
        "src/screens/CustomerCardsScreen.tsx::profile-cards__revoke-confirm",
        "src/screens/CustomerNotificationSettingsScreen.tsx::profile-notifications__prefs",
        "src/screens/MasterBillingScreen.tsx::profile-billing__payout-sum",
        "src/screens/MasterBillingScreen.tsx::profile-billing__status-line",
        "src/screens/MasterBillingScreen.tsx::profile-cards__brand",
        "src/screens/MasterBillingScreen.tsx::profile-cards__consent",
        "src/screens/MasterBillingScreen.tsx::profile-cards__item",
        "src/screens/MasterBillingScreen.tsx::profile-cards__last4",
        "src/screens/MasterBillingScreen.tsx::profile-payout__item",
        "src/screens/MasterBillingScreen.tsx::profile-payout__item-amount",
        "src/screens/MasterBillingScreen.tsx::profile-payout__item-meta",
        "src/screens/MasterBillingScreen.tsx::profile-payout__item-state",
        "src/screens/MasterBillingScreen.tsx::profile-payout__list",
        "src/screens/MasterConversationsScreen.tsx::m-card--inbox",
        "src/screens/MasterCustomersScreen.tsx::master-customers__body",
        "src/screens/MasterDashboardScreen.tsx::m-card--inbox",
        "src/screens/MasterInternalChatThreadScreen.tsx::internal-chat-bubble__stamp",
        "src/screens/MasterServicesScreen.tsx::master-services__body",
        "src/screens/MasterSettingsScreen.tsx::master-settings",
        "src/screens/MasterSettingsScreen.tsx::master-settings__coming-soon",
        "src/screens/MyVisitDetailScreen.tsx::modal__sheet",
        "src/screens/admin/AdminAvailabilityRequestsScreen.tsx::btn-link",
        "src/screens/admin/AdminAvailabilityRequestsScreen.tsx::screen__header",
        "src/screens/admin/AdminInternalChatThreadScreen.tsx::internal-chat-bubble__stamp",
        "src/screens/admin/AdminInternalChatThreadScreen.tsx::internal-chat-thread__sign-helper",
        "src/screens/admin/AdminInternalChatThreadScreen.tsx::internal-chat-thread__sign-toggle",
        "src/screens/admin/AdminNewBookingScreen.tsx::callout--warning",
        "src/screens/admin/AdminNewBookingScreen.tsx::draft-row",
        "src/screens/admin/AdminNewBookingScreen.tsx::draft-rows",
        "src/screens/admin/AdminNewBookingScreen.tsx::section__title",
        "src/screens/admin/AdminNewBookingScreen.tsx::sheet",
        "src/screens/admin/AdminNewBookingScreen.tsx::sheet__item",
        "src/screens/admin/AdminNewBookingScreen.tsx::sheet__panel",
        "src/screens/admin/AdminNewBookingScreen.tsx::sheet__title",
        "src/screens/admin/AdminSalonDayScreen.tsx::badge",
        "src/screens/admin/AdminSalonDayScreen.tsx::btn--danger",
        "src/screens/admin/AdminSalonDayScreen.tsx::btn--ghost",
        "src/screens/admin/AdminSalonDayScreen.tsx::btn--primary",
        "src/screens/admin/AdminSalonDayScreen.tsx::btn-link",
        "src/screens/admin/AdminSalonDayScreen.tsx::callout--warning",
        "src/screens/admin/AdminSalonDayScreen.tsx::muted",
        "src/screens/admin/AdminSalonDayScreen.tsx::salon-day__visit",
        "src/screens/admin/AdminSalonDayScreen.tsx::screen__header",
        "src/screens/admin/AdminSalonDayScreen.tsx::section__title",
        "src/screens/admin/AdminSalonDayScreen.tsx::sheet",
        "src/screens/admin/AdminSalonDayScreen.tsx::sheet__item",
        "src/screens/admin/AdminSettingsPlaceholderScreen.tsx::screen__header",
        "src/screens/admin/AdminTeamScreen.tsx::screen__header",
    }
)


def stylesheet_text(app_root: Path) -> str:
    """Concatenate every stylesheet the app ships, as raw text."""
    sheets = sorted((app_root / STYLES_DIR).glob("*.css"))
    if not sheets:
        raise FileNotFoundError(f"no stylesheets under {app_root / STYLES_DIR}")
    return "\n".join(sheet.read_text(encoding="utf-8") for sheet in sheets)


def used_classes(tsx: str) -> set[str]:
    """Class names from static ``className="..."`` literals only."""
    names: set[str] = set()
    for match in CLASS_ATTR.finditer(tsx):
        names.update(part for part in match.group(1).split() if part)
    return names


def scan(app_root: Path) -> list[str]:
    """Return sorted ``file::class`` keys for every unstyled class name."""
    css = stylesheet_text(app_root)
    found: list[str] = []
    for path in sorted((app_root / SOURCE_DIR).rglob("*.tsx")):
        if ".test." in path.name:
            continue
        rel = path.relative_to(app_root).as_posix()
        for name in sorted(used_classes(path.read_text(encoding="utf-8"))):
            if f".{name}" not in css:
                found.append(f"{rel}::{name}")
    return sorted(found)


def grid_columns(css: str, classes: list[str]) -> int | None:
    """Column count that wins for an element carrying ``classes``.

    Every rule matched here is a single-class selector, so they all have
    the same specificity and the last one in source order wins. That is
    why this keeps the last hit rather than the first.
    """
    winner: int | None = None
    for match in GRID_RULE.finditer(css):
        if match.group(1) in classes:
            winner = int(match.group(2))
    return winner


def scan_tabbar_columns(app_root: Path) -> list[str]:
    """Return one ``file: message`` per tab bar that cannot hold its tabs."""
    css = stylesheet_text(app_root)
    problems: list[str] = []

    for rel in TABBAR_COMPONENTS:
        path = app_root / rel
        if not path.is_file():
            # Not a defect, and deliberately not reported. `main` runs
            # against whatever root it is handed, and the unit tests hand
            # it throwaway trees that contain one screen and a stylesheet
            # and no tab bar at all. Failing on absence made every one of
            # those roots fail this check and turned an unrelated
            # baseline test red.
            #
            # A *rename* that silently disables the check is the real risk
            # here, and it is pinned where the module's docstring says
            # real-tree facts belong: `test_every_listed_tab_bar_exists`
            # in tests/tools/test_miniapp_style_contract.py asserts every
            # path in TABBAR_COMPONENTS resolves inside apps/miniapp.
            continue
        source = path.read_text(encoding="utf-8")

        nav = NAV_CLASS_ATTR.search(source)
        if nav is None:
            problems.append(
                f"{rel}: no static `<nav className=...>` -- this check reads "
                "the class list as text and cannot follow a computed one"
            )
            continue
        classes = nav.group(1).split()

        tabs = len(TAB_ENTRY.findall(source))
        if tabs == 0:
            problems.append(
                f'{rel}: found no `to: "..."` tab entries -- the tab count '
                "is read as text and cannot follow a computed list"
            )
            continue

        columns = grid_columns(css, classes)
        if columns is None:
            problems.append(
                f"{rel}: none of {classes} declares "
                "`grid-template-columns: repeat(N, ...)` in src/styles/"
            )
            continue

        if columns != tabs:
            problems.append(
                f"{rel}: renders {tabs} tabs into a {columns}-column grid "
                f"({' '.join(classes)}) -- the grid must declare exactly as "
                "many columns as the component renders tabs, or the extras "
                "wrap onto a second row and cover the screen above the bar"
            )

    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: miniapp_style_contract.py <apps/miniapp>", file=sys.stderr)
        return 2

    app_root = Path(argv[1])
    if not (app_root / STYLES_DIR).is_dir():
        print(f"miniapp_style_contract: not a Mini App root: {app_root}", file=sys.stderr)
        return 2

    tabbar_problems = scan_tabbar_columns(app_root)
    for problem in tabbar_problems:
        where, message = problem.split(": ", 1)
        print(f"::error file=apps/miniapp/{where}::{message}")

    found = set(scan(app_root))
    new_debt = sorted(found - BASELINE)
    stale = sorted(BASELINE - found)

    for key in new_debt:
        path, name = key.split("::", 1)
        print(f"::error file=apps/miniapp/{path}::class `{name}` has no rule in src/styles/")
    for key in stale:
        print(
            f"::error::BASELINE entry `{key}` is styled now — delete the line "
            "from tools/lint/miniapp_style_contract.py"
        )

    if new_debt or stale or tabbar_problems:
        print(
            f"\nminiapp_style_contract: {len(new_debt)} unstyled class(es), "
            f"{len(stale)} stale baseline entr(ies), "
            f"{len(tabbar_problems)} tab bar(s) that do not fit one row. "
            "A class name with no rule renders as nothing — that is DRF-1066. "
            "A tab bar with more tabs than columns wraps over the screen.",
            file=sys.stderr,
        )
        return 1

    checked = sum(1 for rel in TABBAR_COMPONENTS if (app_root / rel).is_file())
    print(
        f"miniapp_style_contract: clean ({len(BASELINE)} accepted, none new; "
        f"{checked} tab bar(s) fit one row)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
