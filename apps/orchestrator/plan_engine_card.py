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

CB_SAVE_PREFIX = "cb:plan:save:"
SAVE_CALLBACK_RE = re.compile(r"^cb:plan:save:([0-9a-f]{8})$")
CB_EDIT_PREFIX = "cb:plan:edit:"
EDIT_CALLBACK_RE = re.compile(r"^cb:plan:edit:([0-9a-f]{8})$")
CB_DROP_PREFIX = "cb:plan:drop:"
DROP_CALLBACK_RE = re.compile(r"^cb:plan:drop:([0-9a-f]{8}):([0-9]{1,2})$")

#: Слова владельца — лист решений 07.10, п.15.
QUESTION_SAVE = "Сохранить выбранные шаги в мой план?"
BUTTON_SAVE = "Сохранить"
BUTTON_EDIT = "Изменить"

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


def trigger_visible(bot_user: Any) -> bool:
    """Отвечает ли этому аккаунту временный вход. Пустой список — никому."""
    from django.conf import settings

    if not getattr(settings, "PLAN_ENGINE_ENABLED", False):
        return False
    account = f"{getattr(bot_user, 'channel', '')}:{getattr(bot_user, 'channel_user_id', '')}"
    return account in _trigger_accounts()


def is_save_callback(text: str) -> bool:
    return bool(SAVE_CALLBACK_RE.match((text or "").strip()))


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
    """Временная команда сборки плана; ``None`` — не наше (другой текст / нет входа)."""

    if (text or "").strip() != TRIGGER or not trigger_visible(bot_user):
        return None
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
            # Штатные исходы без плана — в том числе ``CLARIFY_PENDING`` и
            # ``PLAN_NOT_JUSTIFIED`` после исключения шага.
            if not keep_previous_on_no_plan:
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
        },
    )
    return _proposal([labels_by_key[key] for key in keys], _token(decision))


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
    if (edit is None and drop is None) or not trigger_visible(bot_user):
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


# ─── вход: «мой план» ────────────────────────────────────────────────────


def try_handle_saved_plan(*, text: str, bot_user: Any, trace_id: str) -> SkillResult | None:
    """«мой план» → сохранённый план нового механизма; ``None`` — не наше.

    ``None`` и тогда, когда такого плана нет: фраза идёт дальше — прежней
    карточке плана. Просмотр вердикта не требует и ограничениями не закрыт.
    """
    from apps.orchestrator.plan_lite_card import looks_like_my_plan_request

    if not trigger_visible(bot_user) or not looks_like_my_plan_request(text):
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
        action_data=next_step_action_data(menu_button()),
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
    if match is None or not trigger_visible(bot_user):
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
        PlanEngineHttpClient().save_plan(
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
    return _named(PLAN_SAVED)


__all__ = [
    "BUTTON_EDIT",
    "BUTTON_SAVE",
    "QUESTION_SAVE",
    "TRIGGER",
    "is_edit_callback",
    "is_save_callback",
    "save_command",
    "trigger_visible",
    "try_handle_plan_edit",
    "try_handle_plan_save",
    "try_handle_saved_plan",
    "try_handle_plan_trigger",
]
