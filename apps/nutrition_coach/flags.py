"""Runtime readers for the two coach switches (DRF-1464, T1).

The flags themselves live in ``config/settings/base.py`` next to the
``NUTRITION_PROACTIVE_*`` block, with the ramp commentary. Read them
only through here, at call time — never import-time — so
``override_settings`` in tests and an operator env change both take
effect without a reload.
"""

from __future__ import annotations

from django.conf import settings


def enabled() -> bool:
    """Master switch. False — every coach surface stays silent."""
    return bool(getattr(settings, "NUTRITION_COACH_ENABLED", False))


def dry_run() -> bool:
    """True unless an operator has explicitly turned the safety off.

    The last switch to open, per the ramp in ``config/settings/base.py``:
    dry-run is switched off only after the dry-run logs have been read
    against the expected behaviour.
    """
    return bool(getattr(settings, "NUTRITION_COACH_DRY_RUN", True))
