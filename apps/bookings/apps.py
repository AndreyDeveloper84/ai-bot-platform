"""AppConfig for the bookings (reminder logic) app.

``ready()`` imports the callback skill module so its
:func:`apps.skills.registry.register` decorator fires at Django boot,
mirroring the pattern used by :class:`apps.skills.apps.SkillsConfig`.
The callback skill registers AFTER the booking-skill (B3) so the
``cb:rem:*`` family is owned by exactly one matcher — there's no overlap
between the two namespaces (``cb:rem:`` vs ``cb:`` general), so order
is correctness-by-construction here.
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
