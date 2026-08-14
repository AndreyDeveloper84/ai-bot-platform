"""E2E-BOT-02A — personal booking-lookup routing.

Root cause (diagnosed on the production MAX path): the channel webhook
handlers dispatch with ``SkillContext(intent=None)``
(``apps/channels/max/handler.py``, ``apps/channels/telegram/handler.py``),
so skill selection falls to the legacy keyword fallbacks. The FAQ
fallback matches generic question signals ("когда", "?", ...) and is
registered BEFORE booking (``apps/skills/apps.py``) — first-match-wins
gave personal booking lookups ("Когда у меня следующая запись?") to
FAQ, and lookup phrasings without the literal "запись" substring
("Покажи мои записи", "На когда я записан?") fell through to echo.

Boundary locked by these tests:

  * personal booking lookup  → booking skill, read-only
    ``show_my_bookings``, no mutation;
  * booking-rules FAQ ("Как записаться?", "Можно ли перенести запись?")
    → faq skill (unchanged);
  * mutation requests ("Перенеси мою запись", ...) → booking skill via
    the LLM tool choice, NEVER the deterministic lookup fast path.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.booking.models import BookingRequest, PendingBookingAction
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.llm.protocol import CompletionResult, ToolCall
from apps.llm.providers.openai_provider import OpenAIProvider
from apps.llm.router import reset_router_cache
from apps.skills.base import SkillContext, SkillResult
from apps.skills.booking.lookup import is_personal_booking_lookup
from apps.skills.booking.skill import BookingSkill
from apps.skills.faq.skill import FAQSkill
from apps.skills.registry import dispatch, registered
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db(transaction=True)


LOOKUP_PHRASES: tuple[str, ...] = (
    "Когда у меня следующая запись?",
    "Когда у меня запись?",
    "Какая у меня следующая запись?",
    "Покажи мои записи",
    "На когда я записан?",
    "У меня есть запись?",
    "Во сколько у меня запись?",
    "Хочу посмотреть свои записи",
    # Review round 4 P1 (coverage regression) — the most frequent live
    # phrasings carry a tail AFTER the booking noun. Review round 5 P1
    # narrowed the tail to a CLOSED allow-list: temporal words, days of
    # week, digit dates, salon scope — NO lexical "на <word>" service
    # branch ("на диктофоне" must stay OUT exactly like "с диктофона";
    # "на маникюр" is not lexically distinguishable from "на вебинар",
    # so services are not recognized at all). Plus a politeness marker
    # and non-word trailing characters (",", emoji).
    "Покажи мои записи на завтра",
    "Какие у меня записи на этой неделе?",
    "Какие у меня записи на прошлой неделе?",
    "Когда у меня запись в салоне?",
    "Какая у меня ближайшая запись к мастеру?",
    "Какие у меня записи на 15 августа?",
    "Какие у меня записи на 5 сентября?",
    # Review round 7 — cover month forms beyond август/сентябрь and the
    # bare-day date (the digit branch was previously only probed by two
    # months; dates are genitive "15 августа", "1 мая", "8 марта", "31
    # декабря", "15").
    "Какие у меня записи на 1 мая?",
    "Какие у меня записи на 8 марта?",
    "Какие у меня записи на 31 декабря?",
    "Какие у меня записи на 15?",
    "Когда у меня запись на пятницу?",
    "Когда у меня запись на завтра в салоне?",
    "Покажи мои записи, пожалуйста",
    "Покажи мои записи 🙂",
    # DRF-1055 — the phrasings the pilot owner and the live-acceptance
    # run actually produced. Kept in the main list (not only in the
    # table below) so they run through the routing matrix and the
    # production dispatch integration too, not just the predicate.
    "Мои записи покажи",
    "мои записи",
    "покажи записи",
    "покажи мои визиты",
    "мои визиты",
    "что у меня записано",
    "мои брони",
    # OD-IR1 — the two corpus phrasings the DRF-1055 detector still
    # missed. Kept here as well as in OD_IR1_CORPUS below so they run
    # through the full routing matrix and the production dispatch
    # integration, not only through the predicate.
    "куда я записан",
    "к кому я записан",
)

# DRF-1055 — the exact table from the brief: thirteen phrasings run
# against ``is_personal_booking_lookup`` in the pilot worker container
# on 2026-08-13. Nine of them were NOT recognised and the turn fell
# through to the concierge LLM, which has no bookings tool and answered
# «я не могу показать ваши записи».
#
# The ``expected`` flag is the post-fix contract. «Покажи по записи» is
# a typo of «покажи мои записи»; the brief leaves it optional (and
# explicitly not at the cost of a false positive) — it now matches as a
# free side effect of the show-imperative rule, and is pinned here so a
# future change to that rule surfaces the consequence.
DRF1055_TABLE: tuple[tuple[str, bool], ...] = (
    ("Мои записи покажи", True),  # ← the owner's own phrasing
    ("мои записи", True),
    ("покажи записи", True),
    ("покажи мои визиты", True),
    ("мои визиты", True),
    ("что у меня записано", True),
    ("мои брони", True),
    ("Покажи по записи", True),  # typo — optional per brief §2
    ("покажи мои записи", True),  # already worked before the fix
    ("когда я записан", True),  # already worked before the fix
)

# OD-IR1 (owner decision «Pilot routing», docs/Ayla-intent-routing —
# «Owner Decision OD-IR1 — Pilot routing») — the owner's minimal
# regression corpus for «Мои записи», verbatim and in the decision's own
# order. Requirement 2 of that decision is the corpus ITSELF, not the
# two phrasings it happened to expose: until Controlled Pilot the
# deterministic router stays, so the next lexical / word-order /
# synonym gap has to be found by this table on CI rather than by a
# person on the pilot.
#
# State on 2026-08-14 before this patch: 11/13. «куда я записан» and
# «к кому я записан» failed on the lookup-signal gate alone — the list
# carried «когда» but neither the WHERE nor the TO-WHOM question.
#
# Add to this tuple — never trim it — when the owner extends the
# corpus; the eight other pilot-critical intents named in OD-IR1
# (booking, appointments, history, repeat, escalation, cancel,
# reschedule, help) are separate work and deliberately not started here.
OD_IR1_CORPUS: tuple[str, ...] = (
    "покажи мои записи",
    "мои записи покажи",
    "мои записи",
    "покажи записи",
    "мои визиты",
    "покажи мои визиты",
    "когда я записан",
    "что у меня записано",
    "мои брони",
    "покажи мои брони",
    "куда я записан",
    "к кому я записан",
    "есть ли у меня записи",
)

# OD-IR1 — the nearest relatives of the two phrasings closed above,
# covered by CLOSED-class extensions only (two exact past-reflexive
# forms in the booked-state class, two word-bounded question words in
# the signal list). No open ``\w+`` slot was introduced: that is what
# produced the left-re-scoping regression pinned by
# LEFT_RESCOPED_PHRASES.
OD_IR1_RELATED_PHRASES: tuple[str, ...] = (
    "куда я записана",
    "к кому я записался",
    "куда я записался",
    "к кому я записалась",
    # The new question words also compose with the existing «у меня»
    # personal marker for free.
    "куда у меня запись",
    "к кому у меня запись",
)

# OD-IR1 requirement 3 — collisions. The «запис» root must not
# intercept foreign semantics, and the two new question words are broad
# enough to deserve their own negatives:
#
#   * «куда идти» / «к кому обратиться» carry the new signal and NO
#     booking noun — the noun gate rejects them;
#   * «куда записаться» / «к кому записаться» carry the new signal AND
#     the root, but they are the intent to CREATE a booking — the worst
#     false positive in the product (DRF-1055 §2), rejected by
#     _CREATE_INTENT_SIGNAL before any positive test runs;
#   * «куда я записал видео» is the recording domain: «записал» is not
#     «записался», and the closed booked-state class keeps them apart.
DIRECTION_QUESTION_NON_LOOKUP_PHRASES: tuple[str, ...] = (
    "куда идти",
    "к кому обратиться",
    "куда записаться",
    "к кому записаться",
    "куда мне записаться",
    "куда идти к мастеру",
    "куда я записал видео",
    "куда я записался на вебинар",
    "куда пропали мои записи с диктофона",
)

# DRF-1055 §2 — the negation window that must NOT break. A person who
# wants to BOOK must never be answered with the list of bookings they
# already have: that is a dead end, and worse than not recognising the
# turn at all. These are the phrasings the brief names plus the
# availability question, which has the exact shape of a lookup
# («покажи» + bare booking noun + temporal complement) and is the one
# new false-positive risk the widened detector introduces.
CREATE_INTENT_PHRASES: tuple[str, ...] = (
    "хочу записаться",
    "запиши меня",
    "записаться на маникюр",
    "можно записаться?",
    "Хочу записаться на маникюр",
    "Можно записаться к Анне?",
    "Запишите меня на массаж",
    "покажи свободные записи",
    "покажи свободные визиты",
    "Какие свободные записи на завтра?",
    "есть свободные визиты на завтра?",
)

# DRF-1055 — the synonyms widen the vocabulary, so the domain guard has
# to hold for them too: «визит» and «бронь» re-scoped by a complement
# are no more a salon booking than «запись вебинара» is. Plus the
# compound trap the closed declension list exists to avoid («визитка»
# is not a declension of «визит»).
SYNONYM_NON_BOOKING_PHRASES: tuple[str, ...] = (
    "покажи мои визиты вебинара",
    "мои брони в отеле",
    "покажи бронь авиабилета",
    "покажи визитку",
    "Когда у меня визит к врачу?",
    "Какие у меня брони на диктофоне?",
)

# DRF-1055 (review) — the domain guard has to be SYMMETRIC. Russian
# re-scopes a noun from the left as readily as from the right, and the
# phrase-final guard cannot see any of it: in «покажи мои аудио записи»
# the noun still stands phrase-final, so the widened detector (the
# show-imperative rule + the possessive-with-adjective marker) accepted
# it until the closed safe-LEAD class was added. These are the mirror
# image of COMPOUND_NON_BOOKING_PHRASES / NON_BOOKING_TAIL_PHRASES and
# exist so a future widening cannot trade the round-3..7 guard away.
LEFT_RESCOPED_PHRASES: tuple[str, ...] = (
    "покажи мои аудио записи",
    "покажи мои видео записи",
    "покажи мои голосовые записи",
    "покажи мои рабочие записи",
    "покажи мои дневниковые записи",
    "какие мои экранные записи",
    "покажи телефонные записи",
    "покажи запись экрана",
    # Not the caller's own bookings — the show-imperative rule reads
    # «покажи <booking noun>» as personal precisely because a 1:1 chat
    # has no other referent; an explicit third-party modifier removes
    # that premise.
    "покажи чужие записи",
    "покажи мои старые записи",
)

# Routing-level subset of the list above. «покажи запись экрана» carries
# the literal «запись», which the booking skill's own legacy keyword
# fallback claims (``apps.skills.booking.skill._BOOKING_KEYWORDS``)
# independently of this detector — and, being an imperative, it is not
# question-shaped, so FAQ does not intercept it first the way it does
# for «Когда у меня запись к врачу?». The turn therefore reaches the
# booking skill and takes the LLM tool-choice path (NOT the lookup fast
# path — the predicate assertion above is what pins that). This is the
# tenant-path keyword fallback having no negation window; pre-existing,
# owned by DRF-1042, deliberately NOT widened here.
LEFT_RESCOPED_ROUTING_PHRASES: tuple[str, ...] = tuple(
    phrase for phrase in LEFT_RESCOPED_PHRASES if "запись" not in phrase.lower()
)

# DRF-1055 (review) — the verb-less personal noun phrase is the shape
# the pilot produced («мои записи»), and it does not stop at the bare
# two-word form. These variants are all CLOSED-class extensions of it:
# a safe adjective, an allow-listed complement, a politeness marker,
# «где». Pinned so the next reviewer sees which shapes are covered on
# purpose rather than by accident.
VERBLESS_LOOKUP_PHRASES: tuple[str, ...] = (
    "мои ближайшие записи",
    "мои следующие записи",
    "мои прошлые записи",
    "мои записи на завтра",
    "мои записи на 15 августа",
    "мои визиты на этой неделе",
    "мои записи на завтра, пожалуйста",
    "где мои записи",
    "покажи мои ближайшие записи",
)

FAQ_PHRASES: tuple[str, ...] = (
    "Как записаться?",
    "Как работает запись?",
    "Какие правила отмены?",
    "Можно ли перенести запись?",
    "Сколько заранее нужно записываться?",
)

MUTATION_PHRASES: tuple[str, ...] = (
    "Перенеси мою запись",
    "Отмени мою запись",
    "Запиши меня на массаж",
)

# Review P1 — phrases that LOOK personal but are not booking lookups:
# a change-request, and "запись" used outside the booking domain.
AMBIGUOUS_NON_LOOKUP_PHRASES: tuple[str, ...] = (
    "Можно поменять мою запись?",
    "Какая у меня запись в трудовой книжке?",
    "Какие у меня записи в дневнике?",
    "Мне нравится моя запись?",
)

# Review round 3 P1 — compound "…запись" words and "запись" re-scoped
# by a complement are NOT salon-booking lookups. The matcher must
# reject these SEMANTICALLY (standalone bare booking word), not via a
# growing vocabulary blacklist.
COMPOUND_NON_BOOKING_PHRASES: tuple[str, ...] = (
    "Когда у меня начнётся запись вебинара?",
    "Какая у меня аудиозапись сохранена?",
    "Покажи мои записи с диктофона",
    "Какие у меня записи в медицинской карте?",
    "Где мои звукозаписи?",
    "Когда выйдет моя запись подкаста?",
    "Покажи мои записи телефонных разговоров",
    "Какие у меня записи экрана сохранились?",
    "Что за запись в реестре у меня?",
    "Когда у меня запись к врачу?",
    "Покажи мои записи камер наблюдения",
)

# Review round 5 P1 — "на <слово>" is NOT an open service branch:
# recording devices / media ("на диктофоне", "на телефоне", "на
# камере", "на компьютере", "на флешке") and events ("на вебинар")
# re-scope "запись" out of the salon-booking domain exactly like the
# round-3 genitive complements. Services ("на маникюр") are not
# lexically distinguishable from "на вебинар", so they are not
# recognized either — a closed temporal/salon list only.
NON_BOOKING_TAIL_PHRASES: tuple[str, ...] = (
    "Покажи мои записи на диктофоне",
    "Какие у меня записи на телефоне?",
    "Покажи мои записи на камере",
    "Какие у меня записи на компьютере?",
    "Покажи мои записи на флешке",
    "Когда у меня запись на вебинар?",
    "Когда у меня запись на маникюр?",
    # Review round 6 — the digit branch must be a closed month list, not
    # "<digit> <any word>": a numeral does not re-scope the domain only
    # when it is a date. "на 4 диктофона" / "на 12 вебинаров" are the
    # same recording-domain leak as "на диктофоне", one word later.
    "Покажи мои записи на 4 диктофона",
    "Какие у меня записи на 12 вебинаров?",
    "Покажи мои записи на 3 канале",
    "Какие у меня записи на 10 гигабайт?",
    # Review round 7 — a month-ROOT prefix is not a date: "<digit> +
    # word starting with a month root" (майки, маяка, мартышки, июльских)
    # is the same leak class as "<digit> + any word" (round 6), one
    # character class later. The month list must be exact genitive
    # forms, no wildcard tail.
    "Какие у меня записи на 3 майки?",
    "Покажи мои записи на 2 маяка",
    "Какие у меня записи на 2 мартышки?",
    "Какие у меня записи на 5 июльских?",
)

# Review round 3 P2 — irregular internal whitespace (multiple spaces,
# tabs) must not change the routing decision.
WHITESPACE_LOOKUP_VARIANTS: tuple[str, ...] = (
    "Когда   у   меня   следующая запись?",
    "Когда\tу\tменя\tследующая запись?",
)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, settings: pytest.FixtureRequest):
    settings.BASE_DIR = tmp_path  # type: ignore[attr-defined]
    settings.LLM_PROVIDER = "openai"  # type: ignore[attr-defined]
    settings.SKILL_LLM_PROVIDER = {}  # type: ignore[attr-defined]
    reset_router_cache()
    cache.clear()
    yield
    cache.clear()
    reset_router_cache()


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="lookup-routing", name="Lookup Routing")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    # Deliberately unlinked: ayla_user_id stays NULL (identity binding is
    # E2E-BOT-02B scope). The read-only lookup must degrade to the
    # controlled empty fallback, never fabricate bookings.
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="lookup-u1",
        chat_id="lookup-u1",
        phone="79991234567",
        client_name="Anna",
    )


@pytest.fixture
def context(tenant: Tenant, bot_user: BotUser) -> SkillContext:
    conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
    return SkillContext(
        conversation=conv,
        bot_user=bot_user,
        message_text="",
        trace_id="t-lookup",
    )


def _with_text(context: SkillContext, text: str) -> SkillContext:
    return SkillContext(
        conversation=context.conversation,
        bot_user=context.bot_user,
        message_text=text,
        trace_id=context.trace_id,
    )


class _FakeYClients:
    """Minimal read-only double: services prefetch + empty records list."""

    def get_services(self, **_: Any) -> list[Any]:
        return []

    def get_staff(self, *, staff_id: Any = None) -> list[Any]:
        return []

    def get_user_records(self) -> list[Any]:
        return []


def _completion(*, text: str = "", tool_calls: list[ToolCall] | None = None) -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=tool_calls or [],
        prompt_tokens=10,
        completion_tokens=20,
        model="mock",
        provider="openai",
        finish_reason="stop" if not tool_calls else "tool_calls",
    )


# ---------------------------------------------------------------------------
# Lookup predicate — the FAQ-vs-own-booking boundary
# ---------------------------------------------------------------------------


class TestLookupPredicate:
    @pytest.mark.parametrize("phrase", LOOKUP_PHRASES)
    def test_personal_lookup_phrases_match(self, phrase: str) -> None:
        assert is_personal_booking_lookup(phrase) is True

    @pytest.mark.parametrize(("phrase", "expected"), DRF1055_TABLE)
    def test_drf1055_live_acceptance_table(self, phrase: str, expected: bool) -> None:
        """DRF-1055 — the thirteen-phrasing table from the live pilot run.

        Nine of these were unrecognised on 2026-08-13 and the turn went
        to the concierge LLM, which cannot list bookings. Reverse word
        order, verb-less requests, possessive-less imperatives and the
        «визит» / «бронь» synonyms are all covered now."""
        assert is_personal_booking_lookup(phrase) is expected

    @pytest.mark.parametrize("phrase", OD_IR1_CORPUS)
    def test_od_ir1_corpus(self, phrase: str) -> None:
        """OD-IR1 requirement 2 — the owner's regression corpus.

        Owner Decision OD-IR1 («Pilot routing») keeps the deterministic
        router until Controlled Pilot on the condition that it closes
        the known lexical / word-order / synonym gaps AND carries a
        regression corpus of natural phrasings. This table IS that
        corpus for «Мои записи», verbatim from the decision.

        It was 11/13 on 2026-08-14: «куда я записан» and «к кому я
        записан» failed the lookup-signal gate, which knew «когда» but
        not the WHERE / TO-WHOM question. The point of pinning all
        thirteen — not just those two — is that the NEXT gap is found
        here rather than by a person on the pilot.
        """
        assert is_personal_booking_lookup(phrase) is True

    @pytest.mark.parametrize("phrase", OD_IR1_RELATED_PHRASES)
    def test_od_ir1_related_forms(self, phrase: str) -> None:
        """OD-IR1 §1 — the nearest relatives of the corpus phrasings.

        «к кому я записался» is what a male speaker says instead of «я
        записан»; «куда у меня запись» composes the new question word
        with the existing «у меня» marker. Both are covered by CLOSED
        classes only — no open ``\\w+`` slot, which is what caused the
        left-re-scoping regression pinned by LEFT_RESCOPED_PHRASES.
        """
        assert is_personal_booking_lookup(phrase) is True

    @pytest.mark.parametrize("phrase", DIRECTION_QUESTION_NON_LOOKUP_PHRASES)
    def test_direction_questions_do_not_collide(self, phrase: str) -> None:
        """OD-IR1 requirement 3 — collisions for the new signals.

        «куда» / «к кому» are as broad as «где», so they only ever
        qualify together with a bare booking noun AND a personal
        reference. «куда идти» has no noun; «куда записаться» is the
        create intent (rejected one gate earlier); «куда я записал
        видео» is the recording domain — «записал» is not «записался».
        """
        assert is_personal_booking_lookup(phrase) is False

    @pytest.mark.parametrize("phrase", CREATE_INTENT_PHRASES)
    def test_create_intent_never_matches(self, phrase: str) -> None:
        """DRF-1055 §2 — the negation window. A request to MAKE a
        booking (or to see free slots) must never be answered with the
        caller's existing bookings: that is a dead end for the person
        who wanted an appointment, and a worse outcome than a miss."""
        assert is_personal_booking_lookup(phrase) is False

    @pytest.mark.parametrize("phrase", LEFT_RESCOPED_PHRASES)
    def test_modifier_before_the_noun_rescopes_it_too(self, phrase: str) -> None:
        """DRF-1055 (review) — the domain guard is symmetric.

        The pre-DRF-1055 matcher got this for free: the noun had to end
        the message, and a personal marker had to be adjacent to it.
        Widening both (show-imperative rule, adjective between the
        possessive and the noun) removed that accident, so the guard is
        now stated explicitly on the left as well — «покажи мои аудио
        записи» is the same class of leak as «записи с диктофона»."""
        assert is_personal_booking_lookup(phrase) is False

    @pytest.mark.parametrize("phrase", VERBLESS_LOOKUP_PHRASES)
    def test_verbless_personal_noun_phrases_match(self, phrase: str) -> None:
        """DRF-1055 — «мои записи» has no lookup verb to find, and the
        shape generalises: a closed-class adjective, an allow-listed
        complement or a politeness marker keeps it a personal lookup."""
        assert is_personal_booking_lookup(phrase) is True

    @pytest.mark.parametrize("phrase", SYNONYM_NON_BOOKING_PHRASES)
    def test_synonyms_keep_the_domain_guard(self, phrase: str) -> None:
        """DRF-1055 — «визит» / «бронь» are matched as closed declension
        lists inside the same standalone-word guard as «запись», so a
        re-scoping complement («визиты вебинара», «брони в отеле») and a
        compound («визитка») reject exactly as they do for «запись»."""
        assert is_personal_booking_lookup(phrase) is False

    @pytest.mark.parametrize(
        "phrase",
        (
            "МОИ ЗАПИСИ ПОКАЖИ",
            "Мои Записи Покажи",
            "мОи ЗаПиСи пОкАжИ",
        ),
    )
    def test_case_and_word_order_are_irrelevant(self, phrase: str) -> None:
        """DRF-1055 §4 — the owner's phrasing was capitalised AND in
        reverse word order; both had to stop mattering."""
        assert is_personal_booking_lookup(phrase) is True

    @pytest.mark.parametrize("phrase", FAQ_PHRASES)
    def test_booking_rules_faq_does_not_match(self, phrase: str) -> None:
        assert is_personal_booking_lookup(phrase) is False

    @pytest.mark.parametrize("phrase", MUTATION_PHRASES)
    def test_mutation_requests_do_not_match(self, phrase: str) -> None:
        assert is_personal_booking_lookup(phrase) is False

    @pytest.mark.parametrize(
        "phrase",
        (
            # First-person but infinitive — "how do I book" is process FAQ.
            "Как я могу записаться?",
            # Personal marker without any booking reference.
            "Когда у меня день рождения?",
            # Booking reference without any personal marker.
            "Когда работает запись?",
            # "есть" hidden inside another word is NOT a lookup signal
            # (review round 4 minor — \bесть\b); a declarative statement
            # without an explicit lookup word is not a lookup.
            "У меня шесть записей",
        ),
    )
    def test_boundary_negatives(self, phrase: str) -> None:
        assert is_personal_booking_lookup(phrase) is False

    @pytest.mark.parametrize("phrase", AMBIGUOUS_NON_LOOKUP_PHRASES)
    def test_ambiguous_or_non_booking_phrases_do_not_match(self, phrase: str) -> None:
        """Review P1 — "?" alone is not a lookup signal; a possessive
        "запись" outside the booking domain (трудовая книжка, дневник)
        or a change-request ("поменять") is NOT a personal lookup."""
        assert is_personal_booking_lookup(phrase) is False

    @pytest.mark.parametrize("phrase", COMPOUND_NON_BOOKING_PHRASES + NON_BOOKING_TAIL_PHRASES)
    def test_compound_and_rescoped_booking_words_do_not_match(self, phrase: str) -> None:
        """Review round 3 P1 — "аудиозапись"/"звукозапись" compounds and
        "запись <complement>" (вебинара, в реестре, к врачу, с диктофона,
        камер наблюдения, …) are outside the booking domain. Rejected
        semantically: the booking noun must be a standalone word with no
        domain-shifting complement."""
        assert is_personal_booking_lookup(phrase) is False

    @pytest.mark.parametrize("phrase", WHITESPACE_LOOKUP_VARIANTS)
    def test_irregular_whitespace_still_matches(self, phrase: str) -> None:
        """Review round 3 P2 — multiple spaces / tabs are normalized."""
        assert is_personal_booking_lookup(phrase) is True


# ---------------------------------------------------------------------------
# Skill matchers
# ---------------------------------------------------------------------------


class TestSkillMatchers:
    @pytest.mark.parametrize("phrase", LOOKUP_PHRASES)
    def test_faq_yields_personal_lookups(self, context: SkillContext, phrase: str) -> None:
        assert FAQSkill().matches(_with_text(context, phrase)) is False

    @pytest.mark.parametrize("phrase", FAQ_PHRASES)
    def test_faq_still_matches_booking_rules_questions(
        self, context: SkillContext, phrase: str
    ) -> None:
        assert FAQSkill().matches(_with_text(context, phrase)) is True

    @pytest.mark.parametrize("phrase", LOOKUP_PHRASES + MUTATION_PHRASES)
    def test_booking_claims_lookups_and_mutations(self, context: SkillContext, phrase: str) -> None:
        assert BookingSkill().matches(_with_text(context, phrase)) is True


# ---------------------------------------------------------------------------
# Registry-order routing matrix (first-match-wins, production order)
# ---------------------------------------------------------------------------


def _first_matching_skill_name(text: str, context: SkillContext) -> str | None:
    ctx = _with_text(context, text)
    for skill in registered():
        if skill.matches(ctx):
            return skill.name
    return None


class TestRoutingMatrix:
    @pytest.mark.parametrize("phrase", LOOKUP_PHRASES)
    def test_lookup_phrases_route_to_booking(self, context: SkillContext, phrase: str) -> None:
        assert _first_matching_skill_name(phrase, context) == "booking"

    @pytest.mark.parametrize("phrase", FAQ_PHRASES)
    def test_faq_phrases_route_to_faq(self, context: SkillContext, phrase: str) -> None:
        assert _first_matching_skill_name(phrase, context) == "faq"

    @pytest.mark.parametrize("phrase", MUTATION_PHRASES)
    def test_mutation_phrases_route_to_booking(self, context: SkillContext, phrase: str) -> None:
        assert _first_matching_skill_name(phrase, context) == "booking"

    @pytest.mark.parametrize("phrase", AMBIGUOUS_NON_LOOKUP_PHRASES)
    def test_ambiguous_phrases_do_not_route_to_booking(
        self, context: SkillContext, phrase: str
    ) -> None:
        assert _first_matching_skill_name(phrase, context) == "faq"

    @pytest.mark.parametrize(
        "phrase",
        COMPOUND_NON_BOOKING_PHRASES
        + NON_BOOKING_TAIL_PHRASES
        + SYNONYM_NON_BOOKING_PHRASES
        + LEFT_RESCOPED_ROUTING_PHRASES,
    )
    def test_compound_phrases_do_not_route_to_booking(
        self, context: SkillContext, phrase: str
    ) -> None:
        # Routing varies (faq for question-marked phrasings, echo for
        # bare imperatives) — the contract is: never booking.
        assert _first_matching_skill_name(phrase, context) != "booking"

    @pytest.mark.parametrize("phrase", WHITESPACE_LOOKUP_VARIANTS)
    def test_whitespace_variants_route_to_booking(self, context: SkillContext, phrase: str) -> None:
        assert _first_matching_skill_name(phrase, context) == "booking"


# ---------------------------------------------------------------------------
# Dispatch-level negatives (review P1): ambiguous/non-booking "запись"
# phrasings must NEVER reach the booking skill's show_my_bookings path.
# ---------------------------------------------------------------------------


class TestAmbiguousDispatchNegatives:
    @pytest.mark.parametrize("phrase", AMBIGUOUS_NON_LOOKUP_PHRASES)
    def test_ambiguous_phrases_never_reach_show_my_bookings(
        self,
        context: SkillContext,
        tenant: Tenant,
        phrase: str,
    ) -> None:
        faq_result = SkillResult(
            reply_text="faq answer",
            action_type="faq",
            meta={"skill": "faq"},
        )
        with (
            patch.object(FAQSkill, "handle", return_value=faq_result) as faq_handle,
            patch.object(BookingSkill, "handle") as booking_handle,
            tenant_scope(tenant),
        ):
            result = dispatch(_with_text(context, phrase))

        assert result is not None
        assert result.meta["skill"] == "faq"
        faq_handle.assert_called_once()
        # The booking skill — and therefore the show_my_bookings fast
        # path — was never entered.
        booking_handle.assert_not_called()
        assert "show_my_bookings" not in [tc.name for tc in result.tool_calls_made]

    @pytest.mark.parametrize(
        "phrase",
        COMPOUND_NON_BOOKING_PHRASES
        + NON_BOOKING_TAIL_PHRASES
        + SYNONYM_NON_BOOKING_PHRASES
        + LEFT_RESCOPED_ROUTING_PHRASES,
    )
    def test_compound_phrases_never_reach_show_my_bookings(
        self,
        context: SkillContext,
        tenant: Tenant,
        phrase: str,
    ) -> None:
        """Review round 3 P1 — through the REAL SkillRegistry.dispatch:
        compound/re-scoped "запись" phrasings must never enter the
        booking skill (its fast path would force show_my_bookings)."""
        with (
            patch.object(BookingSkill, "handle") as booking_handle,
            tenant_scope(tenant),
        ):
            result = dispatch(_with_text(context, phrase))

        assert result is not None
        booking_handle.assert_not_called()
        assert "show_my_bookings" not in [tc.name for tc in result.tool_calls_made]


class TestWhitespaceNormalization:
    """Review round 3 P2 — irregular internal whitespace must not push
    a lookup turn off the booking fast path into FAQ."""

    @pytest.mark.parametrize("phrase", WHITESPACE_LOOKUP_VARIANTS)
    def test_whitespace_variants_dispatch_show_my_bookings(
        self,
        context: SkillContext,
        tenant: Tenant,
        phrase: str,
    ) -> None:
        client = _FakeYClients()
        with (
            patch(
                "apps.integrations.yclients.get_yclients_client",
                return_value=client,
            ),
            patch.object(OpenAIProvider, "complete", side_effect=[_completion(text="")]),
            tenant_scope(tenant),
        ):
            result = dispatch(_with_text(context, phrase))

        assert result is not None
        assert result.meta["skill"] == "booking"
        assert [tc.name for tc in result.tool_calls_made] == ["show_my_bookings"]


# ---------------------------------------------------------------------------
# Production-path integration: message → registry.dispatch → booking →
# show_my_bookings (read-only, no mutation, unlinked-identity fallback)
# ---------------------------------------------------------------------------


class TestProductionDispatchIntegration:
    @pytest.mark.parametrize("phrase", LOOKUP_PHRASES)
    def test_lookup_dispatches_show_my_bookings_read_only(
        self,
        context: SkillContext,
        tenant: Tenant,
        bot_user: BotUser,
        phrase: str,
    ) -> None:
        client = _FakeYClients()
        # Exactly ONE LLM call is expected: the Phase-3 reply render.
        # The lookup fast path selects show_my_bookings deterministically
        # (no Phase-1 tool-choice call). Empty text → the tool's own
        # unlinked/empty fallback copy is used.
        with (
            patch(
                "apps.integrations.yclients.get_yclients_client",
                return_value=client,
            ),
            patch.object(
                OpenAIProvider, "complete", side_effect=[_completion(text="")]
            ) as complete_mock,
            patch.object(FAQSkill, "handle") as faq_handle,
            tenant_scope(tenant),
        ):
            result = dispatch(_with_text(context, phrase))

        assert result is not None
        assert result.meta["skill"] == "booking"
        assert [tc.name for tc in result.tool_calls_made] == ["show_my_bookings"]
        assert complete_mock.call_count == 1
        # Negative: FAQ never touched the turn.
        faq_handle.assert_not_called()
        # Negative: echo would have answered with the verbatim phrase.
        assert result.reply_text != phrase
        # Negative: no mutation side effects — no pending preview, no
        # booking row, unlinked user gets the controlled empty fallback.
        assert PendingBookingAction.all_tenants.filter(tenant=tenant).count() == 0
        assert BookingRequest.all_tenants.filter(tenant=tenant).count() == 0
        assert bot_user.ayla_user_id is None
        assert "пока нет" in result.reply_text.lower()

    @pytest.mark.parametrize("phrase", MUTATION_PHRASES)
    def test_mutation_phrases_are_not_hijacked_into_lookup(
        self,
        context: SkillContext,
        tenant: Tenant,
        phrase: str,
    ) -> None:
        client = _FakeYClients()
        # Phase-1 LLM replies directly (no tool). The deterministic
        # lookup fast path must NOT fire for mutation phrasings, so the
        # Phase-1 call MUST happen and no show_my_bookings is forced.
        with (
            patch(
                "apps.integrations.yclients.get_yclients_client",
                return_value=client,
            ),
            patch.object(
                OpenAIProvider,
                "complete",
                side_effect=[_completion(text="Уточните, пожалуйста, какую запись?")],
            ) as complete_mock,
            tenant_scope(tenant),
        ):
            result = dispatch(_with_text(context, phrase))

        assert result is not None
        assert result.meta["skill"] == "booking"
        assert result.tool_calls_made == []
        assert complete_mock.call_count == 1
        assert "show_my_bookings" not in [tc.name for tc in result.tool_calls_made]

    @pytest.mark.parametrize(
        "phrase",
        (
            "хочу записаться",
            "запиши меня",
            "записаться на маникюр",
            "можно записаться?",
            "Хочу записаться на маникюр",
            "Можно записаться к Анне?",
            "Запишите меня на массаж",
        ),
    )
    def test_create_intent_is_not_hijacked_into_lookup(
        self,
        context: SkillContext,
        tenant: Tenant,
        phrase: str,
    ) -> None:
        """DRF-1055 §2 through the REAL registry: a create-intent turn
        never reaches the deterministic show_my_bookings fast path, so
        nobody who asked to BOOK is answered with the list of bookings
        they already have.

        Which skill claims the turn is the pre-existing boundary and
        deliberately not asserted here: «хочу записаться» goes to the
        booking skill's LLM tool-choice path, while the process
        question «можно записаться?» is claimed by FAQ (registered
        first) exactly as it was before this change."""
        client = _FakeYClients()
        with (
            patch(
                "apps.integrations.yclients.get_yclients_client",
                return_value=client,
            ),
            patch.object(
                OpenAIProvider,
                "complete",
                return_value=_completion(text="На какую услугу вас записать?"),
            ),
            tenant_scope(tenant),
        ):
            result = dispatch(_with_text(context, phrase))

        assert result is not None
        assert "show_my_bookings" not in [tc.name for tc in result.tool_calls_made]

    def test_dispatch_result_log_proves_skill_action_and_tools(
        self,
        context: SkillContext,
        tenant: Tenant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        client = _FakeYClients()
        with (
            patch(
                "apps.integrations.yclients.get_yclients_client",
                return_value=client,
            ),
            patch.object(OpenAIProvider, "complete", side_effect=[_completion(text="")]),
            tenant_scope(tenant),
            caplog.at_level(logging.INFO, logger="apps.skills.registry"),
        ):
            dispatch(_with_text(context, "Когда у меня следующая запись?"))

        messages = [r.getMessage() for r in caplog.records]
        result_lines = [m for m in messages if m.startswith("skills.dispatch.result")]
        assert result_lines, f"missing skills.dispatch.result log line; got: {messages}"
        line = result_lines[0]
        assert "name=booking" in line
        assert "show_my_bookings" in line
