"""Nutrition skills on the global (tenant-less) path — OD-8 / DRF-1268.

The six nutrition skills (``nutrition_anketa``, ``food_scanner``,
``water``, ``food_clarify``, ``health_screening``, ``food_correction``)
are the only pilot features with real user data — and they were
unreachable from the client bot: the skill registry only dispatches on
the per-tenant surface, the global path never calls it (turn_seam maps
``surface="global"`` to the concierge only).

The transfer is deliberately HYBRID, mirroring the canon's own
memory-commands precedent (Intent Model § Does not own: conversational
formulations route to the owning capability WITHOUT becoming a new
intent type):

- **Structured turns stay deterministic** — ``/anketa``, ``cb:anketa:*``,
  ``cb:food:*``, an active anketa FSM claiming its answer, and
  photo-only turns (food scanner). A button tap or an FSM step is not
  prose for the model to interpret; routing it through the LLM would
  add failure modes without adding understanding.
  :func:`try_handle_structured_nutrition_turn` runs the skill classes
  UNCHANGED (their ``matches()`` decides), in the registry order that
  ``apps/skills/apps.py`` documents as load-bearing (food_scanner and
  food_correction before nutrition_anketa — the anketa FSM claims any
  text while active, so the ``cb:food:*`` family must win first).

- **The diary READ is deterministic too (DRF-1302)** —
  :func:`_try_handle_diary_request` claims «что я ел сегодня» / «мой
  дневник» here rather than leaving them to the model. Not because the
  model could not classify them, but because the chips this feature ships
  carry plain text as their callback (tap == typed message on this path):
  a chip only executes if a matcher on THIS side owns the string. The
  model tool ``show_my_records``
  (:mod:`apps.orchestrator.personal_surface`) still covers every phrasing
  the trigger list deliberately does not.

- **Free text goes to the model as tools** — :data:`NUTRITION_TOOL_SPECS`
  registers four concierge tools (``health_screening``, ``log_water``,
  ``clarify_food_entry``, ``start_nutrition_anketa``). The reasons
  behind the registry order become prompt/description requirements, as
  the brief demands: symptoms route to ``health_screening`` BEFORE any
  other tool (DRF-358 T04), a drink mention routes to ``log_water``
  and never to ``clarify_food_entry`` (DRF-819 — «стакан воды» must
  not become a diary-or-typo card). The accepted risk (owner decision,
  brief §8) is that the model may not call the tool where ``matches()``
  would have fired.

Execution details:

- Skills are executed inside ``tenant_scope(get_global_bot_tenant())``.
  The global Conversation is parked under the sentinel tenant, and
  ``write_skill_state`` (the anketa FSM's persistence) requires an
  active tenant scope — the sentinel scope satisfies it without
  touching the shared helper. The sentinel owns no commercial data, so
  the fail-closed commercial-read invariant is unaffected.
- The tools only SELECT the skill; side effects run in the concierge
  wrapper's sync scope after ``asyncio.run`` returns — the same shape
  as ``show_masters`` (ai-core dispatchers stay I/O-free).
- Free-text tools pass the user's own phrase through as
  ``message_text`` — the skills' parsers stay the single source of
  truth instead of teaching the model the beverage/food grammars.
"""

from __future__ import annotations

import logging
from typing import Any

from apps.identity.services.global_tenant import get_global_bot_tenant
from apps.skills.base import SkillContext, SkillResult
from apps.tenancy.context import tenant_scope

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model-callable tools (flat spec format — same shape as
# apps.orchestrator.discovery.SHOW_MASTERS_TOOL_SPEC; the LLM providers
# wrap it into each vendor's wire format themselves).
# ---------------------------------------------------------------------------

HEALTH_SCREENING_TOOL_SPEC: dict[str, Any] = {
    "name": "health_screening",
    "description": (
        "Пользователь сообщает о боли, симптомах или самочувствии "
        "(«болит спина», «ноет шея», «онемела рука»). Вызывай ПЕРВЫМ, "
        "до любых других инструментов и до show_masters: красные флаги "
        "уходят к врачу, обычная боль получает диагностические вопросы "
        "(DRF-358 T04 — холодное «вот наши услуги» на жалобу запрещено)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "symptom_text": {
                "type": "string",
                "description": "Дословная фраза пользователя о симптомах.",
            },
        },
        "required": ["symptom_text"],
    },
}

LOG_WATER_TOOL_SPEC: dict[str, Any] = {
    "name": "log_water",
    "description": (
        "Пользователь сообщает, что выпил напиток («стакан воды», "
        "«кофе 200 мл», «чай»). Записывает напиток в дневник. "
        "Для напитков вызывай ТОЛЬКО этот инструмент, никогда не "
        "clarify_food_entry (DRF-819: «стакан воды» — это лог, а не "
        "карточка «дневник или опечатка»)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "drink_text": {
                "type": "string",
                "description": "Дословная фраза пользователя о напитке.",
            },
        },
        "required": ["drink_text"],
    },
}

CLARIFY_FOOD_ENTRY_TOOL_SPEC: dict[str, Any] = {
    "name": "clarify_food_entry",
    "description": (
        "Пользователь написал что-то похожее на еду («борщ 300г») — "
        "не напиток. Показывает карточку уточнения: записать в дневник "
        "или это опечатка. Напитки — только через log_water."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "food_text": {
                "type": "string",
                "description": "Дословная фраза пользователя про еду.",
            },
        },
        "required": ["food_text"],
    },
}

START_NUTRITION_ANKETA_TOOL_SPEC: dict[str, Any] = {
    "name": "start_nutrition_anketa",
    "description": (
        "Пользователь хочет заполнить или продолжить анкету питания "
        "(цели, нормы калорий, «пройти анкету»). Запускает пошаговую "
        "анкету из 5 вопросов."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

NUTRITION_TOOL_SPECS: list[dict[str, Any]] = [
    # Declaration order mirrors the load-bearing registry order: the
    # screening tool is listed first so it is the first tool the model
    # reads — the DRF-358 T04 priority survives as prompt structure.
    HEALTH_SCREENING_TOOL_SPEC,
    LOG_WATER_TOOL_SPEC,
    CLARIFY_FOOD_ENTRY_TOOL_SPEC,
    START_NUTRITION_ANKETA_TOOL_SPEC,
]

#: action_type values the concierge wrapper must execute after the LLM pass.
NUTRITION_TOOL_ACTIONS = frozenset(spec["name"] for spec in NUTRITION_TOOL_SPECS)


# ---------------------------------------------------------------------------
# Skill execution (shared by both layers).
# ---------------------------------------------------------------------------


def _build_context(
    *,
    message_text: str,
    bot_user: Any,
    conversation: Any,
    trace_id: str,
    has_attachments: bool = False,
) -> SkillContext:
    return SkillContext(
        conversation=conversation,
        bot_user=bot_user,
        message_text=message_text,
        trace_id=trace_id,
        has_attachments=has_attachments,
    )


def _skill_by_name(name: str) -> Any | None:
    """Look up the registered skill INSTANCE by its ``name``.

    ``apps.skills.<x>.skill`` is a module, not an instance — skill
    instances live in the registry (``@register`` instantiates the
    class at import). The registry is populated by
    ``SkillsConfig.ready()`` at Django boot.
    """

    from apps.skills.registry import registered

    for skill in registered():
        if getattr(skill, "name", None) == name:
            return skill
    return None


def _run_skill(skill: Any, context: SkillContext) -> SkillResult:
    """Execute a skill against the sentinel-scoped global conversation.

    ``tenant_scope(sentinel)`` is entered for the duration of the call so
    tenant-requiring helpers (``write_skill_state`` — the anketa FSM's
    persistence) accept the global Conversation row, which is parked
    under the same sentinel tenant. The sentinel holds no commercial
    data, so the fail-closed tenant-read invariant keeps holding.
    """

    with tenant_scope(get_global_bot_tenant()):
        return skill.handle(context)


def execute_nutrition_tool(
    name: str,
    args: dict[str, Any],
    *,
    bot_user: Any,
    conversation: Any,
    trace_id: str,
) -> SkillResult | None:
    """Run the skill behind a model-called nutrition tool.

    Returns ``None`` for an unknown tool name (the caller falls back to
    the safe generic line, same as an unknown tool today). The user's
    own phrase is passed through as ``message_text`` — the skills'
    parsers remain the single source of truth.
    """

    skill_name_by_tool = {
        "health_screening": "health_screening",
        "log_water": "water",
        "clarify_food_entry": "food_clarify",
        "start_nutrition_anketa": "nutrition_anketa",
    }
    skill_name = skill_name_by_tool.get(name)
    if skill_name is None:
        return None
    skill = _skill_by_name(skill_name)
    if skill is None:
        logger.warning(
            "orchestrator.nutrition_global.skill_not_registered skill=%s trace=%s",
            skill_name,
            trace_id,
        )
        return None

    if name == "start_nutrition_anketa":
        # The skill's canonical entry trigger — the FSM start is identical
        # for a typed command and a model-selected tool call.
        text = "/anketa"
    else:
        arg_key = {
            "health_screening": "symptom_text",
            "log_water": "drink_text",
            "clarify_food_entry": "food_text",
        }[name]
        text = str(args.get(arg_key) or "").strip()

    if not text:
        return None
    context = _build_context(
        message_text=text,
        bot_user=bot_user,
        conversation=conversation,
        trace_id=trace_id,
    )
    if not skill.matches(context):
        # The model selected a tool whose parser rejects the phrase it
        # passed (e.g. log_water with an unparseable drink). Better no
        # action than a wrong one — the caller degrades to the generic
        # concierge reply path.
        logger.info(
            "orchestrator.nutrition_global.tool_parser_refused tool=%s trace=%s",
            name,
            trace_id,
        )
        return None
    return _run_skill(skill, context)


# ---------------------------------------------------------------------------
# Deterministic layer: structured turns (callbacks, /anketa, FSM answers,
# photo-only turns).
# ---------------------------------------------------------------------------

_STRUCTURED_CALLBACK_PREFIXES = ("cb:anketa:", "cb:food:")


def _anketa_fsm_active(conversation: Any) -> bool:
    state = getattr(conversation, "skill_state", None)
    return bool(isinstance(state, dict) and state.get("nutrition_anketa"))


def is_structured_nutrition_turn(
    *,
    text: str,
    has_attachments: bool,
    conversation: Any,
) -> bool:
    """Cheap predicate: is this turn owned by a nutrition skill deterministically?

    Free text is NEVER structured — it belongs to the concierge model
    with the nutrition tools above.
    """

    stripped = text.strip()
    if has_attachments and not stripped:
        return True  # photo-only turn → food scanner
    if stripped == "/anketa" or stripped.startswith(_STRUCTURED_CALLBACK_PREFIXES):
        return True
    return _anketa_fsm_active(conversation)


def try_handle_structured_nutrition_turn(
    *,
    text: str,
    attachments: list[dict[str, Any]] | None,
    bot_user: Any,
    conversation: Any,
    trace_id: str,
) -> SkillResult | None:
    """Dispatch a structured turn to the owning nutrition skill, unchanged.

    Returns the skill's :class:`SkillResult`, or ``None`` when no skill
    claims the turn (caller continues its normal ladder). Never raises:
    a nutrition failure must not break the global turn — the caller
    degrades to the concierge.
    """

    has_attachments = bool(attachments)
    if not is_structured_nutrition_turn(
        text=text, has_attachments=has_attachments, conversation=conversation
    ):
        # DRF-1302 — the diary READ. Claimed here, not by the model, for one
        # reason: a chip must lead to something that runs. «📔 Мой дневник»
        # and «📋 Пройти анкету» carry plain text as their callback (tap ==
        # typed message on this path), so the tap only executes if a
        # deterministic matcher owns that text. The model tool
        # (``show_my_records``) still covers every phrasing this list does
        # not -- same two-layer shape memory commands already use.
        #
        # Placed AFTER the structured check so an active anketa FSM keeps
        # first claim on the turn: mid-anketa, «что я ел» is an answer to the
        # question on screen before it is a request for the diary.
        return _try_handle_diary_request(
            text=text, has_attachments=has_attachments, bot_user=bot_user, trace_id=trace_id
        )

    context = _build_context(
        message_text=text,
        bot_user=bot_user,
        conversation=conversation,
        trace_id=trace_id,
        has_attachments=has_attachments,
    )

    # Registry order (apps/skills/apps.py) is load-bearing: the cb:food:*
    # family wins over an active anketa FSM, which claims any text while
    # running. food_clarify owns cb:food:{diary,typo} (no ref) and comes
    # last so it can't swallow the scanner's cb:food:to_diary:*.
    candidate_names = ("food_scanner", "food_correction", "nutrition_anketa", "food_clarify")
    candidates = [s for s in (_skill_by_name(n) for n in candidate_names) if s is not None]
    skill = next((s for s in candidates if s.matches(context)), None)
    if skill is None:
        return None

    if has_attachments and not text.strip() and getattr(skill, "name", None) == "food_scanner":
        # Photo turn: the scanner reads the bytes from a runtime attribute
        # the channel layer is expected to set (per-tenant precedent).
        try:
            from apps.channels.max.photo import download_photo, extract_first_photo_url

            photo_url = extract_first_photo_url(attachments)
            if photo_url is None:
                return None
            conversation.last_photo_bytes = download_photo(photo_url)
        except Exception:  # noqa: BLE001 — photo fetch failure degrades to concierge
            logger.exception(
                "orchestrator.nutrition_global.photo_download_failed trace=%s", trace_id
            )
            return None

    try:
        return _run_skill(skill, context)
    except Exception:  # noqa: BLE001 — nutrition must never break the global turn
        logger.exception(
            "orchestrator.nutrition_global.skill_failed skill=%s trace=%s",
            getattr(skill, "name", "?"),
            trace_id,
        )
        return None


def _try_handle_diary_request(
    *, text: str, has_attachments: bool, bot_user: Any, trace_id: str
) -> SkillResult | None:
    """«что я ел сегодня» → the diary, deterministically. ``None`` otherwise.

    A photo turn is never a diary READ even when the caption says so: the
    scanner owns the bytes, and answering «вот твой день» while dropping the
    photo the person just sent is the worse of the two mistakes.

    Never raises -- the caller's ladder continues to the concierge on any
    failure, exactly as it does for a skill that blows up.
    """

    if has_attachments:
        return None
    try:
        from apps.orchestrator.personal_surface import looks_like_diary_request, render_diary

        period = looks_like_diary_request(text)
        if period is None:
            return None
        reply = render_diary(bot_user, period=period)
    except Exception:  # noqa: BLE001 — the diary must never break the global turn
        logger.exception("orchestrator.nutrition_global.diary_failed trace=%s", trace_id)
        return None
    logger.info("orchestrator.nutrition_global.diary_shown period=%s trace=%s", period, trace_id)
    return SkillResult(
        reply_text=reply.text,
        action_type="nutrition_diary_shown",
        action_data=reply.action_data,
        meta={"reply_kind": "nutrition_diary"},
    )
