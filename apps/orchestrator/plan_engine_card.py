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

Кнопки «Изменить» нет: изменение шага в разговоре не построено, а кнопка без
действия хуже её отсутствия.

### Два замка, и они разные

* **Видимость входа** — :func:`trigger_visible`: включённый
  ``PLAN_ENGINE_ENABLED`` и аккаунт мессенджера в серверном списке
  ``SYNTHETIC_TEST_TRIGGER_ACCOUNTS`` (пуст по умолчанию — входа нет ни у
  кого). Это не разрешение, а «кому команда вообще отвечает»;
* **допуск** решает каталог. Набранная руками команда без допуска каталога
  ничего не включает.

### Безопасность

Каждое действие уходит каталогу с тройкой ЭТОГО хода
(:func:`apps.orchestrator.safety.plan_turn.plan_turn_safety`). Нет тройки —
действие не шлётся, отвечаем именем исхода.

Сохранение — ход ПОДТВЕРЖДЕНИЯ, и у нажатия кнопки вердикт почти всегда
«норма». Каталог вердикт сборки с ним не сравнивает. Поэтому граница на
нашей стороне — :func:`pending_restrictions`: вопрос ``plan.safety_clarify``,
возникший, пока предложение ждало ответа, обязан уйти в ``restrictions``,
каким бы ни был вердикт хода с кнопкой. Каталог этого проверить не может.

Факт «вопрос возник» живёт рядом с самим предложением
(:func:`note_clarify_opened`), а не только в слоте открытого вопроса: слот
живёт два часа (B13), нажать «Сохранить» человек может позже.
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

CB_SAVE_PREFIX = "cb:plan:save:"
SAVE_CALLBACK_RE = re.compile(r"^cb:plan:save:([0-9a-f]{8})$")

#: Слова владельца — лист решений 07.10, п.15.
QUESTION_SAVE = "Сохранить выбранные шаги в мой план?"
BUTTON_SAVE = "Сохранить"

#: Пометка всего, что не утверждённый текст.
TEST_MARK = "тест"

#: Свой ключ в ``Conversation.skill_state``.
STATE_KEY = "plan_engine_pending"

#: Подтверждение сохранения — что именно человек нажал (ключ идемпотентности
#: каталога: ``question_id + option_id + state_revision``).
CONFIRM_QUESTION_ID = "plan.save_confirm"
CONFIRM_OPTION_ID = "save"

#: Стойкий вопрос «уточнить» по плану — имя согласовано с каталогом.
CLARIFY_QUESTION_ID = "plan.safety_clarify"
CAUSE_SAFETY_CLARIFY = "SAFETY_CLARIFY"
SCOPE_PLAN = "PLAN"

OUTCOME_PLAN = "PLAN"

#: Имена исходов, которые рождаются здесь, а не в каталоге.
SAFETY_INPUT_UNAVAILABLE = "SAFETY_INPUT_UNAVAILABLE"
PLAN_RULES_UNAVAILABLE = "PLAN_RULES_UNAVAILABLE"
PLAN_ENGINE_UNAVAILABLE = "PLAN_ENGINE_UNAVAILABLE"
PLAN_STEP_UNLABELLED = "PLAN_STEP_UNLABELLED"
PLAN_PROPOSAL_EXPIRED = "PLAN_PROPOSAL_EXPIRED"
PLAN_SAVED = "PLAN_SAVED"
CLARIFY_PENDING = "CLARIFY_PENDING"

TurnSafetyProvider = Callable[[], Any]


# ─── видимость входа ─────────────────────────────────────────────────────


def _trigger_accounts() -> frozenset[str]:
    from django.conf import settings

    raw = getattr(settings, "SYNTHETIC_TEST_TRIGGER_ACCOUNTS", ()) or ()
    if isinstance(raw, str):
        raw = raw.split(",")
    return frozenset(str(item).strip() for item in raw if str(item).strip())


def trigger_visible(bot_user: Any) -> bool:
    """Отвечает ли этому аккаунту временный вход. Пустой список — никому."""
    from django.conf import settings

    if not getattr(settings, "PLAN_ENGINE_ENABLED", False):
        return False
    account = f"{getattr(bot_user, 'channel', '')}:{getattr(bot_user, 'channel_user_id', '')}"
    return account in _trigger_accounts()


def is_save_callback(text: str) -> bool:
    return bool(SAVE_CALLBACK_RE.match((text or "").strip()))


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


def note_clarify_opened(conversation: Any) -> None:
    """Пометить: пока предложение ждёт ответа, по плану возник вопрос «уточнить».

    Зовёт тот, кто открывает вопрос :data:`CLARIFY_QUESTION_ID`. Пометка
    переживает слот открытого вопроса (два часа) и уходит только вместе с
    предложением.
    """
    pending = _read_pending(conversation)
    if pending is None or pending.get("clarify_open"):
        return
    _write_pending(conversation, {**pending, "clarify_open": True})


def pending_restrictions(conversation: Any, pending: dict[str, Any]) -> list[dict[str, str]]:
    """Ограничения, с которыми предложение обязано сохраниться.

    Источников два, и хватает любого: пометка рядом с предложением и открытый
    сейчас слот вопроса. Вердикт хода с кнопкой сюда не входит намеренно.
    """
    from apps.orchestrator.open_question import pending_question

    question = pending_question(conversation)
    slot_open = question is not None and question.question_id == CLARIFY_QUESTION_ID
    if not (pending.get("clarify_open") or slot_open):
        return []
    return [
        {"scope": SCOPE_PLAN, "cause": CAUSE_SAFETY_CLARIFY, "question_id": CLARIFY_QUESTION_ID}
    ]


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
            [{"label": BUTTON_SAVE, "callback": f"{CB_SAVE_PREFIX}{token}"}]
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
    """Временная команда сборки плана; ``None`` — не наше (другой текст / нет входа)."""

    if (text or "").strip() != TRIGGER or not trigger_visible(bot_user):
        return None

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
            excluded_capability_refs=[],
        )
        outcome = str(document["outcome"])
        decision = document.get("decision")
        if outcome != OUTCOME_PLAN or not isinstance(decision, dict):
            # Штатные исходы без плана — в том числе ``CLARIFY_PENDING``.
            _write_pending(conversation, None)
            return _named(outcome)
        steps = [s for s in decision.get("steps") or [] if isinstance(s, dict)]
        keys = [str(s.get("capability_ref") or "") for s in steps]
        labels_by_key = client.capability_labels(external_user_id=external_user_id, keys=keys)
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
        _write_pending(conversation, None)
        return _named(PLAN_STEP_UNLABELLED)

    _write_pending(
        conversation,
        {
            "decision": decision,
            "clarify_open": False,
            # Ревизия хода, в котором предложение ПОКАЗАНО: она опознаёт это
            # подтверждение у каталога и не меняется от нажатия к нажатию.
            "shown_at_revision": safety.evaluated_at_revision,
        },
    )
    return _proposal([labels_by_key[key] for key in keys], _token(decision))


# ─── вход: «Сохранить» ───────────────────────────────────────────────────


def save_command(
    decision: dict[str, Any],
    safety: Any,
    restrictions: list[dict[str, str]],
    *,
    shown_at_revision: int,
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
    if restrictions:
        command["restrictions"] = restrictions
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
    if match is None or not trigger_visible(bot_user):
        return None

    from apps.integrations.ayla import external_user_id_for
    from apps.integrations.ayla.plan_engine_client import (
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

    restrictions = pending_restrictions(conversation, pending)
    if safety.safety_state == "CLARIFY" and not restrictions:
        # «Уточнить» без названного вопроса не держится и не снимается; каталог
        # такую команду отвергнет. Вопрос и кнопки даёт владелец — до тех пор
        # сохранять при «уточнить» нечем.
        return _named(CLARIFY_PENDING)

    shown_at = pending.get("shown_at_revision")
    if not isinstance(shown_at, int) or isinstance(shown_at, bool):
        return _named(PLAN_PROPOSAL_EXPIRED)
    try:
        command = save_command(
            pending["decision"], safety, restrictions, shown_at_revision=shown_at
        )
    except KeyError:
        return _named(PLAN_PROPOSAL_EXPIRED)
    try:
        PlanEngineHttpClient().save_plan(
            external_user_id=external_user_id_for(bot_user), command=command
        )
    except PlanSaveSafetyBlockedError:
        return _named("PLAN_SAVE_SAFETY_BLOCKED")
    except PlanIdempotencyConflictError:
        return _named("PLAN_IDEMPOTENCY_CONFLICT")
    except PlanGoalNotFoundError:
        return _named("GOAL_NOT_FOUND")
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
    return _named(PLAN_SAVED)


__all__ = [
    "BUTTON_SAVE",
    "CLARIFY_QUESTION_ID",
    "QUESTION_SAVE",
    "TRIGGER",
    "is_save_callback",
    "note_clarify_opened",
    "pending_restrictions",
    "save_command",
    "trigger_visible",
    "try_handle_plan_save",
    "try_handle_plan_trigger",
]
