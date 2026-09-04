"""Render remembered personal context into a system-prompt block (M-C1 / #1101).

Pure bot-layer surfacing: turns a
:class:`apps.identity.services.memory_reader.PersonalContextView` into a short
Russian system-prompt paragraph so the concierge can say «помню, что ты…».

Read/surfacing only — this module never writes and never touches yellow/red
(the reader already scopes to green + summary). Returns ``None`` when there is
nothing to surface, so the caller injects nothing and the happy-path prompt is
unchanged.

Происхождение (P0-3, ``OD_C04_GROUNDED_WHY.md`` §1): этот блок — вторая
prompt-поверхность памяти (первая — ai-core memory block), и она прямо велит
модели говорить «помню, что ты…». Значит выведенный факт здесь опаснее, чем
где-либо ещё: он превращается в приписанную человеку реплику. Локальный стор
держит ОБА происхождения — извлечённое из речи (``source='explicit'``) и
выведенное (``inferred``/``signal``) — поэтому выведенное уходит отдельным
предложением с явным запретом ссылаться на него как на слова клиента.

Когда выведенных фактов нет, абзац байт-в-байт прежний.
"""

from __future__ import annotations

import uuid

from apps.identity.models import MemoryEntry
from apps.identity.services.memory_key_policy import read_current_view
from apps.identity.services.memory_reader import GreenFact, PersonalContextView

# Known green (key, value) → natural Russian phrase. Other keys have
# content-based renderers below; keys without either fall back to a stored
# `display` string or are skipped, so an unrenderable fact is never surfaced
# as raw JSON.
_DIET_PHRASES = {
    "vegan": "придерживается веганского питания",
    "vegetarian": "придерживается вегетарианского питания",
    "keto": "придерживается кето-диеты",
    "halal": "ест халяль",
    "kosher": "ест кошерное",
    # «я теперь снова ем мясо» — the correction row itself is shown honestly.
    "none": "больше не называет ограничений по питанию",
}

_FACT_RENDERERS = {
    "diet": lambda value: _DIET_PHRASES.get(value),
}

_SLOT_PHRASES = {
    "early_morning": "рано утром",
    "morning": "по утрам",
    "afternoon": "днём",
    "evening": "по вечерам",
    "late_evening": "поздно вечером",
}


def _fmt_amount(raw: str) -> str:
    """«3000.00» → «3 000» (human budget line, no kopecks)."""

    head = raw.split(".", 1)[0]
    return f"{int(head):,}".replace(",", " ") if head.isdigit() else raw


def _price_phrase(content: dict) -> str | None:
    lo, hi = content.get("min"), content.get("max")
    if isinstance(lo, str) and lo and isinstance(hi, str) and hi:
        return f"ориентируется на бюджет от {_fmt_amount(lo)} до {_fmt_amount(hi)} ₽"
    if isinstance(hi, str) and hi:
        return f"ориентируется на бюджет до {_fmt_amount(hi)} ₽"
    if isinstance(lo, str) and lo:
        return f"ориентируется на бюджет от {_fmt_amount(lo)} ₽"
    return None


def _render_by_key(key: str, content: dict) -> str | None:
    """Content-based renderers for the DRF-1261 pilot keys."""

    value = content.get("value")
    if key == "preferred_time_slots" and isinstance(value, str):
        phrase = _SLOT_PHRASES.get(value)
        return f"предпочитает время: {phrase}" if phrase else None
    if key == "preferred_districts" and isinstance(value, str) and value:
        # Verbatim as stated (may be inflected) — quoted, never «corrected».
        return f"предпочитает район «{value}»"
    if key == "price_range":
        return _price_phrase(content)
    if key == "favorite_masters" and isinstance(value, str) and value:
        return f"называет любимым мастером «{value}»"
    return None


# Declared-prefs (Ayla side) rendering for the «покажи, что знаешь обо мне»
# merge — the person must see the FULL active memory, not only the bot-local
# rows (silent-remember ruling 2026-08-23: the show/forget loop is what
# justifies remembering without asking).
_DECLARED_DIET_PHRASES = {
    "omnivore": "ест всё",
    "vegetarian": _DIET_PHRASES["vegetarian"],
    "vegan": _DIET_PHRASES["vegan"],
    "keto": _DIET_PHRASES["keto"],
    "halal": _DIET_PHRASES["halal"],
    "kosher": _DIET_PHRASES["kosher"],
    "other": "называет особое питание",
}


def describe_declared_prefs(context: dict) -> dict[str, str]:
    """Render Ayla declared prefs to {memory_key: phrase} for the show merge.

    Only the DRF-1261 pilot keys are rendered; ``favorite_masters`` holds
    SpecialistProfile UUIDs that can't be phrased without a lookup, and the
    deferred keys (busy_days, home/workplace_district, …) are not pilot
    memory — both are skipped rather than shown raw.
    """

    if not isinstance(context, dict):
        return {}
    out: dict[str, str] = {}

    diet = context.get("diet_type")
    if isinstance(diet, str) and diet in _DECLARED_DIET_PHRASES:
        out["diet"] = _DECLARED_DIET_PHRASES[diet]

    slots = context.get("preferred_time_slots")
    if isinstance(slots, list):
        labels = [_SLOT_PHRASES[s] for s in slots if s in _SLOT_PHRASES]
        if labels:
            out["preferred_time_slots"] = "предпочитает время: " + ", ".join(labels)

    districts = context.get("preferred_districts")
    if isinstance(districts, list):
        names = [d for d in districts if isinstance(d, str) and d]
        if names:
            out["preferred_districts"] = "предпочитает районы: " + ", ".join(
                f"«{d}»" for d in names
            )

    lo, hi = context.get("price_range_min"), context.get("price_range_max")
    price = _price_phrase(
        {
            "min": str(lo) if lo not in (None, "") else None,
            "max": str(hi) if hi not in (None, "") else None,
        }
    )
    if price:
        out["price_range"] = price
    return out


def describe_green_content(content: dict) -> str | None:
    """Render a green fact's ``content`` dict to a natural phrase, or None.

    Shared by prompt surfacing (M-C1) and the chat memory commands (M-B4) so
    «помню, что ты…» reads identically wherever it appears. Known (key, value)
    → a fixed phrase; the DRF-1261 keys → content renderers; otherwise a
    writer-stored ``display`` string; else None (an unrenderable fact is never
    surfaced as raw JSON).
    """

    if not isinstance(content, dict):
        return None
    key = content.get("key")
    value = content.get("value")
    renderer = _FACT_RENDERERS.get(key) if isinstance(key, str) else None
    if renderer is not None and isinstance(value, str):
        phrase = renderer(value)
        if phrase:
            return phrase
    # A writer-stored human display string outranks the generic key
    # renderer (legacy rows carry curated wording).
    display = content.get("display")
    if isinstance(display, str) and display.strip():
        return display
    if isinstance(key, str):
        return _render_by_key(key, content)
    return None


# Scanner-correction keys (DRF-1454) are read where they belong — the scanner
# card, through ``apps.orchestrator.memory.food.recall_corrections``. They
# must not ALSO ride every concierge prompt: up to 20 dishes' worth of phrases
# like «блюдо «борщ» называет «борщ по-домашнему»» in every conversation,
# including ones with no food in them (review DRF-1454 — found independently by
# two axes). The exclusion also closes the rollback hole: accumulated rows kept
# rendering after FOOD_SCANNER_MEMORY_ENABLED was flipped off. They stay visible
# in «покажи, что помнишь» — that surface is the person's; this one is the
# model's.
#
# Only ``food_dish_name:`` is written today (owner decision 2026-09-04, variant
# А — see ``food_memory.REMEMBERED_FIELDS``). The portion and macros prefixes
# stay listed as guards: whichever of them DRF-825 revives must be excluded from
# the prompt on the day it appears, not on the day somebody notices.
_PROMPT_EXCLUDED_KEY_PREFIXES = ("food_portion:", "food_dish_name:", "food_macros:")


def _prompt_visible(fact: GreenFact) -> bool:
    """May this fact ride the concierge system prompt?"""

    content = fact.content if isinstance(fact.content, dict) else {}
    key = content.get("key")
    return not (isinstance(key, str) and key.startswith(_PROMPT_EXCLUDED_KEY_PREFIXES))


def _render_fact(fact: GreenFact) -> str | None:
    """Render one green fact to a phrase, or None if it can't be phrased."""

    return describe_green_content(fact.content)


def render_personal_context(view: PersonalContextView) -> str | None:
    """Render `view` into a system-prompt paragraph, or None if nothing to surface."""

    if view.is_empty():
        return None

    parts: list[str] = []
    derived: list[str] = []
    if view.summary:
        # Происхождение summary не хранится ни в каком виде — оставляем его
        # там, где оно было. Это осознанный долг, а не недосмотр: тащить
        # сюда «неизвестно» без источника было бы догадкой о догадке.
        parts.append(view.summary.strip())
    for fact in view.green_facts:
        if not _prompt_visible(fact):
            continue
        phrase = _render_fact(fact)
        if not phrase:
            continue
        if fact.source == MemoryEntry.SOURCE_EXPLICIT:
            parts.append(phrase)
        else:
            derived.append(phrase)

    if not parts and not derived:
        return None

    if parts:
        block = (
            "Что ты уже знаешь об этом клиенте (используй естественно и только когда "
            "уместно — например «помню, что ты…»; НЕ перечисляй списком и НЕ "
            f"придумывай ничего сверх этого): {'; '.join(parts)}."
        )
    else:
        block = ""
    if derived:
        if block:
            block += " "
        block += (
            "Это мы вывели сами, клиент этого НЕ говорил — можешь учитывать, но НЕ "
            f"ссылайся на это как на его слова: {'; '.join(derived)}."
        )
    return block


def render_current_personal_context(user_id: uuid.UUID) -> str | None:
    """Read live green memory, resolve key conflicts, render the prompt paragraph.

    Conflict-aware counterpart to ``read_personal_context`` +
    :func:`render_personal_context`: live rows are collapsed by the key
    policy first (a single-value key surfaces ONE current value — an
    explicit correction beats a fresher inferred row), so the model never
    sees mutually exclusive facts (vegan + keto) in one block.
    """

    return render_personal_context(read_current_view(user_id))
