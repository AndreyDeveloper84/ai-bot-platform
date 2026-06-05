from django.apps import AppConfig
from django.core.signals import setting_changed


class EventsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.events"

    def ready(self) -> None:
        from apps.events.fanout import reset_registry_cache

        # Hotfix C (retro review #3): EVENT_FANOUTS registry is cached
        # after first resolution. Wire setting_changed signal so tests
        # using @override_settings(EVENT_FANOUTS=...) and prod settings-
        # reload flows pick up the new value without manual cache reset.
        def _on_setting_changed(sender, setting, **kwargs):  # noqa: ANN001
            if setting == "EVENT_FANOUTS":
                reset_registry_cache()

        setting_changed.connect(
            _on_setting_changed,
            dispatch_uid="apps.events.reset_fanout_registry_on_settings_change",
        )
