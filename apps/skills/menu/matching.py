"""Pilot conversational matcher + canonical main menu (DRF-963 / Wave 1, variant A).

Pure text processing plus one optional catalog read. No skill imports —
safe to import from any skill module at registration time.

### Why this module exists

Production MAX dispatch runs WITHOUT ``SkillContext.intent`` (the LLM
orchestrator is deliberately off on the pilot), so live routing is driven
by each skill's keyword fallback. Wave 1 Validation (2026-08-05) found the
covered vocabulary too narrow: a customer who writes «Хочу массаж» or
«Мне бы маникюр» hits no keyword at all and used to be echoed back
verbatim (findings U-1 / U-5).

The booking skill owns the booking vocabulary
(``apps.skills.booking.skill._BOOKING_KEYWORDS``: «записаться», «запись»,
«отменить», «перенести», «сколько стоит», …) and that list is NOT
duplicated here — DRF-963 must not touch ``apps/skills/booking/`` (S1
anti-touch). :class:`apps.skills.menu.skill.MenuSkill` registers LAST
before echo, so every phrase this module classifies is a phrase that no
existing skill claimed. Consequence: what we add here is strictly
additive, and no currently-routed turn can change owner.

### What the matcher adds

Two families that carry a booking request without any booking keyword:

* **service mentions** — «Хочу массаж», «Мне бы маникюр», «массаж спины».
  Seeded with the pilot salon's vocabulary and, when a tenant is in scope,
  widened with the tenant's own :class:`apps.catalog.models.CatalogService`
  titles (the brief's «названия услуг тенанта»).
* **availability phrasings** — «есть окошко», «свободное время», «когда
  можно прийти».

Matching is case-insensitive, ``ё``-insensitive and punctuation-insensitive.
Deliberately no stemmer: the patterns are stored as stems already
(«маникюр» matches «маникюра», «маникюрчик»), which is cheap, predictable
and easy for an operator to reason about.

### Canonical menu

:func:`main_menu_buttons` is the single definition of the pilot's main
menu, shared by the honest fallback (U-5), the «Помощь» reply and the
welcome keyboard, so the three surfaces can never drift.

The callbacks are ``cb:menu:*`` slugs that :class:`MenuSkill` translates
back into the canonical Russian phrase the existing skills already claim
(see :data:`MENU_CALLBACK_TEXT`) — no new skill contract is invented, and
MAX's payload charset stays ASCII-safe.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

# Everything that is not a Cyrillic/Latin letter or a digit becomes a space,
# so «Хочу записаться!!!» and «хочу, записаться» normalise identically.
_NON_WORD = re.compile(r"[^0-9a-zа-я]+")


def normalize(text: str) -> str:
    """Lower-case, fold ``ё``→``е``, drop punctuation, collapse whitespace.

    Returns a string padded with single spaces on both ends so callers can
    test for a standalone word with a plain ``" word " in normalized``
    containment check.
    """
    lowered = (text or "").lower().replace("ё", "е")
    return f" {_NON_WORD.sub(' ', lowered).strip()} "


# ---------------------------------------------------------------------------
# U-1 — booking-request vocabulary the booking skill's own keywords miss
# ---------------------------------------------------------------------------

# Service stems of the pilot salon («Формула тела», массаж + уход) plus the
# common beauty vocabulary a walk-in customer uses. Stems, not full words:
# «массаж» covers «массажа» / «массажик», «маникюр» covers «маникюра».
#
# These are matched as PREFIXES of a whole word, so «массаж» matches
# «массажа» but not «промассажировали» — see :func:`_mentions_stem`.
#
# INVARIANT (pinned by ``test_prefix_stems_are_long_enough``): a prefix stem
# is at least :data:`_MIN_PREFIX_STEM` characters. Shorter prefixes swallow
# unrelated words — the 3-letter «спа» matched «спасибо», routing a thank-you
# into the booking flow. Short service words belong in :data:`_SERVICE_WORDS`,
# which matches whole words only.
_MIN_PREFIX_STEM = 5

_SERVICE_STEMS: tuple[str, ...] = (
    "массаж",
    "маникюр",
    "педикюр",
    "стрижк",
    "окрашиван",
    "укладк",
    "ресниц",
    "космето",
    "чистка",
    "пилинг",
    "депиляц",
    "эпиляц",
    "шугаринг",
    "обертыван",
    "антицеллюлит",
    "лимфодренаж",
    "прессотерап",
    "миостимул",
    "вакуумн",
    "релакс",
    "тайск",
    "лифтинг",
    "процедур",
)

# Short or prefix-ambiguous service words — matched as WHOLE words only.
_SERVICE_WORDS: frozenset[str] = frozenset(
    {
        "спа",
        "spa",
        "бровь",
        "брови",
        "бровей",
        "лицо",
        "лица",
        "спина",
        "спины",
        "стоун",
        "стоунтерапия",
    }
)

# Availability / walk-in phrasings that mean «I want a slot» without using
# any booking verb. Substring match is fine — these are multi-word or long
# enough not to collide.
_AVAILABILITY_SIGNALS: tuple[str, ...] = (
    "свободн",
    "окошк",
    "есть место",
    "есть места",
    "какие слоты",
    "на когда можно",
    "когда можно прийти",
    "когда можно подойти",
    "можно прийти",
    "можно подойти",
)


def _mentions_stem(normalized: str, stem: str) -> bool:
    """True when ``normalized`` contains a whole word starting with ``stem``.

    ``normalized`` is space-padded by :func:`normalize`, so a leading space
    anchors the word start; the word may continue with any suffix
    («массаж» → «массажа»), which is the cheap stand-in for stemming.
    """
    return f" {stem}" in normalized


def mentions_service(text: str, *, extra_stems: tuple[str, ...] = ()) -> bool:
    """True when the text names a bookable service.

    ``extra_stems`` carries the tenant's own catalog titles (see
    :func:`tenant_service_stems`) so a salon whose services aren't in the
    seed list still routes correctly.
    """
    normalized = normalize(text)
    if not normalized.strip():
        return False
    if _SERVICE_WORDS.intersection(normalized.split()):
        return True
    return any(_mentions_stem(normalized, stem) for stem in _SERVICE_STEMS + extra_stems)


def looks_like_booking_request(text: str, *, extra_stems: tuple[str, ...] = ()) -> bool:
    """True when the turn is a booking request the other skills didn't claim.

    Only ever consulted for text that already fell through every registered
    skill (MenuSkill is registered last before echo), so this is purely
    additive coverage — it can never take a turn away from booking, FAQ or
    any wellness skill.
    """
    normalized = normalize(text)
    if not normalized.strip():
        return False
    if any(signal in normalized for signal in _AVAILABILITY_SIGNALS):
        return True
    return mentions_service(text, extra_stems=extra_stems)


# Generic words that show up in service titles but carry no service meaning.
# Kept deliberately small: over-filtering costs coverage, under-filtering
# costs precision, and precision failures here are visible to the customer.
_CATALOG_STOPWORDS: frozenset[str] = frozenset(
    {
        "минут",
        "минута",
        "минуты",
        "часов",
        "сеанс",
        "сеанса",
        "абонемент",
        "программа",
        "комплекс",
        "стандарт",
        "премиум",
        "базовый",
        "полный",
        "рублей",
        "скидка",
        "акция",
        "новинка",
        "хит",
    }
)


def tenant_service_stems(tenant: Any) -> tuple[str, ...]:
    """Normalised words from the tenant's mirrored service catalog.

    Reads through the tenant-scoped default manager, NOT ``all_tenants``:
    skills run inside ``tenant_scope`` (the channel consumer enters it
    before dispatch), the marketplace carve-out is the only sanctioned
    cross-tenant catalog read (import-boundary contract MKT1), and a
    routing hint must never be widened by another salon's vocabulary.
    The ``tenant`` argument stays an explicit filter so a scope/context
    mismatch yields no rows rather than the wrong salon's services.

    Best-effort: the catalog mirror is a cache of Ayla's canonical
    services (ADR-0009), so a missing mirror, an unsynced tenant, a
    tenant-less (global) turn or a DB hiccup must degrade to the seed
    vocabulary rather than break the turn.

    The words become PREFIX stems, so the same length invariant as
    :data:`_SERVICE_STEMS` applies (short tokens like «для», «60», «мин»
    would match almost anything). Generic pricing/duration vocabulary that
    routinely appears in service titles is dropped via
    :data:`_CATALOG_STOPWORDS` — «массаж 60 минут» must contribute «массаж»,
    not «минут», or «сколько минут ждать?» would route into booking.
    """
    if tenant is None:
        return ()
    try:
        from apps.catalog.models import CatalogService

        titles = CatalogService.objects.filter(
            tenant=tenant,
            is_active=True,
        ).values_list("name", flat=True)[:200]
    except Exception:  # noqa: BLE001 — routing must never break on a catalog read
        logger.warning("menu.tenant_service_stems_failed", exc_info=True)
        return ()

    stems: set[str] = set()
    for title in titles:
        for word in normalize(str(title)).split():
            if len(word) < _MIN_PREFIX_STEM or word.isdigit():
                continue
            if word in _CATALOG_STOPWORDS:
                continue
            stems.add(word)
    return tuple(sorted(stems))


# ---------------------------------------------------------------------------
# Help / menu vocabulary
# ---------------------------------------------------------------------------

# EXACT normalized phrases only — never substrings.
#
# The help skill registers BEFORE faq (a bot-capability question is not a
# salon-KB question), so its claim overrides the generic FAQ question
# signals. That override MUST stay surgical: a substring match on «помоги»
# would swallow «помогите подобрать массаж», which belongs to booking, and
# «меню» would swallow a question about a spa menu. Requiring the whole
# message to BE the help phrase keeps the override predictable for the
# operator and impossible to trip accidentally.
#
# Slash commands normalise to their bare word («/help» → «help»).
_HELP_PHRASES: frozenset[str] = frozenset(
    {
        "помощь",
        "помоги",
        "помогите",
        "справка",
        "меню",
        "команды",
        "help",
        "menu",
        "что ты умеешь",
        "что умеешь",
        "что ты можешь",
        "что можешь",
        "что вы умеете",
        "что умеет бот",
        "что тут можно",
        "что здесь можно",
        "что я могу",
        "твои возможности",
        "возможности",
    }
)


def looks_like_help_request(text: str) -> bool:
    """True when the WHOLE message is a request for the capability list."""
    return normalize(text).strip() in _HELP_PHRASES


# ---------------------------------------------------------------------------
# Canonical main menu
# ---------------------------------------------------------------------------

MENU_CALLBACK_PREFIX = "cb:menu:"

CALLBACK_MENU_BOOK = "cb:menu:book"
CALLBACK_MENU_MY_BOOKINGS = "cb:menu:my_bookings"
CALLBACK_MENU_RESCHEDULE = "cb:menu:reschedule"
CALLBACK_MENU_CANCEL = "cb:menu:cancel"
CALLBACK_MENU_HELP = "cb:menu:help"

# Each menu button is translated into the canonical Russian phrase that an
# EXISTING skill already claims on the keyword-fallback path, so a tap and
# the equivalent typed message take exactly the same route:
#
#   «Хочу записаться»    → booking (_BOOKING_KEYWORDS: «записаться»)
#   «Покажи мои записи»  → booking (lookup.is_personal_booking_lookup)
#   «Перенести запись»   → booking (_BOOKING_KEYWORDS: «перенести»)
#   «Отменить запись»    → booking (_BOOKING_KEYWORDS: «отменить»)
#
# ``cb:menu:help`` is answered locally and therefore has no entry here.
MENU_CALLBACK_TEXT: dict[str, str] = {
    CALLBACK_MENU_BOOK: "Хочу записаться",
    CALLBACK_MENU_MY_BOOKINGS: "Покажи мои записи",
    CALLBACK_MENU_RESCHEDULE: "Перенести запись",
    CALLBACK_MENU_CANCEL: "Отменить запись",
}


def main_menu_buttons() -> list[dict[str, str]]:
    """The pilot's main menu — one definition, three surfaces.

    Used by the honest fallback (U-5), the «Помощь» reply and the welcome
    keyboard. Channel-agnostic ``{label, callback}`` shape; the channel
    adapter converts it to the native wire format.
    """
    return [
        {"label": "📅 Записаться", "callback": CALLBACK_MENU_BOOK},
        {"label": "📋 Мои записи", "callback": CALLBACK_MENU_MY_BOOKINGS},
        {"label": "🔄 Перенести запись", "callback": CALLBACK_MENU_RESCHEDULE},
        {"label": "❌ Отменить запись", "callback": CALLBACK_MENU_CANCEL},
        {"label": "❓ Помощь", "callback": CALLBACK_MENU_HELP},
    ]


def main_menu_action_data() -> dict[str, Any]:
    """``SkillResult.action_data`` carrying the main menu.

    Emits the platform-canonical envelope (``attachments`` → ``inline_keyboard``)
    because that is the ONLY shape the Telegram adapter reads
    (``apps.channels.telegram.handler._extract_keyboard``); the MAX adapter
    accepts it too (``apps.channels.max.handler._build_attachments`` branch 1).
    Using the flat short-form here would render on MAX and silently vanish on
    Telegram.
    """
    return {
        "attachments": [
            {
                "type": "inline_keyboard",
                "payload": {"buttons": main_menu_buttons()},
            }
        ],
        "kind": "main_menu",
    }
