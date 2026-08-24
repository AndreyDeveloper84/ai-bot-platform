"""Who owns the turn BEFORE the concierge — the inverted default (DRF-1328).

## The failure this module exists to end

On the live pilot (24.08, 04:46) the owner wrote «Найди мне САЛОНЫ массажа»
twice and got master cards both times. The tool that answers that question
had shipped the day before (DRF-1304, ``show_salons``, whose description
says «показать подключённые салоны, НЕ отдельных мастеров») — but the model
never got to choose it. The turn was taken one layer earlier, by the
deterministic branch in :mod:`apps.channels.max.handler`, whose entry
condition was::

    looks_like_booking_request(text)   # «is a service word in there?»

«массажа» is a service word, so the branch claimed the turn and rendered
masters. The same shape had already misfired the day before on a composite
request (DRF-1312).

## Why a new module instead of one more ``if``

The old condition's DEFAULT was «mine», and every concierge capability that
the branch must not swallow had to be subtracted from it by hand::

    отступи, если названо несколько услуг   <- DRF-1312
    отступи, если спросили про салон        <- DRF-1328 would have been this
    отступи, если названа цель              <- next
    …

That list is maintained by whoever remembers it. Nobody remembered twice in
two days, on the same person. And the cost of forgetting is a CONFIDENT
WRONG ANSWER: the user asked for salons and was told, with cards, that these
are the masters for them.

So the default is inverted. The branch now has to PARSE the turn and
recognise it as *exactly* «покажи мастеров по услуге»; everything it cannot
account for goes to the concierge, which has all the tools::

    было:   услуга упомянута?              -> забираю ход
    стало:  разобрал ход целиком и в нём
            нет ничего, кроме запроса
            мастеров по услуге             -> забираю
            во всём остальном              -> консьерж

## The parse (no model, by design)

:func:`claims_direct_show_masters` accepts a turn only when BOTH hold:

1. it names a bookable service — the same stems
   :mod:`apps.skills.menu.matching` already uses, seed list untouched; and
2. **every remaining word is accounted for** — it is either a
   master-request word (:data:`_REQUEST_WORDS` / :data:`_REQUEST_STEMS`), a
   qualifier of the service itself (:data:`_SERVICE_QUALIFIER_STEMS` — the
   body part, the style), or the name of a city we actually serve.

Rule 2 is the inversion. An unknown word is not ignored, it is a REFUSAL:
«салоны», «стоит», «болит», «калорий» are all unknown here, so each of them
hands the turn over without this module having ever heard of the tool that
wants it. A capability built next month is reachable on the day it ships,
because its vocabulary is not in this file.

### Why a model must not do the parsing

The branch exists so an obvious turn does not pay for a model call; putting
a model call in front of it deletes its only reason to exist. The canon says
the same about the intent resolver — :mod:`apps.orchestrator.intent_resolution`,
module docstring: «After the reply, never before it» — established by
DRF-1325 when exactly this use was attempted (``docs/REPORT_DRF1325.md``,
опровержение 1.1).

### Which direction this fails in

A master request written in words this file does not know («массаж 60
минут», «массажик срочно плиз») is handed to the concierge. That costs one
LLM call and still answers correctly. The opposite mistake — claiming a turn
that was not a master request — costs a confident wrong answer, which is
what DRF-1304's tool was built to stop and DRF-1328 was filed about. The
vocabulary below may therefore be widened freely and must be narrowed with
care.

## The roster table

:data:`FAST_PATH_TOOL_CLAIMS` states, for EVERY tool the concierge is armed
with, whether this fast path claims its turns — with sample turns that pin
the routing. ``apps/orchestrator/tests/test_fast_path_claim.py`` reads the
concierge's tool roster out of the source and fails when a tool has no entry
here, so a new capability cannot ship without someone answering the question
this branch got wrong twice.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from apps.skills.menu.matching import ExtraStems, mentions_service, normalize

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The vocabulary a «покажи мастеров по услуге» turn is allowed to be made of
# ---------------------------------------------------------------------------

# NOT reused from ``apps.marketplace.discovery._FILLER_TOKENS``, despite the
# overlap. That list exists to WIDEN a catalog search — dropping a word there
# can only turn up more services, so it is safe for it to contain «салон»,
# «услуга» and «мастер». Here dropping a word means «this word does not stop
# me from claiming the turn», and «салон» / «услуга» are precisely the words
# that MUST stop it: they belong to ``show_salons`` and ``show_services``.
# Sharing one list would re-create DRF-1328 through the back door, from a
# module that has no idea this branch reads it.
#
# Whole words. Anything with a stable stem goes in :data:`_REQUEST_STEMS`.
_REQUEST_WORDS: frozenset[str] = frozenset(
    {
        # «show me someone»
        "найди",
        "найдите",
        "найти",
        "покажи",
        "покажите",
        "показать",
        "подбери",
        "подберите",
        "подобрать",
        "посоветуй",
        "посоветуйте",
        "порекомендуй",
        "порекомендуйте",
        "ищу",
        "искать",
        # wanting / needing
        "хочу",
        "хочется",
        "хотел",
        "хотела",
        "хотелось",
        "нужен",
        "нужна",
        "нужно",
        "надо",
        "интересует",
        # booking verbs — the booking skill owns these on the tenant path; in
        # a global turn they are glue around the service name
        "запиши",
        "запишите",
        "записать",
        "записаться",
        "запись",
        "сходить",
        "прийти",
        "подойти",
        # pronouns / prepositions / particles
        "мне",
        "меня",
        "мой",
        "моя",
        "мою",
        "бы",
        "на",
        "во",
        "по",
        "для",
        "или",
        "же",
        "ли",
        "вы",
        "вас",
        "нас",
        "это",
        "что",
        "как",
        # question words that only ever ask «who does it / where is it done»
        "кто",
        "где",
        "куда",
        "какой",
        "какая",
        "какие",
        "какого",
        "какую",
        "есть",
        "делает",
        "делают",
        "делаете",
        "принимает",
        "принимают",
        "работает",
        "работают",
        # politeness / greetings
        "можно",
        "пожалуйста",
        "плиз",
        "привет",
        "здравствуйте",
        "добрый",
        "доброе",
        "доброго",
        "день",
        "дня",
        "вечер",
        "вечера",
        "утро",
        "утра",
    }
)

# Prefix stems — the same cheap stand-in for stemming
# :mod:`apps.skills.menu.matching` uses, and for the same reason: one entry
# covers «мастера» / «мастеров» / «мастерам».
#
# «свободн…» / «окошк…» are here because an availability phrasing NEXT TO a
# service name («есть окошко на массаж?») is still a master request. On their
# own they name no service, so rule 1 hands them over before this list is
# ever consulted.
_REQUEST_STEMS: tuple[str, ...] = (
    "мастер",
    "специалист",
    "хорош",
    "лучш",
    "недорог",
    "дешев",
    "рядом",
    "поближе",
    "свободн",
    "окошк",
    "завтра",
    "сегодня",
    "срочн",
)

# Words that only ever QUALIFY a service and never name a topic of their own:
# the body part it is done to and the style it is done in. «массаж спины» and
# «классический массаж» are the two commonest real phrasings on the pilot, and
# without this list both would pay for a model call they do not need.
#
# Safe here in a way they are NOT safe in
# ``apps.skills.menu.matching._SERVICE_WORDS`` — which deliberately dropped
# «спина» / «лицо» because there they could CLAIM a turn on their own and
# «устала спина» became a master picker. Nothing in this list can claim
# anything: rule 1 has already required a real service stem before rule 2
# ever reads it. What a qualifier does is only «do not hand over because of
# ME», and a bare body part still never gets that far.
#
# The discriminator for a symptom survives untouched: «болит спина, хочу
# массаж» keeps «болит» as residue and goes to ``health_screening``.
_SERVICE_QUALIFIER_STEMS: tuple[str, ...] = (
    # body parts
    "спин",
    "шея",
    "шеи",
    "шею",
    "лицо",
    "лица",
    "лицу",
    "ног",
    "рук",
    "тела",
    "телу",
    "голов",
    "стоп",
    "живот",
    "плеч",
    "волос",
    # style / kind
    "классическ",
    "спортивн",
    "расслабляющ",
    "лечебн",
    "общ",
    "детск",
    "мужск",
    "женск",
    "глубок",
    "легк",
)


def _is_request_word(token: str) -> bool:
    """True when ``token`` is glue around a master request, not a topic."""
    if token in _REQUEST_WORDS:
        return True
    if any(token.startswith(stem) for stem in _REQUEST_STEMS):
        return True
    return any(token.startswith(stem) for stem in _SERVICE_QUALIFIER_STEMS)


# A token this short carries no topic on its own («в», «а», «к», «и», «с»,
# «у», «о»), and enumerating every Russian one-letter preposition would be a
# second list to maintain. Mirrors ``_MIN_TOKEN_LEN`` in
# ``apps.marketplace.discovery``, which drops them for the same reason.
_MIN_MEANING_LEN = 2

# ``normalize`` has already lower-cased, folded ``ё``→``е`` and replaced every
# non-word character with a space, so this only has to pick the runs back up.
_WORD_RE = re.compile(r"[0-9a-zа-я]+")


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimDecision:
    """Whether the fast path owns this turn, and why not when it does not.

    The reason is logged, never shown — it is what an operator reads in the
    trace when a turn they expected to be instant went to the model.
    """

    claimed: bool
    reason: str
    #: The words that could not be accounted for. Empty when claimed.
    residue: tuple[str, ...] = ()


def decide(text: str, *, extra_stems: ExtraStems = ()) -> ClaimDecision:
    """Parse ``text`` and decide whether the deterministic branch owns it.

    See the module docstring for the two rules. Ordered so the cheap checks
    run first. Everything here is pure string work except two small catalog
    reads, and neither is on the busiest turn:

    * the composite split reads the city set only when the text actually
      contains an enumeration separator;
    * city recognition runs only when a turn that already names a service
      still has an unexplained word left over.

    «хочу массаж» reaches a decision without touching the database at all.
    """
    normalized = normalize(text)
    if not normalized.strip():
        return ClaimDecision(False, "empty")

    # Rule 1 — a masters request names a service. Pure, and the check that
    # ends the overwhelming majority of non-booking turns.
    if not mentions_service(text, extra_stems=extra_stems):
        return ClaimDecision(False, "no_service_named")

    # DRF-1312, now a CLAUSE of the inverted default rather than a standalone
    # exception. Two services enumerated in one turn («массаж и маникюр»)
    # leave no residue — both words are services — yet the branch must still
    # decline: it forwards the raw turn to the catalog as ONE OR-ranked
    # substring, so a part nobody offers scores zero and vanishes, and the
    # user gets a confident half-answer. Only the model can split a sentence
    # into names we are willing to quote back (``show_masters.services``).
    #
    # Costs nothing on the busiest turn: ``split_requested_services`` returns
    # early, before its one query, for any text without an enumeration
    # separator in it.
    if len(_split_requested_services(text)) >= 2:
        return ClaimDecision(False, "composite_request")

    # Rule 2 — and NOTHING ELSE. Every word must be the service itself, glue
    # around a master request, or a city we serve.
    residue = tuple(
        token
        for token in _WORD_RE.findall(normalized)
        if len(token) >= _MIN_MEANING_LEN
        and not token.isdigit()
        and not _is_request_word(token)
        and not _names_service_token(token, extra_stems=extra_stems)
    )
    if not residue:
        return ClaimDecision(True, "show_masters")

    # The only unexplained words still accepted are places we can actually
    # book in. Deliberately last: it is a DB read, and a turn that reaches it
    # has already passed everything free.
    unexplained = _drop_known_cities(residue)
    if not unexplained:
        return ClaimDecision(True, "show_masters")
    return ClaimDecision(False, "unaccounted_words", residue=unexplained)


def claims_direct_show_masters(text: str, *, extra_stems: ExtraStems = ()) -> bool:
    """True when the deterministic show-masters branch may answer this turn.

    The single gate. Both callers use it — the handler, to decide whether to
    run the branch at all, and the branch itself, so a direct call cannot
    bypass the decision.
    """
    decision = decide(text, extra_stems=extra_stems)
    if not decision.claimed:
        logger.info(
            "orchestrator.fast_path.handed_to_concierge reason=%s residue=%s",
            decision.reason,
            ",".join(decision.residue),
        )
    return decision.claimed


def _names_service_token(token: str, *, extra_stems: ExtraStems = ()) -> bool:
    """True when this single word is (part of) a service name.

    A multi-word service («лимфодренажный массаж», «массаж спины») reaches
    :func:`decide` as separate tokens, so each is tested on its own — and a
    word that is in no service vocabulary at all («салоны») correctly
    survives as residue.
    """
    return mentions_service(token, extra_stems=extra_stems)


def _split_requested_services(text: str) -> list[str]:
    """The enumerated service parts of ``text`` — ``[]`` when it is not one.

    Thin wrapper so a catalog hiccup degrades the way this module degrades
    everywhere else: not-claimed, i.e. the concierge answers. That is what the
    two-element placeholder on the failure path means — «treat this as a
    composite», which is the only answer here that cannot produce a confident
    half-answer.
    """
    try:
        from apps.marketplace.discovery import split_requested_services

        return split_requested_services(text)
    except Exception:  # noqa: BLE001 — routing must never break on a catalog read
        logger.warning("orchestrator.fast_path.split_services_failed", exc_info=True)
        return ["", ""]


def _drop_known_cities(tokens: tuple[str, ...]) -> tuple[str, ...]:
    """``tokens`` minus the ones naming a city with bookable masters.

    Best-effort by design: the recognition set is live data behind one small
    DISTINCT (``apps.marketplace.discovery.strip_known_cities``), and a DB
    hiccup must not turn into «this turn is claimed». It degrades to «I could
    not account for these words», i.e. the concierge answers — this module's
    safe direction.
    """
    try:
        from apps.marketplace.discovery import strip_known_cities

        return tuple(strip_known_cities(list(tokens)))
    except Exception:  # noqa: BLE001 — routing must never break on a catalog read
        logger.warning("orchestrator.fast_path.city_lookup_failed", exc_info=True)
        return tokens


# ---------------------------------------------------------------------------
# The roster table — read by the guard test, not by the code above
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolClaim:
    """One concierge tool, and this branch's answer about its turns.

    ``sample_turns`` are not decoration. The guard test runs
    :func:`claims_direct_show_masters` on each of them and asserts the parser
    agrees with ``claimed``, so an entry that SAYS it hands a tool's turns
    over while the parser still swallows them is red, not green.
    """

    tool: str
    claimed: bool
    why: str
    sample_turns: tuple[str, ...]


#: Every tool ``apps.orchestrator.concierge.CONCIERGE_TOOL_SPECS`` arms the
#: model with. Adding a tool there without adding it here fails
#: ``apps/orchestrator/tests/test_fast_path_claim.py`` — that is the whole
#: point of the table.
FAST_PATH_TOOL_CLAIMS: tuple[ToolClaim, ...] = (
    ToolClaim(
        tool="show_masters",
        claimed=True,
        why=(
            "The one turn this branch exists for: a service is named and "
            "nothing else is. Answering it without a model is faster and "
            "cheaper, and when the catalog has someone it is right "
            "(DRF-1102). A zero-result search still hands over (DRF-1283)."
        ),
        sample_turns=(
            "хочу массаж",
            "запиши меня на массаж",
            "покажи массажистов в пензе",
            "где делают лимфодренаж",
            "массаж, пенза",
            "нужен хороший мастер по маникюру",
        ),
    ),
    ToolClaim(
        tool="show_salons",
        claimed=False,
        why=(
            "DRF-1328 itself. «Найди мне САЛОНЫ массажа» names a service, so "
            "the old condition claimed it and answered with masters — the "
            "exact thing DRF-1304's tool description forbids. «салон» is in "
            "no vocabulary this module knows, so the turn is unaccounted for "
            "and goes to the model."
        ),
        sample_turns=(
            "найди мне салоны массажа",
            "какие салоны делают массаж",
            "в каком салоне есть массаж",
            "адрес салона с массажем",
        ),
    ),
    ToolClaim(
        tool="show_services",
        claimed=False,
        why=(
            "A price/menu question names a service too («сколько стоит "
            "массаж»), and answering it with master cards answers a different "
            "question. The catalog tool has the prices; this branch does not."
        ),
        sample_turns=(
            "сколько стоит массаж",
            "какие услуги по массажу у вас есть",
            "прайс на маникюр",
            "что входит в массаж спины",
        ),
    ),
    ToolClaim(
        tool="show_my_records",
        claimed=False,
        why=(
            "A question about the person's OWN data — «что я ел сегодня», "
            "«что ты про меня помнишь» — names no service at all, so this "
            "branch has nothing to claim it by. The near-miss is «покажи мои "
            "записи»: «записи» shares a stem with «записаться», and a branch "
            "that claimed it would answer «here are masters» to «what have I "
            "eaten». The stem is not in this module's vocabulary, so the turn "
            "is unaccounted for and goes to the model (DRF-1302 / DRF-1305)."
        ),
        sample_turns=(
            "что я ел сегодня",
            "мой дневник питания",
            "сколько я выпил воды",
            "что ты про меня помнишь",
            "покажи мои записи",
        ),
    ),
    ToolClaim(
        tool="ask_clarification",
        claimed=False,
        why=(
            "Deciding that a turn is too vague to answer is a judgement about "
            "language, and this parser only recognises turns it is certain "
            "about. The one refusal that is NOT this tool: a criteria-less "
            "turn is answered by ``generate_direct_show_masters_reply`` with "
            "the canon-prescribed question (BOT-003 §9), because an empty "
            "turn needs no judgement."
        ),
        sample_turns=(
            "хочу что-нибудь для себя",
            "посоветуй что выбрать",
        ),
    ),
    ToolClaim(
        tool="health_screening",
        claimed=False,
        why=(
            "A symptom must reach screening BEFORE any other tool (DRF-358 "
            "T04). «Болит спина, хочу массаж» names a service, and the old "
            "condition would have answered it with cards while the symptom "
            "went unread."
        ),
        sample_turns=(
            "болит спина хочу массаж",
            "после массажа кружится голова",
        ),
    ),
    ToolClaim(
        tool="log_water",
        claimed=False,
        why="Nothing about drinking water is a master request; the words are unknown here.",
        sample_turns=(
            "выпил стакан воды",
            "записать 500 мл воды",
        ),
    ),
    ToolClaim(
        tool="clarify_food_entry",
        claimed=False,
        why="A diary entry is not a master request; the words are unknown here.",
        sample_turns=(
            "съел салат и куриную грудку",
            "на обед был суп",
        ),
    ),
    ToolClaim(
        tool="start_nutrition_anketa",
        claimed=False,
        why="Starting the nutrition questionnaire is not a master request.",
        sample_turns=(
            "хочу заполнить анкету по питанию",
            "начать анкету",
        ),
    ),
)

#: Tool name -> claim. Convenience for the guard test and for anyone reading
#: a ``handed_to_concierge`` line in the trace.
FAST_PATH_CLAIM_BY_TOOL: dict[str, ToolClaim] = {
    claim.tool: claim for claim in FAST_PATH_TOOL_CLAIMS
}


__all__ = [
    "FAST_PATH_CLAIM_BY_TOOL",
    "FAST_PATH_TOOL_CLAIMS",
    "ClaimDecision",
    "ToolClaim",
    "claims_direct_show_masters",
    "decide",
]
