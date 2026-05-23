"""AppConfig for the master_api package.

Wires the production cache-backend boot assertion (issue #552). The
helper itself lives in :mod:`config.settings.base` next to the CACHES
config it guards; this ``ready()`` only invokes it at Django app-load
time so a misconfigured production boot crashes loudly before the
first request lands.
"""

from __future__ import annotations

from django.apps import AppConfig


class MasterApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.master_api"

    def ready(self) -> None:
        # Issue #552 — fail-fast guard for the Django cache backend.
        #
        # Lives here (master_api) because this is the first-party app
        # that consumes the cache for cross-worker atomic operations
        # (PR #540 rate limiter + PR #535 follow-ups). The assertion
        # body lives in config.settings.base alongside the CACHES
        # configuration it guards — see _assert_production_cache_backend
        # docstring for the rationale.
        from django.conf import settings

        from config.settings.base import _assert_production_cache_backend

        _assert_production_cache_backend(
            debug=bool(settings.DEBUG),
            caches=dict(settings.CACHES),
        )
