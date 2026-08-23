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
        },
        "required": ["question"],
    },
}

# Defensive cap on a clarification option's button label — these strings are
# model-generated, unlike the catalog-bounded master names `_render_master_cards`
# renders, so nothing upstream already bounds their length.
_MAX_OPTION_LABEL_CHARS = 40


@dataclass(frozen=True)
class DiscoveryReply:
    """A discovery turn's outcome: the reply text + optional channel action_data
    (e.g. the master-card keyboard). ``action_data`` mirrors the per-tenant
    SkillResult shape so the MAX handler renders it via ``_build_attachments``.

    ``persisted`` (W5): True when the assistant turn was already persisted
    by the producer (the AIConcierge store in
    :mod:`apps.orchestrator.concierge`) — the handler must NOT record it
    again. Legacy producers leave it False.
    """

    text: str
    action_data: dict[str, Any] | None = None
    persisted: bool = False


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


def _render_master_cards(
    cards: list[MasterCard],
    *,
    city: str | None = None,
    specialization: str | None = None,
) -> DiscoveryReply:
    """Render discovered masters as a reply + a one-button-per-card keyboard.

    PUBLIC fields only (the DTO carries nothing else). Each button's callback
    is ``cb:discover:book:{tenant_id}:{master_id}`` — the handoff seam
    (#1020) — extended with ``:{service_id}`` when discovery resolved the
    queried service unambiguously (DRF-962), so the tap enters booking with
    the service context instead of the stale-context dead-end.

    ``city`` / ``specialization`` are the query that produced ``cards`` and are
    used ONLY for the empty case, so the refusal can name what was searched for
    (DRF-1283 — see :func:`render_no_match`). Callers that have the query
    should pass it; callers that don't get the generic line.
    """
    if not cards:
        return render_no_match(city=city, specialization=specialization)

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
        service_suffix = f":{card.service_id}" if card.service_id is not None else ""
        buttons.append(
            {
                "label": f"Записаться к {card.name}",
                "callback": (
                    f"{CALLBACK_DISCOVER_BOOK_PREFIX}"
                    f"{card.tenant_id}:{card.master_id}{service_suffix}"
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


def _render_ask_clarification(question: str, options: list[str]) -> DiscoveryReply:
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
    """
    text = (question or "Уточните, пожалуйста?").strip()[:_MAX_REPLY_CHARS]
    cleaned = [str(opt).strip() for opt in options if str(opt).strip()]
    if not cleaned:
        return DiscoveryReply(text=text)
    buttons = [{"label": opt[:_MAX_OPTION_LABEL_CHARS], "callback": opt} for opt in cleaned[:5]]
    action_data = {"attachments": [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]}
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
            logger.info(
                "orchestrator.discovery.show_masters count=%d trace=%s", len(cards), trace_id
            )
            return _render_master_cards(
                cards[:_MAX_MASTER_CARDS], city=city, specialization=specialization
            )

    text = (result.text or "").strip()
    if not text:
        return DiscoveryReply(text=get_fallback("ru"))
    return DiscoveryReply(text=text[:_MAX_REPLY_CHARS])
