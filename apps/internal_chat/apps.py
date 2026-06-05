from django.apps import AppConfig


class InternalChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.internal_chat"
    verbose_name = "Master-Admin internal chat"
