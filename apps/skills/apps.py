from django.apps import AppConfig


class SkillsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.skills"

    def ready(self) -> None:
        # Importing each skill module fires the `@register` decorator
        # so the registry is populated on Django boot. New skills are
        # added to this list as they ship — Sprint 3 / D1 ships echo
        # only; D2 + D3 add privacy_consent + human_handoff.
        from apps.skills.echo import skill as _echo  # noqa: F401
