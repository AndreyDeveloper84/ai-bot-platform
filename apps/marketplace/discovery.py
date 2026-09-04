"""Cross-tenant master discovery — the SOLE sanctioned ``all_tenants``
catalog carve-out (#1018, EPIC #1014).

Everywhere else in the platform, catalog reads are tenant-scoped via
``CatalogMaster.objects`` (auto-filtered by ``current_tenant()``). The
nationwide-discovery vision needs to read masters *across* tenants, so this
module — and only this module, enforced by the ``MKT1`` rule in
``tools/lint/import_boundaries.py`` — uses ``CatalogMaster.all_tenants`` and
maps each row to the public-field :class:`MasterCard` DTO.

Source today is the local catalog mirror; the DTO boundary keeps it
swappable to the Ayla provider-directory API (#249-#251) later.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import NamedTuple
from uuid import UUID

from django.core.paginator import Paginator
from django.db.models import (
    Case,
    Exists,
    F,
    IntegerField,
    Max,
    OuterRef,
    Q,
    QuerySet,
    Value,
    When,
)
from django.db.models.expressions import CombinedExpression
from django.db.models.functions import Coalesce

from apps.catalog.models import CatalogMaster, CatalogService
from apps.marketplace.dto import MasterCard, SalonCard, ServiceCard
from apps.tenancy.models import Tenant

# Cap to keep a discovery call bounded; callers paginate by re-querying with
# a tighter filter for now (cursor pagination lands with the HTTP surface).
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

# Page-based pagination defaults for the public HTTP directory (#249). The
# stdlib Django ``Paginator`` slices the queryset (COUNT + LIMIT/OFFSET), so
# page size — not the list cap above — bounds each call.
_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 50

# Query tokens shorter than this are dropped — a one-character ILIKE
# ``%а%`` matches essentially every service name and adds no signal.
#
# Two is NOT the line at which a token starts carrying signal; it is only the
# line below which it carries none at all. What earns a SHORT token the right
# to be a stem is decided by ``_SHORT_WORD_LEN`` below (DRF-1352).
_MIN_TOKEN_LEN = 2

# Upper bound on tokens per query. Each one becomes an ILIKE against the same
# joined row PLUS a term in the ranking sum, so an unbounded query would be an
# unbounded OR-chain over an unbounded CASE sum.
_MAX_TOKENS = 5

# ─── DRF-1283: OR-ranking, stemming, city recognition ────────────────────
#
# Live pilot 23.08: «покажи массажистов в пензе» answered «мастеров пока не
# нашлось» in 66ms while four masters in that very salon massage. Two
# independent defects produced it and the fix needs both halves.
#
# 1. Tokens were AND-ed. «массаж» found 4, «массаж пенза» found 0 — naming
#    the city, which is ordinary speech and not an edge case, zeroed the
#    query because no service name contains «пенза». Tokens are OR-ed now
#    and the result is RANKED by how many of them ONE service name matched,
#    so the precision AND used to buy survives as ordering instead of as a
#    cliff: an exact «спортивный массаж» still outranks a plain «массаж»,
#    it just no longer erases it.
#
# 2. Matching was one-directional — the user's word was searched for INSIDE
#    the stored name (``name ILIKE %token%``). «масс» found «Массаж» but
#    «массажист» did not: it is LONGER than what is stored. Naming the
#    profession instead of the service found nobody.
#
# ``_STEM_LEN`` answers (2) by truncating each query token to its first 6
# characters before the ILIKE — «массажистов» → «массаж», «маникюрша» →
# «маникю», «эпиляции» → «эпиляц» — which makes the match hold in both
# directions for anything sharing a root of that length. This is deliberately
# NOT a morphological analyser: pymorphy2 / pymystem3 would add a dependency,
# a multi-megabyte dictionary and a per-token lookup to normalise words that a
# 6-character cut already collapses to the same stem, because Russian inflects
# and derives at the END. Six is the shortest cut that stays discriminating in
# this catalog («массаж», «маникю(р)», «эпиляц(ия)», «стрижк(а)»,
# «космет(ология)»): shorter starts merging unrelated services, longer stops
# reaching «массаж» from «массажист».
#
# Truncation only ever WIDENS a match, so on its own it would cost precision.
# It is safe here precisely because of (1): a widened token that matches
# nothing now scores zero instead of zeroing the query, and a token that
# over-matches loses to the tokens that match better. Neither half is safe
# without the other.
_STEM_LEN = 6

# City recognition (DRF-1283). ``tenant.city`` IS the field for this, and
# ``_bookable_qs(city=...)`` already uses it when a caller supplies one — the
# LLM path fills it from the ``show_masters`` tool call. The DETERMINISTIC
# path has no such parser: it forwards the user's raw turn as
# ``specialization``, so «в пензе» arrives as a service token.
#
# Rather than build a geocoder, recognise ONLY the cities we actually serve —
# the distinct ``tenant.city`` values behind bookable masters — and route such
# a token to the city field instead of the service field. A token matches a
# city when the two agree on everything but the last ≤2 characters of the city
# name, which is exactly the width of a Russian case ending: «пензе» ↔
# «Пенза», «москве» ↔ «Москва». That suffix bound is what keeps «краснодаре»
# off «Красноярск» — they share 6 characters, but that leaves 4 of
# «Красноярск» unexplained, not 2.
#
# An unrecognised place name is not geo-search we failed at; it is a token
# with no city behind it, and (1) makes it harmless rather than fatal.
_CITY_MIN_PREFIX = 4
_CITY_MAX_SUFFIX = 2

# ─── DRF-1324: naming a GOAL is not naming a service ─────────────────────
#
# Live pilot 23.08, the first booking ever made through the bot: «запиши на
# лимфодренаж» found the three masters who really do perform it (the service
# relation did its job), but «хочу расслабиться» is a different kind of
# request and the same machinery answers it wrongly. Measured on the contour
# the same evening: «расслабиться» stems to «рассла», the ILIKE finds exactly
# ONE service name containing it — «Массаж ног — глубокое расслабление и
# лимфодренаж» — whose curated goal is ``recharge``, and every one of the
# NINE services actually carrying ``relax`` (Массаж головы, Классический
# массаж, Парный массаж …) is missed. The one hit is a word match with the
# wrong goal; the nine misses are the answer. «хочу подтянуть фигуру» is
# worse still: «подтян» / «фигуру» occur in no service name at all, so the
# turn returns NOBODY while fifteen services carry ``body_shape``.
#
# The catalog already answers this properly. DRF-1308 put the curated goal on
# every mirrored service as ``{"key", "label"}`` — the key deliberately, for
# exactly this filter — and DRF-1317 moved the curation off the «Массаж тела»
# root onto the sub-branches, so the keys are trustworthy. Selecting on the
# key is selection by a FACT of the catalog; selecting on the word is not.
#
# The recognition vocabulary is the LABELS, read from the mirror the same way
# ``_known_cities`` reads the cities: the label is what the client app puts
# in front of the person as a goal chip, so it is also the wording they echo.
# No new vocabulary, no synonym list, no model call — AYLA-DEC-0045 / OD-9 is
# not merely respected here, it is unreachable: nothing infers, the code reads
# a curated key and filters on it.
#
# ### Why a goal query REPLACES the name search rather than joining it
#
# A goal is recognised only when EVERY service token of the query is
# accounted for by ONE goal's label. That is a deliberately tight gate and it
# is what makes the replacement safe:
#
#   «хочу расслабиться»      → {расслабиться} ⊆ «Расслабиться и снять стресс»
#                              → goal relax, name search dropped
#   «снять отёки»            → «отёки» is in no label → NOT a goal query,
#                              the name search runs exactly as before
#   «расслабляющий массаж»   → «массаж» is in no label → name search
#   «лимфодренаж»            → in no label → name search
#
# So the only queries that lose the name search are the ones that named an
# outcome and nothing else — where the name search is precisely what produced
# the wrong answer. Anything with a service word in it keeps DRF-1283's
# OR-ranking untouched. The two modes are mutually exclusive by construction
# (:func:`_parse_query`), which is why no rule is needed for combining them.
#
# ``_GOAL_MIN_PREFIX`` mirrors ``_CITY_MIN_PREFIX``: a token shorter than four
# characters carries no evidence that it named a goal. The stem comparison
# itself reuses ``_STEM_LEN`` — «подтянуть» ↔ «подтянуть», «расслабиться» ↔
# «расслабиться» — for the same reason and with the same limits as everywhere
# else in this module, and NOT a morphological analyser.
_GOAL_MIN_PREFIX = 4

# ─── DRF-1352: what earns a SHORT token the right to be a stem ───────────
#
# Live pilot 24.08. «маникюр» answered honestly — no such service in the
# contour — while «найди мне мастера по маникюру» returned ALL SEVEN bookable
# masters, and so did the bare «по». The stems were ``['найди', 'по',
# 'маникю']``: «по» cleared ``_MIN_TOKEN_LEN`` and went into the OR-chain as
# ``name ILIKE '%по%'``, which matches «под-мышек», «по-верхности»,
# «по-ясницы», «по-сле» — i.e. a large, arbitrary slice of any Russian
# catalog. The polite phrasing got a CONFIDENTLY WRONG answer where the blunt
# one got the right one, which is the worst shape a defect can take: the
# person cannot tell, and cannot guess that being terse would help.
#
# ### Why this is not «add «по» to the filler list»
#
# Behind «по» stand «за», «из», «до», «от», «об», «ко», «при», «для», «под»,
# and every one of them would arrive as its own ticket. Worse, a list is the
# wrong SHAPE of answer here: it says which words are bad, when the question
# is which words are good.
#
# ### The rule, and why the threshold is what it is
#
# ``_STEM_LEN`` is the whole reason substring matching is needed: a stem is a
# six-character PREFIX of an inflected word («массажистов» → «массаж»), and a
# prefix can only be found INSIDE the stored name. But a token shorter than
# the cut was never cut — it IS the whole word the person typed. For such a
# token substring matching buys nothing (there is no suffix to tolerate) and
# costs everything (it matches word interiors, which is exactly the defect).
#
# So a short token is judged as a WORD, and this module already knows how to
# judge one — ``_known_cities`` and ``_known_goals`` both read their
# recognition vocabulary from the live catalog rather than from a list
# somebody maintains. The same answer applies:
#
#   **A token below ``_SHORT_WORD_LEN`` is a stem only if the curated catalog
#   uses it as a whole word, and it then matches at WORD boundaries.**
#
# «лак» is a word of «Гель-лак», «спа» of «СПА-уход», «LPG» of «LPG-массаж» —
# they stay searchable, and none of them had to be foreseen. «по», «за», «из»,
# «до» are words of no service name, so they are not stems, and no preposition
# had to be foreseen either. When a salon adds «СПА» tomorrow, «спа» becomes
# searchable the same day and nobody edits this file.
#
# Both halves are load-bearing and neither is safe alone:
#
# * Attestation alone would be one service name away from re-opening the
#   defect — «Уход ЗА кожей» makes «за» a word of the catalog, and the
#   substring «%за%» then matches «задней поверхности», «загар», «затылок»:
#   the same blowout one preposition along.
# * Word matching alone would not stop it either, because the master's
#   free-text ``specialization`` is matched too, and «Мастер ПО массажу» is
#   how that field is written. Attestation keeps «по» out of the query
#   entirely; word matching keeps an attested short word from becoming a
#   substring licence.
#
# The vocabulary is read from CURATED service names only, never from
# ``specialization`` — free text a master typed would attest exactly the
# function words this exists to exclude.
#
# ### Why four, and not two or six
#
# Four is already this module's line for «carries evidence on its own»:
# ``_CITY_MIN_PREFIX`` and ``_GOAL_MIN_PREFIX`` are both 4, and both say so in
# as many words. The service-name search is the only one of the three matching
# modes that kept 2 — that discrepancy, not the missing preposition, is the
# defect. Six (``_STEM_LEN``) would be the tidier story but is measurably
# wrong: «воск» finds «Восковая депиляция» and «лифт» finds «Лифтинг» today,
# by being real prefixes of longer words, and word-judging them would lose
# both. At four, only two- and three-character tokens are judged as words, and
# a two-or-three-character PREFIX is not a search anybody makes on purpose.
#
# The cost is one small DISTINCT, paid only when a query actually contains a
# short token — the same shape and the same order of magnitude as the two
# vocabulary reads that already run on this path.
_SHORT_WORD_LEN = 4

# Filler words a booking request carries around the actual service name. The
# tool spec asks the model for a service substring, but it does sometimes
# forward the user's phrasing verbatim — «хочу спортивный массаж».
#
# Until DRF-1283 tokens were AND-ed against ONE service name, so a single
# stray «хочу» reduced the whole query to zero results and the user saw
# "мастеров пока не нашлось" for a service the salon actually offers. Tokens
# are OR-ed and ranked now, so no filler word can zero a query any more — but
# one still costs precision (a term that matches nothing flattens the ranking)
# and still consumes one of the _MAX_TOKENS slots. The list keeps its job,
# just a smaller one.
#
# Deliberately a short, literal list and NOT stemming, a stopword corpus, or
# any NLP: it covers the observed phrasings at pilot scale and stays trivially
# auditable. A word here can only ever widen results, never narrow them.
#
# «запиши» / «запишите» / «меня» (DRF-1102): the deterministic new-booking
# branch (apps.orchestrator.concierge.generate_direct_show_masters_reply)
# forwards the user's RAW turn as specialization, not a model-normalized
# substring — so «запиши меня на массаж» arrives whole, and without these
# three the imperative prefix alone zeroed out a query for a service the
# salon actually offers, same failure mode as the «хочу» case above.
_FILLER_TOKENS = frozenset(
    {
        "хочу",
        "хочется",
        "ищу",
        "нужен",
        "нужна",
        "нужно",
        "мне",
        "меня",
        "бы",
        "на",
        "записаться",
        "запись",
        "запиши",
        "запишите",
        "хотел",
        "хотела",
        "можно",
        "пожалуйста",
        "здравствуйте",
        "привет",
        "услуга",
        "услуги",
        "мастер",
        "мастера",
        "салон",
        # DRF-1312 — enumeration glue. A composite request («давай будет
        # несколько: массаж классика, и маникюр») carries a lead-in clause
        # that names no service, and DRF-1312 splits such a request into parts
        # and says out loud when a part matches nothing in the catalog. A part
        # built entirely of these words must therefore reduce to ZERO content
        # tokens (:func:`_content_tokens`), or the bot would announce that
        # «давай будет несколько» is a service it does not offer — a confident
        # lie, and a worse failure than the silence DRF-1312 removes.
        #
        # Same safety argument as the rest of the list: a word here can only
        # widen a search, never narrow it, and none of these nine occurs as a
        # standalone word in a service name.
        "давай",
        "давайте",
        "будет",
        "будут",
        "несколько",
        "ещё",
        "еще",
        "также",
        "тоже",
    }
)


# DRF-1312 — the separators an enumeration of services is written with.
#
# Splits «массаж классика, и маникюр» into parts, so each part can be checked
# against the catalog SEPARATELY and the ones nobody offers can be named out
# loud instead of vanishing into an OR-ranked list that silently answers only
# the half we can serve.
#
# ``re.IGNORECASE`` rather than the module's usual casefold-first, because the
# caller keeps the ORIGINAL substring as the label it will quote back at the
# user — «Маникюр» must not come back as «маникюр» in their own words.
#
# ``-`` is deliberately absent: «гель-лак» is one service, not two. ``:`` is
# present because a lead-in clause ends with one («давай будет несколько:
# массаж…») and the clause must land in its own part to be discarded.
_SERVICE_SPLIT_RE = re.compile(
    r"\s*(?:[,;:/+]|\bи\b|\bплюс\b|\bа\s+также\b)\s*",
    re.UNICODE | re.IGNORECASE,
)

# Upper bound on the parts of one enumeration we will check and quote back.
# Each part costs one EXISTS query and one clause of the reply.
_MAX_SERVICE_PARTS = 5


# Greetings are stripped as PHRASES, before tokenizing, and deliberately not
# as individual filler words. «день» is both half of «добрый день» and half of
# «День красоты» — a real salon package — so listing it as filler silently
# degrades that query to «красоты» and drags in unrelated masters, while NOT
# listing it lets the bare greeting match the package. A word-level list cannot
# express the difference; a phrase can.
# Genitive forms («доброго дня», «доброго времени суток») are as common in
# written Russian as the nominative ones and produce the same zero-result
# fallback if they survive into the AND chain.
#
# No re.IGNORECASE: the input is casefolded before matching, so the flag would
# be dead. Do not add it back without moving the casefold.
_GREETING_RE = re.compile(
    r"\b("
    r"добр(?:ый|ое|ого)\s+(?:день|дня|вечер|вечера|утро|утра)"
    r"|день\s+добрый"
    r"|доброго\s+времени(?:\s+суток)?"
    r")\b",
    re.UNICODE,
)


def _content_tokens(raw: str) -> list[str]:
    """Word runs of ``raw``, greeting- and filler-free, WITHOUT any fallback.

    Split out of :func:`_query_tokens` for DRF-1312. The two differ on exactly
    one question — «what does an all-filler input mean?» — and they must
    answer it differently:

    * A whole QUERY that is all filler still has to be searched with
      something, so :func:`_query_tokens` drops back to the raw words.
    * A PART of an enumeration that is all filler is a lead-in clause, not a
      service («давай будет несколько», «и ещё»), and the honest thing to
      report about it is nothing at all. An empty list is that answer.

    Returning the raw words here instead would make :func:`split_requested_services`
    hand a glue clause to the coverage check, which would find no service
    named «давай будет несколько» and say so out loud.
    """
    without_greeting = _GREETING_RE.sub(" ", raw.casefold())
    words = [
        t for t in re.findall(r"\w+", without_greeting, re.UNICODE) if len(t) >= _MIN_TOKEN_LEN
    ]
    return [t for t in words if t not in _FILLER_TOKENS]


def _query_tokens(raw: str) -> list[str]:
    """Normalize a discovery query into match tokens.

    Tokens are word runs, NOT whitespace-separated chunks. Punctuation must not
    survive into a token: a token is matched as a substring of a service name,
    so «маникюр, педикюр» would look for a service containing the literal
    «маникюр,» and find nothing. Since DRF-1283 that no longer zeroes the whole
    query (tokens are OR-ed, not AND-ed), but it still silently drops a service
    the user named — and a stray comma or period is a far more likely model
    artifact than the guillemets the first version thought to strip.

    Deliberately NOT ``apps.catalog.services.linking.normalize_name``, despite
    the overlap. That helper implements the C6 link contract, where **both**
    sides are normalized in Python before comparison, so its ``ё→е`` step is
    symmetric and safe. Here only the query is normalized — the service name
    is matched raw through ``icontains`` (ILIKE) — so folding ``ё→е`` on one
    side only would turn a perfectly matchable «ёлочный» query into a token
    that can never match the stored «ё». Casefolding is fine because ILIKE is
    already case-insensitive.
    """
    tokens = _content_tokens(raw)
    # If the request was ENTIRELY filler there is nothing to drop back to —
    # keep the raw words so the caller sees a real (if unhelpful) query rather
    # than an empty one it would read as "untokenizable".
    if not tokens:
        without_greeting = _GREETING_RE.sub(" ", raw.casefold())
        tokens = [
            t for t in re.findall(r"\w+", without_greeting, re.UNICODE) if len(t) >= _MIN_TOKEN_LEN
        ]
    # Keep the LAST tokens, not the first. Russian puts the informative noun at
    # the end, so a request longer than the cap is far likelier to be
    # «…записаться на спортивный массаж» than to lead with the service name.
    return tokens[-_MAX_TOKENS:]


def _catalog_words() -> frozenset[str]:
    """Every whole word the live, CURATED service names are built from (DRF-1352).

    The attestation vocabulary for :func:`_attested_tokens`, read from the
    mirror exactly as :func:`_known_cities` reads the cities and
    :func:`_known_goals` reads the goal labels — so «a word we recognise» can
    only ever mean «a word some live service is actually named with».

    ``specialization`` is deliberately NOT in here. It is free text a master
    typed, and it is written «Мастер по массажу» / «Мастер по маникюру» — it
    would attest precisely the function words this vocabulary exists to keep
    out, and it is matched by the same stems (see :func:`_bookable_qs`).

    ``DISTINCT`` on the name column, and the tokenizing is the module's own
    ``\\w+`` so the vocabulary is split the same way a query is. The set is
    small by nature (the pilot's 94 active services yield a few hundred
    words), and the caller only asks for it when a query actually carries a
    short token.
    """
    words: set[str] = set()
    rows = (
        CatalogService.all_tenants.filter(is_active=True)
        .order_by()
        .values_list("name", flat=True)
        .distinct()
    )
    for name in rows:
        words.update(re.findall(r"\w+", (name or "").casefold(), re.UNICODE))
    return frozenset(words)


def _attested_tokens(tokens: list[str], words: frozenset[str] | None = None) -> list[str]:
    """``tokens`` minus the short ones the catalog does not use as words.

    The admission half of DRF-1352 — see the ``_SHORT_WORD_LEN`` block for why
    a short token is judged as a word rather than by its length, and why the
    length line is four.

    Tokens at or above the line pass untouched: they are prefixes, and judging
    a prefix as a word would lose «воск» → «Восковая депиляция».

    The catalog read is skipped entirely when there is nothing short to judge,
    which is the overwhelming majority of queries. ``words`` may be supplied
    by a caller that already has the vocabulary — :func:`split_requested_services`
    classifies every part of an enumeration against the SAME set.
    """
    if all(len(token) >= _SHORT_WORD_LEN for token in tokens):
        return list(tokens)
    if words is None:
        words = _catalog_words()
    return [t for t in tokens if len(t) >= _SHORT_WORD_LEN or t in words]


def _stem_match_q(field: str, stem: str) -> Q:
    """«This stem matches this text field» — as a substring, or as a WORD.

    The matching half of DRF-1352. A stem at or above ``_SHORT_WORD_LEN`` is a
    truncated prefix and must be found INSIDE the stored name, so it keeps the
    ``icontains`` (ILIKE) it has always had. A shorter stem was never
    truncated — it is a whole word that :func:`_attested_tokens` already
    confirmed the catalog uses as one — and it matches only at word
    boundaries, so attesting «за» through «Уход за кожей» cannot turn
    ``%за%`` loose on «задней поверхности».

    The pattern is written with ``[^\\w]`` rather than Postgres's ``\\m``/``\\M``
    so it means the same thing to Django's SQLite ``REGEXP`` (Python ``re``,
    Unicode-aware) as it does to Postgres ``~*`` (where ``\\w`` inside a
    bracket expression expands to ``[[:alnum:]_]``). Case folding of Cyrillic
    under ``~*`` is the same locale property this module's ILIKE matching
    already depends on everywhere else — no new requirement on the database.
    """
    if len(stem) >= _SHORT_WORD_LEN:
        return Q(**{f"{field}__icontains": stem})
    return Q(**{f"{field}__iregex": rf"(^|[^\w]){re.escape(stem)}([^\w]|$)"})


def _known_cities() -> list[str]:
    """The distinct cities we actually have bookable masters in (DRF-1283).

    The recognition set for :func:`_split_known_cities` — deliberately the
    live data and not a gazetteer, so «recognised» can only ever mean «a place
    this marketplace can serve». One small DISTINCT; the ``all_tenants``
    carve-out (MKT1) applies here for the same reason it applies to discovery
    itself — the set spans every tenant.
    """
    return [
        c
        for c in CatalogMaster.all_tenants.filter(
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        .values_list("tenant__city", flat=True)
        .distinct()
        if c
    ]


def _is_city_token(token: str, city_folded: str) -> bool:
    """True when ``token`` names ``city_folded`` in some Russian case form.

    Agreement on everything but the last ``_CITY_MAX_SUFFIX`` characters of
    the CITY — the width of a case ending. See the constants block for why the
    suffix is bounded and not merely the prefix.
    """
    common = 0
    for a, b in zip(token, city_folded):
        if a != b:
            break
        common += 1
    return common >= _CITY_MIN_PREFIX and common >= len(city_folded) - _CITY_MAX_SUFFIX


def _split_known_cities(
    tokens: list[str], cities: list[str] | None = None
) -> tuple[list[str], list[str]]:
    """Split query tokens into ``(service_tokens, named_cities)``.

    A token that names a city we serve is NOT a service token — «пензе» can
    never be a substring of a service name, and before DRF-1283 its presence
    in the AND chain is exactly what zeroed the live query. Returned city
    names are the STORED spellings, ready for a ``tenant__city__in`` filter.

    ``cities`` may be supplied by a caller that already read
    :func:`_known_cities` — :func:`split_requested_services` classifies every
    part of an enumeration against the SAME set and would otherwise repeat
    that DISTINCT once per part on the busiest path in the funnel.
    """
    if cities is None:
        cities = _known_cities()
    if not cities:
        return list(tokens), []
    folded = {c.casefold(): c for c in cities}
    service_tokens: list[str] = []
    named: list[str] = []
    for token in tokens:
        hit = next((stored for f, stored in folded.items() if _is_city_token(token, f)), None)
        if hit is None:
            service_tokens.append(token)
        elif hit not in named:
            named.append(hit)
    return service_tokens, named


def strip_known_cities(tokens: list[str]) -> list[str]:
    """``tokens`` minus the ones naming a city with bookable masters (DRF-1328).

    The public half of :func:`_split_known_cities`, for the pre-LLM claim
    parser (``apps.orchestrator.fast_path``): it needs to know whether the one
    word it could not otherwise account for in «покажи массажистов в пензе» is
    a place we serve, and «recognised city» must mean the same thing there as
    it does inside discovery — the LIVE set, not a gazetteer. Re-deriving it
    there would be a second definition to keep in step.
    """
    service_tokens, _named = _split_known_cities(list(tokens))
    return service_tokens


# ─── DRF-1355: the salon names a ``salon`` argument can resolve to ───────
#
# The live pilot of 24.08 07:51 answered «покажи мне салоны» with the service
# list of a salon the person had never named. The model chose ``show_services``
# and filled its ``salon`` argument out of its own head; the platform executed
# that argument without ever asking whether the person had said it.
#
# AYLA-DEC-0045 / OD-9 says the model is not the authority on what the catalog
# holds — the same rule DRF-1312 applies to service names («не решай сам, есть
# ли услуга»). WHICH SALON a person is asking about is that same kind of
# claim, and it gets the same answer: the model may name a salon, the platform
# rules on whether that name is attributable to the conversation.
#
# This function supplies the half only the catalog can know: the names a
# ``salon`` substring can land on. The routing decision built on top of it
# lives in ``apps.orchestrator.discovery.salon_named_in``, because deciding
# what to ANSWER is not this module's job.
#
# Read from the live mirror rather than from a list somebody maintains, for
# the same reason :func:`_known_cities` and :func:`_known_goals` are: a salon
# that joins tomorrow is recognised the same day and nobody edits this file.


def bookable_salon_names() -> list[str]:
    """The names of every salon on the marketplace surface.

    «Salon» here means what it means everywhere else in this module: a tenant
    with at least one bookable master. Two small queries (the ones
    :func:`_bookable_tenants` already makes) over a set that is a handful of
    rows by nature — six on the pilot — and the only caller reaches it once
    per catalog turn.
    """
    return [tenant.name for tenant in _bookable_tenants().values() if tenant.name]


class ParsedQuery(NamedTuple):
    """What one discovery query asked for, in the three shapes we can match.

    ``stems`` and ``goals`` are MUTUALLY EXCLUSIVE — see :func:`_parse_query`.
    A query either named a service (stems) or named an outcome (goals); the
    ``cities`` are orthogonal to both and narrow either.
    """

    stems: list[str]
    cities: list[str]
    goals: list[str]

    @property
    def is_empty(self) -> bool:
        """True when the query yielded nothing to match on — fail closed."""
        return not self.stems and not self.cities and not self.goals


def _known_goals() -> dict[str, str]:
    """``{goal_key: label}`` for every goal curated onto a live service.

    The recognition vocabulary for :func:`_match_goal_keys` — read from the
    mirror, exactly as :func:`_known_cities` reads the cities, so «a goal we
    recognise» can only ever mean «a goal some live service actually
    carries». The labels are the ones DRF-1308 ships in the ``{"key",
    "label"}`` pair, i.e. the wording the client app shows as a goal chip and
    therefore the wording a person echoes back.

    One query, and ``DISTINCT`` on the jsonb column collapses the pilot's 94
    active services to the handful of distinct goal ARRAYS behind them before
    anything is transferred — the vocabulary is tiny by nature and the row
    count should not decide what this costs. The ``all_tenants`` carve-out
    (MKT1) applies for the same reason it applies to discovery itself: the
    goal taxonomy is shared across tenants.

    A malformed element is skipped rather than raising — the mirror sync
    already drops broken items (DRF-1308), and a read on the query path must
    not become the place that discovers bad data.
    """
    labels: dict[str, str] = {}
    rows = (
        CatalogService.all_tenants.filter(is_active=True)
        .order_by()
        .values_list("goals", flat=True)
        .distinct()
    )
    for row in rows:
        for item in row or []:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            label = item.get("label")
            if isinstance(key, str) and key and isinstance(label, str) and label:
                labels.setdefault(key, label)
    return labels


def _goal_label_words(label: str) -> list[str]:
    """Content words of a goal label, casefolded — the tokens it can be named by.

    «Расслабиться и снять стресс» → ``["расслабиться", "снять", "стресс"]``.
    Words shorter than ``_GOAL_MIN_PREFIX`` are dropped: «и», «в», «о» carry
    no evidence, and letting them count would let any query claim any goal.
    """
    return [
        w for w in re.findall(r"\w+", label.casefold(), re.UNICODE) if len(w) >= _GOAL_MIN_PREFIX
    ]


def _token_names_word(token: str, word: str) -> bool:
    """True when a query token names a goal-label word.

    The same six-character stem cut the service search uses, applied to both
    sides so the match holds in either direction. Both sides must clear
    ``_GOAL_MIN_PREFIX`` first, so a short token can never name a goal.

    The cut is also what keeps «снятие отёков» off ``relax``'s «снять»:
    the two disagree inside six characters, so that query stays a
    service-name query — which is the correct answer for it.
    """
    if len(token) < _GOAL_MIN_PREFIX or len(word) < _GOAL_MIN_PREFIX:
        return False
    return token[:_STEM_LEN] == word[:_STEM_LEN]


def _match_goal_keys(tokens: list[str]) -> list[str]:
    """Goal keys whose label accounts for EVERY token — usually zero or one.

    The tight gate described in the constants block. A goal qualifies only
    when each of the query's service tokens names some content word of that
    goal's label; a single unaccounted token («отёки», «массаж»,
    «лимфодренаж») disqualifies every goal and the caller falls back to the
    name search.

    Requiring one goal to cover the tokens ALONE — rather than letting two
    goals split them between themselves — is what keeps this selection rather
    than interpretation: «подтянуть кожу» is not «body_shape plus skin_care»,
    it is a request this vocabulary cannot account for, and the honest answer
    is to search service names for it.

    Several goals can still qualify when their labels share the naming word;
    the caller OR-s them. Order is by key, so the result is stable.
    """
    if not tokens:
        return []
    matched: list[str] = []
    for key, label in sorted(_known_goals().items()):
        words = _goal_label_words(label)
        if not words:
            continue
        if all(any(_token_names_word(t, w) for w in words) for t in tokens):
            matched.append(key)
    return matched


def _parse_query(raw: str) -> ParsedQuery:
    """Parse a free-text discovery query into stems, cities and goals.

    The ONE parse point, shared by :func:`_bookable_qs` (who matches) and
    :func:`discover_masters` → :func:`_matched_services` (which service
    matched), for the same reason :func:`_service_match_q` is shared: the two
    must never disagree about what the query said.

    Order matters and is load-bearing. Cities are split off FIRST, so «хочу
    расслабиться в пензе» still reads as a goal request narrowed to a city
    rather than as a query with one unaccounted token. Goal recognition then
    runs on what is left, and only if it accounts for ALL of it does the query
    become a goal query — in which case ``stems`` is deliberately EMPTY
    (DRF-1324): the words that named the outcome are not service names, and
    searching for them is the defect being fixed.

    An empty :class:`ParsedQuery` means «asked for something we could not turn
    into anything usable» — the caller fails closed. Stems empty WITH a city
    means «named a place, not a service» («мастера в пензе»), which is a
    perfectly answerable request and must not fail closed.
    """
    tokens = _query_tokens(raw)
    service_tokens, named_cities = _split_known_cities(tokens)
    # DRF-1352 — before anything is matched OR recognised. A short token the
    # catalog does not use as a word is not a service word, and it is not a
    # goal word either: «хочу расслабиться от стресса» carries «от», which
    # `_token_names_word` can never account for, so leaving it in would break
    # goal recognition as surely as it breaks the name search.
    service_tokens = _attested_tokens(service_tokens)
    goal_keys = _match_goal_keys(service_tokens)
    if goal_keys:
        return ParsedQuery(stems=[], cities=named_cities, goals=goal_keys)
    return ParsedQuery(stems=[t[:_STEM_LEN] for t in service_tokens], cities=named_cities, goals=[])


def parse_query(raw: str) -> ParsedQuery:
    """Public wrapper over :func:`_parse_query` for callers OUTSIDE matching.

    One exported entry point rather than a second copy of the rule. Costs the
    two small vocabulary reads (:func:`_known_cities`, :func:`_known_goals`);
    callers already inside this module use the private form and pay them once
    per search.
    """
    return _parse_query(raw)


def query_stems(raw: str) -> list[str]:
    """The stem list of a query, WITHOUT reading the catalog (DRF-1324).

    Half of :func:`_parse_query` — the pure half. City recognition and goal
    recognition both need the catalog, so they are deliberately absent, and a
    city token therefore survives into the list here.

    This exists for the card's booking callback. The reply renderer is a pure
    function of the DTOs it is handed — three suites render cards with no
    database at all, and a renderer that queried would be a layering error,
    not a test problem. So the button carries the stems and the catalog-aware
    half runs at the TAP (:func:`parse_stems`), which is already inside a
    database context.

    A surviving city token is harmless to the name filter it would feed: an
    extra term in an OR that matches no service name adds nothing.
    :func:`parse_stems` strips it properly anyway. The same holds, since
    DRF-1352, for an unattested short token: this list can still carry «по»,
    and :func:`parse_stems` is where it is dropped.
    """
    return [t[:_STEM_LEN] for t in _query_tokens(raw)]


def parse_stems(stems: list[str]) -> ParsedQuery:
    """Apply the catalog-aware half of the parse to stems carried on a callback.

    The counterpart of :func:`query_stems`, and the SAME three steps
    :func:`_parse_query` runs in the same order — city split, then short-word
    attestation (DRF-1352), then goal recognition on what is left — so a
    request read off a button means exactly what it meant when it produced the
    card. Not a second copy of the rule: both call the same three functions.

    Stems are already cut to ``_STEM_LEN``, which is what the goal comparison
    cuts to anyway, so recognition off a stem is identical to recognition off
    the whole word.
    """
    stems = [s for s in stems if s]
    if not stems:
        return ParsedQuery(stems=[], cities=[], goals=[])
    service_stems, cities = _split_known_cities(stems)
    service_stems = _attested_tokens(service_stems)
    goal_keys = _match_goal_keys(service_stems)
    if goal_keys:
        return ParsedQuery(stems=[], cities=cities, goals=goal_keys)
    return ParsedQuery(stems=service_stems, cities=cities, goals=[])


def _trim_filler_edges(part: str) -> str:
    """Drop leading/trailing filler words from an enumeration part (DRF-1312).

    The part is quoted back at the user, so «и ещё маникюр» has to come back as
    «маникюр» — the surviving substring is still their own wording, just
    without the connective that only ever joined it to the previous part.

    Edges only, and never the middle: the words between the first and last
    content word are the service's own name, and «Массаж на дому» must not
    become «Массаж дому» because «на» is in the filler list.
    """
    words = list(re.finditer(r"\w+", part, re.UNICODE))
    content = [m for m in words if m.group(0).casefold() not in _FILLER_TOKENS]
    if not content or len(content) == len(words):
        # All filler (nothing to keep — the caller drops it anyway) or no
        # filler at all: either way the string is already what it should be.
        return part
    return part[content[0].start() : content[-1].end()]


def split_requested_services(raw: str) -> list[str]:
    """Split an ENUMERATION of services into the parts that name a service.

    DRF-1312. «массаж классика, и маникюр» → ``["массаж классика", "маникюр"]``.
    Parts are returned in the caller's OWN spelling, because a caller that
    reports one back to the user must quote what they wrote, not a stem.

    Empty for anything that is not an enumeration — text with no separator in
    it («спортивный массаж») has no part that could have been dropped
    silently, which is the only thing this function exists to find. Answering
    ``[]`` there rather than ``[raw]`` also keeps the ONE query it costs
    (:func:`_known_cities`) off the single-service turn, the busiest in the
    funnel.

    A part survives only if it still has a content token after greeting
    stripping, filler removal (:func:`_content_tokens`) and city recognition
    (:func:`_split_known_cities`). That drops the two kinds of part that name
    no service and must never be reported as a missing one:

    * a lead-in clause — «давай будет несколько», «и ещё» — all filler;
    * a place — «в пензе» in «массаж, в пензе» — a city, not a service.

    So a single-service query with a city («массаж, пенза») yields ONE part,
    not two, and callers keying off ``len(parts) >= 2`` do not mistake it for
    a composite request.

    ### What this is safe to run on

    Only on text a caller is willing to QUOTE BACK. The filler list is short
    and literal by design (see the constants block), so an arbitrary
    conversational turn can always carry glue this does not know — and a part
    that is really glue would be reported as a service we do not offer, which
    is a confident lie and strictly worse than DRF-1312's original silence.

    Its two uses respect that line:

    * ``apps.orchestrator.concierge.generate_direct_show_masters_reply`` runs
      it on the RAW turn only to COUNT parts, never to label one — a
      miscounted turn costs an LLM call, not an untruth;
    * the coverage report runs it on the MODEL's ``specialization`` argument,
      which is already normalized to service wording, and only as the fallback
      for a model that did not fill ``services``.
    """
    chunks: list[str] = []
    for chunk in _SERVICE_SPLIT_RE.split(raw or ""):
        part = _trim_filler_edges(chunk.strip().strip("«»\"'.!?-—–…").strip())
        if part:
            chunks.append(part)
    if len(chunks) < 2:
        return []
    cities = _known_cities()
    # DRF-1352 — the attestation vocabulary is read at most ONCE for the whole
    # enumeration, and only if some part actually carries a short token. A
    # part that reduces to «по» names no service and must not be reported as
    # one, for the same reason a lead-in clause must not be.
    words: frozenset[str] | None = None
    parts: list[str] = []
    for part in chunks:
        service_tokens, _named = _split_known_cities(_content_tokens(part), cities)
        if words is None and any(len(t) < _SHORT_WORD_LEN for t in service_tokens):
            words = _catalog_words()
        if _attested_tokens(service_tokens, words):
            parts.append(part)
        if len(parts) >= _MAX_SERVICE_PARTS:
            break
    return parts


def service_coverage(
    names: list[str],
    *,
    city: str | None = None,
) -> tuple[list[str], list[str]]:
    """Split requested service names into ``(available, missing)`` — by CATALOG.

    DRF-1312. The live failure: «массаж классика, и маникюр» returned five
    massage masters under «Вот мастера, которые могут подойти» while no salon
    in the contour offers a single nail service. Half the request was answered
    and the other half disappeared without a word.

    ``available`` / ``missing`` is that missing half, made explicit. A name is
    ``available`` when :func:`_bookable_qs` — the SAME predicate that produced
    the cards — finds at least one bookable master for that name ALONE. Not
    «the model thinks we do it», not a category lookup (the mirror stores a
    category UUID it cannot resolve, so categories are not available to us at
    all): the catalog answers, one EXISTS per name.

    This is the AYLA-DEC-0045 / OD-9 line drawn in code. Turning a sentence
    into service names is language understanding and may come from the model;
    deciding whether a named service exists is a fact and may not. So the
    caller supplies the names and this function supplies the verdicts.

    ``city`` scopes the verdict exactly as it scopes the answer: if the cards
    were city-filtered, «we don't have it» must mean «not in that city», and
    the caller's wording must say so.

    A name that carries no service token («в пензе», «и ещё») lands in
    NEITHER list — it made no claim, so there is nothing to confirm or deny.
    Names are de-duplicated case-insensitively and capped at
    ``_MAX_SERVICE_PARTS``.
    """
    available: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()
    for raw in list(names)[:_MAX_SERVICE_PARTS]:
        name = str(raw or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        # _content_tokens, NOT _parse_query: the latter falls back to the raw
        # words for an all-filler input («хочу»), which is right for a query
        # that still has to be searched with something and wrong here — a name
        # that reduces to nothing must make no claim, or «хочу» ends up quoted
        # back as a service we do not offer.
        service_tokens, _named_cities = _split_known_cities(_content_tokens(name))
        # DRF-1352, same rule as _parse_query: a short token the catalog does
        # not use as a word makes no claim, so a name that reduces to one
        # («по») belongs in neither list.
        if not _attested_tokens(service_tokens):
            continue
        if _bookable_qs(city=city, specialization=name).exists():
            available.append(name)
        else:
            missing.append(name)
    return available, missing


class PageMeta(NamedTuple):
    """Pagination envelope for a page of discovery results (#249)."""

    page: int
    page_size: int
    total_count: int
    num_pages: int


def _service_row_q() -> Q:
    """Conditions every candidate MasterService row must satisfy, match aside.

    A MasterService row existing IS the statement that the master performs the
    service (there is no status column — see the model); the service itself
    must still be active to be offered.

    Belt-and-braces on inherently cross-tenant querysets: the edge's service
    must belong to the same tenant as the master. The sync path cannot create
    a cross-tenant edge, but discovery is the one reader that sees every
    tenant at once, so it should not depend on a writer-side guarantee to
    avoid surfacing a master for a service they do not offer.
    """
    return Q(services_offered__service__is_active=True) & Q(
        services_offered__service__tenant_id=F("tenant_id")
    )


def _service_match_q(stems: list[str]) -> Q:
    """The service-relation match for one stem list — ANY stem, ONE row.

    One ``Q`` so every condition binds to the SAME joined MasterService row.
    Since DRF-1283 the stems are OR-ed rather than AND-ed: a master surfaces
    when a single service of theirs matches ANY stem, so naming the city
    («массаж пенза») or the profession («массажист») can no longer erase a
    salon that offers exactly the service asked for. What AND used to buy —
    «Спортивный массаж» beating a bare «массаж» — is bought instead by
    :func:`_match_score`, which ranks a row by HOW MANY stems it matched, so
    precision becomes ordering rather than a cliff.

    Binding to one row still matters, for that ranking: a master offering
    «Спортивный маникюр» plus a separate «Тайский массаж» scores 1 for
    «спортивный массаж» (their best single row), not 2, and so ranks below a
    master who actually performs «Спортивный массаж».

    Shared by :func:`_bookable_qs` (who matches) and
    :func:`_matched_services` (which service matched) so the two can never
    drift apart — a master surfaced FOR a service must resolve TO it.
    """
    any_stem = Q()
    for stem in stems:
        any_stem |= _stem_match_q("services_offered__service__name", stem)
    return _service_row_q() & any_stem


def _goal_row_q(goal_keys: list[str]) -> Q:
    """The service-relation match for a GOAL query — ANY goal, ONE row.

    The structural counterpart of :func:`_service_match_q` (DRF-1324). Where
    that one asks «is the query's word inside this service's NAME», this asks
    «does this service CARRY the goal the person named» — a curated fact of
    the catalog (DRF-1308 puts it there, DRF-1317 curates it), not a string
    coincidence.

    ``goals`` is jsonb, so ``__contains=[{"key": k}]`` is a structural
    containment test (``@>``): it matches the element regardless of the
    ``label`` beside the key, which is exactly why DRF-1308 stored the pair
    instead of a bare string.

    Bound into the same joined row as :func:`_service_row_q` for the same
    reason the name match is: the row that qualifies the master must be a row
    the master really performs, in their own tenant.
    """
    any_goal = Q()
    for key in goal_keys:
        any_goal |= Q(services_offered__service__goals__contains=[{"key": key}])
    return _service_row_q() & any_goal


def _relation_match_q(parsed: "ParsedQuery") -> Q:
    """The service-relation match for a parsed query, whichever mode it is in.

    ``stems`` and ``goals`` are mutually exclusive (:func:`_parse_query`), so
    this is a routing point and not a combination rule. It exists so that
    :func:`_bookable_qs` and :func:`_matched_services` cannot drift apart
    about which mode a query was in — the same reason
    :func:`_service_match_q` is shared.
    """
    if parsed.goals:
        return _goal_row_q(parsed.goals)
    return _service_match_q(parsed.stems)


def _match_score(stems: list[str]) -> Coalesce:
    """Rank expression: how many stems the master's BEST service row matched.

    ``MAX`` over the joined rows of a per-row ``CASE`` sum — the aggregate is
    what makes «best row» rather than «total across everything they offer» the
    score, preserving the one-row binding :func:`_service_match_q` documents.

    ``MAX`` (not ``SUM``) is also why this stays correct if Django resolves
    the annotation through a second join of the same relation: duplicating
    rows cannot change a maximum, whereas it would inflate a sum.

    ``COALESCE(..., 0)`` is load-bearing, not defensive. A master matched only
    through the legacy free-text ``specialization`` has NO joined service row,
    so ``MAX`` returns NULL — and Postgres sorts NULLs FIRST under ``DESC``,
    which would rank «matched nothing in the service relation» above every
    real service match. Zero says what NULL meant.
    """
    row = _service_row_q()
    # Annotated because the accumulator changes shape on the second term: one
    # stem is a bare Case, two or more is a CombinedExpression of them.
    total: Case | CombinedExpression | None = None
    for stem in stems:
        term = Case(
            When(
                row & _stem_match_q("services_offered__service__name", stem),
                then=Value(1),
            ),
            default=Value(0),
            output_field=IntegerField(),
        )
        total = term if total is None else total + term
    return Coalesce(Max(total), Value(0), output_field=IntegerField())


def _matched_services(
    master_ids: list[UUID], parsed: "ParsedQuery"
) -> dict[UUID, tuple[UUID, str]]:
    """Resolve which service matched the query, per master — when unambiguous
    AND deliverable to the booking flow.

    Returns ``{master_id: (service_id, service_name)}`` ONLY for masters whose
    query-matching active services collapse to exactly one distinct service.
    A master with several equally-matching services («массаж» → «Спортивный
    массаж» + «Классический массаж») is deliberately absent: auto-picking one
    of them would carry a service the user never chose straight into the
    booking preview. Absent masters fall back to the ask-the-service handoff
    reply.

    "Equally-matching" is DRF-1283's addition. Stems are OR-ed now, so a
    single service matching ONE stem and another matching ALL of them are both
    in the result set — and treating that as ambiguous would silently drop the
    service stamp for the very queries the OR was meant to rescue. Only the
    master's OWN best-scoring rows compete, mirroring :func:`_match_score`
    exactly: «спортивный массаж» resolves to «Спортивный массаж» (2 stems)
    even though «Классический массаж» (1 stem) is also in the set.

    Scored in Python rather than in SQL: the row set is already bounded by the
    discovery ``limit``, and the scoring rule must be provably identical to
    the ranking rule, which is easier to see in eight lines than in a second
    ``CASE`` sum.

    Deliverability gate (review of DRF-962): a stamped service becomes a
    promise on the card — the button must be able to keep it. The booking
    handoff can only ground a service on the Ayla REST path via
    ``ayla_service_id``, so resolution requires ``BOOKING_VIA_AYLA_REST`` ON
    and a non-NULL ``ayla_service_id``. Under the legacy YClients flag the
    mirror's ``external_id`` is the *mysite* pk, not a proven YClients
    service id (`apps/catalog/models.py` vs `apps/skills/booking/skill.py`
    disagree about its family), so no service is ever advertised there —
    cards stay serviceless and the handoff asks for the service instead of
    dispatching an id from an unverified family.

    One query for the whole card set (bounded by the discovery ``limit``);
    the same ``all_tenants`` carve-out and the same match ``Q`` as
    :func:`_bookable_qs`, so resolution can never disagree with discovery.
    """
    from django.conf import settings

    stems = parsed.stems
    if not master_ids or (not stems and not parsed.goals):
        return {}
    if not bool(getattr(settings, "BOOKING_VIA_AYLA_REST", False)):
        return {}
    # ONE .filter() call for the whole condition set: a second filter() on a
    # multi-valued relation would open a SECOND MasterService join, and the
    # values_list below must read from the same joined row the conditions
    # bound to (pinned by the mixed-services test in test_discovery_by_service).
    rows = (
        CatalogMaster.all_tenants.filter(id__in=master_ids)
        .filter(
            _relation_match_q(parsed) & Q(services_offered__service__ayla_service_id__isnull=False)
        )
        .values_list(
            "id",
            "services_offered__service_id",
            "services_offered__service__name",
        )
    )
    # {master_id: (best_score, {(service_id, name), ...})} — only the master's
    # own top tier survives, same rule as _match_score. Stems are already
    # casefolded by _query_tokens, so ``in`` here means what ILIKE meant there.
    #
    # A GOAL query has no stems, so every matching row scores 0 and they all
    # share the top tier — carrying the named goal is not a matter of degree,
    # and ranking one carrier above another would be the recommendation engine
    # this ticket is bounded away from. The «exactly one service» rule below
    # then decides on its own: a master with one service for that goal gets
    # the stamp, a master with four is ambiguous and falls through to the menu
    # (which DRF-1324 narrows by the same goal).
    best: dict[UUID, tuple[int, set[tuple[UUID, str]]]] = {}
    for master_id, service_id, service_name in rows:
        name = service_name or ""
        folded = name.casefold()
        score = sum(1 for stem in stems if stem in folded)
        current = best.get(master_id)
        if current is None or score > current[0]:
            best[master_id] = (score, {(service_id, name)})
        elif score == current[0]:
            current[1].add((service_id, name))
    return {
        master_id: next(iter(pairs))
        for master_id, (_score, pairs) in best.items()
        if len(pairs) == 1
    }


def _bookable_qs(
    *,
    city: str | None = None,
    specialization: str | None = None,
    tenant_id: UUID | None = None,
) -> QuerySet[CatalogMaster]:
    """Cross-tenant queryset of bookable masters, optionally filtered.

    The SOLE ``all_tenants`` carve-out (MKT1). Only ``is_active`` +
    invite-``accepted`` masters (the same ``bookable`` predicate
    customer-facing reads use). Optional ``city`` (exact, case-insensitive, on
    the owning tenant) and ``specialization`` narrow it.

    ### Matching a service (DRF-945)

    ``specialization`` used to filter ``CatalogMaster.specialization`` alone.
    Nothing populates that field — the Ayla specialists feed carries no such
    value — so every service-specific query matched the empty string and
    returned zero masters. That was the live pilot failure: «Пенза,
    спортивный» produced the no-masters-found fallback even though the salon
    offers exactly that service.

    A master now matches when **either** side does:

    * a service they actually perform, joined through ``MasterService`` (the
      canonical relation, mirrored from Ayla's bookable ``SpecialistService``);
    * or the legacy free-text ``specialization``, kept as an OR so
      operator-maintained rows and any future feed that does populate it keep
      working.

    Stems are OR-ed **within one joined row** and the result is ranked by how
    many of them that row matched (DRF-1283 — see the constants block for the
    live failure that forced the change off AND). «спортивный массаж» and
    «массаж спортивный» both put «Спортивный массаж» on top, «массаж» alone
    matches every massage service, and «массаж пенза» no longer matches
    nothing. Word order stops mattering without reaching for fuzzy or vector
    search.

    A token naming a city we serve is routed to ``tenant.city`` instead of the
    service name (:func:`_split_known_cities`) — an explicit ``city`` argument
    still wins, since a caller that parsed the city itself (the LLM path) knows
    better than a token heuristic.

    ### Naming a goal (DRF-1324)

    A query whose every remaining token is accounted for by ONE curated goal
    label («хочу расслабиться», «хочу подтянуть фигуру») selects on the goal
    KEY the mirror carries instead of on the service name. That is a different
    question, not a wider one: on the contour «расслабиться» matches one
    service by name and its curated goal is ``recharge``, while the nine
    services that really carry ``relax`` match nothing. See the constants
    block for why the gate is tight enough to replace the name search rather
    than join it.
    """
    qs = (
        CatalogMaster.all_tenants.filter(
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        .select_related("tenant")  # N+1-safe tenant.city / tenant_id
        .order_by("name", "id")
    )
    if city:
        qs = qs.filter(tenant__city__iexact=city)

    # DRF-1304 - a chip tap addresses a salon by the id the bot itself
    # rendered, never by the name substring the model-called tool matches on:
    # two salons whose names contain one another would resolve the same tap
    # to different places on different days.
    if tenant_id:
        qs = qs.filter(tenant_id=tenant_id)

    # Whitespace-only is "no filter supplied", not "a filter we failed to
    # parse" — the two must not collapse together. Blank conveys no request, so
    # every bookable master is the right answer; «я» conveys a request we
    # cannot serve, and answering it with the whole directory would be wrong.
    if specialization and specialization.strip():
        parsed = _parse_query(specialization)
        stems, named_cities = parsed.stems, parsed.cities
        if parsed.is_empty:
            # The caller asked for something and we could not turn it into a
            # single usable token («я», an emoji, bare punctuation). Fail
            # CLOSED. Falling through would drop the filter entirely and hand
            # back the whole nationwide directory under the heading «Вот
            # мастера, которые могут подойти» — confidently wrong, and worse
            # than an honest empty result.
            return qs.none()

        if named_cities and not city:
            qs = qs.filter(tenant__city__in=named_cities)

        if parsed.goals:
            # The query named an OUTCOME (DRF-1324). Select the masters who
            # perform a service CARRYING that goal and stop — no name match
            # runs, because the words that named the goal are not service
            # names and matching them is the defect: on the contour
            # «расслабиться» finds one service by name and its curated goal is
            # ``recharge``, while all nine ``relax`` services are missed.
            #
            # No ranking annotation: carrying the goal is a yes/no fact, so
            # every result is an equal answer and the stable ``name`` order
            # from above is the honest one. Ordering carriers against each
            # other would be choosing FOR the person — the line the ticket
            # draws between selection and recommendation.
            #
            # DISTINCT is therefore back, and load-bearing. The stem path
            # below drops it because its aggregate annotation groups by the
            # master and collapses the join multiplication for free; with no
            # aggregate to lean on, a master carrying two ``relax`` services
            # would render TWICE — the duplicate-card bug DRF-1283's
            # annotation was written to remove.
            return qs.filter(_goal_row_q(parsed.goals)).distinct()

        if not stems:
            # Every token named a place we serve («мастера в пензе»). The city
            # filter above IS the answer — there is nothing left to narrow by,
            # and failing closed here would answer «no masters» to a request we
            # can serve, the exact class of lie DRF-1283 exists to remove.
            return qs

        service_match = _service_match_q(stems)

        specialization_match = Q()
        for stem in stems:
            specialization_match |= _stem_match_q("specialization", stem)

        # No DISTINCT: annotating with an aggregate groups by the master, which
        # collapses the join multiplication DISTINCT used to clean up (a master
        # offering both «Спортивный массаж» and «Классический массаж» rendered
        # twice for «массаж» without it).
        qs = (
            qs.filter(service_match | specialization_match)
            .annotate(match_score=_match_score(stems))
            .order_by("-match_score", "name", "id")
        )

    return qs


def discover_masters(
    *,
    city: str | None = None,
    specialization: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    resolve_service: bool = False,
) -> list[MasterCard]:
    """Return bookable masters across ALL tenants as public DTOs.

    Only ``is_active`` + invite-``accepted`` masters are returned (the same
    ``bookable`` predicate customer-facing reads use). Optional ``city``
    (exact, case-insensitive, on the owning tenant) and ``specialization``
    narrow the result — the latter matches either a service the master
    actually performs or their free-text specialization, ranked by how well
    (see :func:`_bookable_qs`). ``limit`` is clamped to ``_MAX_LIMIT``, and
    since the result is ranked, the clamp now keeps the BEST matches rather
    than the alphabetically first ones.

    ``resolve_service`` (DRF-962): additionally stamp each card with the ONE
    service that matched the query, when unambiguous (see
    :func:`_matched_services`) — the discovery→booking handoff needs it so a
    card tap lands in booking WITH the service context instead of the
    stale-context dead-end. Off by default: the HTTP directory (#249) and
    other list readers don't pay the extra query.
    """
    limit = max(1, min(limit, _MAX_LIMIT))
    qs = _bookable_qs(city=city, specialization=specialization)
    masters = list(qs[:limit])
    cards = [_to_card(master) for master in masters]
    if resolve_service and specialization and specialization.strip():
        matched = _matched_services([master.id for master in masters], _parse_query(specialization))
        cards = [
            (
                replace(card, service_id=pair[0], service_name=pair[1])
                if (pair := matched.get(card.master_id)) is not None
                else card
            )
            for card in cards
        ]
    return cards


# How many service names a refusal may name back as the alternative it CAN
# serve. Three is the ceiling the salon cards already use
# (``_SALON_SERVICE_SAMPLES``) and the reason is the same one: a refusal that
# answers with a catalogue is a catalogue, and the system prompt forbids those
# («никаких каталог-перечислений»). Three names read as «here is what we do
# instead», twenty read as a price list.
_CITY_SERVICE_SAMPLES = 3

# Ceiling on the (master, service) rows the sample scans. The pilot's
# whole contour is ~500 of them; this is a suggestion, not a survey, and a
# marketplace large enough to exceed the cap has a most-common service well
# inside the first rows anyway.
_SAMPLE_SCAN_ROWS = 2000


def city_service_samples(
    city: str | None = None, *, limit: int = _CITY_SERVICE_SAMPLES
) -> list[str]:
    """Services that ARE bookable in ``city`` right now — at most ``limit`` names.

    DRF-1474. The honest refusal («маникюра в Пензе нет») used to end at
    «назовите другую услугу или другой город», which hands the person the job
    of guessing what this marketplace actually does. On the live turn of
    04.09 they guessed «массаж», it worked, and the transcript then reads as
    though the bot had quietly answered a nail request with a massage list.

    Naming the alternative is what makes it an alternative rather than a
    substitution: the caller states these ARE something else, and states it in
    the same breath as the refusal.

    Ranked by how many bookable masters perform each service — «what most
    people here can be booked for», not an editorial pick — with the name as
    the tiebreak so the same catalog always yields the same three.

    The SAME ``_bookable_qs`` predicate that produced the (empty) card list,
    for the reason :func:`service_coverage` gives: a suggestion this function
    makes must be something discovery would really find.
    """
    limit = max(1, min(int(limit), _CITY_SERVICE_SAMPLES))
    # ``order_by()`` clears the master ordering before the read: the sort
    # columns are not in the selected set, and the ranking below is ours.
    rows = (
        _bookable_qs(city=city)
        .filter(_service_row_q())
        .order_by()
        .values_list("services_offered__service__name", "id")[:_SAMPLE_SCAN_ROWS]
    )
    # Counted in Python rather than by a GROUP BY for the same reason
    # :func:`_matched_services` scores in Python: the row set is bounded by
    # construction, and the rule is easier to see than to reconstruct from an
    # annotate/values pair.
    per_service: dict[str, set[UUID]] = {}
    for name, master_id in rows:
        cleaned = str(name or "").strip()
        if cleaned:
            per_service.setdefault(cleaned, set()).add(master_id)
    ranked = sorted(per_service.items(), key=lambda item: (-len(item[1]), item[0]))
    return [name for name, _masters in ranked[:limit]]


def discover_masters_page(
    *,
    city: str | None = None,
    specialization: str | None = None,
    page: int = 1,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> tuple[list[MasterCard], PageMeta]:
    """Return one page of bookable masters across ALL tenants (#249).

    Page-based pagination via the stdlib :class:`~django.core.paginator.Paginator`
    over the cross-tenant queryset, so COUNT + slice happen in the DB rather
    than over a materialized list. ``page_size`` is clamped to
    ``[1, _MAX_PAGE_SIZE]``; an out-of-range ``page`` is clamped to the last
    page (empty directory still yields page 1). Returns the page's cards plus
    a :class:`PageMeta` envelope.
    """
    page_size = max(1, min(page_size, _MAX_PAGE_SIZE))
    paginator = Paginator(_bookable_qs(city=city, specialization=specialization), page_size)
    page = max(1, min(page, paginator.num_pages))
    page_obj = paginator.page(page)
    cards = [_to_card(master) for master in page_obj.object_list]
    meta = PageMeta(
        page=page,
        page_size=page_size,
        total_count=paginator.count,
        num_pages=paginator.num_pages,
    )
    return cards, meta


def get_master(master_id: UUID) -> MasterCard | None:
    """Return a single bookable master's public card, or ``None`` (#250).

    Looks up by id across ALL tenants through the same ``bookable``
    predicate as discovery; an unknown id OR a non-bookable master
    (inactive / invite not accepted) yields ``None`` → 404 at the view.
    """
    master = _bookable_qs().filter(id=master_id).first()
    return _to_card(master) if master is not None else None


# ─── DRF-1354: finding a master the client named BY NAME ────────────────
#
# Every reader above answers «who does X?». The live pilot of 24.08 asked the
# other question four times in ninety seconds — «запиши к Архипкину Денису
# на завтра» — and nothing in this module could answer it: `specialization`
# matches SERVICES and the master's free-text specialization, never their name.
# So a turn that had already named the person was answered with a list of
# people, including them.
#
# It lives HERE and not in the concierge because ``CatalogMaster.all_tenants``
# is the sole sanctioned cross-tenant carve-out (MKT1) and this module is where
# it is allowed to happen. A name filter written anywhere else would be a
# second carve-out.

#: A name token shorter than this is not a name («к», «на»). Two, not three:
#: «Ян» and «Лю» are real given names on this contour's feed.
_MIN_NAME_TOKEN_LEN = 2

#: Beyond four tokens the caller is passing a sentence, not a name. Every token
#: is AND-ed below, so an extra word can only make the search find nobody — and
#: «никого не нашлось» about a master who is right there is the failure this
#: function exists to remove.
_MAX_NAME_TOKENS = 4

#: Shortest prefix a name token is ever reduced to. Russian names inflect on
#: the ENDING («к Архипкину», «к Денису», «у Сазоновой») while the mirror
#: stores the nominative, so a spoken token is matched by its stem. Two
#: characters is the longest Russian case ending in play here; five is the
#: floor, so «Инна» and «Денис» are never cut at all.
_NAME_STEM_MIN = 5


def _name_stem(token: str) -> str:
    """The prefix of ``token`` that survives Russian case inflection.

    «архипкину» → «архипкин», «денису» → «денис», «инна» → «инна». Chops at
    most two characters and never below :data:`_NAME_STEM_MIN`, so it can only
    ever WIDEN a match — a widened match becomes a disambiguation question
    with real names in it, while a missed one becomes «такого мастера нет»
    about someone who is on the list.
    """
    if len(token) <= _NAME_STEM_MIN:
        return token
    return token[: max(_NAME_STEM_MIN, len(token) - 2)]


def _name_tokens(raw: str) -> list[str]:
    """Word runs of ``raw`` that can plausibly be part of a person's name.

    Fillers are dropped through the SAME list the service search uses
    (:data:`_FILLER_TOKENS`) — «запиши к Денису» must reduce to «денису», or the
    AND below finds nobody. Known city names go too (:func:`strip_known_cities`):
    a model that fills ``master`` with «Денис Пенза» has named one person and
    one place, and the place is already the ``city`` argument's job.

    Empty means «this is not a name», and the caller must NOT fall back to an
    unfiltered read: matching nobody is honest, matching everybody would answer
    «запиши к нему» with the whole directory.
    """
    without_greeting = _GREETING_RE.sub(" ", (raw or "").casefold())
    words = [
        t
        for t in re.findall(r"\w+", without_greeting, re.UNICODE)
        if len(t) >= _MIN_NAME_TOKEN_LEN and t not in _FILLER_TOKENS
    ]
    return strip_known_cities(words)[:_MAX_NAME_TOKENS]


def find_masters_by_name(
    name: str,
    *,
    city: str | None = None,
    service: str | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[MasterCard]:
    """Bookable masters whose NAME contains every token of ``name``.

    ``[]`` when the query tokenizes to nothing or matches nobody — never the
    unfiltered directory (see :func:`_name_tokens`).

    Tokens are AND-ed as substrings, so word ORDER does not matter: «Архипкин
    Денис» and «Денис Архипкин» find the same person, and a bare «Денис»
    finds every Денис — which is the POINT: several results is the answer
    «which one did you mean», and the caller asks it with their names in hand
    instead of redrawing the catalog.

    Substring and not word-boundary on purpose: Russian names inflect in the
    turn («к Денису», «к Архипкину») while the mirror stores the nominative,
    so the STORED name is matched against a prefix of the spoken one. That is
    why the token is trimmed to its stem below rather than compared whole: an
    inflected «денису» would never be a substring of «Денис».

    ``city`` narrows only when it LEAVES someone: an exact ``tenant.city``
    match is the model's guess about a field the person never typed, and a
    wrong guess here would report the named master as missing. ``service``,
    when given, stamps the one service that matched it (same rule and same
    deliverability gate as :func:`discover_masters`), so the booking handoff
    starts with the service context instead of asking for it again.
    """
    tokens = _name_tokens(name)
    if not tokens:
        return []
    qs = _bookable_qs()
    for token in tokens:
        # Match on the STEM of the spoken token so an inflected form still
        # finds the nominative in the mirror (:func:`_name_stem`).
        qs = qs.filter(name__icontains=_name_stem(token))
    masters = list(qs[: max(1, min(limit, _MAX_LIMIT))])
    if city and city.strip() and len(masters) > 1:
        folded = city.strip().casefold()
        in_city = [m for m in masters if (m.tenant.city or "").casefold() == folded]
        if in_city:
            masters = in_city
    cards = [_to_card(master) for master in masters]
    if service and service.strip():
        matched = _matched_services([master.id for master in masters], _parse_query(service))
        cards = [
            (
                replace(card, service_id=pair[0], service_name=pair[1])
                if (pair := matched.get(card.master_id)) is not None
                else card
            )
            for card in cards
        ]
    return cards


# ─── DRF-1304: salons & services on the discovery surface ────────────────────
#
# The concierge could show masters (``show_masters``) but had no tool for the
# two questions the live owner actually asked on 23.08: «какие салоны у нас
# есть?» and «что у вас есть по лицу». These two readers answer them from the
# same mirror and the same bookable predicate as master discovery — a salon is
# a tenant with at least one bookable master, a service is an ACTIVE mirror row
# of such a tenant. Nothing here invents data: an empty result is the honest
# answer, and missing fields (address, price, duration) stay missing.
#
# Deliberately NOT grouped by the canonical service template: the pilot
# salon's canonical coverage is 0 of 58 rows (35 of 36 for the loaded salons)
# and the template link has no mirror column — it rides in
# ``CatalogService.raw`` at best. A canonical grouping would silently show a
# fraction of the catalog; a flat list shows it all.

# How many service names ride a salon card as the «что там делают» sample.
_SALON_SERVICE_SAMPLES = 3


def _bookable_tenants(
    *, city: str | None = None, tenant_id: UUID | None = None
) -> dict[UUID, Tenant]:
    """Active tenants having at least one bookable master, keyed by id.

    ``order_by()`` clears the master ordering before DISTINCT — Postgres
    rejects SELECT DISTINCT whose ORDER BY columns are not in the select list.
    """
    tenant_ids = (
        _bookable_qs(city=city, tenant_id=tenant_id)
        .order_by()
        .values_list("tenant_id", flat=True)
        .distinct()
    )
    return {t.id: t for t in Tenant.objects.filter(id__in=tenant_ids)}


def _master_address(master: CatalogMaster) -> str:
    """The salon address as mirrored on a master row, or "".

    The address is per-master, not per-tenant: ``Tenant`` has no address
    column — the Ayla specialists feed carries it in the specialist payload,
    mirrored into ``CatalogMaster.raw``. Four of the pilot's masters carry
    none, so "" is a normal value, not an error.
    """
    raw = master.raw
    if not isinstance(raw, dict):
        return ""
    return str(raw.get("address") or "").strip()


def discover_salons(
    *,
    city: str | None = None,
    tenant_id: UUID | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[SalonCard]:
    """Return salons (tenants with bookable masters) as public DTOs.

    Optional ``city`` (exact, case-insensitive, on the tenant) narrows the
    result — same semantics as :func:`discover_masters`; ``tenant_id`` narrows
    it to one salon (the chip-tap read — see :func:`get_salon`). Each card carries
    the salon's address (first non-empty one among its bookable masters —
    "" when none of them has one), its bookable-master count, and a count +
    short sample of its active services («что там делают»). Three bounded
    queries total: masters, tenants, service names.
    """
    limit = max(1, min(limit, _MAX_LIMIT))
    masters = list(_bookable_qs(city=city, tenant_id=tenant_id))
    if not masters:
        return []
    masters_by_tenant: dict[UUID, list[CatalogMaster]] = {}
    for master in masters:
        masters_by_tenant.setdefault(master.tenant_id, []).append(master)
    tenants = _bookable_tenants(city=city, tenant_id=tenant_id)
    service_rows = (
        CatalogService.all_tenants.filter(tenant_id__in=tenants, is_active=True)
        .order_by("name")
        .values_list("tenant_id", "name")
    )
    services_by_tenant: dict[UUID, list[str]] = {}
    for tenant_id, service_name in service_rows:
        services_by_tenant.setdefault(tenant_id, []).append(service_name)

    cards: list[SalonCard] = []
    for tenant_id, tenant in sorted(tenants.items(), key=lambda kv: (kv[1].name, str(kv[0]))):
        salon_masters = masters_by_tenant.get(tenant_id, [])
        if not salon_masters:
            continue  # inactive tenant — its masters are not a public salon
        address = next((a for a in (_master_address(m) for m in salon_masters) if a), "")
        service_names = services_by_tenant.get(tenant_id, [])
        cards.append(
            SalonCard(
                tenant_id=tenant_id,
                name=tenant.name,
                city=tenant.city,
                address=address,
                master_count=len(salon_masters),
                service_count=len(service_names),
                sample_services=tuple(service_names[:_SALON_SERVICE_SAMPLES]),
            )
        )
        if len(cards) >= limit:
            break
    return cards


def get_salon(tenant_id: UUID) -> SalonCard | None:
    """Return one salon's public card by tenant id, or ``None`` (DRF-1304).

    The read behind a salon chip's tap. ``None`` means the salon stopped being
    a salon between the render and the tap — went inactive, or its last
    bookable master did. The caller says so; it must not fall back to a name
    search, which could land the tap on a different salon.
    """
    return next(iter(discover_salons(tenant_id=tenant_id, limit=1)), None)


def service_rows_match_q(parsed: "ParsedQuery") -> Q:
    """Narrow a ``CatalogService`` queryset by a parsed query — the ROW form.

    The counterpart of :func:`_relation_match_q`, for callers holding services
    directly rather than reaching them through a master. Same routing, same
    mutual exclusion: a goal query tests the curated ``goals`` key, a service
    query tests the name.

    Exported because the discovery → booking handoff must narrow the master's
    service menu by the SAME request that surfaced that master (DRF-1324). The
    live failure it removes: «запиши на лимфодренаж» found the three masters
    who really perform it, and then offered the first ten of Сазонова's
    nineteen services in alphabetical order — «Биоэнергетический массаж
    детский» second, the two lymphatic-drainage services sixth and seventh —
    and the booking that came out of that tap was for the children's massage.
    The master was selected by the request; the menu behind them was not.

    An empty ``Q()`` — a parse that yielded neither stems nor goals — matches
    everything. Callers must check the parse before using it, exactly as
    :func:`_bookable_qs` fails closed rather than dropping its filter.
    """
    if parsed.goals:
        any_goal = Q()
        for key in parsed.goals:
            any_goal |= Q(goals__contains=[{"key": key}])
        return any_goal
    any_stem = Q()
    for stem in parsed.stems:
        any_stem |= _stem_match_q("name", stem)
    return any_stem


def service_rows_score(parsed: "ParsedQuery") -> Case | CombinedExpression | None:
    """Rank expression for :func:`service_rows_match_q`, or ``None``.

    ``None`` for a goal query, deliberately: carrying a goal is a yes/no fact
    and ordering its carriers against each other would be recommendation, not
    selection. Callers fall back to their stable name order there.
    """
    score: Case | CombinedExpression | None = None
    for stem in parsed.stems:
        term = Case(
            When(_stem_match_q("name", stem), then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
        score = term if score is None else score + term
    return score


def discover_services(
    *,
    salon: str | None = None,
    tenant_id: UUID | None = None,
    city: str | None = None,
    query: str | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[ServiceCard]:
    """Return active services of salons on the platform, as public DTOs.

    Filters (all optional, AND-ed): ``salon`` — substring of the tenant name;
    ``tenant_id`` — that one salon, exactly (the chip-tap read: the button
    carries the id, so the follow-up must not re-run a name match);
    ``city`` — exact, case-insensitive, on the tenant; ``query`` — free text
    matched against service names through the same stem machinery as master
    discovery (:func:`_parse_query`): tokens OR-ed, ranked by how many stems
    one name matched, and a token naming a city we serve routes to the city
    filter, so «массаж в пензе» works here exactly as it does for masters.
    A query that names a GOAL and nothing else selects on the curated goal key
    instead (DRF-1324), unranked — see :func:`service_rows_score`.
    An untokenizable query fails CLOSED (empty list) rather than dropping the
    filter — same posture as ``_bookable_qs``.

    Only services of tenants with at least one bookable master are shown:
    a salon no client can book at is not on the surface.
    """
    limit = max(1, min(limit, _MAX_LIMIT))
    bookable_tenant_ids = _bookable_qs().order_by().values_list("tenant_id", flat=True).distinct()
    qs = CatalogService.all_tenants.filter(
        is_active=True,
        tenant__is_active=True,
        tenant_id__in=bookable_tenant_ids,
    ).select_related("tenant")

    salon = (salon or "").strip()
    city = (city or "").strip()
    query = (query or "").strip()
    if tenant_id:
        qs = qs.filter(tenant_id=tenant_id)
    if salon:
        qs = qs.filter(tenant__name__icontains=salon)
    if city:
        qs = qs.filter(tenant__city__iexact=city)

    order: tuple[str, ...] = ("tenant__name", "name", "id")
    if query:
        parsed = _parse_query(query)
        stems, named_cities = parsed.stems, parsed.cities
        if parsed.is_empty:
            return []
        if named_cities and not city:
            qs = qs.filter(tenant__city__in=named_cities)
        if parsed.goals:
            # «хочу расслабиться» selects the services that CARRY ``relax``,
            # not the ones whose name happens to contain «рассла» (DRF-1324).
            # No ranking annotation — see :func:`service_rows_score`.
            qs = qs.filter(service_rows_match_q(parsed))
        elif stems:
            any_stem = Q()
            # No join multiplication here (the tenant join is many-to-one and
            # the filter binds the row's own name), so a plain per-row CASE
            # sum ranks — MAX-over-rows is the master-side requirement only.
            score: Case | CombinedExpression | None = None
            for stem in stems:
                cond = _stem_match_q("name", stem)
                any_stem |= cond
                term = Case(
                    When(cond, then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                )
                score = term if score is None else score + term
            qs = qs.filter(any_stem).annotate(match_score=score)
            order = ("-match_score", "name", "id")
    # DRF-1304 — «is there anyone to book with for this service» decides
    # whether the renderer may put a chip on the line. Nobody performing it is
    # a normal state (a salon lists a service its masters are not mapped to),
    # and a chip leading there would spend the user's trust on a dead end.
    # ``.order_by()`` because an EXISTS subquery has nothing to sort.
    performs_it = _bookable_qs().order_by().filter(services_offered__service_id=OuterRef("pk"))
    qs = qs.annotate(has_bookable_master=Exists(performs_it))

    rows = qs.order_by(*order)[:limit]
    return [
        ServiceCard(
            tenant_id=service.tenant_id,
            service_id=service.id,
            name=service.name,
            price_from=service.price_from,
            duration_min=service.duration_min,
            salon_name=service.tenant.name,
            city=service.tenant.city,
            has_bookable_master=service.has_bookable_master,
        )
        for service in rows
    ]


def discover_masters_for_service(
    service_id: UUID, *, limit: int = _DEFAULT_LIMIT
) -> list[MasterCard]:
    """Return the bookable masters who perform ONE service (DRF-1304).

    The read behind a service chip's tap. Each card is stamped with that
    service (id + name), so the booking button the card carries enters booking
    WITH the service context — the same DRF-962 seam ``resolve_service`` fills
    on the query path, except here nothing is inferred from text: the user
    tapped the service itself.

    Empty list when the service is gone or inactive, its salon went inactive,
    or nobody bookable performs it any more. All of those mean «no one to book
    with», and the caller must say that rather than invent a master.
    """
    limit = max(1, min(limit, _MAX_LIMIT))
    service = (
        CatalogService.all_tenants.select_related("tenant")
        .filter(id=service_id, is_active=True, tenant__is_active=True)
        .first()
    )
    if service is None:
        return []
    # tenant_id from the SERVICE, not from the caller: an edge may only bind a
    # master to a service of their own tenant (see _service_row_q).
    masters = list(
        _bookable_qs(tenant_id=service.tenant_id).filter(services_offered__service_id=service.id)[
            :limit
        ]
    )
    return [
        replace(_to_card(master), service_id=service.id, service_name=service.name)
        for master in masters
    ]


def _to_card(master: CatalogMaster) -> MasterCard:
    """Map a catalog row to the public DTO — the single projection point.

    Deliberately enumerates each public field by hand (no ``**vars``) so a
    new commercial field on ``CatalogMaster`` can never silently leak into
    discovery output. ``test_dto`` pins the allowed field set.
    """
    return MasterCard(
        tenant_id=master.tenant_id,
        master_id=master.id,
        name=master.name,
        specialization=master.specialization,
        rating=master.rating,
        photo_url=master.photo_url,
        city=master.tenant.city,
    )
