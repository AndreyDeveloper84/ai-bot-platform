from django.apps import AppConfig


class PersonaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.persona"
    verbose_name = "Служебное: характер бота"
