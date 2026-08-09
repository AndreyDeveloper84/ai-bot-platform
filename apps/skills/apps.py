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

        # Sprint 9 / P5 (DRF-822) — food_correction owns
        # cb:food:correct:{field}:{scan_id} (3 fields). Below
        # food_scanner so the cb:food:* family is grouped.
        from apps.skills.food_correction import skill as _food_correction  # noqa: F401

        # Sprint 9 / P6 (DRF-823) — cross_domain owns the Ayla insight
        # card callbacks cb:cross:{seen,dismiss,convert}:{shown_id}.
        # Insight-card emission (post-food-log hook) lands Phase 1.
        from apps.skills.cross_domain import skill as _cross_domain  # noqa: F401

        # Sprint 9 / P3 (DRF-820) — nutrition_anketa: 5-step FSM via D3,
        # POSTs to Ayla upsert_profile on completion. Claims plain text
        # AND cb:anketa:choice:* while an FSM is in flight (resume
        # path). Order: above echo so resumed turns aren't swallowed.
        from apps.skills.nutrition_anketa import skill as _anketa  # noqa: F401

        # Sprint 9 / P4 (DRF-821) — food_clarify runs BEFORE faq so the
        # DRF-358 fallback card catches "Борщ 300г" before the LLM
        # gives a cold "не могу с заказом". Cheap regex, no network.
        from apps.skills.food_clarify import skill as _food_clarify  # noqa: F401

        # DRF-963 — HelpSkill MUST precede faq. FAQ's keyword fallback
        # claims anything question-shaped (a bare «?» is a signal), so
        # «что ты умеешь?» became a KB lookup: two LLM calls to answer a
        # question about the bot itself, and an operator handoff when the
        # LLM is down. HelpSkill matches a CLOSED set of whole-message
        # phrases, so the override can't swallow real salon questions.
        # HelpSkill lives in its own module (apps.skills.menu.help_skill)
        # precisely so this import does NOT also register its sibling
        # MenuSkill — @register fires per module, and MenuSkill must land
        # last, just before echo (see the bottom of this method).
        from apps.skills.menu import help_skill as _help  # noqa: F401
        from apps.skills.faq import skill as _faq  # noqa: F401

        # Phase 1 / B3 (DRF-839) — booking skill: LLM-tool-use flow over
        # 4 YClients-backed tools. Registers AFTER faq because the FAQ
        # keyword fallback would otherwise swallow questions like
        # "сколько стоит запись" before the booking intent classifier
        # gets a chance. NOTE: production webhook dispatch does NOT set
        # ctx.intent (unlike the orchestrator pipeline), so the legacy
        # keyword fallbacks drive live routing and this order is load-
        # bearing. E2E-BOT-02A made the order safe for personal booking
        # lookups: faq yields them (apps/skills/booking/lookup.py) and
        # booking claims them explicitly below the intent gate.
        from apps.skills.booking import skill as _booking  # noqa: F401

        # D-10 (2026-08-04) — booking gate + reminder callback skills
        # MUST register BEFORE echo. They live in ``apps.bookings``,
        # whose ``ready()`` runs AFTER ``apps.skills`` (INSTALLED_APPS
        # order) — so their natural registration point used to append
        # them after the always-matching echo and ``cb:book:confirm:*``
        # / ``cb:rem:*`` taps (and the D-10 text-confirmation path)
        # were echo-claimed in production dispatch. Importing here
        # puts them in their correct slot; ``apps.bookings.apps``
        # keeps its own import as a no-op fallback (module cache).
        from apps.bookings import callbacks as _booking_callbacks  # noqa: F401

        # 2026-05-20 — welcome skill ports the /start greeting into a
        # dedicated handler that emits an inline keyboard with Mini App
        # quick-actions (📅 Записаться / 📋 Мои записи / 👤 Профиль / ❓ Задать
        # вопрос / ❓ Помощь). Registered BEFORE echo so /start lands here; the
        # cb:welcome:* callbacks route the «❓ Задать вопрос» tap to a
        # helpful prompt rather than verbatim echo. Restores the inline-
        # keyboard UX that mysite's MAX SDK shipped pre-platform-cutover.
        from apps.skills.welcome import skill as _welcome  # noqa: F401

        # #738 (Round-2 P-1) — payment_failed skill owns ``cb:payment:
        # retry:<payment_id>`` button taps shipped by the threshold-
        # gated DM (``apps/eventbus/consumers/payment.py::handle_payment_
        # failed`` → ``on_payment_failed_event`` direct call). The
        # eventbus consumer lazy-imports the entry-point function, so
        # WITHOUT this eager import here the ``@register`` decorator
        # never fires on workers that haven't tripped a threshold
        # themselves — leaving the inline button dead (echo-claimed).
        # Registered BEFORE echo because echo always matches.
        from apps.skills.payment_failed import skill as _payment_failed  # noqa: F401

        # DRF-963 (Wave 1, variant A) — menu / honest-fallback skill. MUST be
        # the LAST registration before echo: it claims every non-empty text
        # turn, so anything it sees is a turn no other skill wanted — i.e. a
        # turn that used to be echoed back verbatim (findings U-1 / U-5).
        # Registering it here makes the widened booking coverage strictly
        # additive: it cannot take a turn away from booking, FAQ or any
        # wellness skill, which is what keeps CG-1..CG-8 regression-free.
        # Echo stays registered after it and keeps the empty-text /
        # attachment-only replies. (Its sibling HelpSkill was registered
        # far earlier, before faq — separate module, separate position.)
        from apps.skills.menu import skill as _menu  # noqa: F401

        from apps.skills.echo import skill as _echo  # noqa: F401
