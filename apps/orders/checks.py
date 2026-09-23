"""Boot-time statement: which payment mode this deployment is in (DRF-2340).

``AYLA_PAYMENTS_TEST_MODE`` used to be invisible: no settings file named it,
and the only reader was ``getattr(settings, …, True)`` inside the payments
client. A contour could hand people stub checkout URLs — «оплатил», no money
— and nothing anywhere said so.

The hard guard lives in ``config/settings/production.py``: production has no
default at all and refuses to boot without an explicit value, the same shape
as ``AYLA_INTERNAL_API_TOKEN`` / ``SENTRY_DSN``. This check is its cheap
companion for every OTHER contour: ``manage.py check`` states the mode out
loud, before a single payment is attempted.

### Warning, not Error, and deliberately so

It decides nothing. A test-mode contour is a legitimate configuration (the
stand runs that way today), so an ``Error`` would block deploys over a
correct setup. The point is that the answer stops being invisible — the
failure this ticket exists for was silence, not a wrong value.

### Only when the answer is surprising

It fires for stub links on a contour with ``DEBUG = False`` — a stand, a
staging box, anything deploy-shaped — and stays quiet in local dev and CI,
where stub links are the obvious and correct answer.

That silence is not laziness; it is the DRF-2021 decision. A warning
printed by every green run teaches readers to skip the
``System check identified`` line, and the next warning of the same shape —
a real one — goes past the same way. That ticket went as far as injecting a
fake ``MAX_BOT_WEB_APP`` into CI rather than let a correct warning become
wallpaper, and recorded that ``0 silenced`` must stay zero.

### One word, and nothing else

``manage.py check`` output is copied into deploy logs and tickets. The
message names the mode and the setting; never the base URL, never a token,
never an amount, never a person.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.checks import Warning as CheckWarning, register


@register()
def check_payments_mode_declared(app_configs: Any, **kwargs: Any) -> list[CheckWarning]:
    """payments.W001 — say whether checkout links are real or stubs."""

    # Direct read, not ``getattr`` with a default: the hidden third argument
    # is exactly what this ticket removed, and every contour declares it.
    if not settings.AYLA_PAYMENTS_TEST_MODE:
        return []
    if settings.DEBUG:
        # Local dev and CI: stub links are the obvious answer — see above.
        return []
    return [
        CheckWarning(
            "Payments mode: test.",
            hint=(
                "Stub checkout links: nobody can actually pay, on a contour "
                "that is not DEBUG. Deliberate on a stand; production has no "
                "default and refuses to boot without an explicit "
                "AYLA_PAYMENTS_TEST_MODE."
            ),
            id="payments.W001",
        )
    ]
