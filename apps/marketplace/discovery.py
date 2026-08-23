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
from django.db.models import Case, F, IntegerField, Max, Q, QuerySet, Value, When
from django.db.models.expressions import CombinedExpression
from django.db.models.functions import Coalesce

from apps.catalog.models import CatalogMaster
from apps.marketplace.dto import MasterCard

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
    }
)


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
    without_greeting = _GREETING_RE.sub(" ", raw.casefold())
    words = [
        t for t in re.findall(r"\w+", without_greeting, re.UNICODE) if len(t) >= _MIN_TOKEN_LEN
    ]
    tokens = [t for t in words if t not in _FILLER_TOKENS]
    # If the request was ENTIRELY filler there is nothing to drop back to —
    # keep the raw words so the caller sees a real (if unhelpful) query rather
    # than an empty one it would read as "untokenizable".
    if not tokens:
        tokens = words
    # Keep the LAST tokens, not the first. Russian puts the informative noun at
    # the end, so a request longer than the cap is far likelier to be
    # «…записаться на спортивный массаж» than to lead with the service name.
    return tokens[-_MAX_TOKENS:]


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


def _split_known_cities(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Split query tokens into ``(service_tokens, named_cities)``.

    A token that names a city we serve is NOT a service token — «пензе» can
    never be a substring of a service name, and before DRF-1283 its presence
    in the AND chain is exactly what zeroed the live query. Returned city
    names are the STORED spellings, ready for a ``tenant__city__in`` filter.
    """
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


def _parse_query(raw: str) -> tuple[list[str], list[str]]:
    """Parse a free-text discovery query into ``(service_stems, named_cities)``.

    The ONE parse point, shared by :func:`_bookable_qs` (who matches) and
    :func:`discover_masters` → :func:`_matched_services` (which service
    matched), for the same reason :func:`_service_match_q` is shared: the two
    must never disagree about what the query said.

    Empty stems with NO city means «asked for something we could not turn into
    a usable token» — the caller fails closed. Empty stems WITH a city means
    «named a place, not a service» («мастера в пензе»), which is a perfectly
    answerable request and must not fail closed.
    """
    tokens = _query_tokens(raw)
    service_tokens, named_cities = _split_known_cities(tokens)
    return [t[:_STEM_LEN] for t in service_tokens], named_cities


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
        any_stem |= Q(services_offered__service__name__icontains=stem)
    return _service_row_q() & any_stem


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
            When(row & Q(services_offered__service__name__icontains=stem), then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
        total = term if total is None else total + term
    return Coalesce(Max(total), Value(0), output_field=IntegerField())


def _matched_services(master_ids: list[UUID], stems: list[str]) -> dict[UUID, tuple[UUID, str]]:
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

    if not master_ids or not stems:
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
            _service_match_q(stems) & Q(services_offered__service__ayla_service_id__isnull=False)
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

    # Whitespace-only is "no filter supplied", not "a filter we failed to
    # parse" — the two must not collapse together. Blank conveys no request, so
    # every bookable master is the right answer; «я» conveys a request we
    # cannot serve, and answering it with the whole directory would be wrong.
    if specialization and specialization.strip():
        stems, named_cities = _parse_query(specialization)
        if not stems and not named_cities:
            # The caller asked for something and we could not turn it into a
            # single usable token («я», an emoji, bare punctuation). Fail
            # CLOSED. Falling through would drop the filter entirely and hand
            # back the whole nationwide directory under the heading «Вот
            # мастера, которые могут подойти» — confidently wrong, and worse
            # than an honest empty result.
            return qs.none()

        if named_cities and not city:
            qs = qs.filter(tenant__city__in=named_cities)

        if not stems:
            # Every token named a place we serve («мастера в пензе»). The city
            # filter above IS the answer — there is nothing left to narrow by,
            # and failing closed here would answer «no masters» to a request we
            # can serve, the exact class of lie DRF-1283 exists to remove.
            return qs

        service_match = _service_match_q(stems)

        specialization_match = Q()
        for stem in stems:
            specialization_match |= Q(specialization__icontains=stem)

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
        matched = _matched_services(
            [master.id for master in masters], _parse_query(specialization)[0]
        )
        cards = [
            (
                replace(card, service_id=pair[0], service_name=pair[1])
                if (pair := matched.get(card.master_id)) is not None
                else card
            )
            for card in cards
        ]
    return cards


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
