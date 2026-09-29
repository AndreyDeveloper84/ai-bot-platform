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
- Free-text tools run the skills' own parsers rather than teaching the
  model the beverage/food grammars. ``log_water`` is executed on the
  phrase the MODEL passed — its normalisation («и водички дёрнул
  стакан» → «стакан воды») is what the beverage grammar can read.
  ``health_screening`` is executed on the phrase the PERSON typed
  (DRF-1542): it has no grammar to normalise, only a symptom
  classifier, and a paraphrased red flag would decay to soft pain.
  ``clarify_food_entry`` is executed on the PERSON's phrase too
  (DRF-2078): the phrase it remembers is what «📔 В дневник» later
  estimates, and the model's paraphrase drops the one thing the food
  grammar cannot recover — the portion («борщ 250» → «борщ» → 100 g).
  See :func:`execute_nutrition_tool`.

- **A dish with a portion skips the model altogether (DRF-2078)** —
  :func:`_try_handle_food_with_grams` claims «борщ 250» / «гречка
  200 г» deterministically and shows the estimate card straight away:
  one confirmation instead of «это про еду?» → tap → card → confirm.
  Drinks are never claimed there (DRF-819: «кофе 200 мл» stays with
  the model and ``log_water``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from apps.identity.services.global_tenant import get_global_bot_tenant
from apps.orchestrator.ui.keyboards import parse_callback
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
        "или это опечатка. Напитки — только через log_water. "
        # DRF-2287 (живой проход 22.09): вопрос принимали за запись.
        "Вопрос о справочнике блюд или о том, что ты умеешь («а торт в "
        "справочнике есть?»), — не запись: этот инструмент не вызывай, ответь "
        "словами."
        # DRF-2285: про фото здесь НЕ говорим — фото распознаётся только при
        # FOOD_PHOTO_SCAN_ENABLED, и строку об этом даёт флаг-зависимый блок
        # промпта (concierge._nutrition_tools_prompt_block), а не описание.
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

#: DRF-1994 (решение U) / DRF-1295 — инструменты, которые гасит единый
#: выключатель ``NUTRITION_ENABLED``. Это ТРИ из четырёх. ``health_screening``
#: сюда не входит намеренно и не по забывчивости: на ``RED_FLAG`` он отвечает
#: «сначала к врачу» до чтения памятки (§35 п.5 владельца) — это грубая
#: защита второго слоя, а владелец постановил, что «safety coarse guard
#: remains mandatory even in Core Pilot». Гасить её флагом ПИТАНИЯ значило бы
#: снять защитную реплику ради выключения еды. Положительный контроль —
#: ``test_nutrition_single_switch_1994``: скрининг жив при выключенном флаге.
NUTRITION_ONLY_TOOL_NAMES: frozenset[str] = frozenset(
    {"log_water", "clarify_food_entry", "start_nutrition_anketa"}
)


def _nutrition_enabled() -> bool:
    """Тот же читатель, что у меню, анкеты и воды — один флаг, одно место."""
    from apps.skills.menu.marketplace import nutrition_enabled

    return nutrition_enabled()


def _nutrition_unavailable_text() -> str:
    from apps.skills.menu.marketplace import NUTRITION_UNAVAILABLE_TEXT

    return NUTRITION_UNAVAILABLE_TEXT


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
    message_text: str,
) -> SkillResult | None:
    """Run the skill behind a model-called nutrition tool.

    Returns ``None`` for an unknown tool name (the caller falls back to
    the safe generic line, same as an unknown tool today).

    ``message_text`` — реплика ЧЕЛОВЕКА на этом ходу (DRF-1542).
    **Обязателен намеренно, без умолчания.** Пустая строка — законное
    значение (ход без текста, например одно фото), и по ней скрининг
    честно воздерживается. Но умолчание сделало бы ровно это же
    воздержание молчаливой ценой забытого аргумента: новый вызывающий
    выключил бы скрининг симптомов, ничего не заметив, и гарантия
    DRF-358 T04 отвалилась бы без единого падения. Забыть обязательный
    аргумент нельзя — это ``TypeError`` на месте вызова. До
    этого тикета её здесь не было, и докстринг обещал ровно то, чего код
    не делал: «*The user's own phrase is passed through as
    ``message_text``*». Передавался пересказ МОДЕЛИ, а вето
    (``skill.matches``) считалось по нему же — то есть модель проверяла
    себя собой и всегда соглашалась. На живом диалоге владельца 06.09
    классификатор на трёх ходах из пяти честно вернул ``NONE``, а
    скрининг ответил всё равно: вето не может наложить вето на того, кто
    его породил. Здесь код возвращается к своему собственному описанию —
    это не новое поведение.
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

    # DRF-1994 — ворота на уровне инструмента, ДО навыка, и не ``None``.
    # Навыки анкеты/воды/еды гасят себя сами в ``handle``, но между
    # инструментом и ``handle`` стоит ``skill.matches`` с правом вето, а
    # вето здесь возвращает ``None`` — и ``None`` отдаёт ход МОДЕЛИ, которая
    # может заговорить о питании сама (DRF-1295). Заглушка отсюда закрывает
    # и этот путь. ``health_screening`` не в ``NUTRITION_ONLY_TOOL_NAMES`` —
    # см. комментарий у константы.
    if name in NUTRITION_ONLY_TOOL_NAMES and not _nutrition_enabled():
        logger.info(
            "orchestrator.nutrition_global.tool_nutrition_off tool=%s trace=%s", name, trace_id
        )
        return SkillResult(
            reply_text=_nutrition_unavailable_text(),
            meta={"reply_kind": f"{skill_name}_nutrition_off"},
        )

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

    if name == "clarify_food_entry":
        # DRF-2078 — фраза еды тоже берётся у ЧЕЛОВЕКА, и вот почему это
        # не то же, что у `log_water`. Навык еды на этом ходу ничего не
        # разбирает: он ЗАПОМИНАЕТ фразу (`text_entry.remember_source`) и
        # рисует карточку «Это про еду?», а разбирать её будет тап
        # «📔 В дневник» на следующем ходу. Пересказ модели («борщ 250» →
        # «борщ») грамматике не помогает — наполнители парсер снимает
        # сам, — а порцию теряет, и потерянное не восстановить: тап несёт
        # только payload. Диалог владельца: «борщ 250» → «В дневник» →
        # запись на 100 г. До этого тикета докстринг модуля защищал
        # пересказ как «нормализацию, которую грамматика может прочесть»;
        # для еды это было верно про грамматику и ложно про число.
        #
        # Пересказ модели остаётся ТОЛЬКО запасным входом — ход без текста
        # (одно фото с подписью в аргументе инструмента): там фразы
        # человека нет, и терять нечего.
        human_text = str(message_text or "").strip()
        if human_text:
            # Без вето `skill.matches`: его детектор (`looks_like_food_drink`,
            # ≤30 символов) — дешёвая ДО-модельная эвристика «похоже на еду».
            # Здесь модель уже решила, что это еда, и вето сказало бы «нет»
            # ровно длинным фразам («на обед съела борщ 250 и котлету») —
            # тем, где пересказ терял бы больше всего. Что из фразы
            # читается, решит тап: `parse_food_text` не разберёт — спросит
            # «что было» словами, а не подставит 100 г.
            context = _build_context(
                message_text=human_text,
                bot_user=bot_user,
                conversation=conversation,
                trace_id=trace_id,
            )
            return _run_skill(skill, context)

    if name == "health_screening":
        # DRF-1542 — половина Б. Скрининг судится по словам ЧЕЛОВЕКА, а
        # не по пересказу модели, и по ним же исполняется.
        #
        # `log_water` остаётся на пересказе модели, намеренно: он
        # разбирает ГРАММАТИКУ напитка («стакан воды»), и там пересказ —
        # нормализация, которая парсеру помогает: человек говорит «и
        # водички дёрнул стакан», модель отдаёт «стакан воды», парсер
        # матчит второе и не матчит первое. Подставить ему реплику
        # человека значило бы сузить его там, где он работает. У
        # скрининга грамматики нет — есть классификатор симптомов, и он
        # обязан читать симптом из уст человека: перефразированный
        # моделью красный флаг («онемела рука» → «болит рука»)
        # деградировал бы до SOFT.
        human_text = str(message_text or "").strip()
        if not human_text:
            logger.info(
                "orchestrator.nutrition_global.screening_veto_no_user_text trace=%s",
                trace_id,
            )
            return None
        context = _build_context(
            message_text=human_text,
            bot_user=bot_user,
            conversation=conversation,
            trace_id=trace_id,
        )
        if not skill.matches(context):
            # Либо человек симптома на этом ходу не называл («Что ты
            # понимаешь?»), либо те же вопросы уже заданы (памятка,
            # apps.skills.health_screening.memo). И то и другое — повод
            # вернуть ход модели, а не выдать константу в шестой раз.
            logger.info(
                "orchestrator.nutrition_global.screening_veto tool=%s trace=%s",
                name,
                trace_id,
            )
            return None
        return _run_skill(skill, context)

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

#: Семейства ``cb:``, чьи тапы ДЕТЕРМИНИРОВАННО принадлежат навыкам питания.
#:
#: Это список исключений, и он разъезжается ровно одним способом: новая
#: клавиатура — новое семейство — забытый префикс. Так и вышло с
#: ``cb:pc_consent:`` (DRF-2074): экран согласия анкеты (#1664) выписывал
#: кнопки, ``NutritionAnketaSkill.matches`` их ловил, а сюда семейство не
#: попало — и тап «согласен» с экрана, который бот сам нарисовал, уезжал в
#: ветку «Я пока не поняла» (``handler.py``, DRF-1491). Поэтому список
#: держит сторож класса ``test_pc_consent_callbacks_structured_2074``:
#: перепись ``cb:``-констант клавиатур навыков питания с самих модулей, и
#: каждая обязана быть структурной. Новое семейство без строки здесь —
#: красный тест, а не жалоба владельца из MAX.
#: ``cb:plan:`` — DRF-2125: тапы карточки плана («Подтвердить план» /
#: «Не сейчас» / «Записаться») разбираются детерминированно до навыков
#: (:func:`apps.orchestrator.plan_lite_card.try_handle_plan_callback`).
_STRUCTURED_CALLBACK_PREFIXES = ("cb:anketa:", "cb:food:", "cb:pc_consent:", "cb:plan:")


# ---------------------------------------------------------------------------
# DRF-990 — что тап анкеты значит В ИСТОРИИ (а не в текущем ходу)
# ---------------------------------------------------------------------------
#
# Маршрутизация выше — про ТЕКУЩИЙ ход: payload доезжает до навыка нетронутым,
# и так и должно быть (``NutritionAnketaSkill.matches`` разбирает именно
# ``cb:anketa:choice:*`` / ``cb:anketa:edit:*``, а golden-фикстуры
# ``apps/replay/fixtures/golden/nutrition_anketa/`` это воспроизводят).
#
# Историю же диалога читает консьерж на БУДУЩИХ ходах, и там строка
# «cb:anketa:choice:gender:female» с ролью ``user`` выглядит как то, что
# человек написал ему словами. Это и есть DRF-990, и DRF-1268 его не закрыл:
# маршрутизация и персистенс — разные читатели одного события.
#
# Поэтому здесь ровно один вопрос: чем этот тап был КАК РЕПЛИКА. Ответов три,
# и разделение между ними содержательное, а не техническое:
#
#   * тап по варианту (``choice``) — это ОТВЕТ человека о себе. Он остаётся в
#     истории, но своей человеческой формулировкой: «Женский», «Похудеть».
#     Пропустить его мимо истории было бы хуже, чем кажется: текстовые шаги
#     той же анкеты (возраст, рост, вес) человек набирает руками, и они в
#     истории есть всегда — пропуск оставил бы запись, где «30» есть, а пола
#     нет;
#   * ``start`` / ``edit`` — НАВИГАЦИЯ («открой анкету», «вернись к весу»).
#     Человек этим ничего о себе не сказал, в историю не идёт ничего — ровно
#     как ``cb:catalog:*`` (DRF-1304);
#   * не тап вовсе — вызывающий пишет текст как есть.
#
# Разбирается ФОРМА, а не префикс: человек может НАБРАТЬ «cb:anketa: …»
# руками, и подменять ему его собственные слова нельзя (правило C01,
# ``apps/channels/tests/test_first_contact_c01.py``).

#: Строгая форма payload'а анкеты: сегменты из ``[a-z_]``, без пробелов.
#: Покрывает ``cb:anketa:start``, ``cb:anketa:edit:{step}`` и
#: ``cb:anketa:choice:{step}:{value}`` — всё, что выкладывает
#: :func:`apps.orchestrator.ui.keyboards.anketa_choice_keyboard`.
_ANKETA_CALLBACK_RE = re.compile(r"^cb:anketa:[a-z_]+(?::[a-z_]+){0,2}$")


@dataclass(frozen=True)
class AnketaTap:
    """Разбор тапа анкеты глазами ИСТОРИИ диалога.

    ``history_text`` — фраза, которой этот тап является как реплика, или
    ``None``, если репликой он не является вовсе (навигация) и в историю
    не должно попасть ничего.
    """

    history_text: str | None


def resolve_anketa_tap(text: str) -> AnketaTap | None:
    """Разобрать тап анкеты; ``None`` — «это не тап анкеты».

    ``None`` означает «обычное сообщение»: вызывающий не трогает ни текст
    хода, ни персистенс. Это важнее, чем кажется, — функция стоит перед
    записью в историю, и ошибка в сторону «это тап» либо стёрла бы человеку
    его собственную реплику, либо подменила бы её.

    Нераспознанный, но правильной формы payload (снятая кнопка, значение,
    которого больше нет в таблице) — это навигация: в историю не идёт ничего.
    Сырой ``cb:`` в истории — ровно тот дефект, который здесь чинится, а
    выдумать за человека фразу нечем.
    """

    from apps.skills.nutrition_anketa.fsm import choice_keyboard_options

    stripped = (text or "").strip()
    if not _ANKETA_CALLBACK_RE.match(stripped):
        return None

    parsed = parse_callback(stripped)
    if parsed is None:
        return AnketaTap(history_text=None)

    if parsed.get("action") != "choice":
        # start / edit / что угодно ещё — навигация.
        return AnketaTap(history_text=None)

    ref = parsed.get("ref") or ""
    step, _, value = ref.partition(":")

    if step == "diet":
        # DRF-2310. Второй шаг, чья метка НЕ идёт в историю, и по той же
        # причине, что скрининг: «Халяль» и «Кошер» называют веру человека,
        # а это спецкатегория 152-ФЗ наравне со здоровьем. Метка легла бы в
        # ``record_global_message(role="user")`` — в постоянное хранилище,
        # которое на следующих ходах читает промпт консьержа.
        #
        # Сам ответ от этого не теряется: он уезжает в каталог как значение
        # профиля, с согласием и по своему пути. В историю чата копия не
        # нужна, и раздел 9 решения владельца её прямо запрещает.
        return AnketaTap(history_text=None)

    if step == "screening":
        # Единственный шаг анкеты, чья метка НЕ идёт в историю.
        #
        # Ответы скрининга §7.1 — беременность, кормление, расстройство
        # пищевого поведения, заболевание — это спецкатегория 152-ФЗ, и
        # анкета их намеренно не хранит: решила ветку и забыла
        # (``apps.skills.nutrition_anketa.skill``). Подстановка метки здесь
        # свела бы это на нет: «Беременность или кормление» легла бы в
        # ``record_global_message(role="user")`` как собственная реплика
        # человека — то есть в постоянное хранилище, — и историю читает
        # промпт консьержа на следующих ходах. Раздел 9 решения владельца
        # это запрещает прямо: чувствительные поля не попадают в общую
        # память и промпт без отдельного назначения.
        #
        # Поэтому тап считается навигацией: ответ по-прежнему решает ветку,
        # но копии за собой не оставляет.
        return AnketaTap(history_text=None)

    try:
        # Ровно та таблица, из которой построена клавиатура
        # (``skill._render_step`` -> ``anketa_choice_keyboard``): человек
        # нажал одну из ЭТИХ меток, и в историю идёт она же. Не копия —
        # иначе переименованный вариант разъехался бы с тем, что нажали.
        # KeyError — шаг без клавиатуры (возраст/рост/вес) или шаг из
        # будущего: подставлять нечего.
        options = choice_keyboard_options(step)
    except KeyError:
        return AnketaTap(history_text=None)
    return AnketaTap(history_text=next((lbl for lbl, slug in options if slug == value), None))


# ---------------------------------------------------------------------------
# DRF-990, продолжение — то же самое для ``cb:food:*``
# ---------------------------------------------------------------------------
#
# Буквальный близнец анкеты: тот же вход (:data:`_STRUCTURED_CALLBACK_PREFIXES`),
# та же маршрутизация ПО payload'у, та же дыра в персистенсе. И тот же ответ —
# фраза, — по той же структурной причине:
#
#   * еду человек называет ТЕКСТОМ («борщ 300г») или присылает фото, а после
#     «✏️ Уточнить» ДОНАБИРАЕТ поправку словами. Эти ходы в историю попадают
#     всегда. Пропуск тапов оставил бы запись, где «борщ 300г» есть, а
#     подтвердил его человек или отверг — неизвестно, и модель на следующем
#     ходу достроит это сама;
#   * «✅ В дневник» и «❌ Не то» — это высказывания человека о том, что он
#     ел: подтверждение и поправка. Именно они делают запись дневника его
#     записью.
#
# Метка берётся из тех же строителей клавиатур, что её и выложили. Совпадение
# проверяется ДОСЛОВНО по всему payload'у: таблица строится с тем же
# ``scan_id``, что пришёл, и payload обязан совпасть с одной из построенных
# строк целиком. Поэтому «формы» угадывать не нужно — payload признаётся
# тапом тогда и только тогда, когда клавиатура могла его выложить.
# ``cb:food:to_diary`` без ``scan_id`` или ``cb:food:correct:nope:…`` под это
# не подходят: подставлять нечего, в историю не идёт ничего.

#: Строгая форма payload'а еды: сегменты без пробелов, ``scan_id`` — id Ayla.
#: Отсекает набранное руками «cb:food: …» ДО того, как оно будет принято за
#: тап и стёрто из истории (правило C01).
_FOOD_CALLBACK_RE = re.compile(r"^cb:food:[a-z_]+(?::[A-Za-z0-9_-]+){0,2}$")


def food_tap_labels(scan_id: str) -> dict[str, str]:
    """``{payload: метка}`` для клавиатур еды, построенных с этим ``scan_id``.

    Не копия таблицы, а вызов самих строителей
    (:mod:`apps.orchestrator.ui.keyboards`) — переименованная кнопка едет в
    историю уже новым именем, без правки здесь.
    """
    from apps.orchestrator.ui.keyboards import (
        correction_choice_keyboard,
        food_drink_clarify_keyboard,
        food_recognition_keyboard,
        food_text_deleted_keyboard,
        food_text_estimate_keyboard,
        food_text_logged_keyboard,
    )

    return {
        button["callback"]: button["label"]
        for button in (
            *food_drink_clarify_keyboard(),
            *food_recognition_keyboard(scan_id),
            *correction_choice_keyboard(scan_id),
            *food_text_estimate_keyboard(),
            *food_text_logged_keyboard(scan_id),
            *food_text_deleted_keyboard(scan_id),
        )
    }


#: OD-WATER-TAP-HISTORY (H2, ``docs/OWNER_QUESTIONS_2026-09-12.md``) — как тап
#: правки/отмены СВОЕГО действия ложится в историю диалога. Владелец не ответил;
#: решение 15.09 распространено и на чипы записи дневника (DRF-1838). ЕДИНСТВЕННАЯ
#: точка выбора:
#:
#: * ``"silence"`` — вариант (б), рекомендован окном питания и главным окном:
#:   в историю ничего, ход виден по ответу бота (как «Не присылать», DRF-1468);
#: * ``"phrase"``  — вариант (а): подпись кнопки ложится репликой человека.
#:
#: Любое другое значение читается как молчание. Сырой payload — никогда: тап
#: распознаётся всегда, иначе обработчик канала записал бы ``cb:…`` (DRF-988).
EDIT_TAP_HISTORY = "silence"


def _food_entry_tap(text: str) -> bool:
    """Тап под сохранённой записью — по тому же шаблону, что маршрут скилла."""
    from apps.orchestrator.ui.keyboards import ENTRY_CALLBACK_RE

    return bool(ENTRY_CALLBACK_RE.match(text))


def edit_tap_history_text(label: str | None) -> str | None:
    """Текст истории для тапа правки своей записи — по :data:`EDIT_TAP_HISTORY`."""
    if EDIT_TAP_HISTORY == "phrase" and label:
        return label
    return None


def resolve_food_tap(text: str) -> AnketaTap | None:
    """Разобрать тап еды; ``None`` — «это не тап еды».

    Возвращает тот же :class:`AnketaTap`, что и резолвер анкеты: вопрос у
    них один — «чем этот тап был как реплика», — и заводить второй тип с тем
    же единственным полем значило бы притвориться, что вопросы разные.
    """
    stripped = (text or "").strip()
    if not _FOOD_CALLBACK_RE.match(stripped):
        return None
    # Последний сегмент — это ``scan_id`` у тех кнопок, что его несут, и часть
    # имени действия у тех, что нет (``cb:food:diary``). Оба случая
    # разрешаются одинаково: строим таблицу с ним и ищем ТОЧНОЕ совпадение.
    scan_id = stripped.rsplit(":", 1)[-1]
    if _food_entry_tap(stripped):
        # DRF-1838 / H2 — правка своей записи: фраза или молчание, одной точкой.
        return AnketaTap(history_text=edit_tap_history_text(food_tap_labels(scan_id).get(stripped)))
    return AnketaTap(history_text=food_tap_labels(scan_id).get(stripped))


# ---------------------------------------------------------------------------
# DRF-1468 — тап «Не присылать» (``cb:nutri:stop:*``) глазами ИСТОРИИ
# ---------------------------------------------------------------------------
#
# Кнопка отписки на каждом proactive-исходящем. Это высказывание кнопкой,
# но фразы за ней нет: метка одна на все поверхности («Не присылать»), а
# смысл тапа целиком в payload'е. Подставлять метку в историю значило бы
# записать за человека слова, которых он не говорил (тап ≠ «написал
# „Не присылать"»), а сырой ``cb:`` в истории — ровно дефект DRF-988.
# Поэтому в историю не идёт НИЧЕГО: ход остаётся виден по ответу-
# подтверждению бота, как у навигационных тапов анкеты и ``cb:catalog:*``.
#
# Форма строгая, по тому же правилу C01: «cb:nutri:stop:вода», набранное
# руками, тапом не является и истории не касается.

#: Строгая форма payload'а кнопки отписки: латиница/подчёркивания, без
#: пробелов. Покрывает и поверхности из будущего — неизвестная поверхность
#: это вопрос ОТВЕТА (stale-подтверждение), а не персистенса.
_NUTRI_STOP_CALLBACK_RE = re.compile(r"^cb:nutri:stop:[a-z_]+$")


def resolve_plan_tap(text: str) -> AnketaTap | None:
    """Разобрать тап карточки плана (``cb:plan:*``, DRF-2125); ``None`` — не наш.

    ФРАЗА — «Подтвердить план» / «Не сейчас» / «Записаться» — это
    высказывания человека о своём плане, как «✅ В дневник» у еды: человек
    сам спросил «мой план» текстом, и молчание оставило бы в истории вопрос
    без его ответа. Версия шаблона из payload'а в фразу не попадает —
    человек её не видел. Форма строгая (``PLAN_CALLBACK_RE``): набранное
    руками «cb:plan: …» тапом не является и истории не касается.
    """
    from apps.orchestrator.plan_lite_card import is_plan_callback, tap_history_text

    stripped = (text or "").strip()
    if not is_plan_callback(stripped):
        return None
    return AnketaTap(history_text=tap_history_text(stripped))


def resolve_nutri_stop_tap(text: str) -> AnketaTap | None:
    """Разобрать тап «Не присылать»; ``None`` — «это не тап отписки».

    Возвращает тот же :class:`AnketaTap`: вопрос один — «чем этот тап был
    как реплика», — и ответ здесь всегда «ничем»: ``history_text=None``.
    """

    stripped = (text or "").strip()
    if not _NUTRI_STOP_CALLBACK_RE.match(stripped):
        return None
    return AnketaTap(history_text=None)


def _anketa_fsm_active(conversation: Any) -> bool:
    state = getattr(conversation, "skill_state", None)
    return bool(isinstance(state, dict) and state.get("nutrition_anketa"))


def _manual_target_pending(conversation: Any) -> bool:
    """Ждёт ли бот число / подтверждение ручного ориентира (DRF-2138)?

    Та же форма, что у :func:`_food_correction_pending`, и та же причина:
    вопрос, который бот задал сам, владеет ответом. Свежесть решает навык.
    """

    try:
        from apps.skills.nutrition_anketa.skill import manual_target_pending

        return manual_target_pending(conversation)
    except Exception:  # noqa: BLE001 — a predicate must never break the turn
        logger.exception("orchestrator.nutrition_global.manual_target_pending_check_failed")
        return False


def _update_weight_pending(conversation: Any) -> bool:
    """Ждёт ли бот вес — «обнови вес» (DRF-2139)? Та же форма, что выше."""

    try:
        from apps.skills.nutrition_anketa.skill import update_weight_pending

        return update_weight_pending(conversation)
    except Exception:  # noqa: BLE001 — a predicate must never break the turn
        logger.exception("orchestrator.nutrition_global.update_weight_pending_check_failed")
        return False


def _update_weight_phrase(text: str) -> bool:
    """Детерминированный вход «мой вес 65» / «обнови вес» (DRF-2139)."""

    try:
        from apps.skills.nutrition_anketa.skill import update_weight_phrase

        return update_weight_phrase(text)
    except Exception:  # noqa: BLE001 — a predicate must never break the turn
        logger.exception("orchestrator.nutrition_global.update_weight_phrase_check_failed")
        return False


def _manual_target_phrase(text: str) -> bool:
    """Детерминированный вход «мне врач назначил 1800 ккал» (DRF-2138)."""

    try:
        from apps.skills.nutrition_anketa.skill import manual_target_phrase

        return manual_target_phrase(text)
    except Exception:  # noqa: BLE001 — a predicate must never break the turn
        logger.exception("orchestrator.nutrition_global.manual_target_phrase_check_failed")
        return False


def _food_correction_pending(conversation: Any) -> bool:
    """Is a fresh «✏️ Уточнить» prompt still waiting for its answer? (DRF-1454)

    Same shape as :func:`_anketa_fsm_active` and for the same reason: a question
    the bot asked on the previous turn owns the answer that follows it. Without
    this the correction the person types falls through to the concierge and is
    forgotten — which was the whole reason the scanner kept re-asking.

    Delegated to the skill so freshness is decided in one place: the skill
    expires an unanswered prompt, and a stale record must not keep plain text
    away from the concierge and the diary-request handler for good.
    """

    try:
        from apps.skills.food_correction.skill import has_pending_correction

        return has_pending_correction(conversation)
    except Exception:  # noqa: BLE001 — a predicate must never break the turn
        logger.exception("orchestrator.nutrition_global.correction_pending_check_failed")
        return False


def _food_text_pending(conversation: Any) -> bool:
    """Ждёт ли текстовый ввод еды ответа — граммов или «что было»? (DRF-1837)

    Тот же вопрос, что у анкеты и поправки скана: бот спросил — ответ его.
    Свежесть решает сам навык; ошибка чтения — «не ждёт», ход уходит дальше.
    """

    try:
        from apps.skills.food_clarify.text_entry import has_pending_text_entry

        return has_pending_text_entry(conversation)
    except Exception:  # noqa: BLE001 — a routing hint must never break the turn
        logger.exception("orchestrator.nutrition_global.food_text_pending_check_failed")
        return False


def is_structured_nutrition_turn(
    *,
    text: str,
    has_attachments: bool,
    conversation: Any,
) -> bool:
    """Cheap predicate: is this turn owned by a nutrition skill deterministically?

    Free text is NEVER structured — it belongs to the concierge model
    with the nutrition tools above — with one exception per open question the
    bot itself asked: an in-flight anketa step, a pending food correction, a
    pending manual target (DRF-2138) — and one deterministic phrase, «мне врач
    назначил 1800 ккал», which is a number the person carries, not a question
    for the model.
    """

    stripped = text.strip()
    if has_attachments and not stripped:
        return True  # photo-only turn → food scanner
    if stripped == "/anketa" or stripped.startswith(_STRUCTURED_CALLBACK_PREFIXES):
        return True
    return (
        _anketa_fsm_active(conversation)
        or _food_correction_pending(conversation)
        or _food_text_pending(conversation)
        or _manual_target_pending(conversation)
        or _manual_target_phrase(stripped)
        or _update_weight_pending(conversation)
        or _update_weight_phrase(stripped)
    )


def _has_image_attachment(attachments: list[dict[str, Any]] | None) -> bool:
    """Есть ли среди вложений ``image`` — единственный тип, который сканер еды читает."""
    return any(isinstance(a, dict) and a.get("type") == "image" for a in attachments or [])


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

    # DRF-1942 — «фото без текста → сканер еды» решает ТИП вложения, а не
    # сам факт вложения: голосовое (``audio``) сюда не относится, ему
    # отвечает handler. ``video``/``file`` и прочее — как раньше не были
    # фото, так и остаются: сканер их всё равно не прочитал бы.
    has_attachments = _has_image_attachment(attachments)
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
        diary = _try_handle_diary_request(
            text=text, has_attachments=has_attachments, bot_user=bot_user, trace_id=trace_id
        )
        if diary is not None:
            return diary
        # DRF-2101 — «мой план»: карточка Plan Lite из wellness-context, тем же
        # приёмом, что чтение дневника; под флагом, иначе текст — модели.
        if not has_attachments:
            from apps.orchestrator.plan_lite_card import try_handle_my_plan

            try:
                plan = try_handle_my_plan(
                    text=text, bot_user=bot_user, trace_id=trace_id, conversation=conversation
                )
            except Exception:  # noqa: BLE001 — план не должен ломать глобальный ход
                logger.exception(
                    "orchestrator.nutrition_global.plan_lite_failed trace=%s", trace_id
                )
                plan = None
            if plan is not None:
                return plan
        # DRF-2078 — «борщ 250»: блюдо с порцией не нуждается в модели.
        return _try_handle_food_with_grams(
            text=text,
            has_attachments=has_attachments,
            bot_user=bot_user,
            conversation=conversation,
            trace_id=trace_id,
        )

    if text.strip().startswith("cb:plan:"):
        # DRF-2125 — тапы карточки плана: не навык, а детерминированный
        # разбор рядом с самой карточкой. Выключенный флаг — честный ответ
        # там же («кнопка не действует»); неверная форма — ``None``, и ход
        # идёт дальше, как у остальных семейств (fallback канала).
        from apps.orchestrator.plan_lite_card import try_handle_plan_callback

        try:
            return try_handle_plan_callback(
                text=text, bot_user=bot_user, trace_id=trace_id, conversation=conversation
            )
        except Exception:  # noqa: BLE001 — план не должен ломать глобальный ход
            logger.exception(
                "orchestrator.nutrition_global.plan_callback_failed trace=%s", trace_id
            )
            return None

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
        # The predicate said «structured» and no skill claimed it after all.
        # Before DRF-1454 that combination was impossible for plain text (an
        # in-flight anketa claims ANY text), and returning None was right. A
        # pending food correction is different: it claims only text shaped like
        # its answer, so «что я ел сегодня» typed while a correction is open is
        # structured-but-unclaimed — and used to skip the deterministic diary
        # handler entirely for the ten minutes the prompt stayed open. A chip
        # that leads to nothing is worse than no chip (DRF-1302), so the turn
        # continues down the same ladder the non-structured branch uses.
        return _try_handle_diary_request(
            text=text, has_attachments=has_attachments, bot_user=bot_user, trace_id=trace_id
        )

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


#: DRF-2078 — верхняя граница фразы для ярлыка «блюдо + порция». Длиннее —
#: это рассказ, а не запись, и он идёт модели, как и раньше. Та же
#: величина, что у детектора ``looks_like_food_drink`` (``hints._MAX_LEN``),
#: не импорт: два детектора с одним числом — совпадение, а не связь.
_FOOD_WITH_GRAMS_MAX_LEN = 30


def _try_handle_food_with_grams(
    *,
    text: str,
    has_attachments: bool,
    bot_user: Any,
    conversation: Any,
    trace_id: str,
) -> SkillResult | None:
    """«борщ 250» → карточка оценки сразу, детерминированно. ``None`` — не наше.

    Диалог владельца (DRF-2078): «борщ 250» → модель → «Это про еду?» → «В
    дневник» → оценка → подтверждение. Два подтверждения, и на первом же
    шаге пересказ модели терял порцию. Блюдо с НАЗВАННОЙ порцией не
    нуждается ни в вопросе «это еда?», ни в модели: человек уже сказал и
    что, и сколько. Ярлык ведёт прямо к шагу 3 §109 — «Я распознала так» с
    одним подтверждением (``cb:food:text_log``), запись только после него.

    Границы, каждая — намеренно:

    * только с порцией: «борщ» без числа идёт модели и получает карточку
      «Это про еду?» как раньше — ярлык не отменяет защиту от опечаток
      (DRF-358), он обходит её там, где число делает опечатку невероятной;
    * напитки не берутся: «кофе 200 мл» — ``log_water`` через модель
      (DRF-819), и грамматика напитков (``parse_beverage``) решает это ДО
      нас; иначе ярлык завёл бы кофе в дневник еды;
    * не в :func:`is_structured_nutrition_turn`: свободный текст остаётся
      неструктурным для внешнего предиката (сторож «free text never
      claimed»), ярлык живёт рядом с чтением дневника (DRF-1302) — тот же
      двухслойный приём;
    * контур питания выключен — ``None``: ход уходит модели, где заглушку
      даёт ``execute_nutrition_tool``; ярлык при выключенном флаге не
      меняет ни строки поведения;
    * с фото — не наше: фото без подписи читает сканер, фото с подписью
      уходит модели (:meth:`FoodScannerSkill.matches` требует пустой текст;
      подпись до распознавателя не доходит — DRF-2110, решение: не
      передавать, маршрутизация фото+подпись — отдельный лист).

    Никогда не бросает: отказ дневника не должен ломать глобальный ход —
    как у чтения дневника, ход продолжается к модели.
    """

    if has_attachments:
        return None
    stripped = text.strip()
    if not stripped or len(stripped) > _FOOD_WITH_GRAMS_MAX_LEN:
        return None
    if not _nutrition_enabled():
        return None
    try:
        from apps.skills.food_clarify import text_entry
        from apps.skills.water.parser import BeverageMatch, parse_beverage

        parsed = text_entry.parse_food_text(stripped)
        if parsed is None or parsed.grams is None:
            return None
        if isinstance(parse_beverage(stripped), BeverageMatch):
            return None
        context = _build_context(
            message_text=stripped,
            bot_user=bot_user,
            conversation=conversation,
            trace_id=trace_id,
        )
        with tenant_scope(get_global_bot_tenant()):
            result = text_entry.show_estimate(context, parsed.dish, parsed.grams, corrected=False)
    except Exception:  # noqa: BLE001 — nutrition must never break the global turn
        logger.exception("orchestrator.nutrition_global.food_with_grams_failed trace=%s", trace_id)
        return None
    logger.info(
        "orchestrator.nutrition_global.food_with_grams_shortcut kind=%s trace=%s",
        (result.meta or {}).get("reply_kind"),
        trace_id,
    )
    return result
