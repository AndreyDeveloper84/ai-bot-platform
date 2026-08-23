"""The person's own surface: «что я ел» and «что ты про меня помнишь».

DRF-1302 + DRF-1305, built as ONE surface because for the person it is one
question asked two ways. The owner's ruling on DRF-1260 — «запоминать молча
и показывать в списке» — is a ruling about a *list*, and the food diary is
the other half of the same list: what the bot recorded, and what the bot
remembers. Splitting them would build two screens where one is asked for,
and would put two conflicting edits into the same concierge file.

### What was missing

The diary existed only as the button that PUTS something into it
(``cb:food:diary`` / ``cb:food:to_diary:*`` in
:mod:`apps.orchestrator.ui.keyboards`). Nothing anywhere read it back —
not a command, not a skill, not a concierge tool. DRF-1268 moved four
nutrition skills onto the global path as tools (``health_screening``,
``log_water``, ``clarify_food_entry``, ``start_nutrition_anketa``); every
one of them WRITES. This module is the read.

Memory was closer but not there: :func:`apps.persona.memory_commands.
handle_memory_command` already claims «покажи что знаешь обо мне» on the
global path *deterministically*, ahead of the model. What it does not have
is a tail — its ``_SHOW_TRIGGERS`` is a fixed substring list, so «что ты
про меня помнишь» (the exact phrase in the DRF-1305 acceptance criterion)
missed it and fell through to a generic model answer. See the report for
the measurement.

### Two layers, deterministic first — the memory-commands precedent

1. **Explicit triggers execute without a model.**
   :func:`looks_like_diary_request` claims «что я ел сегодня», «мой
   дневник», «что там с водой» and their neighbours from
   :func:`apps.orchestrator.nutrition_global.try_handle_structured_nutrition_turn`,
   the same place ``/anketa`` is claimed. Memory keeps its existing
   deterministic claim in ``memory_commands``.

   This layer is what makes a CHIP honest. Tap == typed message on this
   path (see ``_render_ask_clarification`` in
   :mod:`apps.orchestrator.discovery`), so a chip executes only if
   something on OUR side claims the string it carries. Three of the four
   chips here are claimed by a deterministic matcher — «/anketa»
   (``is_structured_nutrition_turn``), «что я ел сегодня»
   (``looks_like_diary_request``), «забудь питание»
   (``handle_memory_command``).

   The fourth, «стакан воды», is model-routed and is called out as such
   at :data:`CHIP_WATER`: DRF-1268 gives all free text on this path to the
   concierge, and no deterministic entry for a drink exists to claim. Its
   safeguard is different in kind — the callback is the literal example the
   tool description and the prompt both teach — and a test pins that so the
   two cannot drift.

2. **The phrasing tail goes to the model as a tool.**
   :data:`SHOW_MY_RECORDS_TOOL_SPEC` — one tool, one ``section``
   argument. One tool rather than two because the *renderers* are one
   surface and the concierge wiring is one branch; the ``section``
   argument is what keeps the ANSWER matched to the question, so «что я
   ел» does not also dump the memory list.

### Every printed number traces — DRF-1285's boundary, reused not restated

The owner's rule (DRF-1295): we may speak about the person's DATA, never
about their BODY. :mod:`apps.nutrition_proactive.render` already expresses
that boundary in code, and its
``test_every_number_printed_comes_from_the_inputs`` pins it. So the daily
view here CALLS ``render_daily_report`` rather than growing a second
renderer that would drift from the first — the only difference is that a
pull does not carry the push's «не пиши мне» footer, which is why that
renderer grew an ``include_opt_out`` switch instead of a copy.

The weekly view is three lines built here, each one a field of
``DeficitsResponse`` — days observed, average protein against the
person's own goal, days below it. Ayla's free-form ``hint`` is
deliberately NOT printed: it is written as a signal for a model
(:mod:`apps.orchestrator.nutrition_context` consumes it that way), not as
reviewed user copy, and an unbounded upstream sentence is exactly the
thing that would put a number on screen that traces to nothing.

### Consent — PERSONAL_DATA, and why not HEALTH

The 152-ФЗ baseline (``ConsentType.PERSONAL_DATA``, ADR-0011 §11) gates
this surface. HEALTH — which :mod:`apps.orchestrator.nutrition_context`
additionally requires — deliberately does NOT:

* That module ships the person's week INTO AN LLM PROMPT. This one renders
  it deterministically back TO THE PERSON WHO LOGGED IT. Showing a subject
  their own record on their own request is a subject right (ADR-0011 §8),
  the same basis ``memory_commands``' «покажи что знаешь» runs on — and it
  gates on PERSONAL_DATA alone.
* The WRITE path is already ungated: ``WaterSkill`` and ``food_scanner``
  post to Ayla today, on the live pilot, with no health-consent check
  anywhere. Requiring a stricter basis to read back what we accept without
  one would be an inconsistency, not a protection.
* Nobody on the pilot holds HEALTH (measured, DRF-1284/DRF-1305). Gating
  on it would ship this surface dead — which is the exact complaint
  DRF-1302 was opened about.

Fail-closed: a consent read that throws reads as «no consent».

### Failure — degrade, never raise

Every path here runs after the turn's idempotency key is claimed, so an
exception would LOSE the reply rather than retry it. Ayla down, token
missing, malformed payload → an honest sentence, never a fabricated
number and never a stack trace. Same contract as
:mod:`apps.orchestrator.nutrition_context` and
:mod:`apps.orchestrator.memory_block`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from apps.orchestrator.discovery import DiscoveryReply

logger = logging.getLogger(__name__)

#: Own budget rather than the concierge's 600, for the same reason DRF-1304
#: gave its catalog one: «all» renders two sections plus chips, and a 600-char
#: clip would cut a real line of the person's own diary in half while the
#: buttons under it stayed. Still bounded — a reply nobody scrolls is not an
#: answer either.
_MAX_PERSONAL_REPLY_CHARS = 1400

# ---------------------------------------------------------------------------
# Model-callable tool (flat spec — same shape as SHOW_MASTERS_TOOL_SPEC).
# ---------------------------------------------------------------------------

#: The three sections the person can ask for. ``all`` exists because «покажи
#: всё, что ты про меня знаешь» is a real question and answering half of it
#: would be a worse answer than answering it.
SECTION_DIARY = "diary"
SECTION_MEMORY = "memory"
SECTION_ALL = "all"

#: ``today`` / ``week``. Anything else is normalised to ``today``: a period we
#: cannot read is a period we must not pretend to have read.
PERIOD_TODAY = "today"
PERIOD_WEEK = "week"

SHOW_MY_RECORDS_TOOL_SPEC: dict[str, Any] = {
    "name": "show_my_records",
    "description": (
        "Человек спрашивает про СВОИ данные: что он ел или пил, свой "
        "дневник питания, сколько калорий за сегодня или за неделю "
        "(«что я ел сегодня», «мой дневник», «сколько я выпил воды») — "
        "или про то, что бот о нём запомнил («что ты про меня помнишь», "
        "«что ты обо мне знаешь», «какие данные обо мне»). Показывает "
        "записанное и запомненное списком. Только показывает — ничего не "
        "записывает и не удаляет."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "enum": [SECTION_DIARY, SECTION_MEMORY, SECTION_ALL],
                "description": (
                    "diary — вопрос про еду, воду, калории, дневник. "
                    "memory — вопрос про то, что бот запомнил о человеке. "
                    "all — человек просит показать всё сразу."
                ),
            },
            "period": {
                "type": "string",
                "enum": [PERIOD_TODAY, PERIOD_WEEK],
                "description": (
                    "Только для section=diary. today — за сегодня (по умолчанию), week — за неделю."
                ),
            },
        },
        "required": ["section"],
    },
}

#: action_type values the concierge wrapper must execute after the LLM pass.
PERSONAL_TOOL_ACTIONS = frozenset({SHOW_MY_RECORDS_TOOL_SPEC["name"]})


# ---------------------------------------------------------------------------
# Chips. Every callback below is a string one of the two DETERMINISTIC
# matchers claims — never a phrase that only the model could interpret.
# ---------------------------------------------------------------------------

#: Starts the 5-step anketa. Claimed by ``is_structured_nutrition_turn``
#: (``stripped == "/anketa"``), so the tap runs the FSM, not the model.
CHIP_ANKETA = {"label": "📋 Пройти анкету", "callback": "/anketa"}

#: The ONE chip here that is model-routed rather than matcher-claimed, and the
#: distinction is worth naming because the rest of this module leans on it.
#:
#: DRF-1268 sends all free text on the global path to the concierge, so
#: «стакан воды» reaches ``WaterSkill`` only after the model picks
#: ``log_water``. There is no deterministic entry for a drink and inventing one
#: would fight that ticket's design, not extend it.
#:
#: What makes the chip safe anyway is that its callback is the LITERAL example
#: the model is given twice — in ``LOG_WATER_TOOL_SPEC["description"]`` and
#: again in the concierge prompt's nutrition-priority block, both of which name
#: «стакан воды» in so many words and forbid routing it to
#: ``clarify_food_entry`` (DRF-819). ``test_the_water_chip_is_the_phrase_the_model_is_taught``
#: fails the day the chip and those two strings drift apart, which is the only
#: way this one could quietly stop working.
#:
#: And the far end still holds: ``parse_beverage("стакан воды")`` is a
#: confident 250 ml, so once selected the skill logs rather than asks.
CHIP_WATER = {"label": "💧 Записать стакан воды", "callback": "стакан воды"}

#: Claimed by :func:`looks_like_diary_request` below.
CHIP_DIARY = {"label": "📔 Мой дневник", "callback": "что я ел сегодня"}

#: Said instead of an «Исправить» chip. There is NO correction command to put
#: behind such a chip: correction in this system is implicit — a new explicit
#: statement supersedes the old fact through ``record_explicit_green_facts`` /
#: ``supersede_entries``. A chip that opened a dead end would be worse than
#: the sentence that tells the person the move that actually works.
MEMORY_CORRECT_HINT = "Что-то не так — просто напиши, как правильно, и я поправлю."

#: Ayla unreachable. Names what happened without inventing a single number.
DIARY_UNAVAILABLE_TEXT = (
    "Не могу сейчас поднять твой дневник — сервис питания не отвечает. Попробуй через минуту."
)

#: PERSONAL_DATA is not granted. The bot must not read, and must not pretend
#: the record is empty either — «пусто» and «мне нельзя смотреть» are
#: different truths.
CONSENT_CLOSED_TEXT = (
    "Чтобы показать твои записи, мне нужно согласие на обработку личных "
    "данных — без него я к ним не обращаюсь."
)

#: No anketa yet. The diary has nothing to be measured against, so the anketa
#: is not a suggestion here — it is the missing half of the answer.
NO_PROFILE_TEXT = "Норм пока нет — я ещё не считала их для тебя."


# ---------------------------------------------------------------------------
# Deterministic trigger — the diary half.
# ---------------------------------------------------------------------------

# Explicit, not a fuzzy classifier. A false claim here HIJACKS a turn that
# belonged to discovery, which is why ``memory_commands`` keeps its own
# trigger list explicit too. The model tool above is what covers everything
# these lines deliberately do not.
_DIARY_TRIGGERS: tuple[str, ...] = (
    "что я ел",
    "что я ела",
    "что я сегодня ел",
    "что я пил",
    "что я пила",
    "мой дневник",
    "дневник питания",
    "покажи дневник",
    "покажи мой дневник",
    "сколько я съел",
    "сколько я съела",
    "сколько я выпил",
    "сколько я выпила",
    "сколько калорий я",
    "мои калории",
    "что там с водой",
)

#: A week is named, not guessed. Without one of these the period is today —
#: the read we can always make in one call.
_WEEK_MARKERS: tuple[str, ...] = ("недел", "за 7 дней", "за семь дней")


def _normalise(text: str) -> str:
    """Lowercase, collapse whitespace, fold ё→е — same as memory_commands."""
    return re.sub(r"\s+", " ", (text or "").strip().lower().replace("ё", "е"))


def looks_like_diary_request(text: str) -> str | None:
    """The requested period, or ``None`` when this text is not a diary ask.

    ``None`` is the «not mine, carry on» signal the whole global ladder is
    built on (``handle_memory_command``, ``try_handle_structured_nutrition_turn``,
    ``generate_direct_show_masters_reply`` all use it).
    """
    norm = _normalise(text)
    if not norm:
        return None
    if not any(trigger in norm for trigger in _DIARY_TRIGGERS):
        return None
    if any(marker in norm for marker in _WEEK_MARKERS):
        return PERIOD_WEEK
    return PERIOD_TODAY


def diary_is_reachable() -> bool:
    """True where a «что я ел сегодня» tap really lands on the diary.

    :func:`looks_like_diary_request` is claimed by
    ``try_handle_structured_nutrition_turn``, which only the GLOBAL path
    calls; the per-tenant skill ladder has no branch for it and would answer
    a diary chip with a generic fallback. The anketa runs on both surfaces,
    so its closing chips ask here rather than assume.

    The sentinel tenant is the global path's marker: ``_run_skill`` enters
    ``tenant_scope(get_global_bot_tenant())`` for the duration of a
    globally-dispatched skill, so inside one, ``current_tenant()`` IS the
    sentinel and nothing else is.

    Fail-closed: anything unexpected reads as «not reachable», which costs a
    button and never produces a dead one.
    """

    try:
        from apps.identity.services.global_tenant import get_global_bot_tenant
        from apps.tenancy.context import current_tenant

        active = current_tenant()
        if active is None:
            return False
        return getattr(active, "id", None) == getattr(get_global_bot_tenant(), "id", object())
    except Exception:  # noqa: BLE001 — a chip is never worth a raised turn
        logger.exception("orchestrator.personal_surface.reachability_check_failed")
        return False


# ---------------------------------------------------------------------------
# Consent.
# ---------------------------------------------------------------------------


def personal_records_consent_open(bot_user: Any) -> bool:
    """PERSONAL_DATA granted? Fail-closed on any error.

    ``has_global_consent`` (not ``has_consent``): the concierge runs
    tenant-less, and the tenant-scoped sibling raises when unscoped.
    """
    try:
        from apps.consent.models import ConsentRecord
        from apps.consent.services import has_global_consent

        return has_global_consent(bot_user, ConsentRecord.ConsentType.PERSONAL_DATA.value)
    except Exception:  # noqa: BLE001 — fail-closed: no consent proven, no read
        logger.exception("orchestrator.personal_surface.consent_check_failed")
        return False


# ---------------------------------------------------------------------------
# The diary.
# ---------------------------------------------------------------------------


def render_diary(bot_user: Any, *, period: str = PERIOD_TODAY) -> DiscoveryReply:
    """The person's own food/water record, with chips that execute.

    Never raises. Ayla unreachable → :data:`DIARY_UNAVAILABLE_TEXT`; no
    consent → :data:`CONSENT_CLOSED_TEXT`; nothing logged → the honest
    «записей не было» line ``render_daily_report`` already owns.
    """
    if not personal_records_consent_open(bot_user):
        return _reply(CONSENT_CLOSED_TEXT, [])

    profile = _fetch_profile(bot_user)
    if period == PERIOD_WEEK:
        return _render_week(bot_user, profile)
    return _render_today(bot_user, profile)


def _render_today(bot_user: Any, profile: Any) -> DiscoveryReply:
    summary = _fetch(bot_user, "daily_summary")
    if summary is None:
        # The summary is the required half — without it there is no day to
        # show. Water alone would be a partial answer presented as a whole.
        return _reply(DIARY_UNAVAILABLE_TEXT, _diary_chips(profile))
    water = _fetch(bot_user, "get_water_today")

    from apps.nutrition_proactive.render import render_daily_report

    # The SAME renderer the evening push uses (DRF-1285) — and therefore the
    # same boundary and the same «every number traces» test. `include_opt_out`
    # is off because the person PULLED this: offering to stop sending it makes
    # no sense for a message nobody sent.
    text = render_daily_report(summary, water, profile, include_opt_out=False)
    if profile is None:
        text = f"{text}\n\n{NO_PROFILE_TEXT}"
    return _reply(text, _diary_chips(profile))


def _render_week(bot_user: Any, profile: Any) -> DiscoveryReply:
    """Seven days as the aggregate Ayla actually exposes.

    There is no per-day and no per-meal read on the contract route table
    (``apps/integrations/ayla/tests/test_contract_route_table.py``) — five
    GETs exist and none of them lists entries. So «неделя» is
    ``DeficitsResponse``, three lines, each a field of it. Reconstructing a
    week by looping ``daily_summary`` seven times would put seven blocking
    HTTP calls inside one turn to render numbers this aggregate already
    carries.
    """
    deficits = _fetch(bot_user, "weekly_deficits")
    if deficits is None:
        return _reply(DIARY_UNAVAILABLE_TEXT, _diary_chips(profile))

    days = _clamp_int(getattr(deficits, "days_observed", 0))
    if not days:
        return _reply("За неделю записей не было — считать нечего.", _diary_chips(profile))

    lines = ["Питание за неделю.", "", f"Дней с записями: {days}."]
    pct = _as_float(getattr(deficits, "protein_avg_pct_goal", None))
    if pct is not None:
        # % of the goal in the person's OWN profile — Ayla's arithmetic over
        # what they logged against what they chose. Not a claim about them.
        lines.append(f"Белок: в среднем {round(pct)}% от твоей нормы.")
    streak = _clamp_int(getattr(deficits, "protein_low_streak_days", 0))
    if streak:
        lines.append(f"Дней подряд ниже нормы белка: {streak}.")
    # Ayla's free-form ``hint`` is NOT appended — see the module docstring.
    return _reply("\n".join(lines), _diary_chips(profile))


def _diary_chips(profile: Any) -> list[dict[str, str]]:
    """Chips for a diary view. Each callback is claimed deterministically.

    Without a profile the anketa IS the next step — the diary has nothing to
    be measured against until it exists, and today nothing anywhere offers
    it: a person has to guess that ``/anketa`` is a command. With a profile,
    the one-tap water log is the cheapest real thing the person can do next.
    """
    if profile is None:
        return [dict(CHIP_ANKETA)]
    return [dict(CHIP_WATER)]


# ---------------------------------------------------------------------------
# The memory list.
# ---------------------------------------------------------------------------


def render_memory(bot_user: Any) -> DiscoveryReply:
    """What the bot remembers, as a list, with a «Забыть: X» chip per domain.

    Text comes from :func:`apps.persona.memory_commands.render_memory_summary`
    — the SAME sentence the deterministic «покажи что знаешь» command emits,
    so the two entrances cannot drift into two different answers about the
    same person.

    Empty is said honestly. A placeholder here would be a fabricated fact
    about a person, which is the one thing this surface may never produce.
    """
    if not personal_records_consent_open(bot_user):
        return _reply(CONSENT_CLOSED_TEXT, [])
    try:
        from apps.persona.memory_commands import memory_show_chips, render_memory_summary

        text = render_memory_summary(bot_user)
        chips = memory_show_chips(bot_user)
    except Exception:  # noqa: BLE001 — memory must never break the turn
        logger.exception("orchestrator.personal_surface.memory_render_failed")
        return _reply("Не могу сейчас поднять, что о тебе помню.", [])
    if chips:
        text = f"{text}\n\n{MEMORY_CORRECT_HINT}"
    return _reply(text, chips)


# ---------------------------------------------------------------------------
# The tool executor (concierge side).
# ---------------------------------------------------------------------------


def execute_personal_tool(
    name: str, args: dict[str, Any], *, bot_user: Any
) -> DiscoveryReply | None:
    """Run the read behind a model-called ``show_my_records``.

    Deterministic like the nutrition (DRF-1268) and catalog (DRF-1304) tools:
    the reply is rendered here from real data, so no second model pass is
    spent rephrasing the person's own numbers — and no model gets the chance
    to round one.

    ``None`` for an unknown name (caller degrades to the safe line).
    """
    if name not in PERSONAL_TOOL_ACTIONS:
        return None
    if not isinstance(args, dict):
        args = {}

    section = str(args.get("section") or SECTION_ALL).strip().lower()
    if section not in {SECTION_DIARY, SECTION_MEMORY, SECTION_ALL}:
        # A garbled section is not a reason to answer nothing: «all» is the
        # superset, so the person still gets what they asked for plus context.
        section = SECTION_ALL
    period = str(args.get("period") or PERIOD_TODAY).strip().lower()
    if period != PERIOD_WEEK:
        period = PERIOD_TODAY

    logger.info(
        "orchestrator.personal_surface.show_my_records section=%s period=%s", section, period
    )

    if section == SECTION_DIARY:
        return render_diary(bot_user, period=period)
    if section == SECTION_MEMORY:
        return render_memory(bot_user)

    diary = render_diary(bot_user, period=period)
    memory = render_memory(bot_user)
    return _merge(diary, memory)


def _merge(first: DiscoveryReply, second: DiscoveryReply) -> DiscoveryReply:
    """Two sections in one message, chips concatenated and de-duplicated.

    Order is stable (diary chips first) so the same question does not produce
    a differently-ordered keyboard on two consecutive turns.
    """
    buttons: list[dict[str, str]] = []
    seen: set[str] = set()
    for reply in (first, second):
        for button in (reply.action_data or {}).get("buttons") or []:
            callback = button.get("callback", "")
            if callback in seen:
                continue
            seen.add(callback)
            buttons.append(button)
    return _reply(f"{first.text}\n\n{second.text}", buttons)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _reply(text: str, buttons: list[dict[str, str]]) -> DiscoveryReply:
    """Text + chips in the flat ``action_data["buttons"]`` shape.

    Flat rather than the ``attachments`` envelope because this reply travels
    BOTH routes: as a ``DiscoveryReply`` from the concierge and as a
    ``SkillResult`` from the deterministic layer. ``_build_attachments`` in
    the MAX handler reads the flat form on either.

    No buttons → ``action_data=None``: an empty inline keyboard renders as a
    broken widget, not as a message without buttons.
    """
    clipped = text[:_MAX_PERSONAL_REPLY_CHARS]
    if not buttons:
        return DiscoveryReply(text=clipped)
    return DiscoveryReply(text=clipped, action_data={"buttons": buttons})


def _fetch(bot_user: Any, method: str) -> Any | None:
    """One Ayla GET, best-effort. ``None`` on every failure.

    Same degradation ladder as ``nutrition_context._fetch_deficits``: an
    unconfigured environment (the ``NUTRITION_SERVICE_TOKEN`` case DRF-1293
    tracks) is a DEBUG non-event, an outage is INFO, anything else gets a
    traceback — and all three return ``None`` so the turn survives.
    """
    try:
        from apps.integrations.ayla import external_user_id_for, get_nutrition_client

        client = get_nutrition_client()
        external_id = external_user_id_for(bot_user)
    except Exception as exc:  # noqa: BLE001 — unconfigured env is not an error
        logger.debug("orchestrator.personal_surface.disabled: %s", exc)
        return None
    try:
        from apps.integrations.ayla import NutritionAPIError, NutritionUnavailableError

        return asyncio.run(getattr(client, method)(external_user_id=external_id))
    except (NutritionUnavailableError, NutritionAPIError) as exc:
        logger.info("orchestrator.personal_surface.skip method=%s reason=%s", method, exc)
        return None
    except Exception:  # noqa: BLE001 — never break the turn; key already claimed
        logger.exception("orchestrator.personal_surface.fetch_failed method=%s", method)
        return None


def _fetch_profile(bot_user: Any) -> Any | None:
    """The norms, or ``None``.

    ``None`` is ambiguous by construction — «no anketa yet» and «Ayla is
    down» both land here — and that ambiguity is safe: both cases render the
    same way (no targets, no remark), because in both cases we do not know
    the targets. The one thing neither may do is print a default.
    """
    return _fetch(bot_user, "get_profile")


def _clamp_int(raw: Any, *, ceiling: int = 366) -> int:
    try:
        return max(0, min(ceiling, int(raw)))
    except (TypeError, ValueError):
        return 0


def _as_float(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return max(0.0, min(1000.0, value))
