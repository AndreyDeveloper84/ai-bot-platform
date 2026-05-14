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

        # Sprint 9 / P7 (DRF-824) — health_screening MUST precede every
        # service-suggesting skill so "болит спина" gets a diagnostic
        # question rather than a cold "вот наши услуги". Red-flag
        # symptoms ("онемение" / "не могу встать") get redirected to a
        # doctor. The DRF-358 T04 fix; voice mirrors D1 examples.
        from apps.skills.health_screening import skill as _health  # noqa: F401

        # Sprint 9 / P2 (DRF-819) — water fast-lane MUST precede
        # food_clarify so "стакан воды" lands an Ayla log instead of
        # the diary-or-typo card. Both are cheap regex; water wins on
        # confident parse.
        from apps.skills.water import skill as _water  # noqa: F401

        # Sprint 9 / P1 (DRF-818) — food_scanner owns photo turns + the
        # cb:food:{to_diary,clarify,reject}:{scan_id} callbacks. Order
        # vs water: water only matches short non-attachment text, so
        # the order between them is moot, but we keep food_scanner here
        # for grouping. Above food_clarify because cb:food:to_diary:*
        # (P1) must not be claimed by cb:food:diary (P4) — the parsers
        # are distinct callbacks so collision is structural-not-textual.
        from apps.skills.food_scanner import skill as _food_scanner  # noqa: F401

        # Sprint 9 / P4 (DRF-821) — food_clarify runs BEFORE faq so the
        # DRF-358 fallback card catches "Борщ 300г" before the LLM
        # gives a cold "не могу с заказом". Cheap regex, no network.
        from apps.skills.food_clarify import skill as _food_clarify  # noqa: F401
        from apps.skills.faq import skill as _faq  # noqa: F401
        from apps.skills.echo import skill as _echo  # noqa: F401
