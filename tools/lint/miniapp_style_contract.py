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


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: miniapp_style_contract.py <apps/miniapp>", file=sys.stderr)
        return 2

    app_root = Path(argv[1])
    if not (app_root / STYLES_DIR).is_dir():
        print(f"miniapp_style_contract: not a Mini App root: {app_root}", file=sys.stderr)
        return 2

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

    if new_debt or stale:
        print(
            f"\nminiapp_style_contract: {len(new_debt)} unstyled class(es), "
            f"{len(stale)} stale baseline entr(ies). "
            "A class name with no rule renders as nothing — that is DRF-1066.",
            file=sys.stderr,
        )
        return 1

    print(f"miniapp_style_contract: clean ({len(BASELINE)} accepted, none new).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
