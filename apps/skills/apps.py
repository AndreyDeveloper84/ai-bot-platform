from django.apps import AppConfig


class SkillsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.skills"

    def ready(self) -> None:
        # Importing each skill module fires the `@register` decorator
        # so the registry is populated on Django boot. ORDER MATTERS —
        # the dispatcher walks the registry in declaration order and
        # the first matches() returning True wins. Echo MUST be last
        # because it always matches.
        from apps.skills.privacy_consent import skill as _privacy  # noqa: F401
        from apps.skills.human_handoff import skill as _handoff  # noqa: F401

        # Sprint 9 / P4 (DRF-821) — food_clarify runs BEFORE faq so the
        # DRF-358 fallback card catches "Борщ 300г" before the LLM
        # gives a cold "не могу с заказом". Cheap regex, no network.
        from apps.skills.food_clarify import skill as _food_clarify  # noqa: F401
        from apps.skills.faq import skill as _faq  # noqa: F401
        from apps.skills.echo import skill as _echo  # noqa: F401
