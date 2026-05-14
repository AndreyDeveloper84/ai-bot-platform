"""Water-tracker skill — text-based beverage log.

Sprint 9 / P2 (DRF-819). When a short message parses as a beverage +
volume (``parse_beverage`` regex hits), the skill logs it to Ayla and
replies with a short confirmation.

## Why this comes BEFORE ``food_clarify`` in the registry

Per skill order in ``apps/skills/apps.py``:

    privacy → handoff → water → food_clarify → faq → echo

Water is the **fast lane**. A confident parse (``стакан воды``) lands a
log call in Ayla and a "записала, 250 мл" reply within ~50 ms. Only
when the parse misses do we fall through to ``food_clarify``, which
emits the diary-or-typo card.

## Async bridge

Following the FAQ-skill pattern: ``handle`` is sync per the skill
protocol, but the Ayla client is async. We wrap with ``asyncio.run``
internally. Pipeline is sync from the dispatcher's POV.

## Refusal handling

``parse_beverage`` returns the sentinel ``"REFUSED"`` when the user
explicitly declines («не скажу»). In a free-text water-log context
this is unusual (one types a beverage OR not) but the parser is shared
with anketa code in mysite — we keep the path. A REFUSED match falls
through (skill returns matches=False so the next skill takes the turn).

## Error handling

* ``NutritionUnavailableError`` (Ayla down / circuit open) → graceful
  fallback message. The user sees "сейчас не могу записать, попробуй
  через минуту"; no stack trace.
* Any other ``NutritionAPIError`` → same graceful fallback. We do not
  surface internal codes to users.
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from apps.integrations.ayla import (
    NutritionAPIError,
    NutritionUnavailableError,
    external_user_id_for,
    get_nutrition_client,
)
from apps.skills.base import SkillContext, SkillResult
from apps.skills.registry import register
from apps.skills.water.parser import REFUSED, BeverageMatch, parse_beverage

logger = logging.getLogger(__name__)

# Cap message length so we don't run the parser on full questions —
# the same 30-char cap food_clarify uses.
_MAX_LEN = 30


_AYLA_DOWN_FALLBACK = "Не получилось записать прямо сейчас — попробуй через минуту."


@register
class WaterSkill:
    """Fast-lane beverage log. Matches short text shaped as a drink."""

    name: ClassVar[str] = "water"

    def matches(self, context: SkillContext) -> bool:
        text = context.message_text.strip()
        if not text or len(text) > _MAX_LEN:
            return False
        result = parse_beverage(text)
        return isinstance(result, BeverageMatch)

    def handle(self, context: SkillContext) -> SkillResult:
        text = context.message_text.strip()
        parsed = parse_beverage(text)

        # matches() already screened — but be defensive: parse may
        # return REFUSED or None if the message was edited between
        # matches() and handle() (rare; pipeline doesn't re-fetch).
        if parsed == REFUSED or parsed is None:
            return SkillResult(
                reply_text="",
                should_send=False,
                meta={"reply_kind": "water_skip"},
            )

        assert isinstance(parsed, BeverageMatch)

        external_id = external_user_id_for(context.bot_user)
        try:
            entry = asyncio.run(
                get_nutrition_client().add_water(
                    external_user_id=external_id,
                    ml=parsed.ml,
                    beverage_slug=parsed.slug,
                )
            )
        except NutritionUnavailableError:
            logger.warning(
                "water.ayla_unavailable user=%s slug=%s",
                external_id,
                parsed.slug,
            )
            return SkillResult(
                reply_text=_AYLA_DOWN_FALLBACK,
                meta={"reply_kind": "water_ayla_down"},
            )
        except NutritionAPIError:
            logger.exception("water.ayla_api_error user=%s", external_id)
            return SkillResult(
                reply_text=_AYLA_DOWN_FALLBACK,
                meta={"reply_kind": "water_ayla_error"},
            )

        reply = _format_reply(entry, parsed)
        return SkillResult(
            reply_text=reply,
            action_type="water_logged",
            action_data={
                "entry_id": entry.entry_id,
                "slug": parsed.slug,
                "ml": parsed.ml,
                "water_ml": entry.water_ml,
            },
            meta={"reply_kind": "water_logged"},
        )


def _format_reply(entry, parsed: BeverageMatch) -> str:
    """Compose the user-facing confirmation.

    Mirrors the mysite voice: terse, friendly. When the beverage has a
    water-coefficient < 1 (coffee 0.95, etc.) Ayla returns ``water_ml <
    ml``; we show both numbers so the user understands why "стакан
    кофе" doesn't count as 250 ml of water. Milestone text comes
    straight from Ayla.
    """
    parts: list[str] = []
    if entry.ml == entry.water_ml:
        parts.append(f"Записала {entry.ml} мл 💧")
    else:
        parts.append(f"Записала {entry.ml} мл — {entry.water_ml} мл в счёт воды 💧")
    if entry.today_total_ml and entry.today_norm_ml:
        parts.append(f"Сегодня: {entry.today_total_ml} из {entry.today_norm_ml} мл.")
    if entry.milestone_text:
        parts.append(entry.milestone_text)
    if entry.alcohol_recovery_hint:
        parts.append("Может стакан воды? 😊")
    return " ".join(parts)
