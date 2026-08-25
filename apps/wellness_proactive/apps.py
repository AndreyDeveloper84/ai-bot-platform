from django.apps import AppConfig


class WellnessProactiveConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.wellness_proactive"
    label = "wellness_proactive"
    verbose_name = "Proactive wellness layer (Personal Plan occasions)"
