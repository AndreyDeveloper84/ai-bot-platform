"""Django AppConfig for the observability package."""

from __future__ import annotations

from django.apps import AppConfig


class ObservabilityConfig(AppConfig):
    name = "apps.observability"
    label = "observability"
    verbose_name = "Observability"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """Register the DRF-1391 config-drift guards and report them once.

        Registration makes them part of `manage.py check` / `migrate`
        (so CI and the deploy log see them); the direct call covers
        `uvicorn config.asgi:application`, which never runs system
        checks and is exactly the process the pilot's `web` service is.
        """

        from django.core.checks import register

        from apps.observability.checks import (
            check_allowed_hosts_not_wildcard,
            check_env_file_drift,
            log_startup_config_drift,
        )

        register(check_env_file_drift)
        register(check_allowed_hosts_not_wildcard)
        log_startup_config_drift()
