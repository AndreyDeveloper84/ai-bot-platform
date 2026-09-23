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

    test_mode = bool(getattr(settings, "AYLA_PAYMENTS_TEST_MODE", True))
    mode = "test" if test_mode else "live"
    hint = (
        "Stub checkout links: nobody can actually pay. Deliberate on a "
        "stand; in production the settings module refuses to boot without "
        "an explicit AYLA_PAYMENTS_TEST_MODE."
        if test_mode
        else "Real checkout links: taps take money."
    )
    return [
        CheckWarning(
            f"Payments mode: {mode}.",
            hint=hint,
            id="payments.W001",
        )
    ]
