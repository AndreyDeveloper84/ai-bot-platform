"""AppConfig for the bookings (reminder logic) app.

``ready()`` imports the callback skill module so its
:func:`apps.skills.registry.register` decorator fires at Django boot,
mirroring the pattern used by :class:`apps.skills.apps.SkillsConfig`.

D-10 note (2026-08-04): the canonical registration point moved to
``SkillsConfig.ready()`` (imported right after the booking skill,
BEFORE echo) — this app's ``ready()`` runs later in INSTALLED_APPS
order, which used to leave both callback skills behind the
always-matching echo and therefore unreachable via production
dispatch. The import below is kept as a no-op fallback for boot
paths where ``apps.skills`` is not installed (module cache makes the
double import free); when both apps load normally, registration
happens inside ``SkillsConfig.ready()``.
"""

from __future__ import annotations

from django.apps import AppConfig


class BookingsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.bookings"

    def ready(self) -> None:
        # Import-for-side-effect: register the cb:rem:* skill with the
        # platform skill registry. Mirrors apps.skills.apps.SkillsConfig.
        from apps.bookings import callbacks as _callbacks  # noqa: F401
