"""AppConfig for the admin_api package.

``ready()`` registers the ``SITE_DOMAIN`` deploy guard (DRF-1079); the
check body lives in :mod:`apps.admin_api.checks` next to its rationale.
"""

from django.apps import AppConfig


class AdminApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.admin_api"

    def ready(self) -> None:
        from django.core.checks import register

        from apps.admin_api.checks import check_site_domain

        register(check_site_domain)
