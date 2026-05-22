from django.apps import AppConfig
from django.core.signals import setting_changed


class EventBusConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.eventbus"
    verbose_name = "Domain event bus"

    def ready(self) -> None:
        from apps.eventbus import signals  # noqa: F401  — register post_save handlers
        from apps.eventbus.dispatcher import reset_registry_cache
        from apps.eventbus.startup_checks import warn_if_atomic_requests_true

        # PR #507 adversarial pass A5: log a startup WARNING (NOT
        # ImproperlyConfigured — too disruptive for shared-config
        # deploys) if ATOMIC_REQUESTS=True is enabled for the default
        # database. Under that setting an exception bubbling up from
        # the ingest view would roll back the audit + DLQ rows we
        # need for ops triage. Current view code catches exceptions
        # inside dispatch_envelope and returns 500 cleanly (no
        # exception escapes), so the present implementation is safe;
        # the warning is forward-defense.
        warn_if_atomic_requests_true()

        # Hotfix C (retro review #3): the subscriber registry is cached
        # after first resolution per :func:`_subscribers`. Wire the
        # Django ``setting_changed`` signal so:
        #   - `@override_settings(DOMAIN_EVENT_SUBSCRIBERS=...)` in tests
        #     transparently picks up the new value
        #   - prod admin-reload flows (e.g. SIGHUP-style settings refresh)
        #     don't return a stale registry
        # Receiver filters on the setting name to avoid clearing the
        # cache on unrelated settings flips (LRU-style guard).
        def _on_setting_changed(sender, setting, **kwargs):  # noqa: ANN001
            if setting == "DOMAIN_EVENT_SUBSCRIBERS":
                reset_registry_cache()

        setting_changed.connect(
            _on_setting_changed,
            dispatch_uid="apps.eventbus.reset_registry_on_settings_change",
        )
