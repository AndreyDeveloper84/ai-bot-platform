"""Food history — READ from Ayla's diary, never a copy of it (DRF-1467).

The owner asked for one thing: «я хочу, чтобы клиент мог сразу записать
сфотканное блюдо и его разбор в память». On the fork «копия у себя или
чтение у Ayla» the ruling was **«чтением из Ayla, копию не делаем»**
(2026-09-04). This module is that read, and the only one.

### What already existed, and what did not

The photographed dish is **already saved**: ``food_scanner`` posts it to
Ayla through ``log_meal`` the moment the person taps «В дневник». Nothing
was being lost — it simply lived at Ayla and nothing on our side ever
asked for it back:

* the person asking «что я ел сегодня» got calories and macros and **not
  one dish name** — :func:`apps.nutrition_proactive.render.render_daily_report`
  read ``summary.entries`` only to test it for truthiness (``_anything_logged``)
  and then threw the rows away;
* the scanner re-recognising a dish already logged an hour ago had no way
  to know that, and offered «Записать в дневник?» as if the day were empty;
* the concierge's nutrition block
  (:mod:`apps.orchestrator.nutrition_context`) carried the *weekly protein
  aggregate* and, in its own words, «no meal rows» — so the dietitian
  surface DRF-1464 will build on it had nothing to be a dietitian about.

``GET /nutrition/internal/summary/`` has carried the rows the whole time:
``entries[] = {id, dish_name, calories, protein_g, fat_g, carbs_g,
meal_type, logged_at}`` (Ayla ``FoodLogEntrySerializer``). This module is
the missing reader, not a new endpoint and not a new store.

### Why there is no copy — and no cache either

:func:`apps.orchestrator.memory.food.note_meal` refuses to store what was
eaten, and stays refusing. Two locks, both still shut:

1. **Yellow zone.** Meal history is ``MemoryEntry.SENSITIVITY_YELLOW``.
   The pilot runs green only; no collection flow for yellow exists.
2. **A weaker basis.** The diary at Ayla sits behind the ``HEALTH``
   consent. The same health profile mirrored into ``MemoryEntry`` would
   be the same data protected worse.

A cache is the same copy under another name — a five-minute TTL still
means a row of somebody's health data at rest in the bot's database, on a
basis nobody granted, that a 152-ФЗ erasure request would have to find.
So there is none: every reader here goes to Ayla, or says it could not.

The usual objection — «what if the network to Ayla is down?» — does not
apply on this contour. The bot (8014) and the backend (8000) are the same
machine behind the same local nginx. «No network to Ayla» means «Ayla is
down», and a downed Ayla cannot recognise the photograph either, because
recognition is also hers. A copy would only cover «Ayla alive enough to
recognise, not alive enough to hand back the diary».

### Honest refusal, not silence and not invention

The status is carried explicitly (:class:`Status`) rather than smuggled in
an empty list, because «Ayla did not answer» and «you logged nothing today»
are two different truths, and a surface that confuses them lies to the
person about what they ate.

The refusal itself is worded by the surface that was asked, not by this
module — there is no copy here to keep in sync with theirs. Where the
history WAS the question, that sentence already exists and is used:
:data:`apps.orchestrator.personal_surface.DIARY_UNAVAILABLE_TEXT` («не могу
сейчас поднять твой дневник — сервис питания не отвечает»). Where it was
not — the recognition card, the concierge prompt — the honest response is
to add nothing, and both callers do exactly that.

The circuit breaker inside
:class:`apps.integrations.ayla.nutrition_client.NutritionClient` is used,
never bypassed: a call made while the breaker is open raises
``NutritionUnavailableError("circuit_open")`` and lands on
:attr:`Status.UNAVAILABLE` like any other outage.

### Consent — HEALTH is the gate, checked before anything else

Same two keys as :mod:`apps.orchestrator.nutrition_context`, and for the
same reason: nutrition is special-category data under 152-ФЗ ст. 10.
``PERSONAL_DATA`` is the baseline (ADR-0011 §11); ``HEALTH`` is the
special-category basis, with a capture flow since DRF-1453. Both are read
through ``has_global_consent`` because the concierge runs tenant-less.
Fail-closed: a consent read that raises reads as «no consent».

:func:`apps.orchestrator.personal_surface.render_diary` deliberately gates
on ``PERSONAL_DATA`` alone and is left alone — showing subjects their own
record on their own request is a subject right (ADR-0011 §8), argued at
length in that module. This module feeds surfaces that are NOT that: a
recognition card and an LLM prompt. The stricter gate belongs to the
stricter use, and the shipped surface is not regressed to match.

### Never raises

Every entry point returns a value. Callers run after the turn's
idempotency key is claimed, where an exception loses the reply rather
than retrying it — the contract :mod:`apps.orchestrator.nutrition_context`
and :mod:`apps.orchestrator.memory_block` already keep.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from apps.orchestrator.memory.food import dish_slug

logger = logging.getLogger(__name__)

#: How many of today's meals ever leave this module. A day with more rows
#: than this is a day nobody reads to the end, and every consumer here is
#: length-bounded anyway (a recognition card, a prompt block, a chat reply).
MAX_MEALS = 12

#: Longest dish name we pass on. Matches ``memory.food._MAX_DISH_LEN`` so a
#: name that survives one module survives the other; anything longer is a
#: recogniser accident, not a dish.
MAX_DISH_CHARS = 64

#: Sanity ceiling for a single plate's calories. Not nutrition policy — it
#: rejects a field that arrived holding something other than kcal rather
#: than printing it back to the person as fact.
MAX_MEAL_KCAL = 10_000


class Status(str, Enum):
    """Why a read produced what it produced. Never raised, always returned."""

    #: Ayla answered. ``meals`` is what she has — possibly nothing.
    OK = "ok"
    #: PERSONAL_DATA and/or HEALTH not granted. No call was made.
    NO_CONSENT = "no_consent"
    #: Outage, circuit open, 4xx, misconfigured token, malformed payload.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Meal:
    """One row of Ayla's diary, as far as we ever carry it.

    ``calories`` is ``0`` when Ayla sent none — distinguishable from a real
    zero only in principle, and no consumer here prints a bare ``0 ккал``.
    """

    dish: str
    calories: int
    meal_type: str

    @property
    def slug(self) -> str:
        return dish_slug(self.dish)


@dataclass(frozen=True)
class TodayDiary:
    """The answer to «what is in the diary today» — including «I don't know»."""

    status: Status
    meals: tuple[Meal, ...] = ()

    @property
    def ok(self) -> bool:
        """Ayla answered. Says nothing about whether the day is empty."""
        return self.status is Status.OK

    @property
    def is_empty(self) -> bool:
        """Ayla answered and the day holds nothing. False when we don't know."""
        return self.ok and not self.meals

    def has_dish(self, dish: Any) -> bool:
        """Is this dish already logged today? False whenever we cannot tell.

        Keyed through :func:`apps.orchestrator.memory.food.dish_slug` — the
        same normalisation the scanner's own memory keys use, so «Борщ» from
        the recogniser and «борщ» in the diary are one dish on both sides.
        """
        slug = dish_slug(dish)
        if not slug or not self.ok:
            return False
        return any(meal.slug == slug for meal in self.meals)

    def dish_names(self) -> tuple[str, ...]:
        return tuple(meal.dish for meal in self.meals)


#: Returned wherever the answer is «we did not ask». Shared instance so a
#: caller may compare cheaply; frozen, so sharing is safe.
UNKNOWN = TodayDiary(Status.UNAVAILABLE)


def read_consent_open(bot_user: Any) -> bool:
    """PERSONAL_DATA **and** HEALTH granted? Fail-closed on any error.

    ``has_global_consent`` rather than the tenant-scoped ``has_consent``:
    the concierge and the global skill path run with
    ``current_tenant() is None``, where the scoped sibling raises.
    """
    try:
        from apps.consent.models import ConsentRecord
        from apps.consent.services import has_global_consent

        return has_global_consent(
            bot_user, ConsentRecord.ConsentType.PERSONAL_DATA.value
        ) and has_global_consent(bot_user, ConsentRecord.ConsentType.HEALTH.value)
    except Exception:  # noqa: BLE001 — fail-closed: no consent proven, no read
        logger.exception("orchestrator.food_history.consent_check_failed")
        return False


def meals_from_summary(summary: Any) -> tuple[Meal, ...]:
    """``SummaryResponse.entries`` → meals. Pure, defensive, never raises.

    Split out from :func:`read_today` so a caller that ALREADY holds the
    summary — :func:`apps.orchestrator.personal_surface.render_diary` fetched
    it one line earlier — reads the rows out of the value in its hand instead
    of issuing a second identical GET. Passing a value along is not a cache:
    nothing is stored and nothing outlives the turn.

    Every field is coerced here rather than trusted. ``entries`` is the one
    part of the summary the client hands over raw (``list(body.get("entries")
    or [])``), so this is the first place its contents are looked at at all.
    """
    rows = getattr(summary, "entries", None)
    if not isinstance(rows, list):
        return ()

    meals: list[Meal] = []
    for row in rows:
        if len(meals) >= MAX_MEALS:
            break
        if not isinstance(row, dict):
            continue
        dish = _clean_dish(row.get("dish_name"))
        if not dish:
            # A row we cannot name is a row we cannot show. Dropping it is
            # honest; «блюдо» as a placeholder would be filler presented as
            # a record.
            continue
        meals.append(
            Meal(
                dish=dish,
                calories=_clamp_kcal(row.get("calories")),
                meal_type=_clean_meal_type(row.get("meal_type")),
            )
        )
    return tuple(meals)


def read_today(bot_user: Any, *, date: str | None = None) -> TodayDiary:
    """Today's diary, straight from Ayla. Never raises, never stores.

    Order is the gate first: :func:`read_consent_open` must pass before a
    single byte leaves Ayla, so a closed gate costs no HTTP call and cannot
    be distinguished by timing from a granted one that found nothing.
    """
    if not read_consent_open(bot_user):
        return TodayDiary(Status.NO_CONSENT)

    summary = _fetch_summary(bot_user, date=date)
    if summary is None:
        return UNKNOWN
    return TodayDiary(Status.OK, meals_from_summary(summary))


# ─── internals ─────────────────────────────────────────────────────────────


def _fetch_summary(bot_user: Any, *, date: str | None) -> Any | None:
    """One Ayla GET, best-effort. ``None`` on every failure.

    The degradation ladder ``nutrition_context._fetch_deficits`` and
    ``personal_surface._fetch`` both use: an unconfigured environment is a
    DEBUG non-event, an outage is INFO, anything unexpected gets a
    traceback — and all three return ``None``.
    """
    try:
        from apps.integrations.ayla import external_user_id_for, get_nutrition_client

        client = get_nutrition_client()
        external_id = external_user_id_for(bot_user)
    except Exception as exc:  # noqa: BLE001 — unconfigured env is not an error
        logger.debug("orchestrator.food_history.disabled: %s", exc)
        return None

    try:
        from apps.integrations.ayla import NutritionAPIError, NutritionUnavailableError

        # ``with_comment`` stays off: this reader wants the rows, and Ayla's
        # generated comment is a second LLM call on her side that no consumer
        # here prints.
        return asyncio.run(client.daily_summary(external_user_id=external_id, date=date))
    except (NutritionUnavailableError, NutritionAPIError) as exc:
        # Includes the breaker's own «circuit_open» — used, not routed around.
        logger.info("orchestrator.food_history.unavailable reason=%s", exc)
        return None
    except Exception:  # noqa: BLE001 — never break the turn; key already claimed
        logger.exception("orchestrator.food_history.fetch_failed")
        return None


def _clean_dish(raw: Any) -> str:
    """Whitespace-collapsed, length-capped, control chars gone.

    Dish names are recogniser output, i.e. free-form text of unknown
    provenance heading for both a chat reply and an LLM prompt. The prompt
    side crosses ``build_safe_inputs`` as well (delimiters + brace escape);
    this is the part that has to hold on the chat side too.
    """
    if not isinstance(raw, str):
        return ""
    name = " ".join(ch for ch in raw.split() if ch)
    name = "".join(ch for ch in name if ch.isprintable())
    name = " ".join(name.split()).strip()
    if not name or name.isdigit():
        return ""
    return name[:MAX_DISH_CHARS]


def _clamp_kcal(raw: Any) -> int:
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_MEAL_KCAL, value))


def _clean_meal_type(raw: Any) -> str:
    """Ayla's enum slug, or ``""``. Never rendered raw — a label, not copy."""
    if not isinstance(raw, str):
        return ""
    slug = raw.strip().lower()
    return slug[:32] if slug.isascii() and slug.replace("_", "").isalnum() else ""
