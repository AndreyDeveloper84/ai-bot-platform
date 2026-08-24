"""Tenant-less discovery reply generator (#1026 / EPIC #1014).

The nationwide bot answers a discovery turn at ``current_tenant()=None`` using
the frozen ayla-ai-core marketplace voice (``AYLA_MARKETPLACE_VOICE``) and
bot-platform's OWN LLM runtime (``apps.llm.router``) — the same mechanism the
per-tenant skills use. Since W5 (pilot 2026-08-15) the concierge DM itself
runs on ayla-ai-core's ``AIConcierge`` — see
:mod:`apps.orchestrator.concierge`. This module keeps the shared building
blocks (prompt builder, ``SHOW_MASTERS_TOOL_SPEC`` — joined by
``SHOW_SALONS_TOOL_SPEC`` / ``SHOW_SERVICES_TOOL_SPEC`` and their deterministic
executor since DRF-1304 — and the card renderers) plus the
hand-rolled reply generator as the tested fallback.

Tenant-independent by construction: ``get_provider(None, ...)`` short-circuits
to the per-skill / org-wide provider tier, and the prompt reads NO
tenant-scoped / commercial data. Any LLM failure degrades to a safe fallback
line, never a 500.

Memory surfacing (M-C1 / #1101): the caller MAY pass an optional
``personal_context`` block — the user's GREEN cross-channel memory + UPC summary
(read via ``apps.identity.services.memory_reader``, rendered by
``apps.persona.memory_surface``). ``UserPersonalContext`` is cross-tenant by
design (ADR-0011) and carries no commercial/tenant-scoped state, so surfacing it
on the tenant-less path is consistent with the discovery contract. When absent
the prompt is unchanged (happy-path intact).
"""

from __future__ import annotations

import base64

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from apps.llm.router import get_router
from apps.marketplace.discovery import (
    discover_masters,
    discover_masters_for_service,
    discover_salons,
    discover_services,
    get_salon,
    query_stems,
    service_coverage,
    split_requested_services,
)
from apps.marketplace.dto import MasterCard, SalonCard, ServiceCard
from apps.orchestrator.llm.templates import get_fallback
from apps.persona.memory_surface import render_personal_context

if TYPE_CHECKING:
    from apps.identity.services.memory_reader import PersonalContextView

logger = logging.getLogger(__name__)

# Provider-routing skill slug (Tier-2 `SKILL_LLM_PROVIDER` lookup, optional).
DISCOVERY_SKILL = "discovery"

_MAX_REPLY_CHARS = 600
_MAX_MASTER_CARDS = 5
# Five, like the master cards: «какие салоны у вас есть» asks for an answer,
# not for the catalog. The «…и это не все» tail carries the rest (DRF-1304).
_MAX_SALON_CARDS = 5
_MAX_SERVICE_CARDS = 8

# Budget for the DETERMINISTIC catalog lists (DRF-1304). _MAX_REPLY_CHARS is a
# leash on the MODEL's prose — it is literally in the prompt («Ответ не длиннее
# 600 символов») — and a card list is neither prose nor model output. Real
# Penza rows are long («Центр коррекции фигуры «Afrodita» — Пенза, ул.
# Московская, 74, БЦ «Московский», 1 этаж»), so 600 cut five salons mid-word
# and took the tail hint with them, while the chips for the cut rows still
# rendered — a message whose text and buttons disagree. MAX's own limit is far
# above this.
_MAX_CATALOG_REPLY_CHARS = 1400

# Callback prefix for the discovery → booking handoff button (#1020). Carries
# the PUBLIC ids from the MasterCard DTO:
# ``cb:discover:book:{tenant_id}:{master_id}`` — plus, when discovery resolved
# the queried service unambiguously (DRF-962),
# ``cb:discover:book:{tenant_id}:{master_id}:{service_id}``. The global handler
# detects this, enters tenant_scope(T), and routes into the per-tenant booking
# flow WITH the service context — without it the booking skill's pick_master
# guard correctly refuses the serviceless tap («Контекст записи устарел»).
# No commercial data is in the callback.
CALLBACK_DISCOVER_BOOK_PREFIX = "cb:discover:book:"

# Callback prefixes for the catalog chips (DRF-1304). Same ``cb:{domain}:
# {action}:{ref}`` shape :func:`apps.orchestrator.ui.keyboards.parse_callback`
# decodes, and the same rule as the booking prefix above: the ref is a PUBLIC
# id the bot itself just rendered, never free text.
#
#   cb:catalog:services:{tenant_id}   salon chip  -> that salon's services
#   cb:catalog:masters:{service_id}   service chip -> who performs it
#
# The second one lands on ``_render_master_cards``, whose buttons are the
# booking prefix above — so the chain «какие салоны» -> услуги -> мастер ->
# запись is tappable end to end, and the model is asked nothing after the
# first turn: every step below is a deterministic read.
CALLBACK_CATALOG_SERVICES_PREFIX = "cb:catalog:services:"
CALLBACK_CATALOG_MASTERS_PREFIX = "cb:catalog:masters:"
CATALOG_CALLBACK_PREFIXES = (
    CALLBACK_CATALOG_SERVICES_PREFIX,
    CALLBACK_CATALOG_MASTERS_PREFIX,
)

# OpenAI-shaped function spec — the discovery LLM calls this when the user wants
# to find/see masters. We execute it via the sanctioned marketplace carve-out
# (apps.marketplace.discovery.discover_masters), the SOLE cross-tenant catalog
# reader. The tool only takes public filters; no tenant/commercial inputs.
SHOW_MASTERS_TOOL_SPEC: dict[str, Any] = {
    "name": "show_masters",
    "description": (
        "Show bookable beauty masters across all salons matching the user's "
        "request. Call this when the user wants to find, browse, or pick a "
        "master. Requires at least a city or a specialization: with neither, "
        "there is nothing to match on — use ask_clarification instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City to filter by (optional)."},
            "specialization": {
                "type": "string",
                "description": "Specialization / service substring (optional), e.g. 'маникюр'.",
            },
            # DRF-1312. The user says «массаж и маникюр»; the answer used to be
            # five massage masters and silence about the nails, because the
            # request reached the catalog as ONE substring and a half of it
            # that matches nothing simply scores zero.
            #
            # The model is asked to name the parts because splitting a
            # sentence into services is language understanding. It is NOT
            # asked whether we offer them: the platform checks each name
            # against the catalog itself (AYLA-DEC-0045 / OD-9 — the model is
            # not the authority on what exists) and states the missing ones
            # verbatim in the reply.
            "services": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": _MAX_MASTER_CARDS,
                "description": (
                    "EVERY distinct service the user named, one per element, in "
                    "their own words — e.g. ['массаж классика', 'маникюр'] for "
                    "«давай будет несколько: массаж классика, и маникюр». Always "
                    "fill this when more than one service is named: the platform "
                    "checks each against the catalog separately and tells the "
                    "user which ones nobody offers. Do NOT judge availability "
                    "yourself and do NOT drop a service you think is missing. "
                    "Fill `specialization` as well."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_MASTER_CARDS,
                "description": "How many masters to show.",
            },
        },
        "required": [],
    },
}

# OpenAI-shaped function specs (DRF-1304) — the two questions the concierge
# could not answer on the live pilot (23.08: «какие салоны у нас есть?» met
# silence, while the mirror held 6 tenants / 9 masters / 94 services). Same
# flat shape as SHOW_MASTERS_TOOL_SPEC; executed through the same sanctioned
# marketplace carve-out, which is now the sole cross-tenant reader for salons
# (discover_salons) and services (discover_services) too.
SHOW_SALONS_TOOL_SPEC: dict[str, Any] = {
    "name": "show_salons",
    "description": (
        "Показать подключённые салоны (НЕ отдельных мастеров): название, город, "
        "адрес и что там делают. Вызывай, когда пользователь спрашивает про салоны "
        "или адреса — «какие салоны у вас есть», «где вы находитесь», «куда можно "
        "прийти». Город необязателен: без него перечисли все подключённые салоны "
        "(их немного — это ответ на вопрос, а не перечисление каталога)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "Город для фильтра (необязательно)."},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_SALON_CARDS,
                "description": "Сколько салонов показать.",
            },
        },
        "required": [],
    },
}

SHOW_SERVICES_TOOL_SPEC: dict[str, Any] = {
    "name": "show_services",
    "description": (
        "Показать услуги с ценой и длительностью. Вызывай, когда пользователь "
        "спрашивает, что делают в конкретном салоне («какие услуги в BodyFormula»), "
        "или ищет услугу по запросу («что у вас есть по лицу», «сколько стоит "
        "массаж»). Нужен хотя бы один фильтр — салон, город или запрос; без них "
        "уточняй через ask_clarification, каталог целиком не перечисляй."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "salon": {
                "type": "string",
                "description": "Название салона (подстрока, необязательно), e.g. 'BodyFormula'.",
            },
            "city": {"type": "string", "description": "Город для фильтра (необязательно)."},
            "query": {
                "type": "string",
                "description": "Подстрока услуги (необязательно), e.g. 'лицо', 'массаж'.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_SERVICE_CARDS,
                "description": "Сколько услуг показать.",
            },
        },
        "required": [],
    },
}

#: action_type values the concierge executes deterministically — real mirror
#: data rendered as-is, no second model pass spent on rephrasing them.
CATALOG_TOOL_ACTIONS = frozenset({SHOW_SALONS_TOOL_SPEC["name"], SHOW_SERVICES_TOOL_SPEC["name"]})

#: The three shapes a clarification turn can take (DRF-1362, owner decision of
#: 22.08 «P0 ADAPTERS»). The owner's decisions call these "existing
#: conversational states"; they were not. A repo-wide search for
#: ``CONFIRM_ONE`` / ``CHOOSE_MANY`` / ``FREE_CLARIFICATION`` over ``apps/``,
#: ``docs/`` and ``tests/`` returned nothing — the only clarification
#: instrument that existed was :data:`ASK_CLARIFICATION_TOOL_SPEC` below, and
#: it carried no mode at all.
#:
#: Why the mode must be *stated* rather than guessed: from the consumer's
#: side, "confirm one of these" and "here are some ideas, or say your own"
#: render identically today — a question plus N buttons. The difference is
#: entirely about what a tap MEANS and whether typing something else is an
#: equally valid answer, and that knowledge lives in the model. Without the
#: metadata a consumer cannot tell them apart even though it already draws
#: both, so the two screens of C02 / C03 have nothing to branch on.
#:
#: Deliberately separable from the multi-select rendering below: the mode is
#: useful on its own, on the clarification turns that already ship.
CLARIFICATION_MODE_CONFIRM_ONE = "confirm_one"
CLARIFICATION_MODE_CHOOSE_MANY = "choose_many"
CLARIFICATION_MODE_FREE = "free"

CLARIFICATION_MODES = (
    CLARIFICATION_MODE_CONFIRM_ONE,
    CLARIFICATION_MODE_CHOOSE_MANY,
    CLARIFICATION_MODE_FREE,
)

# OpenAI-shaped function spec (DRF-1102) — lets the concierge ask a clarifying
# question AS A TOOL CALL, with tappable options, instead of the only other
# option it has today: plain assistant text. Plain text doesn't move the
# conversation state, so a user who can't guess the exact catalog wording
# (e.g. a service name) gets stuck re-answering the same question forever
# (DRF-1102 §1). Shape mirrors ayla-ai-core's own ``ask_clarification`` tool
# (``ayla_ai_core.tools.ASK_CLARIFICATION``) — kept as a local FLAT spec, not
# a direct import, because ``apps.llm.providers.openai_provider`` wraps every
# entry of ``tools`` in ``{"type": "function", "function": spec}`` itself;
# importing ai-core's already-nested constant here would double-wrap it.
ASK_CLARIFICATION_TOOL_SPEC: dict[str, Any] = {
    "name": "ask_clarification",
    "description": (
        "Ask a clarifying question with suggested answer options, when the "
        "user's request is genuinely unclear. Prefer this over asking in "
        "plain text — options let the user answer with one tap instead of "
        "having to guess exact wording."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 5 short tappable answer options (optional).",
                "maxItems": 5,
            },
            # DRF-1362. Optional and last, so a model that ignores it behaves
            # exactly as it does today — `normalize_clarification_mode` then
            # derives the mode from the options, which is what every existing
            # clarification in the pilot already means.
            "mode": {
                "type": "string",
                "enum": list(CLARIFICATION_MODES),
                "description": (
                    "How the user is meant to answer. 'confirm_one' — pick "
                    "exactly one of the options (the default when options are "
                    "given). 'choose_many' — several options can apply at once "
                    "and the user confirms when done. 'free' — the options are "
                    "only examples; answering in the user's own words is "
                    "expected and equally valid."
                ),
            },
        },
        "required": ["question"],
    },
}

# Defensive cap on a clarification option's button label — these strings are
# model-generated, unlike the catalog-bounded master names `_render_master_cards`
# renders, so nothing upstream already bounds their length.
_MAX_OPTION_LABEL_CHARS = 40

#: Hard ceiling on tappable options, mirroring the tool spec's ``maxItems``.
#: Deliberately NOT raised here: the sibling five-button limit from DRF-1200
#: sits right beside it, and lifting one without the other is how a keyboard
#: silently loses its tail.
_MAX_CLARIFICATION_OPTIONS = 5


@dataclass(frozen=True)
class DiscoveryReply:
    """A discovery turn's outcome: the reply text + optional channel action_data
    (e.g. the master-card keyboard). ``action_data`` mirrors the per-tenant
    SkillResult shape so the MAX handler renders it via ``_build_attachments``.

    ``persisted`` (W5): True when the assistant turn was already persisted
    by the producer (the AIConcierge store in
    :mod:`apps.orchestrator.concierge`) — the handler must NOT record it
    again. Legacy producers leave it False.

    ``outage`` (DRF-1348): True when this reply exists ONLY because the model
    could not be reached — the LLM call itself raised and the turn degraded to
    the safe line. Set in exactly one place
    (:func:`apps.orchestrator.concierge.generate_concierge_reply`, the
    ``llm_error`` return) and nowhere else: the other producers of the same
    safe line — a blank clarification, an unknown tool name — are the model
    ANSWERING badly, which is a different fact and must not offer «Повторить»
    for a turn that was, in fact, taken.

    Читается каналом, чтобы нарисовать состояние «AI недоступна» из макета C01
    вместо молчаливого «отвечу через минуту», который ничего не отвечает.
    Legacy producers leave it False, так что для них ничего не меняется.
    """

    text: str
    action_data: dict[str, Any] | None = None
    persisted: bool = False
    outage: bool = False


def _discovery_voice_fields() -> dict[str, str]:
    """The marketplace voice, from the one place that owns it.

    The fallback mirror that used to live here has moved to
    :mod:`apps.persona.voice` alongside the other two surfaces. It was a
    hand-copied duplicate of the frozen constant with nothing comparing the
    two — a divergence would have surfaced as «CI says one thing, prod says
    another». A test there now pins them equal.
    """

    from apps.persona.voice import frozen_voice_fields

    return frozen_voice_fields()


def build_discovery_prompt(
    message_text: str,
    *,
    history: list[dict[str, Any]] | None = None,
    personal_context: str | None = None,
) -> list[dict[str, str]]:
    """Render the ChatML messages for a discovery turn.

    System message is composed manually from the marketplace voice fields (NOT
    ``ayla_ai_core.render_system_prompt`` — that is booking-domain coupled and
    forbidden by the import allow-list). ``history`` is the short per-turn
    memory (``short_term.recall``). ``personal_context`` is the optional
    surfacing block (M-C1) — a pre-rendered paragraph of the user's GREEN
    memory; appended to the system message when present, omitted otherwise.
    """
    voice = _discovery_voice_fields()
    system_parts = [
        f"Ты — {voice['assistant_name']}, AI-помощник «{voice['business_name']}».",
        "Ты помогаешь клиенту по всей стране подобрать подходящего "
        f"{voice['domain']}-мастера и записаться — конкретный салон выбирается "
        "только в момент записи.",
        "Это разговор-знакомство (discovery): отвечай тепло и кратко, "
        "задавай уточняющие вопросы про услугу, город и предпочтения. НЕ "
        "называй конкретный салон, цену или адрес — этих данных пока нет.",
        f"Если вопрос не про запись к мастеру — мягко верни в тему: "
        f"«{voice['off_topic_redirect']}»",
        f"Ответ не длиннее {_MAX_REPLY_CHARS} символов.",
    ]
    if personal_context:
        system_parts.append(personal_context)
    system_text = "\n\n".join(system_parts)
    messages: list[dict[str, str]] = [{"role": "system", "content": system_text}]
    for item in history or []:
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message_text})
    return messages


# Cap on the query text echoed back in a no-match refusal. The strings come
# from the model's tool arguments (or, on degraded paths, the user's own turn)
# — bounded enough to trust, not bounded enough to paste unclipped into a
# reply that also has a _MAX_REPLY_CHARS budget to keep.
_MAX_ECHOED_QUERY_CHARS = 60


def render_no_match(city: str | None = None, specialization: str | None = None) -> DiscoveryReply:
    """The honest refusal for a search that genuinely matched nobody (DRF-1283).

    The line this replaces — «По вашему запросу мастеров пока не нашлось —
    уточните город или услугу» — asked for the two things the user had most
    likely just supplied. On the live turn «покажи массажистов в пензе» both
    the service AND the city were named, and answering «уточните город или
    услугу» reads as «я вас не понял»: the bot denies understanding a sentence
    it understood, which is worse than the missing result itself.

    So: name back what WAS understood, say what specifically is not there, and
    ask only for something not already given. Asking for a city is right when
    no city was named and wrong when one was — the branch below is that
    distinction, nothing more.

    NOTE this is the deterministic wording, and since DRF-1283 the common
    zero-result path does not reach it: the handler hands a zero-result
    booking turn to the concierge, which phrases the refusal itself off
    ``_build_tool_result_message``. This is the wording for the degraded
    paths — pass budget exhausted, follow-up pass failed, legacy generator —
    where no model turn is available to do it.
    """
    service = (specialization or "").strip()[:_MAX_ECHOED_QUERY_CHARS]
    place = (city or "").strip()[:_MAX_ECHOED_QUERY_CHARS]
    if service and place:
        # «такого … нет», not «такой услуги … нет»: with both halves named we
        # know the COMBINATION matched nobody, not which half is missing —
        # the service may exist elsewhere, the city may have no masters yet.
        # Saying the narrower thing would be a confident guess.
        text = (
            f"«{service}» в городе {place} — такого у наших мастеров сейчас нет. "
            "Назовите другую услугу или другой город, и я поищу ещё."
        )
    elif service:
        text = (
            f"«{service}» — такой услуги у наших мастеров сейчас нет. "
            "Подскажите город или другую услугу, и я поищу ещё."
        )
    elif place:
        text = (
            f"В городе {place} подключённых мастеров пока нет. "
            "Назовите другой город, и я поищу ещё."
        )
    else:
        # Genuinely nothing to acknowledge — the only case where asking for
        # both the city and the service is the honest question.
        text = "По вашему запросу мастеров пока не нашлось — уточните город или услугу."
    return DiscoveryReply(text=text[:_MAX_REPLY_CHARS])


def render_missing_services(missing: list[str], city: str | None = None) -> str:
    """The one sentence that says a named service is not in the catalog (DRF-1312).

    The single wording point for the half of a composite request nobody can
    serve, so the deterministic renderer below and any future caller cannot
    phrase the same fact two ways.

    Quotes the user's OWN words back — ``missing`` carries the spelling they
    used (see ``apps.marketplace.discovery.service_coverage``) — because the
    sentence is a refusal and a refusal has to be recognisable as an answer to
    what was asked, not to a stem we reduced it to.

    Scoped to ``city`` when the search was, for the same reason
    :func:`render_no_match` distinguishes the two: «такого нет» about a
    city-filtered search is a claim about that city, and saying it unqualified
    would deny a service that may exist one city over.
    """
    quoted = ", ".join(f"«{name.strip()[:_MAX_ECHOED_QUERY_CHARS]}»" for name in missing if name)
    if not quoted:
        return ""
    plural = len(missing) > 1
    place = (city or "").strip()[:_MAX_ECHOED_QUERY_CHARS]
    if place:
        subject = "таких услуг" if plural else "такой услуги"
        return f"{quoted} в городе {place} — {subject} у наших мастеров сейчас нет."
    subject = "таких услуг" if plural else "такой услуги"
    return f"{quoted} — {subject} у наших мастеров сейчас нет."


# ─── DRF-1324: the card's button carries the REQUEST, not just the ids ─────
#
# Live pilot 23.08, the first booking ever made through the bot. «запиши на
# лимфодренаж» surfaced the three masters who really do perform lymphatic
# drainage — the master search was right. The tap then asked «Выберите услугу
# мастера Сазонова Инна» and offered the first TEN of her nineteen services in
# alphabetical order: «Биоэнергетический массаж детский» second, the two
# lymphatic-drainage services sixth and seventh. The booking that came out of
# that tap was for the children's massage.
#
# The request died at the callback boundary. ``cb:discover:book:{T}:{M}``
# carries who, and — since DRF-962 — sometimes what, but never WHY this master
# was on the list, so the menu behind the tap could not be the menu of the
# request. Everything else about the turn was correct.
#
# The fix is the grammar the module already believes in: a button carries what
# it means (``_render_salon_cards``: «a chip tap addresses a salon by the id
# the bot itself rendered, never by the name substring»). Re-deriving the
# query from the conversation at tap time would break that rule and would be
# wrong the moment the user types something else between the render and the
# tap; the callback is the only place the query is provably the one that
# produced this very card.
#
# ### What rides, and what does NOT
#
# The STEMS, and only the stems — ``apps.marketplace.discovery.query_stems``,
# the pure half of the parse. City recognition and goal recognition both read
# the catalog, so they run at the TAP
# (``apps.marketplace.discovery.parse_stems``), inside the handoff, which is
# already querying anyway.
#
# That split is not an optimisation, it is the layering: rendering a card must
# stay a pure function of the DTOs it is handed. Three suites render cards
# with no database at all; a renderer that queried broke every one of them,
# and marking them ``django_db`` would have buried the error rather than
# answered it.
#
# ### Encoding
#
# ``base64url`` of the stem list:
#
# * ASCII out — every existing ``cb:`` payload is hex, so a Cyrillic payload
#   would be the first one on the wire and is not something to find out about
#   on a live pilot;
# * no ``:`` in the base64url alphabet (``A-Za-z0-9-_``), so the colon split
#   the handler does stays unambiguous;
# * stems, not the raw turn, so the segment stays short — each is ≤ 6
#   characters and there are at most five.
#
# Padding is stripped and restored: ``=`` is the one character MAX rejects in
# an ``open_app`` payload (``apps/channels/max/outbound.py``), and while this
# is a ``callback`` payload, spending nothing to stay inside the stricter rule
# is cheaper than discovering the difference in production.
_QUERY_REF_SEP = ","

# A query ref longer than this is dropped and the tap degrades to the
# unfiltered menu — the pre-DRF-1324 behaviour. MAX documents no callback
# payload limit we can rely on, and the two UUIDs already spend ~90
# characters, so the ceiling is deliberately generous enough for a real query
# (five six-character stems encode to 44) and still far short of anything that
# could truncate a payload silently.
_MAX_QUERY_REF_CHARS = 96

# Upper bound on stems read back OUT of a ref. ``_MAX_TOKENS`` bounds what the
# tokenizer produces, but a decoder must bound what it accepts on its own — a
# hand-written payload is not obliged to have come from the encoder.
_MAX_QUERY_REF_STEMS = 5


def encode_query_ref(raw: str | None) -> str:
    """Encode a discovery query for a booking callback, or ``""``. PURE.

    No catalog read — see the block above for why that matters. ``""`` for a
    query with nothing to carry and for one that would not fit; both mean «the
    menu behind this tap cannot be narrowed», which the handoff answers with
    the full list exactly as it did before DRF-1324.

    Duplicate stems are dropped (``dict.fromkeys`` keeps the order): «массаж
    массаж» would otherwise spend two of the five slots on one term.
    """
    if not raw or not raw.strip():
        return ""
    stems = list(dict.fromkeys(query_stems(raw)))
    if not stems:
        return ""
    payload = _QUERY_REF_SEP.join(stems)
    ref = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return ref if len(ref) <= _MAX_QUERY_REF_CHARS else ""


def decode_query_ref(ref: str) -> list[str]:
    """The stems :func:`encode_query_ref` wrote; ``[]`` on anything unexpected.

    A forged, truncated or stale ref decodes to ``[]``, which the handoff
    reads as «do not narrow». Degrading to the full service menu is the honest
    failure here: it is the answer this surface gave until today, and it can
    only ever show the user more of their own master's real services — never
    fewer, and never a service someone else performs.

    The caller turns these stems into a request with
    ``apps.marketplace.discovery.parse_stems``, which is where the catalog
    gets a say.
    """
    ref = (ref or "").strip()
    if not ref or len(ref) > _MAX_QUERY_REF_CHARS:
        return []
    try:
        payload = base64.urlsafe_b64decode(ref + "=" * (-len(ref) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return []
    return [p for p in payload.split(_QUERY_REF_SEP) if p][:_MAX_QUERY_REF_STEMS]


def _render_master_cards(
    cards: list[MasterCard],
    *,
    city: str | None = None,
    specialization: str | None = None,
    available_services: list[str] | None = None,
    missing_services: list[str] | None = None,
) -> DiscoveryReply:
    """Render discovered masters as a reply + a one-button-per-card keyboard.

        PUBLIC fields only (the DTO carries nothing else). Each button's callback
        is ``cb:discover:book:{tenant_id}:{master_id}`` — the handoff seam
        (#1020) — extended with ``:{service_id}`` when discovery resolved the
        queried service unambiguously (DRF-962), so the tap enters booking with
        the service context instead of the stale-context dead-end, and with a
        fourth ``:{query_ref}`` segment (DRF-1324) carrying the REQUEST, so the
        ask-the-service menu behind an unresolved tap is the menu of that request
        rather than the master's whole roster in alphabetical order.

    The SAME ref goes on every button — whichever master is tapped,
        the menu behind them is narrowed by the request that put them all on this
        list — and building it stays PURE (:func:`encode_query_ref`), so this
        function remains a function of the DTOs it is handed and nothing else.

        ``city`` / ``specialization`` are the query that produced ``cards`` and are
        used ONLY for the empty case, so the refusal can name what was searched for
        (DRF-1283 — see :func:`render_no_match`). Callers that have the query
        should pass it; callers that don't get the generic line.

        ### Partial coverage (DRF-1312)

        ``missing_services`` are the parts of a COMPOSITE request that the catalog
        cannot serve, already verified against it (``service_coverage``); they are
        stated in the reply instead of vanishing. That statement goes FIRST, ahead
        of the cards, for two reasons: the ``_MAX_REPLY_CHARS`` clip below eats the
        tail, and the only line that must never be lost is the one that says what
        we cannot do; and it must be read BEFORE the list, or a list that answers
        half the request reads as an answer to all of it — the exact impression
        DRF-1312 was filed about.

        The header then binds the list to the half we CAN serve, naming it from
        ``available_services`` when the caller knows those names. «Вот мастера,
        которые могут подойти» under a request half of which was just refused
        would be the same silent overclaim in a longer message.
    """
    if not cards:
        return render_no_match(city=city, specialization=specialization)

    query_ref = encode_query_ref(specialization)

    missing_line = render_missing_services(missing_services or [], city)
    lines: list[str] = []
    if missing_line:
        available = [name.strip() for name in (available_services or []) if name and name.strip()]
        if available:
            named = ", ".join(f"«{name[:_MAX_ECHOED_QUERY_CHARS]}»" for name in available)
            header = f"А вот по запросу {named} — мастера, которые могут подойти:"
        else:
            header = "А вот по остальной части запроса — мастера, которые могут подойти:"
        lines = [missing_line, "", header]
    else:
        lines = ["Вот мастера, которые могут подойти:"]
    buttons: list[dict[str, str]] = []
    for card in cards:
        # The rating domain is 1..5, so a stored 0.00 is not a rating at all
        # — it is the absence of one. Ratings are derived from reviews, the
        # pilot has none and cannot have any yet, so EVERY card carries 0.00
        # and the None-guard — written for a value that never arrives — let
        # all of them through as «★ 0.00», which reads as «bad master»
        # (DRF-1224). Same shape as the em-dash below: guard the value that
        # actually shows up, not the one the schema allows.
        has_rating = card.rating is not None and card.rating >= 1
        rating = f" · ★ {card.rating}" if has_rating else ""
        city = f" · {card.city}" if card.city else ""
        # The em-dash belongs to the specialization, not to the line. Ayla's
        # specialists feed carries no specialization, so since DRF-945 made
        # service-relation matching the primary discovery path, the empty case
        # is the COMMON one — an unconditional dash renders every card as
        # «• Массажист —  · Пенза».
        spec = f" — {card.specialization}" if card.specialization else ""
        # Surface the resolved service on the card line: the button will carry
        # it into booking, so the user must SEE what they are tapping into.
        # Gate on the NAME, not the id: apps/marketplace/discovery.py:240
        # normalises a NULL service name to "" while keeping the id, and an
        # id-only card would render a bare « ·  » — the em-dash bug again.
        # The id still rides the callback below regardless of the name.
        service = f" · {card.service_name}" if card.service_name else ""
        lines.append(f"• {card.name}{spec}{service}{rating}{city}")
        # The service segment stays positional, so a query ref without a
        # resolved service rides behind an EMPTY one — «::ref», not «:ref» —
        # or the handler would read the ref as a malformed service id and the
        # tap would lose both.
        service_part = str(card.service_id) if card.service_id is not None else ""
        suffix = f":{service_part}" if (service_part or query_ref) else ""
        if query_ref:
            suffix += f":{query_ref}"
        buttons.append(
            {
                "label": f"Записаться к {card.name}",
                "callback": (
                    f"{CALLBACK_DISCOVER_BOOK_PREFIX}{card.tenant_id}:{card.master_id}{suffix}"
                ),
            }
        )
    action_data = {"attachments": [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]}
    return DiscoveryReply(text="\n".join(lines)[:_MAX_REPLY_CHARS], action_data=action_data)


# ─── DRF-1304: salon / service card renderers + deterministic executor ──────
#
# Everything below renders real mirror data or says honestly that there is
# none — a missing address/price/duration is omitted from the line, never
# rendered as «None» and never invented.
#
# ### Chips (owner's call, 23.08)
#
# A list of five salons as a paragraph is the wrong answer to «какие салоны у
# вас есть»: the next thing the person wants is one of them, and a paragraph
# makes them type its name back. Each card therefore carries a chip, exactly
# as the master card has since #1020.
#
# The binding rule is the owner's: a chip must lead to something that really
# executes — a button landing in «я вас не понял» is worse than no button,
# because the trust was already spent. So every chip here carries a `cb:`
# callback the ladder in ``apps/channels/max/handler.py`` catches BY PREFIX
# and answers from a by-id read (:func:`execute_catalog_callback`) — no model
# turn, nothing inferred from text, nothing that can miss. A service nobody
# bookable performs gets NO chip (``ServiceCard.has_bookable_master``): the
# line stays, the dead end does not.


def render_no_salons(city: str | None = None) -> DiscoveryReply:
    """The honest empty answer for ``show_salons`` — names the city if given."""
    place = (city or "").strip()[:_MAX_ECHOED_QUERY_CHARS]
    if place:
        text = (
            f"В городе {place} подключённых салонов пока нет. Назовите другой город — проверю там."
        )
    else:
        text = "Подключённых салонов пока нет."
    return DiscoveryReply(text=text[:_MAX_REPLY_CHARS])


def _salon_place(card: SalonCard) -> str:
    """« — Пенза, ул. Леонова, 15а» / « — Пенза» / «» for a salon card.

    The mirrored address usually ALREADY starts with the city (live pilot:
    «Пенза, ул. Карпинского, 33А»), and gluing city + address unconditionally
    printed it twice — «SPAtrium — Пенза, Пенза, ул. Карпинского, 33А». The
    city is dropped from the prefix exactly when the address opens with it;
    an address from another city (mirror drift) still shows both, because
    then the two really are different facts.
    """
    city = (card.city or "").strip()
    address = (card.address or "").strip()
    if not address:
        return f" — {city}" if city else ""
    if city and address.casefold().startswith(city.casefold()):
        return f" — {address}"
    return f" — {city}, {address}" if city else f" — {address}"


def _render_salon_cards(
    salons: list[SalonCard], *, shown: int, city: str | None = None
) -> DiscoveryReply:
    """Render salons: name — city, address + a short «что там делают» sample,
    plus one chip per salon whose tap opens that salon's services.

    ``address`` may legitimately be "" (the pilot salon's masters carry none)
    — the line simply goes without it. A salon whose mirror holds no active
    services says «Услуги пока не загружены» instead of inventing a list —
    and gets no chip either: its tap would open an empty list.
    """
    if not salons:
        return render_no_salons(city=city)
    header = (
        f"Вот наши салоны в городе {city.strip()[:_MAX_ECHOED_QUERY_CHARS]}:"
        if city and city.strip()
        else "Вот салоны, которые к нам подключены:"
    )
    lines = [header]
    buttons: list[dict[str, str]] = []
    for card in salons[:shown]:
        lines.append(f"• {card.name}{_salon_place(card)}")
        if card.sample_services:
            more = card.service_count - len(card.sample_services)
            tail = f" и ещё {more}" if more > 0 else ""
            lines.append(f"  Что делают: {', '.join(card.sample_services)}{tail}.")
            buttons.append(
                {
                    "label": card.name[:_MAX_OPTION_LABEL_CHARS],
                    "callback": f"{CALLBACK_CATALOG_SERVICES_PREFIX}{card.tenant_id}",
                }
            )
        else:
            # No active services mirrored — the tap would open «услуги пока не
            # загружены», which the line already says. No chip.
            lines.append("  Услуги пока не загружены.")
    if len(salons) > shown:
        lines.append("…и это не все — назовите город, покажу точнее.")
    if buttons:
        lines.append("Нажмите на салон — покажу, что там делают.")
    return _reply_with_chips("\n".join(lines), buttons)


def _format_price(price: Any) -> str:
    """«1700» for an integral Decimal, «1700.50» otherwise — no trailing .00."""
    return str(int(price)) if price == price.to_integral_value() else str(price.normalize())


def render_no_services(
    *,
    salon: str | None = None,
    city: str | None = None,
    query: str | None = None,
    salon_known: bool = False,
) -> DiscoveryReply:
    """The honest empty answer for ``show_services`` (DRF-1283's rule applied
    here too: name back what WAS understood, ask only for what was not given).

    ``salon_known`` separates «no such salon on the platform» from «the salon
    is here but its service list is empty» — two different truths.
    """
    place = (city or "").strip()[:_MAX_ECHOED_QUERY_CHARS]
    service = (query or "").strip()[:_MAX_ECHOED_QUERY_CHARS]
    name = (salon or "").strip()[:_MAX_ECHOED_QUERY_CHARS]
    if name and not salon_known:
        text = f"Салона «{name}» среди подключённых пока нет. Могу показать, какие салоны есть."
    elif name and service:
        # The salon IS here and the query simply matched nothing in it —
        # «услуги не загружены» would be a lie about a loaded catalog.
        text = (
            f"«{service}» в салоне «{name}» — такой услуги сейчас нет. "
            "Могу показать всё, что там делают."
        )
    elif name:
        text = f"В салоне «{name}» услуги пока не загружены."
    elif service and place:
        text = (
            f"«{service}» в городе {place} — таких услуг у нас сейчас нет. "
            "Назовите другую услугу или другой город, и я поищу ещё."
        )
    elif service:
        text = (
            f"«{service}» — такой услуги у нас сейчас нет. "
            "Подскажите другую или спросите, что есть в конкретном салоне."
        )
    elif place:
        text = f"В городе {place} услуг пока не нашлось. Назовите другой город — проверю там."
    else:
        text = "Услуги пока не загружены — попробуйте спросить про конкретный салон."
    return DiscoveryReply(text=text[:_MAX_REPLY_CHARS])


def _render_service_cards(
    services: list[ServiceCard],
    *,
    shown: int,
    salon: str | None = None,
    city: str | None = None,
    query: str | None = None,
) -> DiscoveryReply:
    """Render services: name — price · duration, salon named when results span
    several.

    Each service whose salon has someone bookable to perform it also carries a
    chip: the tap shows those masters, with the booking button already stamped
    with this service (DRF-962). A service nobody performs keeps its line and
    loses its chip — see the section header.

    Price renders only when the mirror carries a real one: ``price_from`` NULL
    or 0.00 is omitted — «от 0 ₽» would read as «бесплатно» for rows whose
    price was simply never filled (the CATALOG_NORMALIZATION В-4 concern), and
    a missing price must not be invented. Same for ``duration_min``. Services
    not linked to a canonical template are shown AS IS — linking covers only
    part of the catalog (0 of 58 at the pilot salon), so a canonical grouping
    would silently hide most of it.
    """
    if not services:
        return render_no_services(salon=salon, city=city, query=query)
    visible = services[:shown]
    salon_names = {card.salon_name for card in visible}
    if query and query.strip():
        header = f"Вот что есть по «{query.strip()[:_MAX_ECHOED_QUERY_CHARS]}»:"
    elif city and city.strip():
        header = f"Вот услуги в городе {city.strip()[:_MAX_ECHOED_QUERY_CHARS]}:"
    elif salon and salon.strip():
        header = "Вот услуги этого салона:"
    else:
        header = "Вот услуги, которые есть:"
    lines = [header]
    buttons: list[dict[str, str]] = []
    for card in visible:
        line = f"• {card.name}"
        if card.price_from is not None and card.price_from > 0:
            line += f" — от {_format_price(card.price_from)} ₽"
        if card.duration_min:
            line += f" · {card.duration_min} мин"
        if len(salon_names) > 1:
            line += f" ({card.salon_name})"
        lines.append(line)
        if card.has_bookable_master:
            buttons.append(
                {
                    "label": card.name[:_MAX_OPTION_LABEL_CHARS],
                    "callback": f"{CALLBACK_CATALOG_MASTERS_PREFIX}{card.service_id}",
                }
            )
    if len(services) > shown:
        lines.append("…это не всё — уточните запрос, и я покажу точнее.")
    if buttons:
        lines.append("Нажмите на услугу — покажу, к кому записаться.")
    return _reply_with_chips("\n".join(lines), buttons)


# BOT-003 §9 / prohibition #22 applies to services the same way it applies to
# masters: «какие у вас услуги?» with no salon, city, or query can only be
# answered by dumping the whole catalog — enumeration standing in for an
# answer. The question asks for exactly the information that makes the answer
# real.
NO_SERVICE_CRITERIA_QUESTION = (
    "Что именно подсказать — услуги конкретного салона или что-то по виду, "
    "например «лицо» или «массаж»?"
)


def has_service_criteria(salon: str | None, city: str | None, query: str | None) -> bool:
    """True when a ``show_services`` call carries at least one real filter."""
    return bool((salon or "").strip() or (city or "").strip() or (query or "").strip())


def render_no_service_criteria_clarification() -> DiscoveryReply:
    """The canon-prescribed reply to a criteria-less ``show_services`` call."""
    return _render_ask_clarification(NO_SERVICE_CRITERIA_QUESTION, [])


def _reply_with_chips(text: str, buttons: list[dict[str, str]]) -> DiscoveryReply:
    """Wrap rendered text + chips in the keyboard envelope the MAX handler
    reads (``_build_attachments``, shape (1) — the platform-canonical one the
    booking skill and the master card already use).

    Empty ``buttons`` yields a plain reply with ``action_data=None``: an empty
    ``inline_keyboard`` attachment is a widget with nothing in it, which reads
    as a broken message rather than as a message without buttons.
    """
    if not buttons:
        return DiscoveryReply(text=text[:_MAX_CATALOG_REPLY_CHARS])
    action_data = {"attachments": [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]}
    return DiscoveryReply(text=text[:_MAX_CATALOG_REPLY_CHARS], action_data=action_data)


def _parse_uuid_ref(callback_text: str, prefix: str) -> UUID | None:
    """The id a catalog chip carries, or ``None`` when it is not a valid one.

    Callback text arrives from the channel and is not trusted to be what we
    rendered: a malformed ref must degrade to the honest «карточка устарела»
    line, never raise inside the turn.
    """
    try:
        return UUID(callback_text[len(prefix) :].strip())
    except (ValueError, AttributeError):
        return None


#: Said when a chip's target no longer exists — the salon went inactive, the
#: service was deactivated, or the card is simply from an old message. It names
#: what happened and offers the one move that always works, so the tap still
#: ends somewhere the user can act.
CATALOG_STALE_CARD_TEXT = (
    "Эта карточка уже неактуальна — каталог с тех пор обновился. "
    "Спросите «какие салоны у вас есть», и я покажу заново."
)


def execute_catalog_callback(callback_text: str) -> DiscoveryReply | None:
    """Answer a catalog chip tap from a by-id read (DRF-1304).

    Returns ``None`` when ``callback_text`` is not a catalog callback at all,
    so the caller's ladder can keep matching. Everything else — including a
    malformed or stale ref — returns a reply: a tap must never fall through to
    the concierge, which would answer a raw ``cb:…`` string as if it were
    something the person said.

    Deterministic by construction, like :func:`execute_catalog_tool`: no model
    call, so a tap costs a database read and nothing else.
    """
    if callback_text.startswith(CALLBACK_CATALOG_SERVICES_PREFIX):
        tenant_id = _parse_uuid_ref(callback_text, CALLBACK_CATALOG_SERVICES_PREFIX)
        if tenant_id is None:
            return DiscoveryReply(text=CATALOG_STALE_CARD_TEXT)
        salon = get_salon(tenant_id)
        if salon is None:
            return DiscoveryReply(text=CATALOG_STALE_CARD_TEXT)
        services = discover_services(tenant_id=tenant_id, limit=_MAX_SERVICE_CARDS + 1)
        logger.info(
            "orchestrator.discovery.catalog_tap kind=services count=%d",
            len(services),
        )
        if not services:
            # The salon IS here (get_salon just confirmed it) — this is the
            # «loaded catalog is empty» truth, not «no such salon».
            return render_no_services(salon=salon.name, salon_known=True)
        return _render_service_cards(services, shown=_MAX_SERVICE_CARDS, salon=salon.name)

    if callback_text.startswith(CALLBACK_CATALOG_MASTERS_PREFIX):
        service_id = _parse_uuid_ref(callback_text, CALLBACK_CATALOG_MASTERS_PREFIX)
        if service_id is None:
            return DiscoveryReply(text=CATALOG_STALE_CARD_TEXT)
        cards = discover_masters_for_service(service_id, limit=_MAX_MASTER_CARDS)
        logger.info(
            "orchestrator.discovery.catalog_tap kind=masters count=%d",
            len(cards),
        )
        if not cards:
            # The chip was rendered only for services somebody performed, so
            # this is the race (mapping removed, master left) — not the norm.
            return DiscoveryReply(
                text=(
                    "На эту услугу сейчас записаться не к кому. "
                    "Спросите, что ещё есть в этом салоне — подберу другое."
                )
            )
        return _render_master_cards(cards)

    return None


def execute_catalog_tool(name: str, args: dict[str, Any]) -> DiscoveryReply | None:
    """Run the marketplace read behind a model-called salon/service tool.

    Deterministic, like the nutrition tools (DRF-1268): the reply is rendered
    from real mirror data right here, so the turn's cost does not grow — no
    second model pass rephrases it. Returns ``None`` for an unknown tool name
    (the caller degrades to the safe line, same as today).
    """
    if not isinstance(args, dict):
        args = {}

    def _limit(raw: Any, default: int) -> int:
        return min(int(raw), default) if isinstance(raw, int) and raw > 0 else default

    if name == SHOW_SALONS_TOOL_SPEC["name"]:
        city = args.get("city") or None
        limit = _limit(args.get("limit"), _MAX_SALON_CARDS)
        # limit+1: the «это не всё» tail must KNOW there is more, not guess it
        # from a list that happens to fill the page.
        salons = discover_salons(city=city, limit=limit + 1)
        logger.info("orchestrator.discovery.show_salons count=%d", len(salons))
        return _render_salon_cards(salons, shown=limit, city=city)

    if name == SHOW_SERVICES_TOOL_SPEC["name"]:
        salon = args.get("salon") or None
        city = args.get("city") or None
        query = args.get("query") or None
        if not has_service_criteria(salon, city, query):
            return render_no_service_criteria_clarification()
        limit = _limit(args.get("limit"), _MAX_SERVICE_CARDS)
        services = discover_services(salon=salon, city=city, query=query, limit=limit + 1)
        logger.info("orchestrator.discovery.show_services count=%d", len(services))
        if not services and salon:
            # «No such salon» and «the salon is here but its list is empty»
            # are different truths — check the name against the salons we
            # actually have before choosing which one to say.
            needle = salon.strip().casefold()
            salon_known = any(needle in card.name.casefold() for card in discover_salons(city=city))
            return render_no_services(salon=salon, city=city, query=query, salon_known=salon_known)
        return _render_service_cards(services, shown=limit, salon=salon, city=city, query=query)

    return None


def normalize_clarification_mode(raw: Any, options: list[str]) -> str:
    """The clarification mode a call means, given what the model actually said.

    Never raises and never returns something outside :data:`CLARIFICATION_MODES`
    — a model-supplied string is untrusted input, and a clarification is the
    turn we reach precisely when things are already unclear.

    Two derivations when ``raw`` is missing or unrecognised, both chosen to
    reproduce today's behaviour exactly rather than to be clever:

    * **no options → ``free``.** The renderer already sends a bare question
      and the user has nothing to tap, so free text is the only answer that
      can exist. Naming it does not change the turn.
    * **options → ``confirm_one``.** Every clarification the pilot ships
      today is a pick-one; the buttons carry their own text as the callback,
      i.e. one tap == one typed answer, which IS confirm-one semantics.

    ``choose_many`` is therefore never inferred. Multi-select changes what a
    tap does, so it must be asked for explicitly — deriving it from a list of
    options would silently rewrite the meaning of clarifications that work.
    """
    if isinstance(raw, str):
        candidate = raw.strip().lower()
        if candidate in CLARIFICATION_MODES:
            return candidate
    return CLARIFICATION_MODE_CONFIRM_ONE if options else CLARIFICATION_MODE_FREE


def clarification_mode_of(action_data: dict[str, Any] | None) -> str | None:
    """Read the mode back off a rendered clarification, for consumers.

    ``None`` when this ``action_data`` is not a *tappable* clarification (a
    card keyboard, a booking reply, ``None``, or the option-less question) —
    so a consumer can branch on "is this a clarification, and which kind" in
    one call without having to know the envelope's shape.

    This is the half of DRF-1362 §1 that makes the mode worth carrying: the
    channel receives a keyboard either way, and this is the only thing that
    tells it whether a typed answer is expected alongside the buttons.

    An option-less clarification deliberately carries NO block and reads back
    as ``None``. It has no keyboard, so there is nothing for the mode to
    disambiguate — free text is the only answer that can exist — and giving
    it an ``action_data`` where it had ``None`` would change every such turn
    already in the pilot for no gain.
    """
    if not isinstance(action_data, dict):
        return None
    block = action_data.get("clarification")
    if not isinstance(block, dict):
        return None
    mode = block.get("mode")
    return mode if isinstance(mode, str) and mode in CLARIFICATION_MODES else None


def _render_ask_clarification(
    question: str,
    options: list[str],
    mode: Any = None,
) -> DiscoveryReply:
    """Render an ``ask_clarification`` tool call as reply text + a tap keyboard.

    Each option becomes a button whose callback is the option's OWN text —
    the same "tap == typed answer" contract the master-card buttons above
    already use on this path. MAX delivers a tapped payload through the same
    field a typed message would (see ``is_menu_callback`` in
    ``apps.skills.menu.matching`` for the established precedent), so a tap
    simply re-enters the normal turn as if the user had typed that option —
    no separate decode step, no server-side pending-question state to keep in
    sync (unlike the legacy ``cb:ai:answer:{conv}:{idx}`` scheme).

    No options → plain question text, no keyboard: the user answers freely.

    ``mode`` (DRF-1362) is metadata ONLY. It rides in
    ``action_data["clarification"]["mode"]`` — a key no channel renderer
    reads, since ``apps.channels.max.handler._build_attachments`` looks at
    ``attachments`` / ``buttons`` / ``button_rows`` and nothing else. That is
    the point: the wire bytes of every clarification already in the pilot are
    unchanged by this argument, whatever it says. Adding a mode must not
    quietly rewrite turns that work.

    The option-less branch keeps ``action_data=None`` for the same reason,
    one step stricter: with no keyboard there is nothing to disambiguate, and
    a bare question that used to carry ``None`` must keep carrying ``None``.
    """
    text = (question or "Уточните, пожалуйста?").strip()[:_MAX_REPLY_CHARS]
    cleaned = [str(opt).strip() for opt in options if str(opt).strip()]
    if not cleaned:
        return DiscoveryReply(text=text)
    resolved = normalize_clarification_mode(mode, cleaned)
    shown = cleaned[:_MAX_CLARIFICATION_OPTIONS]
    buttons = [{"label": opt[:_MAX_OPTION_LABEL_CHARS], "callback": opt} for opt in shown]
    action_data = {
        "attachments": [{"type": "inline_keyboard", "payload": {"buttons": buttons}}],
        "clarification": {"mode": resolved, "options": shown},
    }
    return DiscoveryReply(text=text, action_data=action_data)


# ─── choose_many: one purchase across two screens ─────────────────────────


#: Multi-select callback grammar (DRF-1362). Flat slugs, digits only after the
#: verb — no ``=``, ``&`` or ``?``, which MAX rejects outright on ``open_app``
#: payloads (`apps/channels/max/outbound.py::_button_to_max`, Guard 3) and
#: which there is no reason to risk here either.
#:
#:     cb:clarify:tg:{mask}:{index}   toggle option {index}
#:     cb:clarify:ok:{mask}           «Продолжить» — submit the accumulated set
#:     cb:clarify:no                  «Ни один вариант» — close with nothing
#:
#: ``{mask}`` is the CURRENT selection encoded as a bitmask (bit *i* set ==
#: option *i* chosen), carried in the payload of every button in the keyboard
#: and rewritten on each redraw.
#:
#: Carrying the selection in the keyboard rather than in a server-side record
#: is the one design decision here worth defending. The legacy screen this is
#: modelled on kept its selection in FSM state
#: (`legacy_maxbot/handlers/health_screening.py:259-264`,
#: ``context.update_data(chronic_selected=…)``) because it had an FSM to keep
#: it in. This path has none, and inventing per-conversation pending-question
#: state would reintroduce exactly what ``_render_ask_clarification`` above
#: says it avoided: a record that has to stay in sync with a message. The
#: keyboard IS the state — it is redrawn on every tap anyway, it cannot go
#: stale relative to the message it hangs under, and a tap on a keyboard from
#: three messages ago carries that keyboard's own mask, not a newer one.
#:
#: Five options cap the mask at 31, so the longest payload is
#: ``cb:clarify:tg:31:4`` — 18 bytes.
CLARIFY_CALLBACK_PREFIX = "cb:clarify:"
CLARIFY_TOGGLE_PREFIX = "cb:clarify:tg:"
CLARIFY_SUBMIT_PREFIX = "cb:clarify:ok:"
CLARIFY_NONE_CALLBACK = "cb:clarify:no"

#: Selected / unselected marks. Prefixed, not appended: MAX truncates a long
#: button label at the tail, so a trailing mark is the first thing lost.
CLARIFY_MARK_ON = "☑ "
CLARIFY_MARK_OFF = "☐ "

CLARIFY_SUBMIT_LABEL = "Продолжить"
CLARIFY_NONE_LABEL = "Ни один вариант"


@dataclass(frozen=True)
class ClarifyTap:
    """A decoded ``cb:clarify:*`` tap.

    ``kind`` is one of ``"toggle"`` / ``"submit"`` / ``"none"``. ``mask`` is
    the selection the tapped keyboard was carrying (0 for ``"none"``);
    ``index`` is the toggled option and is None for the other two.
    """

    kind: str
    mask: int = 0
    index: int | None = None


def parse_clarify_callback(callback_text: str) -> ClarifyTap | None:
    """Decode a multi-select tap, or ``None`` if this is not one.

    Returns ``None`` — never raises — for anything malformed, so a caller's
    prefix ladder keeps matching and a garbled payload degrades to "not a
    clarify tap" rather than to a 500. Out-of-range masks and indices are
    malformed: they can only come from a hand-edited payload, and clamping
    them would silently select an option the user never saw.
    """
    if not isinstance(callback_text, str):
        return None
    if callback_text == CLARIFY_NONE_CALLBACK:
        return ClarifyTap(kind="none")
    if callback_text.startswith(CLARIFY_TOGGLE_PREFIX):
        rest = callback_text[len(CLARIFY_TOGGLE_PREFIX) :]
        parts = rest.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            return None
        mask, index = int(parts[0]), int(parts[1])
        if not (0 <= index < _MAX_CLARIFICATION_OPTIONS):
            return None
        if not (0 <= mask < (1 << _MAX_CLARIFICATION_OPTIONS)):
            return None
        return ClarifyTap(kind="toggle", mask=mask, index=index)
    if callback_text.startswith(CLARIFY_SUBMIT_PREFIX):
        rest = callback_text[len(CLARIFY_SUBMIT_PREFIX) :]
        if not rest.isdigit():
            return None
        mask = int(rest)
        if not (0 <= mask < (1 << _MAX_CLARIFICATION_OPTIONS)):
            return None
        return ClarifyTap(kind="submit", mask=mask)
    return None


def apply_clarify_toggle(mask: int, index: int) -> int:
    """Flip option ``index`` in ``mask``. Pure; the whole of "toggle" logic."""
    return mask ^ (1 << index)


def selected_clarification_options(options: list[str], mask: int) -> list[str]:
    """The chosen options, in the order they were offered.

    Order matters: it is the order the user read them in, and the submitted
    answer is a sentence assembled from these.
    """
    return [opt for i, opt in enumerate(options[:_MAX_CLARIFICATION_OPTIONS]) if mask & (1 << i)]


#: Said when a multi-select tap arrives but the question behind it is gone —
#: the assistant message was pruned, or the tap is from an old screen whose
#: options nobody can name any more. Same shape as the stale catalog chip:
#: state what happened, offer the one move that always works.
#: Drawn above a redrawn multi-select keyboard when the original question's
#: wording could not be recovered. Deliberately generic — inventing a
#: paraphrase of a question we no longer hold would put words in the bot's
#: mouth that it never said the first time.
_MULTISELECT_REDRAW_QUESTION = "Выберите всё, что подходит, и нажмите «Продолжить»:"

CLARIFY_STALE_TEXT = (
    "Этот вопрос уже неактуален — я потеряла варианты, которые предлагала. "
    "Напишите, что нужно, своими словами — подберу заново."
)

#: Said when the user closes a multi-select without choosing anything. NOT a
#: dead end and NOT «ничего не найдено»: nothing was asked for yet, so the
#: honest next move is to invite the answer in their own words.
CLARIFY_NONE_TEXT = "Поняла, ни один вариант не подошёл. Расскажите своими словами, что ищете?"


@dataclass(frozen=True)
class ClarifyOutcome:
    """What a ``cb:clarify:*`` tap resolves to.

    Exactly one of the two is set, and that is the whole decision the caller
    has to make:

    * ``reply`` — draw this. A toggle redraw (same screen, new marks) or a
      terminal line. ``redraw`` says whether it should REPLACE the message the
      tap came from rather than follow it.
    * ``submit_text`` — the accumulated answer, phrased as the user would have
      typed it. The caller re-enters its normal turn with this as the text,
      which is what "submit -> повторное разрешение" means: the selection is
      resolved by the same machinery that resolves anything a person says, not
      by a second parallel path that could disagree with it.
    """

    reply: DiscoveryReply | None = None
    submit_text: str = ""
    redraw: bool = False


def execute_clarify_callback(
    callback_text: str,
    options: list[str],
    question: str = "",
) -> ClarifyOutcome | None:
    """Resolve a multi-select tap against the options that were offered.

    ``None`` when ``callback_text`` is not a clarify tap at all, so the
    caller's prefix ladder keeps matching — same contract as
    :func:`execute_catalog_callback`.

    ``options`` is the list the ORIGINAL question offered and ``question`` is
    its wording, both recovered by the caller from the assistant turn the tap
    belongs to. Empty ``options`` means the question is gone: every tap then
    degrades to :data:`CLARIFY_STALE_TEXT` rather than to a keyboard with no
    labels or an answer assembled from nothing.

    ``question`` is carried through rather than paraphrased because the
    wording is the model's. A redraw that re-worded the question on every tap
    would read as the bot changing its mind mid-answer; :data:`_MULTISELECT_
    REDRAW_QUESTION` is the fallback only when the original is unrecoverable.

    Deterministic: no model call, no database read. A tap costs the redraw and
    nothing else.
    """
    tap = parse_clarify_callback(callback_text)
    if tap is None:
        return None

    if not options:
        # The mask is meaningless without the labels it indexes.
        logger.info("orchestrator.discovery.clarify_tap kind=%s outcome=stale", tap.kind)
        return ClarifyOutcome(reply=DiscoveryReply(text=CLARIFY_STALE_TEXT))

    if tap.kind == "none":
        logger.info("orchestrator.discovery.clarify_tap kind=none outcome=closed")
        return ClarifyOutcome(reply=DiscoveryReply(text=CLARIFY_NONE_TEXT), redraw=True)

    if tap.kind == "toggle":
        assert tap.index is not None  # parse_clarify_callback guarantees it
        mask = apply_clarify_toggle(tap.mask, tap.index)
        logger.info(
            "orchestrator.discovery.clarify_tap kind=toggle selected=%d",
            len(selected_clarification_options(options, mask)),
        )
        return ClarifyOutcome(
            reply=render_multiselect_clarification(
                (question or "").strip() or _MULTISELECT_REDRAW_QUESTION,
                options,
                mask=mask,
            ),
            redraw=True,
        )

    chosen = selected_clarification_options(options, tap.mask)
    if not chosen:
        # «Продолжить» with nothing ticked is the same intent as «Ни один
        # вариант» — answering it with an empty submitted text would send a
        # blank turn into the concierge.
        logger.info("orchestrator.discovery.clarify_tap kind=submit outcome=empty")
        return ClarifyOutcome(reply=DiscoveryReply(text=CLARIFY_NONE_TEXT), redraw=True)

    logger.info("orchestrator.discovery.clarify_tap kind=submit selected=%d", len(chosen))
    return ClarifyOutcome(submit_text=", ".join(chosen))


def render_multiselect_clarification(
    question: str,
    options: list[str],
    *,
    mask: int = 0,
) -> DiscoveryReply:
    """Draw the ``choose_many`` screen at a given selection state.

    One option per row, each carrying its mark and the mask the NEXT tap
    would start from, then «Продолжить» and «Ни один вариант». Redrawing
    this with a new ``mask`` and pushing it through
    ``apps.channels.max.outbound.edit_message_or_send`` is what makes two
    taps update one message instead of stacking three — the same shape as
    `legacy_maxbot/handlers/health_screening.py:265-280`, which has done it
    on this channel since Phase 3.2A.

    **What goes on the button and what goes in the text** (for the C02
    handoff): a MAX button is one line capped at
    :data:`_MAX_OPTION_LABEL_CHARS` = 40 characters, and two of those are
    spent on the mark. So the button carries the normalised option and
    NOTHING else — no second line, no descriptive icon. Anything the mock
    puts under the option's title belongs in ``question`` instead, which is
    a 4000-character message body. That is a redraw of the design, not a
    loss of it.

    Falls back to a plain question with no keyboard when there are no
    options, matching :func:`_render_ask_clarification` — a multi-select
    over an empty set is a bare question, not an empty keyboard.
    """
    text = (question or "Уточните, пожалуйста?").strip()[:_MAX_REPLY_CHARS]
    cleaned = [str(opt).strip() for opt in options if str(opt).strip()]
    shown = cleaned[:_MAX_CLARIFICATION_OPTIONS]
    if not shown:
        return DiscoveryReply(text=text)

    rows: list[list[dict[str, str]]] = []
    for i, opt in enumerate(shown):
        chosen = bool(mask & (1 << i))
        mark = CLARIFY_MARK_ON if chosen else CLARIFY_MARK_OFF
        label = f"{mark}{opt}"[:_MAX_OPTION_LABEL_CHARS]
        rows.append([{"label": label, "callback": f"{CLARIFY_TOGGLE_PREFIX}{mask}:{i}"}])
    rows.append([{"label": CLARIFY_SUBMIT_LABEL, "callback": f"{CLARIFY_SUBMIT_PREFIX}{mask}"}])
    rows.append([{"label": CLARIFY_NONE_LABEL, "callback": CLARIFY_NONE_CALLBACK}])

    action_data = {
        "button_rows": rows,
        "clarification": {
            "mode": CLARIFICATION_MODE_CHOOSE_MANY,
            "options": shown,
            "mask": mask,
        },
    }
    return DiscoveryReply(text=text, action_data=action_data)


# BOT-003 §9 / prohibition #22 (docs/AUDIT_DIALOGUE_MODEL.md §2.3): «It does not
# use an arbitrary catalogue fallback». ``show_masters`` declares ``"required":
# []``, so a call with neither city nor specialization is legal — and it used to
# reach ``discover_masters()`` unfiltered, i.e. every bookable master in every
# tenant, ordered ``("name", "id")``, rendered under «Вот мастера, которые могут
# подойти:». That is the whole nationwide catalogue, alphabetically, offered as
# an answer — enumeration standing in for a recommendation, and it fired exactly
# when the model was least sure what the user wanted.
#
# What canon prescribes instead is not «say nothing found» — masters DO exist,
# so the no-match line would be a lie — but the §9 boundary: «if additional
# useful information can realistically enable a responsible recommendation,
# continue discovery only as needed under Q3». With zero criteria, a service or
# a city is exactly such information, so this is the definitional material
# blocking gap (§6), not the «unnecessary questioning» the same paragraph bans:
# we are not asking to avoid admitting no-match, we are asking because nothing
# has been asked for yet.
NO_CRITERIA_QUESTION = "Чтобы подобрать мастера, подскажите: какая услуга нужна и в каком городе?"


def has_discovery_criteria(city: str | None, specialization: str | None) -> bool:
    """True when a ``show_masters`` call carries at least one real filter.

    Whitespace-only counts as absent — ``_bookable_qs`` treats a blank
    ``specialization`` as "no filter supplied" (it says so at
    ``apps/marketplace/discovery.py``), which is precisely the unfiltered
    catalogue read this guard exists to keep out of a conversational turn.
    """
    return bool((city or "").strip() or (specialization or "").strip())


def render_no_criteria_clarification() -> DiscoveryReply:
    """The canon-prescribed reply to a criteria-less ``show_masters`` call."""
    return _render_ask_clarification(NO_CRITERIA_QUESTION, [])


def requested_services(args: dict[str, Any], specialization: str | None) -> list[str]:
    """The distinct services ONE ``show_masters`` call asked for (DRF-1312).

    Empty unless the call is COMPOSITE — two or more services. A single-service
    request needs nothing from this: either the catalog has it (the cards ARE
    the answer) or it does not (the zero-result path already names it and says
    so, DRF-1283). The half-answered request is the only one that lied.

    Two sources, in order:

    1. ``services`` — the model's own split, which is what the tool spec asks
       for. Splitting a sentence into service names is language understanding
       and this is where it belongs.
    2. ``specialization`` — split here, as the fallback for a model that
       filled only the substring. Safe to split because it is already
       normalized to service wording;
       ``apps.marketplace.discovery.split_requested_services`` documents why
       the user's raw turn is NOT.

    Note what neither source decides: whether we OFFER any of them. That is
    ``service_coverage``'s answer, off the catalog (AYLA-DEC-0045 / OD-9).
    """
    raw = args.get("services")
    if isinstance(raw, list):
        names = [str(item).strip() for item in raw if str(item or "").strip()]
        if len(names) >= 2:
            return names[:_MAX_MASTER_CARDS]
        if names:
            # The model named exactly one service. Not composite — nothing
            # here can be half-answered, so spend no EXISTS on it.
            return []
    parts = split_requested_services(specialization or "")
    return parts if len(parts) >= 2 else []


def generate_discovery_reply(
    message_text: str,
    *,
    history: list[dict[str, Any]] | None = None,
    personal_context: "PersonalContextView | None" = None,
    trace_id: str | None = None,
) -> DiscoveryReply:
    """Generate a discovery reply via the tenant-less LLM path (tool-capable).

    ``get_router().get_provider(None, skill="discovery")`` — ``tenant=None``
    short-circuits to the per-skill / org-wide provider tier, so NO tenant is
    required and no tenant-scoped read happens. The LLM may call the
    ``show_masters`` tool, which we execute via the sanctioned marketplace
    carve-out (``discover_masters``) and render as a master-card keyboard. On any
    ``LLMError`` (or an empty completion) returns a safe fallback line.

    ``personal_context`` (M-C1 / #1101) is the user's surfaceable GREEN memory;
    rendered into a system-prompt block when non-empty, ignored otherwise.
    """
    context_block = render_personal_context(personal_context) if personal_context else None
    messages = build_discovery_prompt(message_text, history=history, personal_context=context_block)
    try:
        provider = get_router().get_provider(None, skill=DISCOVERY_SKILL, op="complete")
        model = getattr(provider, "default_completion_model", None) or ""
        result = asyncio.run(
            provider.complete(messages, model=model, tools=[SHOW_MASTERS_TOOL_SPEC])
        )
    except Exception as exc:  # noqa: BLE001 — discovery must never 500; degrade to fallback
        logger.warning("orchestrator.discovery.llm_error trace=%s err=%s", trace_id, exc)
        return DiscoveryReply(text=get_fallback("ru"))

    # The model asked to see masters → run the cross-tenant carve-out + render.
    for call in result.tool_calls:
        if call.name == SHOW_MASTERS_TOOL_SPEC["name"]:
            args = call.arguments if isinstance(call.arguments, dict) else {}
            limit = args.get("limit")
            city = args.get("city") or None
            specialization = args.get("specialization") or None
            if not has_discovery_criteria(city, specialization):
                logger.info("orchestrator.discovery.show_masters.no_criteria trace=%s", trace_id)
                return render_no_criteria_clarification()
            cards = discover_masters(
                city=city,
                specialization=specialization,
                limit=int(limit) if isinstance(limit, int) and limit > 0 else _MAX_MASTER_CARDS,
                resolve_service=True,
            )
            # DRF-1312 — a composite request is checked service by service, so
            # the half nobody offers is stated rather than dropped.
            available, missing = service_coverage(
                requested_services(args, specialization), city=city
            )
            logger.info(
                "orchestrator.discovery.show_masters count=%d missing=%d trace=%s",
                len(cards),
                len(missing),
                trace_id,
            )
            return _render_master_cards(
                cards[:_MAX_MASTER_CARDS],
                city=city,
                specialization=specialization,
                available_services=available,
                missing_services=missing,
            )

    text = (result.text or "").strip()
    if not text:
        return DiscoveryReply(text=get_fallback("ru"))
    return DiscoveryReply(text=text[:_MAX_REPLY_CHARS])
