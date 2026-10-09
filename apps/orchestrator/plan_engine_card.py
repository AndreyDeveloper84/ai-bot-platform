"""План нового механизма в разговоре — сборка и сохранение из хода (DRF-2885).

Решение владельца 08.10 (D1): план создаётся и обсуждается в чате — и в боте,
и в чате Mini App. Оба входа идут одним ходом, поэтому здесь одно место.

### Что это и чем НЕ является

Это **путь для сквозной проверки на подготовленных данных**, не продуктовая
поверхность. Вход — временная команда :data:`TRIGGER`; настоящую реплику
или кнопку входа, обвязку карточки и слова исходов даёт владелец.

Поэтому на экране человека здесь только три рода слов:

* **подписи шагов** — то, что отдал каталог (``plan/capability-labels/``):
  клиентская формулировка подтверждённого знания. Объяснений из кодов
  утверждений здесь не собирается (лист решений 07.10, п.13);
* **три фразы владельца** из п.15 того же листа — вопрос и кнопка сохранения;
* **имя исхода с пометкой «тест»** — всё остальное: «плана нет», отказ,
  сбой, «сохранено». Сочинённых объяснений нет.

«Изменить» (слово владельца, тот же п.15) показывает шаги кнопками; тап по
шагу убирает его и пересобирает план в каталоге. Заменить шаг или добавить
свой каталог не умеет — здесь этого нет. Частичное принятие — то же самое:
убрал лишнее и сохранил оставшееся.

### Входы

* **настоящий** (решение владельца 08.10): кнопка «Составить план»
  (:data:`CB_COMPOSE`) и свободная просьба, которую опознаёт модель
  (:func:`compose_for_request`). Достаточно включённого
  ``PLAN_ENGINE_ENABLED``;
* **отладочный** — команда :data:`TRIGGER`: дополнительно нужен аккаунт
  мессенджера в серверном списке ``SYNTHETIC_TEST_TRIGGER_ACCOUNTS`` (пуст
  по умолчанию). Для итоговой приёмки не годится.

### Замки

* **механизм** — :func:`engine_enabled`: без него не отвечает ничего;
* **отладочная команда** — :func:`trigger_visible`, см. выше;
* **допуск** решает каталог. Набранная руками команда без допуска каталога
  ничего не включает.

### Безопасность

Каждое действие уходит каталогу с тройкой ЭТОГО хода
(:func:`apps.orchestrator.safety.plan_turn.plan_turn_safety`). Нет тройки —
действие не шлётся, отвечаем именем исхода.

Сохранение несёт тройку хода НАЖАТИЯ; что делать при вердикте «уточнить»,
решает каталог — здесь своей блокировки нет.

Универсального вопроса «уточнить» нет (решение владельца 08.10: «универсальный
вопрос CLARIFY без причины придумывать не будем»). Ограничения плана с
настоящими причинами появятся вместе с таблицей причин каталога; до тех пор
карточка их не шлёт и не открывает.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from apps.skills.base import SkillResult

logger = logging.getLogger(__name__)

#: Временная команда входа — только для сквозной проверки.
TRIGGER = "/plan_test"

#: «Составить план» — настоящий вход кнопкой (решение владельца 08.10).
CB_COMPOSE = "cb:plan:compose"
BUTTON_COMPOSE = "Составить план"

CB_SAVE_PREFIX = "cb:plan:save:"
SAVE_CALLBACK_RE = re.compile(r"^cb:plan:save:([0-9a-f]{8})$")
CB_EDIT_PREFIX = "cb:plan:edit:"
EDIT_CALLBACK_RE = re.compile(r"^cb:plan:edit:([0-9a-f]{8})$")
CB_DROP_PREFIX = "cb:plan:drop:"
DROP_CALLBACK_RE = re.compile(r"^cb:plan:drop:([0-9a-f]{8}):([0-9]{1,2})$")

CB_DISCUSS_PREFIX = "cb:plan:discuss:"
#: ``saved`` — обсуждают сохранённый план; восемь знаков — предложение.
DISCUSS_SAVED = "saved"
DISCUSS_CALLBACK_RE = re.compile(r"^cb:plan:discuss:(saved|[0-9a-f]{8})$")
#: Замена действующего плана предложением: восемь знаков — начало id предложения.
CB_REPLACE_PREFIX = "cb:plan:replace:"
CB_KEEP_PREFIX = "cb:plan:keep:"
REPLACE_CALLBACK_RE = re.compile(r"^cb:plan:(replace|keep):([0-9a-f]{8})$")

#: Слова владельца — лист решений 07.10, п.15 («Полная замена»).
QUESTION_REPLACE = "Заменить текущий план новым? Прежний останется в истории"
BUTTON_REPLACE = "Заменить план"
BUTTON_KEEP = "Оставить текущий"

#: Предложение, ждущее ответа о замене, — свой ключ в ``Conversation.skill_state``.
REPLACE_KEY = "plan_engine_replace"
#: Имена исходов замены — слов владельца для них нет.
PLAN_REPLACED = "PLAN_REPLACED"
PLAN_KEPT = "PLAN_KEPT"

#: Слова владельца — лист решений 07.10, п.15.
QUESTION_SAVE = "Сохранить выбранные шаги в мой план?"
BUTTON_SAVE = "Сохранить"
BUTTON_EDIT = "Изменить"
#: Слова владельца — задание §9 и решение 08.10: кнопка и первая реплика.
BUTTON_DISCUSS = "Обсудить"
DISCUSS_OPENING = "Давай обсудим твой план. Что хочешь изменить или уточнить?"

#: Открытое обсуждение — свой ключ в ``Conversation.skill_state``.
DISCUSSION_KEY = "plan_engine_discussion"
SUBJECT_PROPOSAL = "proposal"
SUBJECT_SAVED = "saved"

#: Пометка всего, что не утверждённый текст.
TEST_MARK = "тест"

#: Свой ключ в ``Conversation.skill_state``.
STATE_KEY = "plan_engine_pending"

#: Подтверждение сохранения — что именно человек нажал (ключ идемпотентности
#: каталога: ``question_id + option_id + state_revision``).
CONFIRM_QUESTION_ID = "plan.save_confirm"
CONFIRM_OPTION_ID = "save"

OUTCOME_PLAN = "PLAN"

#: Имена исходов, которые рождаются здесь, а не в каталоге.
SAFETY_INPUT_UNAVAILABLE = "SAFETY_INPUT_UNAVAILABLE"
PLAN_RULES_UNAVAILABLE = "PLAN_RULES_UNAVAILABLE"
PLAN_ENGINE_UNAVAILABLE = "PLAN_ENGINE_UNAVAILABLE"
PLAN_STEP_UNLABELLED = "PLAN_STEP_UNLABELLED"
PLAN_PROPOSAL_EXPIRED = "PLAN_PROPOSAL_EXPIRED"
PLAN_SAVED = "PLAN_SAVED"
#: «Изменить» нажато: под ответом — шаги, тап убирает шаг и пересобирает.
PLAN_EDIT = "PLAN_EDIT"
#: Сохранённый план нового механизма — показ по «мой план».
PLAN_CURRENT = "PLAN_CURRENT"

TurnSafetyProvider = Callable[[], Any]


# ─── видимость входа ─────────────────────────────────────────────────────


def _trigger_accounts() -> frozenset[str]:
    from django.conf import settings

    raw = getattr(settings, "SYNTHETIC_TEST_TRIGGER_ACCOUNTS", ()) or ()
    if isinstance(raw, str):
        raw = raw.split(",")
    return frozenset(str(item).strip() for item in raw if str(item).strip())


def engine_enabled() -> bool:
    """Включён ли новый механизм плана. От него зависит ВСЁ в этом модуле."""
    from django.conf import settings

    return bool(getattr(settings, "PLAN_ENGINE_ENABLED", False))


def trigger_visible(bot_user: Any) -> bool:
    """Отвечает ли этому аккаунту ОТЛАДОЧНАЯ команда. Пустой список — никому.

    Только про команду :data:`TRIGGER`. Настоящий вход (кнопка «Составить
    план», свободная просьба), кнопки карточки и показ сохранённого плана от
    списка не зависят — им достаточно включённого механизма.
    """
    if not engine_enabled():
        return False
    account = f"{getattr(bot_user, 'channel', '')}:{getattr(bot_user, 'channel_user_id', '')}"
    return account in _trigger_accounts()


def is_save_callback(text: str) -> bool:
    return bool(SAVE_CALLBACK_RE.match((text or "").strip()))


def is_replace_callback(text: str) -> bool:
    return bool(REPLACE_CALLBACK_RE.match((text or "").strip()))


def is_discuss_callback(text: str) -> bool:
    return bool(DISCUSS_CALLBACK_RE.match((text or "").strip()))


def is_edit_callback(text: str) -> bool:
    stripped = (text or "").strip()
    return bool(EDIT_CALLBACK_RE.match(stripped) or DROP_CALLBACK_RE.match(stripped))


# ─── то, что ждёт подтверждения ──────────────────────────────────────────


def _read_pending(conversation: Any) -> dict[str, Any] | None:
    state = getattr(conversation, "skill_state", None)
    row = state.get(STATE_KEY) if isinstance(state, dict) else None
    return row if isinstance(row, dict) and isinstance(row.get("decision"), dict) else None


def _write_pending(conversation: Any, value: dict[str, Any] | None) -> None:
    from apps.orchestrator.open_question import write_conversation_state

    write_conversation_state(conversation, STATE_KEY, value)


def _token(decision: dict[str, Any]) -> str:
    return str(decision.get("decision_id") or "").replace("-", "")[:8]


# ─── ответы ──────────────────────────────────────────────────────────────


def _named(outcome: str) -> SkillResult:
    """Исход по имени с пометкой — без объяснения, которого владелец не давал.

    Под ответом — «Меню» (§72: ответ не оставляет человека без следующего шага).
    """
    from apps.orchestrator.next_steps import menu_button, next_step_action_data

    kind = "plan_engine_outcome"
    return SkillResult(
        reply_text=f"{outcome} · {TEST_MARK}",
        action_type=kind,
        action_data=next_step_action_data(menu_button()),
        meta={"reply_kind": kind, "plan_outcome": outcome},
    )


def _proposal(labels: list[str], token: str) -> SkillResult:
    from apps.orchestrator.discovery import keyboard_envelope

    kind = "plan_engine_proposal"
    lines = [f"• {label}" for label in labels]
    return SkillResult(
        reply_text="\n".join([*lines, "", QUESTION_SAVE]),
        action_type=kind,
        action_data=keyboard_envelope(
            [
                {"label": BUTTON_SAVE, "callback": f"{CB_SAVE_PREFIX}{token}"},
                {"label": BUTTON_EDIT, "callback": f"{CB_EDIT_PREFIX}{token}"},
                {"label": BUTTON_DISCUSS, "callback": f"{CB_DISCUSS_PREFIX}{token}"},
            ]
        ),
        meta={"reply_kind": kind, "plan_outcome": OUTCOME_PLAN},
    )


# ─── вход: команда ───────────────────────────────────────────────────────


def try_handle_plan_trigger(
    *,
    text: str,
    bot_user: Any,
    conversation: Any,
    trace_id: str,
    turn_safety: TurnSafetyProvider | None,
) -> SkillResult | None:
    """Вход сборки плана: кнопка «Составить план» или отладочная команда.

    ``None`` — не наше (другой текст / механизм выключен / команда не этому
    аккаунту).
    """

    stripped = (text or "").strip()
    by_command = stripped == TRIGGER and trigger_visible(bot_user)
    by_button = stripped == CB_COMPOSE and engine_enabled()
    if not (by_command or by_button):
        return None
    _write_discussion(conversation, None)
    return _compose(
        bot_user=bot_user,
        conversation=conversation,
        trace_id=trace_id,
        turn_safety=turn_safety,
        excluded=[],
        keep_previous_on_no_plan=False,
    )


def _compose(
    *,
    bot_user: Any,
    conversation: Any,
    trace_id: str,
    turn_safety: TurnSafetyProvider | None,
    excluded: list[str],
    keep_previous_on_no_plan: bool,
) -> SkillResult:
    """Собрать план без ``excluded`` и показать предложение.

    ``keep_previous_on_no_plan`` — пересборка после «убрать шаг»: если без
    этого шага плана не получилось, прежнее предложение остаётся в силе —
    человек отказался от изменения, а не от плана.
    """

    from apps.integrations.ayla import external_user_id_for
    from apps.integrations.ayla.plan_engine_client import PlanEngineError, PlanEngineHttpClient
    from apps.planning_rules.registry import PlanningRegistryError, load_registry
    from apps.planning_rules.wire import registry_wire_body

    safety = turn_safety() if turn_safety is not None else None
    if safety is None:
        return _named(SAFETY_INPUT_UNAVAILABLE)
    try:
        rules_registry = registry_wire_body(load_registry())
    except PlanningRegistryError:
        # Fail-closed: пустой реестр вместо настоящего — утверждение «правил нет».
        logger.error("orchestrator.plan_engine_card.rules_unavailable trace=%s", trace_id)
        return _named(PLAN_RULES_UNAVAILABLE)

    client = PlanEngineHttpClient()
    external_user_id = external_user_id_for(bot_user)
    try:
        document = client.compose_decision(
            external_user_id=external_user_id,
            safety_state=safety.safety_state,
            safety_policy_version=safety.safety_policy_version,
            rules_registry=rules_registry,
            excluded_capability_refs=list(excluded),
        )
        outcome = str(document["outcome"])
        decision = document.get("decision")
        if outcome != OUTCOME_PLAN or not isinstance(decision, dict):
            # Штатные исходы без плана — в том числе ``PLAN_NOT_JUSTIFIED``
            # после исключения шага.
            if not keep_previous_on_no_plan:
                _write_pending(conversation, None)
            return _named(outcome)
        steps = [s for s in decision.get("steps") or [] if isinstance(s, dict)]
        keys = [str(s.get("capability_ref") or "") for s in steps]
        details = client.capability_details(external_user_id=external_user_id, keys=keys)
        labels_by_key = {key: row["label"] for key, row in details.items()}
        effects_by_key = {
            key: row["expected_effect"] for key, row in details.items() if row["expected_effect"]
        }
    except PlanEngineError as exc:
        logger.warning(
            "orchestrator.plan_engine_card.compose_failed trace=%s class=%s",
            trace_id,
            type(exc).__name__,
        )
        return _named(PLAN_ENGINE_UNAVAILABLE)

    if not keys or any(key not in labels_by_key for key in keys):
        # Шаг без подписи человеку показать нечем, а сохранять то, чего он не
        # видел, нельзя.
        if not keep_previous_on_no_plan:
            _write_pending(conversation, None)
        return _named(PLAN_STEP_UNLABELLED)

    _write_pending(
        conversation,
        {
            "decision": decision,
            # Ревизия хода, в котором предложение ПОКАЗАНО: она опознаёт это
            # подтверждение у каталога и не меняется от нажатия к нажатию.
            "shown_at_revision": safety.evaluated_at_revision,
            # Что человек уже убрал и как шаги названы — для «Изменить».
            # Шаги двух сборок сопоставляются по ключу способности:
            # ``step_id`` у каждой сборки новый.
            "excluded": list(excluded),
            "labels": {key: labels_by_key[key] for key in keys},
            # «Зачем шаг» — слова каталога; есть не у каждого шага.
            "effects": {key: effects_by_key[key] for key in keys if key in effects_by_key},
        },
    )
    return _proposal([labels_by_key[key] for key in keys], _token(decision))


def compose_for_request(*, bot_user: Any, conversation: Any, trace_id: str) -> SkillResult | None:
    """Свободная просьба составить план — её опознала модель (инструмент).

    Решение владельца 08.10: «свободная просьба в чате … не требовать точной
    кодовой фразы». Модель только ВЫБИРАЕТ инструмент; план собирает каталог
    с тройкой этого хода, а ответ — та же карточка, что у кнопки.

    ``None`` — механизм выключен: инструмент в этом случае модели и не
    предлагается, а ветка здесь — второй рубеж.
    """
    if not engine_enabled():
        return None
    from apps.orchestrator.safety.plan_turn import turn_safety_of

    _write_discussion(conversation, None)
    return _compose(
        bot_user=bot_user,
        conversation=conversation,
        trace_id=trace_id,
        turn_safety=lambda: turn_safety_of(conversation),
        excluded=[],
        keep_previous_on_no_plan=False,
    )


# ─── вход: «Изменить» ────────────────────────────────────────────────────


def _pending_for(conversation: Any, token: str) -> dict[str, Any] | None:
    pending = _read_pending(conversation)
    if pending is None or _token(pending["decision"]) != token:
        return None
    return pending


def _step_keys(pending: dict[str, Any]) -> list[str]:
    steps = pending["decision"].get("steps") or []
    return [str(s.get("capability_ref") or "") for s in steps if isinstance(s, dict)]


def try_handle_plan_edit(
    *,
    text: str,
    bot_user: Any,
    conversation: Any,
    trace_id: str,
    turn_safety: TurnSafetyProvider | None,
) -> SkillResult | None:
    """«Изменить» и тап по шагу; ``None`` — не наше (форма / нет входа).

    «Изменить» ничего не меняет — показывает шаги кнопками. Тап по шагу
    убирает его и пересобирает план в каталоге: изменение проходит ту же
    серверную сборку, что и первое предложение, и до «Сохранить» ничего не
    сохранено. Частичное принятие — это оно же: убрал лишнее, сохранил.
    """
    from apps.orchestrator.discovery import keyboard_envelope

    stripped = (text or "").strip()
    edit = EDIT_CALLBACK_RE.match(stripped)
    drop = DROP_CALLBACK_RE.match(stripped)
    if (edit is None and drop is None) or not engine_enabled():
        return None

    token = (edit or drop).group(1)  # type: ignore[union-attr]
    pending = _pending_for(conversation, token)
    if pending is None:
        return _named(PLAN_PROPOSAL_EXPIRED)
    keys = _step_keys(pending)
    raw_labels = pending.get("labels")
    labels: dict[str, str] = raw_labels if isinstance(raw_labels, dict) else {}
    if not keys or any(key not in labels for key in keys):
        return _named(PLAN_PROPOSAL_EXPIRED)

    if edit is not None:
        kind = "plan_engine_edit"
        return SkillResult(
            reply_text=f"{PLAN_EDIT} · {TEST_MARK}",
            action_type=kind,
            action_data=keyboard_envelope(
                [
                    {"label": labels[key], "callback": f"{CB_DROP_PREFIX}{token}:{index}"}
                    for index, key in enumerate(keys)
                ]
            ),
            meta={"reply_kind": kind, "plan_outcome": PLAN_EDIT},
        )

    index = int(drop.group(2))  # type: ignore[union-attr]
    if index >= len(keys):
        return _named(PLAN_PROPOSAL_EXPIRED)
    already = [str(k) for k in pending.get("excluded") or []]
    return _compose(
        bot_user=bot_user,
        conversation=conversation,
        trace_id=trace_id,
        turn_safety=turn_safety,
        excluded=[*already, keys[index]],
        keep_previous_on_no_plan=True,
    )


# ─── «Обсудить» ──────────────────────────────────────────────────────────
#
# Решение владельца 08.10: кнопка «Обсудить» на плане; первая реплика —
# дословно; в контекст модели — план и его шаги; «ответ модели сам по себе
# план не меняет: изменение проходит серверную проверку и подтверждение».
#
# Что знает модель: подписи шагов (слова каталога) под номерами и вид плана
# (предложение или сохранённый). Чего ей НЕ даётся, потому что этого нет:
# текста цели (у решения — только её идентификатор), обоснований шагов
# (в решении — коды утверждений, прозы нет), ограничений (таблица причин
# каталога пуста) и предпочтений. Ключи способностей и идентификаторы в
# подсказку не идут.


def _read_discussion(conversation: Any) -> dict[str, Any] | None:
    state = getattr(conversation, "skill_state", None)
    row = state.get(DISCUSSION_KEY) if isinstance(state, dict) else None
    return row if isinstance(row, dict) else None


def _write_discussion(conversation: Any, value: dict[str, Any] | None) -> None:
    from apps.orchestrator.open_question import write_conversation_state

    if value is None and _read_discussion(conversation) is None:
        return
    write_conversation_state(conversation, DISCUSSION_KEY, value)


def _proposal_effects(conversation: Any) -> list[str | None]:
    """«Зачем» по шагам текущего предложения, по порядку; ``None`` — текста нет."""
    pending = _read_pending(conversation)
    if pending is None:
        return []
    raw = pending.get("effects")
    effects: dict[str, Any] = raw if isinstance(raw, dict) else {}
    return [
        str(effects[key]) if isinstance(effects.get(key), str) and effects[key] else None
        for key in _step_keys(pending)
    ]


def _proposal_labels(conversation: Any) -> list[str] | None:
    """Подписи шагов текущего предложения по порядку — или ``None``."""
    pending = _read_pending(conversation)
    if pending is None:
        return None
    keys = _step_keys(pending)
    raw = pending.get("labels")
    labels: dict[str, Any] = raw if isinstance(raw, dict) else {}
    if not keys or any(not isinstance(labels.get(key), str) for key in keys):
        return None
    return [str(labels[key]) for key in keys]


def discussed_plan(conversation: Any) -> tuple[str, list[tuple[str, str | None]]] | None:
    """Что сейчас обсуждают: вид плана и шаги (подпись, «зачем») — или ``None``.

    Предложение читается из того, что ждёт подтверждения, а не из снимка:
    убрали шаг — обсуждается уже новое предложение. Сохранённый план — снимок
    подписей на момент нажатия «Обсудить».
    """
    if not engine_enabled():
        return None
    row = _read_discussion(conversation)
    if row is None:
        return None
    if row.get("subject") == SUBJECT_PROPOSAL:
        labels = _proposal_labels(conversation)
        if not labels:
            return None
        effects = _proposal_effects(conversation)
        return (
            SUBJECT_PROPOSAL,
            [(label, effects[i] if i < len(effects) else None) for i, label in enumerate(labels)],
        )
    if row.get("subject") == SUBJECT_SAVED:
        raw = row.get("labels")
        saved = [x for x in raw if isinstance(x, str) and x] if isinstance(raw, list) else []
        raw_effects = row.get("effects")
        saved_effects = raw_effects if isinstance(raw_effects, list) else []
        if not saved:
            return None
        return (
            SUBJECT_SAVED,
            [
                (
                    label,
                    saved_effects[i]
                    if i < len(saved_effects)
                    and isinstance(saved_effects[i], str)
                    and saved_effects[i]
                    else None,
                )
                for i, label in enumerate(saved)
            ],
        )
    return None


def discussion_allows_removal(conversation: Any) -> bool:
    """Можно ли в этом обсуждении убрать шаг: только у предложения.

    Изменение сохранённого плана идёт через предложение и подтверждение
    замены — этого пути у каталога пока нет.
    """
    found = discussed_plan(conversation)
    return found is not None and found[0] == SUBJECT_PROPOSAL


def render_plan_discussion_block(conversation: Any) -> str:
    """Абзац подсказки консьержа, пока план обсуждают; иначе пустая строка."""
    found = discussed_plan(conversation)
    if found is None:
        return ""
    subject, plan_steps = found
    steps = "\n".join(
        f"{n}. {label}" + (f" — зачем: {effect}" if effect else "")
        for n, (label, effect) in enumerate(plan_steps, start=1)
    )
    if subject == SUBJECT_PROPOSAL:
        kind = "Это ПРЕДЛОЖЕНИЕ плана: оно ещё не сохранено."
        change = (
            "Если клиент хочет убрать шаг — вызови инструмент plan_remove_step "
            "с номером шага: платформа пересоберёт план и сама покажет новый "
            "вариант. Сохраняет план только кнопка «Сохранить» под ним — "
            "сам ты его не сохраняешь и не говори, что сохранил."
        )
    else:
        kind = "Это СОХРАНЁННЫЙ план клиента."
        change = (
            "Изменить сохранённый план в этом разговоре пока нельзя — если "
            "клиент просит, честно скажи, что такой возможности пока нет."
        )
    return (
        "Клиент обсуждает свой план. " + kind + " Шаги плана (названия — слова "
        "платформы, приводи их дословно):\n" + steps + "\n"
        "Правила обсуждения плана:\n"
        "- План составляет и меняет только платформа. Не добавляй, не заменяй "
        "и не придумывай шаги; не предлагай своих вариантов плана.\n"
        "- О шаге говори только то, что следует из его названия и из текста "
        "после «зачем:», если он есть. На вопрос «почему этот шаг» приводи "
        "текст после «зачем:» дословно — это слова платформы. Если у шага "
        "такого текста нет — не сочиняй причину, скажи, что объяснения пока "
        "нет.\n"
        "- Не обещай результат и не давай медицинских советов.\n"
        "- Добавить свой шаг или заменить один шаг другим пока нельзя — скажи "
        "об этом прямо.\n"
        "- " + change
    )


def try_handle_plan_discuss(
    *, text: str, bot_user: Any, conversation: Any, trace_id: str
) -> SkillResult | None:
    """Тап «Обсудить»; ``None`` — не наше (форма / механизм выключен).

    Открывает обсуждение и отвечает первой репликой владельца — дословно.
    Ничего не собирает и не меняет; вердикта не требует.
    """
    match = DISCUSS_CALLBACK_RE.match((text or "").strip())
    if match is None or not engine_enabled():
        return None

    from apps.orchestrator.next_steps import menu_button, next_step_action_data

    token = match.group(1)
    if token == DISCUSS_SAVED:
        saved_steps = _saved_plan_steps(bot_user, trace_id)
        if saved_steps is None:
            return _named(PLAN_ENGINE_UNAVAILABLE)
        if not saved_steps:
            return _named(PLAN_PROPOSAL_EXPIRED)
        _write_discussion(
            conversation,
            {
                "subject": SUBJECT_SAVED,
                "labels": [label for label, _ in saved_steps],
                # Тем же порядком, что подписи; у шага без текста — ``None``.
                "effects": [effect for _, effect in saved_steps],
            },
        )
    else:
        if _pending_for(conversation, token) is None or not _proposal_labels(conversation):
            return _named(PLAN_PROPOSAL_EXPIRED)
        _write_discussion(conversation, {"subject": SUBJECT_PROPOSAL})

    kind = "plan_engine_discuss"
    return SkillResult(
        reply_text=DISCUSS_OPENING,
        action_type=kind,
        action_data=next_step_action_data(menu_button()),
        meta={"reply_kind": kind, "plan_outcome": "PLAN_DISCUSS"},
    )


def _saved_plan_steps(bot_user: Any, trace_id: str) -> list[tuple[str, str | None]] | None:
    """Шаги сохранённого плана (подпись, «зачем»); ``[]`` — показать нечего; ``None`` — сбой."""
    from apps.integrations.ayla import external_user_id_for
    from apps.integrations.ayla.plan_engine_client import PlanEngineError, PlanEngineHttpClient

    client = PlanEngineHttpClient()
    external_user_id = external_user_id_for(bot_user)
    try:
        plan = client.get_plan(external_user_id=external_user_id)
        if plan is None:
            return []
        raw_revision = plan.get("revision")
        revision: dict[str, Any] = raw_revision if isinstance(raw_revision, dict) else {}
        steps = [s for s in revision.get("steps") or [] if isinstance(s, dict)]
        keys = [str(s.get("capability_ref") or "") for s in steps]
        details = (
            client.capability_details(external_user_id=external_user_id, keys=keys) if keys else {}
        )
    except PlanEngineError as exc:
        logger.warning(
            "orchestrator.plan_engine_card.discuss_read_failed trace=%s class=%s",
            trace_id,
            type(exc).__name__,
        )
        return None
    if not keys or any(key not in details for key in keys):
        return []
    return [(details[key]["label"], details[key]["expected_effect"]) for key in keys]


def remove_step_for_request(
    *, bot_user: Any, conversation: Any, trace_id: str, step_number: Any
) -> SkillResult | None:
    """Убрать шаг по просьбе в обсуждении — его номер назвала модель.

    Тот же путь, что у тапа по шагу: исключение и пересборка в каталоге с
    тройкой этого хода, новое предложение с «Сохранить». Ответ модели план
    не меняет — меняет серверная сборка, а сохраняет нажатие человека.

    ``None`` — убирать нечего или номер не из этого плана: вызывающий отвечает
    сам. Номер вне плана не «поправляется» до ближайшего.
    """
    if not discussion_allows_removal(conversation):
        return None
    pending = _read_pending(conversation)
    if pending is None:
        return None
    keys = _step_keys(pending)
    if isinstance(step_number, bool) or not isinstance(step_number, int):
        return None
    if step_number < 1 or step_number > len(keys):
        return None

    from apps.orchestrator.safety.plan_turn import turn_safety_of

    already = [str(k) for k in pending.get("excluded") or []]
    return _compose(
        bot_user=bot_user,
        conversation=conversation,
        trace_id=trace_id,
        turn_safety=lambda: turn_safety_of(conversation),
        excluded=[*already, keys[step_number - 1]],
        keep_previous_on_no_plan=True,
    )


# ─── вход: «мой план» ────────────────────────────────────────────────────


def try_handle_saved_plan(*, text: str, bot_user: Any, trace_id: str) -> SkillResult | None:
    """«мой план» → сохранённый план нового механизма; ``None`` — не наше.

    ``None`` и тогда, когда такого плана нет: фраза идёт дальше — прежней
    карточке плана. Просмотр вердикта не требует и ограничениями не закрыт.
    """
    from apps.orchestrator.plan_lite_card import looks_like_my_plan_request

    if not engine_enabled() or not looks_like_my_plan_request(text):
        return None

    from apps.integrations.ayla import external_user_id_for
    from apps.integrations.ayla.plan_engine_client import PlanEngineError, PlanEngineHttpClient
    from apps.orchestrator.next_steps import menu_button, next_step_action_data

    client = PlanEngineHttpClient()
    external_user_id = external_user_id_for(bot_user)
    try:
        plan = client.get_plan(external_user_id=external_user_id)
        if plan is None:
            return None
        raw_revision = plan.get("revision")
        revision: dict[str, Any] = raw_revision if isinstance(raw_revision, dict) else {}
        steps = [s for s in revision.get("steps") or [] if isinstance(s, dict)]
        keys = [str(s.get("capability_ref") or "") for s in steps]
        labels = (
            client.capability_labels(external_user_id=external_user_id, keys=keys) if keys else {}
        )
    except PlanEngineError as exc:
        logger.warning(
            "orchestrator.plan_engine_card.read_failed trace=%s class=%s",
            trace_id,
            type(exc).__name__,
        )
        return _named(PLAN_ENGINE_UNAVAILABLE)

    if not keys or any(key not in labels for key in keys):
        return _named(PLAN_STEP_UNLABELLED)
    kind = "plan_engine_current"
    lines = [f"• {labels[key]}" for key in keys]
    return SkillResult(
        reply_text="\n".join([*lines, "", f"{PLAN_CURRENT} · {TEST_MARK}"]),
        action_type=kind,
        action_data=next_step_action_data(
            {"label": BUTTON_DISCUSS, "callback": f"{CB_DISCUSS_PREFIX}{DISCUSS_SAVED}"},
            menu_button(),
        ),
        meta={"reply_kind": kind, "plan_outcome": PLAN_CURRENT},
    )


# ─── вход: «Сохранить» ───────────────────────────────────────────────────


def save_command(
    decision: dict[str, Any], safety: Any, *, shown_at_revision: int
) -> dict[str, Any]:
    """Команда сохранения каталога: решение без правок + подтверждение + тройка.

    Две ревизии, и они разные по смыслу:

    * ``confirmation.state_revision`` — ревизия хода, в котором предложение
      показано. Входит в ключ идемпотентности каталога
      (пользователь + ``decision_id`` + вопрос + вариант + эта ревизия), поэтому
      обязана быть одной для всех нажатий на этой карточке: с ревизией хода
      нажатия второе нажатие дало бы второй план;
    * ``evaluated_at_revision`` в тройке — ревизия хода НАЖАТИЯ: при каком
      состоянии разговора вынесен вердикт. В ключ не входит.
    """
    command: dict[str, Any] = {
        "decision_id": decision["decision_id"],
        "goal_ref": decision["goal_ref"],
        "confirmation": {
            "question_id": CONFIRM_QUESTION_ID,
            "option_id": CONFIRM_OPTION_ID,
            "state_revision": shown_at_revision,
        },
        "provenance": {"policy_versions": decision["policy_versions"]},
        "decision": {
            "steps": decision["steps"],
            "assertions": decision.get("assertions", []),
            "validation": decision["validation"],
        },
        **safety.as_wire(),
    }
    return command


def try_handle_plan_save(
    *,
    text: str,
    bot_user: Any,
    conversation: Any,
    trace_id: str,
    turn_safety: TurnSafetyProvider | None,
) -> SkillResult | None:
    """Тап «Сохранить»; ``None`` — не наше (форма / нет входа)."""

    match = SAVE_CALLBACK_RE.match((text or "").strip())
    if match is None or not engine_enabled():
        return None

    from apps.integrations.ayla import external_user_id_for
    from apps.integrations.ayla.plan_engine_client import (
        PlanCapabilityNotConfirmedError,
        PlanEngineContractError,
        PlanEngineError,
        PlanEngineHttpClient,
        PlanGoalNotFoundError,
        PlanIdempotencyConflictError,
        PlanSaveSafetyBlockedError,
    )

    pending = _read_pending(conversation)
    if pending is None or _token(pending["decision"]) != match.group(1):
        return _named(PLAN_PROPOSAL_EXPIRED)

    safety = turn_safety() if turn_safety is not None else None
    if safety is None:
        return _named(SAFETY_INPUT_UNAVAILABLE)

    shown_at = pending.get("shown_at_revision")
    if not isinstance(shown_at, int) or isinstance(shown_at, bool):
        return _named(PLAN_PROPOSAL_EXPIRED)
    try:
        command = save_command(pending["decision"], safety, shown_at_revision=shown_at)
    except KeyError:
        return _named(PLAN_PROPOSAL_EXPIRED)
    try:
        saved = PlanEngineHttpClient().save_plan(
            external_user_id=external_user_id_for(bot_user), command=command
        )
    except PlanSaveSafetyBlockedError:
        return _named("PLAN_SAVE_SAFETY_BLOCKED")
    except PlanIdempotencyConflictError:
        return _named("PLAN_IDEMPOTENCY_CONFLICT")
    except PlanGoalNotFoundError:
        return _named("GOAL_NOT_FOUND")
    except PlanCapabilityNotConfirmedError:
        # Шаг стоит на способности, которой для этого человека больше нет в
        # знании: предложение устарело — собирать заново, а не «недоступно».
        _write_pending(conversation, None)
        return _named("PLAN_CAPABILITY_NOT_CONFIRMED")
    except PlanEngineContractError as exc:
        logger.error(
            "orchestrator.plan_engine_card.save_contract_violation trace=%s reason=%s",
            trace_id,
            exc.reason,
        )
        return _named("PLAN_CONTRACT_VIOLATION")
    except PlanEngineError as exc:
        logger.warning(
            "orchestrator.plan_engine_card.save_failed trace=%s class=%s",
            trace_id,
            type(exc).__name__,
        )
        return _named(PLAN_ENGINE_UNAVAILABLE)

    # Предложение оставляем: повторное нажатие шлёт ТУ ЖЕ команду, и каталог
    # узнаёт её сам — второго плана не будет. Уходит оно со следующей сборкой.
    # Обсуждение закрыто: обсуждали предложение, а оно стало планом.
    _write_discussion(conversation, None)
    asked = _replacement_question(conversation, saved)
    if asked is not None:
        return asked
    _write_replace(conversation, None)
    return _named(PLAN_SAVED)


# ─── предложение и замена действующего плана ─────────────────────────────
#
# У цели уже есть действующий план — каталог сохраняет новый как ПРЕДЛОЖЕНИЕ
# и действующий не трогает. Действующим предложение делает только отдельное
# «да» человека (решение владельца: «сохранённый черновик не вытесняет
# действующий план без подтверждения замены»). «Да» относится к конкретному
# плану: если за это время действующим стал другой, каталог откажет.


def _hex8(plan_id: Any) -> str:
    return str(plan_id or "").replace("-", "")[:8].lower()


def _read_replace(conversation: Any) -> dict[str, Any] | None:
    state = getattr(conversation, "skill_state", None)
    row = state.get(REPLACE_KEY) if isinstance(state, dict) else None
    if not isinstance(row, dict):
        return None
    if not isinstance(row.get("plan_id"), str) or not isinstance(row.get("replaces_plan_id"), str):
        return None
    return row


def _write_replace(conversation: Any, value: dict[str, Any] | None) -> None:
    from apps.orchestrator.open_question import write_conversation_state

    if value is None and _read_replace(conversation) is None:
        return
    write_conversation_state(conversation, REPLACE_KEY, value)


def _replacement_question(conversation: Any, saved: Any) -> SkillResult | None:
    """Вопрос о замене, если сохранённое — предложение; иначе ``None``.

    Предложение каталог опознаёт двумя полями сразу: статус ``proposed`` и
    ``replaces.plan_id``. Одно без другого вопросом не становится: спрашивать
    о замене плана, которого не назвали, нельзя.
    """
    from apps.orchestrator.discovery import keyboard_envelope

    if not isinstance(saved, dict):
        return None
    plan = saved.get("plan")
    replaces = saved.get("replaces")
    if not isinstance(plan, dict) or plan.get("status") != "proposed":
        return None
    plan_id = plan.get("plan_id")
    replaces_plan_id = replaces.get("plan_id") if isinstance(replaces, dict) else None
    if not isinstance(plan_id, str) or not isinstance(replaces_plan_id, str):
        return None
    token = _hex8(plan_id)
    if len(token) != 8 or not replaces_plan_id:
        return None
    _write_replace(conversation, {"plan_id": plan_id, "replaces_plan_id": replaces_plan_id})
    kind = "plan_engine_replace_question"
    return SkillResult(
        reply_text=QUESTION_REPLACE,
        action_type=kind,
        action_data=keyboard_envelope(
            [
                {"label": BUTTON_REPLACE, "callback": f"{CB_REPLACE_PREFIX}{token}"},
                {"label": BUTTON_KEEP, "callback": f"{CB_KEEP_PREFIX}{token}"},
            ]
        ),
        meta={"reply_kind": kind, "plan_outcome": "PLAN_PROPOSED"},
    )


def try_handle_plan_replace(
    *,
    text: str,
    bot_user: Any,
    conversation: Any,
    trace_id: str,
    turn_safety: TurnSafetyProvider | None,
) -> SkillResult | None:
    """«Заменить план» и «Оставить текущий»; ``None`` — не наше (форма / нет входа).

    * «Заменить план» — замена в каталоге с тройкой хода НАЖАТИЯ. Повторное
      нажатие дубля не даёт: каталог отвечает «уже заменено».
    * «Оставить текущий» — предложение уходит в архив; действующий план не
      меняется. Вердикта не требует: это отказ, а не расширение действующего.
    """
    match = REPLACE_CALLBACK_RE.match((text or "").strip())
    if match is None or not engine_enabled():
        return None

    from apps.integrations.ayla import external_user_id_for
    from apps.integrations.ayla.plan_engine_client import (
        PlanEngineContractError,
        PlanEngineError,
        PlanEngineHttpClient,
        PlanNotFoundError,
        PlanReplacementTargetChangedError,
        PlanSaveSafetyBlockedError,
        PlanTransitionRefusedError,
    )

    action, token = match.group(1), match.group(2)
    waiting = _read_replace(conversation)
    if waiting is None or _hex8(waiting["plan_id"]) != token:
        return _named(PLAN_PROPOSAL_EXPIRED)

    client = PlanEngineHttpClient()
    external_user_id = external_user_id_for(bot_user)
    try:
        if action == "keep":
            client.archive_plan(external_user_id=external_user_id, plan_id=waiting["plan_id"])
            _write_replace(conversation, None)
            return _named(PLAN_KEPT)

        safety = turn_safety() if turn_safety is not None else None
        if safety is None:
            return _named(SAFETY_INPUT_UNAVAILABLE)
        client.replace_plan(
            external_user_id=external_user_id,
            plan_id=waiting["plan_id"],
            replaces_plan_id=waiting["replaces_plan_id"],
            safety_state=safety.safety_state,
            safety_policy_version=safety.safety_policy_version,
            evaluated_at_revision=safety.evaluated_at_revision,
        )
    except PlanSaveSafetyBlockedError:
        # Вопрос остаётся в силе: человек может ответить на следующем ходе.
        return _named("PLAN_SAVE_SAFETY_BLOCKED")
    except PlanReplacementTargetChangedError:
        _write_replace(conversation, None)
        return _named("PLAN_REPLACEMENT_TARGET_CHANGED")
    except (PlanTransitionRefusedError, PlanNotFoundError):
        # Это уже не предложение (замещено новым, отклонено) или его нет.
        _write_replace(conversation, None)
        return _named(PLAN_PROPOSAL_EXPIRED)
    except PlanEngineContractError as exc:
        logger.error(
            "orchestrator.plan_engine_card.replace_contract_violation trace=%s reason=%s",
            trace_id,
            exc.reason,
        )
        return _named("PLAN_CONTRACT_VIOLATION")
    except PlanEngineError as exc:
        logger.warning(
            "orchestrator.plan_engine_card.replace_failed trace=%s class=%s",
            trace_id,
            type(exc).__name__,
        )
        return _named(PLAN_ENGINE_UNAVAILABLE)

    # Состояние вопроса оставляем: повторное «Заменить план» шлёт ТО ЖЕ
    # подтверждение, и каталог сам отвечает «уже заменено». Уходит оно со
    # следующим сохранением.
    return _named(PLAN_REPLACED)


__all__ = [
    "BUTTON_COMPOSE",
    "BUTTON_DISCUSS",
    "BUTTON_KEEP",
    "BUTTON_REPLACE",
    "QUESTION_REPLACE",
    "DISCUSS_OPENING",
    "BUTTON_EDIT",
    "CB_COMPOSE",
    "BUTTON_SAVE",
    "QUESTION_SAVE",
    "TRIGGER",
    "compose_for_request",
    "discussed_plan",
    "discussion_allows_removal",
    "is_discuss_callback",
    "is_replace_callback",
    "try_handle_plan_replace",
    "remove_step_for_request",
    "render_plan_discussion_block",
    "try_handle_plan_discuss",
    "engine_enabled",
    "is_edit_callback",
    "is_save_callback",
    "save_command",
    "trigger_visible",
    "try_handle_plan_edit",
    "try_handle_plan_save",
    "try_handle_saved_plan",
    "try_handle_plan_trigger",
]
