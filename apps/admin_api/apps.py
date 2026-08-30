"""AppConfig for the admin_api package.

``ready()`` registers the master-invite deploy guards — ``SITE_DOMAIN``
(DRF-1079) and ``MAX_BOT_WEB_APP`` (DRF-1349); the check bodies live in
:mod:`apps.admin_api.checks` next to their rationale.
"""

from django.apps import AppConfig


class AdminApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.admin_api"

    def ready(self) -> None:
        from django.core.checks import register

        from apps.admin_api.checks import check_bot_web_app, check_site_domain

        register(check_site_domain)
        register(check_bot_web_app)
