from django.apps import AppConfig


class ChannelsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.channels"

    def ready(self) -> None:
        # Import handlers module to register all channels with the
        # workers registry — `@register("ingress:<channel>")` runs
        # on import. Sprint 2 ships only `max`; Sprint 3+ siblings
        # land alongside their channel adapter modules.
        from apps.channels import handlers  # noqa: F401 — registration side effect
