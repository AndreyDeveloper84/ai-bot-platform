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
        from apps.eventbus.startup_checks import (
            warn_if_atomic_requests_true,
            warn_if_event_ingest_hmac_missing,
            warn_if_tenant_verify_fail_open,
        )

        # PR #507 adversarial pass A5: log a startup WARNING (NOT
        # ImproperlyConfigured — too disruptive for shared-config
        # deploys) if ATOMIC_REQUESTS=True is enabled for the default
        # database.
        warn_if_atomic_requests_true()

        # Round-2 adversarial pass AS2: log a startup WARNING if the
        # proxy-trust configuration risks XFF spoofing or single-bucket
        # collapse behind a proxy. See apps/eventbus/ingest_ip.py.
        warn_if_proxy_trust_unconfigured()

        # Round-3 NEW-5: log a startup WARNING if the tenant-verify
        # fail-open flag is set. Surfaces the pre-#246 exposure
        # window in the ops dashboard at first deploy.
        warn_if_tenant_verify_fail_open()

        # O1/S4 — log a startup WARNING when the ingest HMAC secret is
        # empty (every envelope would be rejected with 401 no_secret).
        warn_if_event_ingest_hmac_missing()

        # #442 — Register booking.* consumer family handlers with the
        # ingest dispatcher. Each consumer module exposes a
        # ``register_*_handlers`` function that's called here so the
        # registry shape is deterministic at every Django start.
        from apps.eventbus.consumers.booking import register_booking_handlers

        register_booking_handlers()

        # #443 — Register payment.* consumer family handlers.
        from apps.eventbus.consumers.payment import register_payment_handlers

        register_payment_handlers()

        # #444 — Register catalog (service.updated) consumer.
        from apps.eventbus.consumers.catalog import register_catalog_handlers

        register_catalog_handlers()

        # #446 — Register identity (user.profile.updated) consumer.
        from apps.eventbus.consumers.identity import register_identity_handlers

        register_identity_handlers()

        # #445 — Register schedule (master.schedule.updated) consumer.
        from apps.eventbus.consumers.schedule import register_schedule_handlers

        register_schedule_handlers()

        # #445 — Register reviews (review.created) consumer.
        from apps.eventbus.consumers.reviews import register_reviews_handlers

        register_reviews_handlers()

        # C4 (pilot 2026-08-15) — Register billing consumer family
        # (subscription.activated / subscription.past_due /
        # billing.fee_charged).
        from apps.eventbus.consumers.billing import register_billing_handlers

        register_billing_handlers()

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
