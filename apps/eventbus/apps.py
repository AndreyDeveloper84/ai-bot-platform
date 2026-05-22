from django.apps import AppConfig
from django.core.signals import setting_changed


class EventBusConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.eventbus"
    verbose_name = "Domain event bus"

    def ready(self) -> None:
        from apps.eventbus import signals  # noqa: F401  — register post_save handlers
        from apps.eventbus.dispatcher import reset_registry_cache
        from apps.eventbus.ingest_ip import warn_if_proxy_trust_unconfigured
        from apps.eventbus.startup_checks import warn_if_atomic_requests_true

        # PR #507 adversarial pass A5: log a startup WARNING (NOT
        # ImproperlyConfigured — too disruptive for shared-config
        # deploys) if ATOMIC_REQUESTS=True is enabled for the default
        # database.
        warn_if_atomic_requests_true()

        # Round-2 adversarial pass AS2: log a startup WARNING if the
        # proxy-trust configuration risks XFF spoofing or single-bucket
        # collapse behind a proxy. See apps/eventbus/ingest_ip.py.
        warn_if_proxy_trust_unconfigured()

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
