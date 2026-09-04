"""Food-scanner memory (DRF-1454) — what was clarified, what did not suit, what was eaten.

The scanner had no memory at all: a grep for ``memory`` / ``ayla_ai_core`` over
``apps/skills/food_scanner`` and ``apps/skills/food_clarify`` returned zero hits.
Every photo started from a blank slate, so the bot re-asked what the person had
already corrected and knew nothing about what had not suited them. This module is
that memory — and, just as importantly, the perimeter that decides what the pilot
is allowed to keep at all.

## Three kinds, three zones — and only one of them is green

Zones are per-fact (``MemoryEntry.sensitivity_zone``; ADR-0011 §5 +
``docs/specs/memory-entry-schema.md`` §5). Green rides the PERSONAL_DATA welcome
consent and needs no per-entry ``consent_at``; yellow/red require one and are not
actively collected in the pilot. Food is more sensitive than an ordinary
preference, so nothing here defaults to green.

* **«как это блюдо называется» → GREEN.** The name a person typed into *our*
  recognition card is a record about *our own* behaviour: «for this dish we
  already asked, and this is the answer we were given». It exists to stop the bot
  asking twice — the service-contract basis green was defined for (ADR-0011 §11).
  It is a naming preference, not a measurement: nothing about the meal, its size
  or its nutrition is inferable from «этот салат я называю „греческий“».

  **Weight and macros are NOT kept — owner decision of 2026-09-04, variant А.**
  They belong to the nutrition diary, and by the ADR-0009 ownership matrix the
  diary is Ayla's, not the bot's. Until ``nutrition_client`` grows an update
  endpoint (DRF-825) a corrected portion has no way to reach the canonical
  record, so keeping it here would produce two diverging numbers for one meal:
  «помню 500 г» on the card while ``log_meal`` files Ayla's 250. One source of
  truth is worth more than an early feature — see
  :data:`REMEMBERED_FIELDS`.

* **«что ел» → YELLOW, therefore not stored here.** A dated log of meals is not a
  preference; accumulated it *is* a nutrition profile — and that profile already
  has an owner. Ayla holds the diary behind the HEALTH special-category consent
  (:mod:`apps.orchestrator.nutrition_context`, 152-ФЗ ст. 10). A second copy in
  the bot would rebuild the same profile on a weaker basis, which is exactly what
  zone discipline exists to prevent. :func:`note_meal` is the declared perimeter:
  it classifies, logs, and refuses. Not re-asking *within* a conversation does not
  need durable memory — the last card rides in ``Conversation.skill_state``,
  dialogue state under the conversation's own retention.

* **«что не подошло» → RED / YELLOW, perimeter only.** An intolerance or an
  allergy is special-category data; DRF-1290 already ruled that such a statement
  never lands in green — dropped with an explicit log, never silently, never
  stored. A plain exclusion («свинину не ем») is not a diagnosis, but it *reveals*
  religion or a diagnosis, and data revealing a special category is treated as
  one; green is wrong for it too. :func:`note_refusal` classifies and drops.

  The «Не то» tap is a different animal and must not be confused with a refusal:
  it says the *recogniser* was wrong about this photo, not that the person avoids
  the dish. It is a quality signal about us, not a fact about them, so it is no
  personal memory at all — :func:`note_recognition_rejected` only logs.

Yellow and red are «фундамент»: the classification lives in code and the write is
one switch away, but the switch stays off until the yellow/red consent-capture
flow ships. Flipping it early would build a religious/medical profile out of food
refusals with nothing authorising it — and the sanctioned writer would drop the
row anyway (``memory_writer._check_minor_protection`` fails closed until #597), so
the only thing an early flip buys is a false belief that we remembered.

## Consent — memory's own gate, not HEALTH's

Two bases, both checked, both fail-closed:

1. ``can_store_green_memory`` — the PERSONAL_DATA welcome consent, which is
   green's 152-ФЗ basis for a local ``MemoryEntry`` (ADR-0011 §11).
2. :func:`apps.consent.services.has_memory_consent` (zone ``green``) — the
   MEMORY_CONSENT_SPEC basis, **global per ``ayla_user_id``** and therefore read
   cross-tenant, never through the tenant-scoped ``has_consent`` (which raises on
   the tenant-less path this code runs on).

The pilot onboarding grants both in one tap (DRF-1311), so requiring both costs a
consented user nothing and closes the gate for everyone else. That two bases exist
for one write is a known seam flagged by
:mod:`apps.identity.services.personal_context` — unifying them is a Decision-Log
call, not something this module may settle by quietly picking one.

## Provenance — only «сказал сам» counts as the person's words

Membership is tested against :data:`ayla_ai_core.STATED_SOURCES`, the one
dictionary the three repositories share. Only a declared «stated»/«explicit» value
is the person's own words; everything else — including a value this code has never
heard of — is a derivation. Writes are stamped ``source='explicit'`` (the
sanctioned writer then stamps ``provenance='user_stated'``) because a correction
*is* typed by the person. Reads surface a remembered correction only when its
source is in ``STATED_SOURCES``: a derived row may never come back to the user as
«ты поправил».

## Never breaks the turn

Every public function is best-effort. By the time a skill runs, the turn's
idempotency key is claimed — an exception here would lose the reply on retry
rather than retry it. All failures are logged and swallowed; the caller sees an
:class:`Outcome` or an empty :class:`FoodRecall` and the turn proceeds unchanged.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from django.db.transaction import TransactionManagementError

from apps.consent.memory import can_store_green_memory
from apps.consent.services import has_memory_consent
from apps.identity.models import MemoryEntry
from apps.identity.services.ayla_link import ensure_ayla_link
from apps.identity.services.memory_key_policy import read_current_view, select_current_facts
from apps.identity.services.memory_reader import (
    get_or_create_personal_context,
    read_green_entries,
)
from apps.identity.services.memory_writer import supersede_entries, write_entry

logger = logging.getLogger(__name__)


# ─── provenance dictionary — one rule, two possible homes ──────────────────
#
# The rule belongs to ``ayla_ai_core`` (see the module docstring), and the
# library grew a name for it — ``STATED_SOURCES`` — in the commit that closed
# «цитатой считается только объявленное сказал сам». That commit is NOT in the
# revision this repo pins (``pyproject.toml`` → ``ayla-ai-core@ee6425ac``), and
# bumping the pin is its own change with its own checklist: the library has TWO
# production consumers, so a bump is a coordinated release, not a line in a
# feature PR.
#
# So the import is attempted and the value falls back to the same two strings
# the library defines. The fallback is transitional and must not outlive the
# bump: ``test_the_stated_dictionary_matches_the_library`` fails the moment the
# pin lands with a DIFFERENT set, so the two cannot silently drift, and the
# fallback disappears in the bump PR.
try:  # pragma: no cover — exercised by whichever pin the environment carries
    from typing import cast

    from ayla_ai_core import STATED_SOURCES as _LIBRARY_STATED_SOURCES

    # The pinned library resolves unknown attributes through a PEP 562
    # ``__getattr__`` typed ``-> object``, which mypy rejects as the right
    # operand of ``in`` — annotate the contract instead of inheriting it.
    STATED_SOURCES: frozenset[str] = cast(frozenset[str], _LIBRARY_STATED_SOURCES)
except ImportError:  # pin predates the dictionary
    # "stated" — the library's own name; "explicit" — the backend's name for
    # exactly the same thing (and the value `MemoryEntry.SOURCE_EXPLICIT` uses).
    STATED_SOURCES = frozenset({"stated", "explicit"})


# ─── the three correction fields (mirror food_correction's callbacks) ──────

FIELD_GRAMS = "grams"
FIELD_NAME = "name"
FIELD_MACROS = "macros"

#: Every field the ✏️ card can ask about. This is the PROMPT vocabulary: all
#: three questions are still asked, and all three answers are still claimed by
#: the food_correction skill instead of falling through to the concierge.
CORRECTION_FIELDS: tuple[str, ...] = (FIELD_GRAMS, FIELD_NAME, FIELD_MACROS)

#: ...of which the bot is allowed to REMEMBER exactly one.
#:
#: Owner decision of 2026-09-04 (variant А, Q-NUTRITION-01): weight and macros
#: are diary data, and by the ADR-0009 ownership matrix the nutrition diary
#: belongs to Ayla. ``nutrition_client`` has no update endpoint yet — it arrives
#: with DRF-825 — so a portion corrected here could never reach the canonical
#: record, and storing it locally would leave two diverging numbers for one
#: meal (rules 1 and 5 of ADR-0009). The dish NAME has no such owner: it is a
#: naming preference about our own recognition, so it stays.
#:
#: When DRF-825 lands, the portion correction goes to Ayla — not into this
#: tuple.
REMEMBERED_FIELDS: tuple[str, ...] = (FIELD_NAME,)

# One memory key per (field, dish) — one key, because one field is remembered.
# Unknown keys are single-cardinality by default in ``memory_key_policy``, which
# is exactly what a calibration wants: one current name per dish, superseded
# (not duplicated) when re-corrected.
_KEY_PREFIX: dict[str, str] = {
    FIELD_NAME: "food_dish_name",
}

_WRITE_PURPOSE = "food_scanner:clarification"
_MEMORY_KIND = "preference"

# Longest dish name we will key on. A name longer than this is not a dish, and a
# memory key is not a place to put a paragraph.
_MAX_DISH_LEN = 64
# Sanity bounds for the two answers we read but do not keep (see
# REMEMBERED_FIELDS): they still have to be READ, because the reply quotes the
# number back and an unreadable answer must re-ask rather than acknowledge.
# A plate is not a sack of potatoes — an out-of-range number is a typo.
_MIN_GRAMS = 1
_MAX_GRAMS = 5000
# One macro of one plate. Its job is to reject a date typed into the macros
# prompt («12/08/2026»), not to police nutrition.
_MAX_MACRO_G = 999

# How many distinct dishes one person may accumulate corrections for.
#
# This bound is the same argument that keeps «что ел» out of the store, applied
# to the store itself: a green zone has no auto-TTL and supersession keeps
# history, so an unbounded dish namespace would slowly become the very nutrition
# profile this module refused to build — only in the weaker zone. A cap keeps
# the set «the handful of dishes this person actually corrects» instead.
#
# It refuses NEW dishes rather than evicting old ones: eviction is a deletion,
# and deletions here belong to the person (152-ФЗ), not to a cap. Corrections to
# dishes already remembered keep working at the cap.
_MAX_DISHES = 20


def scanner_memory_enabled() -> bool:
    """Deploy-free rollback switch. Default ON — see the settings comment.

    Read through Django settings (not a module-level env snapshot) so tests flip
    it with the ``settings`` fixture and an operator flips it with a restart,
    matching :func:`apps.orchestrator.memory_block.concierge_memory_enabled`.
    False restores the pre-DRF-1454 behaviour exactly: no write, no recall, and
    no pending correction to claim a plain-text turn with.
    """

    from django.conf import settings

    return bool(getattr(settings, "FOOD_SCANNER_MEMORY_ENABLED", True))


class Outcome(str, Enum):
    """What a remember-call actually did. Counted in logs; never raised."""

    WRITTEN = "written"
    DUPLICATE = "duplicate"
    DISABLED = "disabled"
    #: the per-person dish cap — see _MAX_DISHES.
    CAP_REACHED = "cap_reached"
    #: yellow/red perimeter — classified, logged, deliberately not stored.
    DROPPED_SENSITIVE = "dropped_sensitive"
    #: the field is not ours to keep — see REMEMBERED_FIELDS (weight / macros).
    NOT_REMEMBERED = "not_remembered"
    NO_CONSENT = "no_consent"
    NO_IDENTITY = "no_identity"
    FORGOTTEN = "forgotten"
    UNPARSED = "unparsed"
    ERROR = "error"


# ─── the sensitive perimeter (DRF-1290 shape) ──────────────────────────────

# Medical markers → red (152-ФЗ ст. 10 special category). The allergy stems the
# green-fact extractor already refuses on, kept in sync deliberately, plus the
# health states a person actually types into the ✏️ prompt (review DRF-1454:
# «у меня диабет» / «я беременна» / «кормлю грудью» all passed the «dish name»
# filter and were written to the green zone — the least protected one).
_MEDICAL_RE = re.compile(
    r"аллерг|непереносимост"
    r"|\bдиабет\w*|\bгастрит\w*|\bцелиаки\w*|\bязв\w*"
    r"|\bпанкреатит\w*|\bподагр\w*|\bанеми\w*"
    r"|\bбеременн\w*|\bкормлю\s+грудью\b|\bгрудн\w+\s+вскармливани\w*\b",
    re.IGNORECASE,
)

# Plain dietary exclusion → yellow. Not a diagnosis, but a strong channel for
# one (and for religion), so it is not green either.
#
# Every alternative here is a statement ABOUT THE PERSON. «без сахара» is not:
# it is how a drink is ordered, and «Кофе без сахара» is a dish name, not a
# refusal. Treating it as one answered a name correction with «это
# чувствительные данные» — the false positive costs more than the miss, because
# it puts the refusal script in front of somebody who refused nothing.
_EXCLUSION_RE = re.compile(
    r"\bне\s+(?:ем|ешь|едим|пью|употребля\w*|переношу|куша\w*)\b"
    r"|\bмне\s+нельзя\b"
    r"|\bисключ\w+\s+из\s+рациона\b"
    # Self-declared diets and fasting: they reveal religion or a diagnosis,
    # and data revealing a special category is treated as one.
    r"|\bвегетариан\w*|\bвеган\w*|\bхалял\w*|\bкошерн\w*"
    r"|\bпощусь\b|\bпостюсь\b|\b(?:держу|соблюдаю)\s+пост\b",
    re.IGNORECASE,
)


def classify_refusal(text: str) -> str:
    """Zone for a «это мне не подошло» statement — ``""`` when it is not one.

    Red beats yellow: a clause carrying a medical marker is special-category even
    when it also reads as a plain exclusion.
    """

    if not isinstance(text, str) or not text.strip():
        return ""
    if _MEDICAL_RE.search(text):
        return MemoryEntry.SENSITIVITY_RED
    if _EXCLUSION_RE.search(text):
        return MemoryEntry.SENSITIVITY_YELLOW
    return ""


# ─── value parsing ─────────────────────────────────────────────────────────

_GRAMS_RE = re.compile(r"\d+")
# «12/8/32» and the labelled «Б12 / Ж8 / У32» people actually type. The short
# non-digit run after each separator is the label; anything longer is prose.
_MACROS_RE = re.compile(r"(\d{1,4})\s*[/|]\s*\D{0,3}(\d{1,4})\s*[/|]\s*\D{0,3}(\d{1,4})")


def parse_correction_value(field: str, text: str) -> Any | None:
    """Turn the person's free-text answer into the value to remember, or ``None``.

    Conservative on purpose (the extractor's rule, DRF-1260): a value we cannot
    read confidently is dropped, never guessed. A guessed correction is worse than
    no correction — it makes the *next* card wrong in the person's name.
    """

    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return None

    if field == FIELD_GRAMS:
        # «-300» — минус не часть числа для ``\d+``, и 300 г сохранялись как
        # будто человек подтвердил порцию (ревью DRF-1454). Отрицательной
        # порции не бывает: знак перед любой цифрой обесценивает весь ответ.
        if re.search(r"[-−]\s*\d", text):
            return None
        match = _GRAMS_RE.search(text)
        if match is None:
            return None
        grams = int(match.group())
        return grams if _MIN_GRAMS <= grams <= _MAX_GRAMS else None

    if field == FIELD_MACROS:
        match = _MACROS_RE.search(text)
        if match is None:
            return None
        parts = [int(part) for part in match.groups()]
        # Grams have a sanity range and macros used to have none, so «12/08/2026»
        # parsed to «12/8/2026» and was printed back on the card as this person's
        # own figure. A macro is a portion of a plate, not a year.
        if any(part > _MAX_MACRO_G for part in parts) or not any(parts):
            return None
        return "/".join(str(part) for part in parts)

    if field == FIELD_NAME:
        # A bare number is an answer to the *grams* question, not a dish name —
        # ``_clean_name`` rejects it. Case is kept: the KEY is normalised, the
        # value the person typed is not — «Куриная грудка» must not come back
        # to them as «куриная грудка».
        return _clean_name(text)

    return None


def _clean_name(text: Any) -> str | None:
    """The dish name as the person wrote it: whitespace collapsed, nothing else."""

    if not isinstance(text, str):
        return None
    name = " ".join(text.split()).strip()
    if not name or name.isdigit():
        return None
    return name[:_MAX_DISH_LEN]


def _dish_slug(dish: Any) -> str:
    """Normalised dish key: lowercased, whitespace-collapsed, length-capped."""

    if not isinstance(dish, str):
        return ""
    slug = " ".join(dish.split()).strip().lower()
    if not slug or slug.isdigit():
        return ""
    return slug[:_MAX_DISH_LEN]


def _memory_key(field: str, dish_slug: str) -> str | None:
    prefix = _KEY_PREFIX.get(field)
    if prefix is None or not dish_slug:
        return None
    return f"{prefix}:{dish_slug}"


def _display(dish_display: str, value: Any) -> str:
    """The phrase the person sees for this row in «покажи, что помнишь».

    Stored ON the row because that is the convention
    :func:`apps.persona.memory_surface.describe_green_content` reads — a
    writer-stored ``display`` outranks the per-key renderer, and a row with
    neither is silently unrenderable, i.e. invisible.

    Invisible is not an option here. The silent-remember ruling (2026-08-23)
    that allows the bot to store a fact without asking rests on the show/forget
    loop: a row the person cannot see is a row we had no right to write. One
    phrase, written once. The concierge PROMPT is not a reader of these rows —
    they are excluded there (:data:`apps.persona.memory_surface.
    _PROMPT_EXCLUDED_KEY_PREFIXES`); the readers are the person's own memory
    list and the scanner card.

    ``dish_display`` is the dish as the person wrote it, not the normalised
    key: the chat says «Куриная грудка», and the list must not answer with
    «куриная грудка» (review DRF-1454). The KEY stays lowercased — matching
    is ours, spelling is theirs.

    One phrase and no ``field`` argument, because :data:`REMEMBERED_FIELDS`
    holds one field.
    """

    return f"блюдо «{dish_display}» называет «{value}»"


# ─── read side ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FoodRecall:
    """What we already clarified with this person about this dish.

    One field, mirroring :data:`REMEMBERED_FIELDS`: the name. Weight and macros
    are asked and answered but never kept, so there is nothing to recall for
    them and :meth:`has` says ``False`` — which is what makes the card ask
    about them again instead of quoting a number nobody stands behind.

    Only values whose stored provenance is in :data:`ayla_ai_core.STATED_SOURCES`
    reach this object — everything here is safe to attribute to the person.
    """

    dish_name: str | None = None

    def is_empty(self) -> bool:
        return self.dish_name is None

    def has(self, field: str) -> bool:
        """Was ``field`` already clarified for this dish? Drives «не переспрашивать»."""

        return field == FIELD_NAME and self.dish_name is not None


EMPTY_RECALL = FoodRecall()


def recall_corrections(bot_user: Any, *, dish: str) -> FoodRecall:
    """Everything the person already corrected for ``dish``. Never raises.

    Read-only, and deliberately so: it resolves an existing ``ayla_user_id`` but
    never mints one. Reading memory is not a «dependent action» in the J-O3 sense
    — a person with nothing stored must not acquire a permanent Ayla subject
    merely by being shown a card.
    """

    dish_slug = _dish_slug(dish)
    if not dish_slug or not scanner_memory_enabled():
        return EMPTY_RECALL
    try:
        user_id = _existing_ayla_user_id(bot_user)
        if user_id is None:
            return EMPTY_RECALL
        if not has_memory_consent(user_id, "green"):
            logger.info(
                "food_memory.read_gate_closed reason=no_memory_green bot_user=%s",
                getattr(bot_user, "id", "?"),
            )
            return EMPTY_RECALL

        wanted = {
            _memory_key(field, dish_slug): field
            for field in REMEMBERED_FIELDS
            if _memory_key(field, dish_slug)
        }
        found: dict[str, Any] = {}
        for fact in read_current_view(user_id).green_facts:
            content = fact.content if isinstance(fact.content, dict) else {}
            field = wanted.get(content.get("key"))
            if field is None:
                continue
            # Provenance rule (OD_C04 §1): only a declared «сказал сам» value may
            # be handed back as the person's own correction. An unknown source is
            # a derivation — the safe side is the default.
            if fact.source not in STATED_SOURCES:
                continue
            found[field] = content.get("value")

        recall = FoodRecall(dish_name=_as_text(found.get(FIELD_NAME)))
        if not recall.is_empty():
            logger.info(
                "food_memory.recall bot_user=%s fields=%s",
                getattr(bot_user, "id", "?"),
                sorted(f for f in REMEMBERED_FIELDS if recall.has(f)),
            )
        return recall
    except Exception:  # noqa: BLE001 — memory recall must never break the turn
        logger.exception(
            "food_memory.recall_failed bot_user=%s",
            getattr(bot_user, "id", "?"),
        )
        return EMPTY_RECALL


def _as_text(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return text or None


def _existing_ayla_user_id(bot_user: Any) -> uuid.UUID | None:
    raw = getattr(bot_user, "ayla_user_id", None)
    if not raw or not isinstance(raw, (uuid.UUID, str)):
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


# ─── write side — «что уже уточнял» (green) ────────────────────────────────


def remember_correction(bot_user: Any, *, dish: str, field: str, value: Any) -> Outcome:
    """Persist one correction as a green ``MemoryEntry``. Never raises.

    Ownership first: a field outside :data:`REMEMBERED_FIELDS` is somebody
    else's data and returns :attr:`Outcome.NOT_REMEMBERED` before anything is
    read, resolved or written. The check lives HERE rather than in the calling
    skill so that the policy has one home — a second caller cannot acquire a
    different opinion about who owns the nutrition diary.

    Gate order after that mirrors :func:`apps.orchestrator.memory.
    personal_context.record_explicit_green_facts` and for the same reason (owner
    ruling J-O3): consent first, then the parsed value, and only then identity —
    a turn that ends up storing nothing must not mint a permanent Ayla subject.
    """

    if field not in REMEMBERED_FIELDS:
        # Counted, never stored — the same shape as the yellow/red perimeter,
        # for the same kind of reason: the data has an owner and it is not us.
        logger.info(
            "food_memory.not_remembered field=%s bot_user=%s stored=0 owner=ayla_diary",
            field,
            getattr(bot_user, "id", "?"),
        )
        return Outcome.NOT_REMEMBERED

    dish_slug = _dish_slug(dish)
    key = _memory_key(field, dish_slug)
    if key is None or value is None or value == "":
        return Outcome.UNPARSED
    if not scanner_memory_enabled():
        return Outcome.DISABLED

    try:
        if not can_store_green_memory(bot_user):
            return Outcome.NO_CONSENT

        user_id = ensure_ayla_link(bot_user, trigger="food_memory_write")
        if user_id is None:
            # Ayla unreachable or resolution failed: memory is keyed on this id,
            # so there is no valid key to write under. The next turn retries.
            return Outcome.NO_IDENTITY

        if not has_memory_consent(user_id, "green"):
            logger.info(
                "food_memory.write_gate_closed reason=no_memory_green bot_user=%s",
                getattr(bot_user, "id", "?"),
            )
            return Outcome.NO_CONSENT

        upc = get_or_create_personal_context(user_id)
        # Never accrete memory onto a forgotten person. Erasure is independent of
        # consent, so the gates above do not cover it (152-ФЗ право на забвение).
        if upc.soft_deleted_at is not None or upc.forget_all_requested_at is not None:
            return Outcome.FORGOTTEN

        # Superseded rows are still «live» to the reader (it filters only on the
        # delete columns), and letting them into the dedup made a return to an
        # earlier value silently impossible: «плов» → «плов узбекский» → «плов»
        # matched the first, dead row, answered «Запомнила», and left «плов
        # узбекский» as the current value. Dedup therefore compares against the
        # CURRENT fact set only —
        # the same set ``recall_corrections`` will read back — so a DUPLICATE
        # verdict means «this is already what we would tell you», never «we once
        # heard this». Excluding them also bounds the scan by number of dishes
        # rather than by number of corrections ever made.
        live_rows = [
            row
            for row in read_green_entries(user_id)
            if row.status != MemoryEntry.STATUS_SUPERSEDED
        ]
        current = select_current_facts(live_rows)
        dishes: set[str] = set()
        for row in current:
            content = row.content if isinstance(row.content, dict) else {}
            if content.get("key") == key and content.get("value") == value:
                return Outcome.DUPLICATE
            remembered_dish = content.get("dish")
            if isinstance(remembered_dish, str) and remembered_dish:
                dishes.add(remembered_dish)

        if dish_slug not in dishes and len(dishes) >= _MAX_DISHES:
            logger.info(
                "food_memory.dish_cap_reached bot_user=%s dishes=%d",
                getattr(bot_user, "id", "?"),
                len(dishes),
            )
            return Outcome.CAP_REACHED

        entry = write_entry(
            user_id=user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            # The person typed this into our card — it is their own words, and
            # the sanctioned writer stamps provenance='user_stated' from it.
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind=_MEMORY_KIND,
            content={
                "key": key,
                "value": value,
                "dish": dish_slug,
                "field": field,
                # Makes the row visible in «покажи, что помнишь» — see _display.
                # The dish keeps the person's spelling; the key stays a slug.
                "display": _display(_clean_name(dish) or dish_slug, value),
            },
            request_id=uuid.uuid4(),
            purpose=_WRITE_PURPOSE,
            consent_at=None,  # green: service-contract basis, no per-entry consent
        )
        if entry is None:
            return Outcome.ERROR

        # A re-correction of the same dish replaces the previous one instead of
        # coexisting with it: two names for one dish is a contradiction the card
        # would otherwise have to choose between (DRF-1261 «исправляю»).
        displaced = [
            row
            for row in live_rows
            if row.id != entry.id
            and isinstance(row.content, dict)
            and row.content.get("key") == key
        ]
        # INSERT and this UPDATE are two transactions, deliberately: the
        # sanctioned writer's forensic path uses ``atomic(durable=True)`` and
        # refuses to run inside a caller's block, so wrapping both would break
        # it the day the yellow/red perimeter opens. A crash in between leaves
        # two live rows of one key, which the key policy resolves on read
        # (freshest wins) — self-healing, and the reason this is tolerable.
        superseded = 0
        if displaced:
            superseded = supersede_entries(
                replaced_by=entry,
                entries=displaced,
                reason=MemoryEntry.SUPERSESSION_CORRECTED,
            )

        # Count + field only. The value is the person's food — never in a log.
        logger.info(
            "food_memory.clarification_written bot_user=%s field=%s superseded=%d",
            getattr(bot_user, "id", "?"),
            field,
            superseded,
        )
        return Outcome.WRITTEN
    except TransactionManagementError:
        # The writer's durable audit refused to run inside somebody's atomic
        # block — fail-loud by design (ADR-0011 §11.3: the rejection row is
        # 152-ФЗ гл. 3 forensic evidence). Unreachable while the perimeter is
        # shut; logged at ERROR rather than swallowed into the generic branch so
        # that opening the perimeter over a caller that wraps this in atomic is
        # visible instead of silent.
        logger.error(
            "food_memory.write_forensic_at_risk bot_user=%s field=%s — "
            "remember_correction was called inside a transaction.atomic block",
            getattr(bot_user, "id", "?"),
            field,
        )
        return Outcome.ERROR
    except Exception:  # noqa: BLE001 — memory write must never break the turn
        logger.exception(
            "food_memory.write_failed bot_user=%s field=%s",
            getattr(bot_user, "id", "?"),
            field,
        )
        return Outcome.ERROR


# ─── perimeter — «что ел» (yellow) and «что не подошло» (red / yellow) ──────


def note_meal(bot_user: Any, *, dish: str) -> Outcome:
    """Classify a recognised dish as yellow meal history and refuse to store it.

    The declared perimeter for «что ел»: the zone is decided here, in code, and
    the write is one switch away — but it stays shut. Ayla owns the diary behind
    the HEALTH consent; a second copy in the bot would be the same profile on a
    weaker basis. Logs a count, never the dish.
    """

    if not _dish_slug(dish):
        return Outcome.UNPARSED
    logger.info(
        "food_memory.zone_gated kind=meal_history zone=%s bot_user=%s stored=0",
        MemoryEntry.SENSITIVITY_YELLOW,
        getattr(bot_user, "id", "?"),
    )
    return Outcome.DROPPED_SENSITIVE


def note_refusal(bot_user: Any, *, text: str) -> Outcome:
    """Classify «это я не ем» / «у меня непереносимость» and refuse to store it.

    DRF-1290's shape, extended from allergies to food exclusions: recognised,
    counted, never stored, never silent. ``UNPARSED`` when the text carries no
    refusal marker at all — nothing was recognised, so nothing was dropped.
    """

    zone = classify_refusal(text)
    if not zone:
        return Outcome.UNPARSED
    # Zone + a stable marker tag only. The clause itself is the very thing we
    # refused to keep — putting it in a log would store it after all.
    logger.info(
        "food_memory.zone_gated kind=refusal zone=%s bot_user=%s stored=0",
        zone,
        getattr(bot_user, "id", "?"),
    )
    return Outcome.DROPPED_SENSITIVE


def note_recognition_rejected(bot_user: Any, *, scan_id: str) -> None:
    """The «Не то» tap — a fact about the recogniser, not about the person.

    Deliberately not memory: «не то» means we read the photo wrong, and inferring
    «он это не ест» from it would be exactly the fabrication DRF-1260 forbids.
    Kept as a quality signal so recognition accuracy stays measurable.
    """

    logger.info(
        "food_memory.recognition_rejected bot_user=%s scan=%s",
        getattr(bot_user, "id", "?"),
        scan_id,
    )
