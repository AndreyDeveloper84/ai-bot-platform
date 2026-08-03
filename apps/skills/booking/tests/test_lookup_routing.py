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

    @pytest.mark.parametrize("phrase", COMPOUND_NON_BOOKING_PHRASES + NON_BOOKING_TAIL_PHRASES)
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

    @pytest.mark.parametrize("phrase", COMPOUND_NON_BOOKING_PHRASES + NON_BOOKING_TAIL_PHRASES)
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
