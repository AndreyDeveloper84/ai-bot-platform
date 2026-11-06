"""Вход безопасности для действий с планом — из вердикта ЭТОГО хода (DRF-2885).

Каталог принимает действие с планом только вместе с тройкой: состояние
безопасности, версия политики и ревизия, при которой вердикт вынесен. Тройку
он не сверяет — хранит рядом с действием («при каком состоянии человек
действовал»). Требований у него два: вердикт и ревизия — из ОДНОГО хода, и в
пределах разговора ревизия не убывает.

До этого модуля у живого хода такой тройки не было. Вердикт считался на
каждом ходе (:func:`apps.orchestrator.safety.gate.evaluate_inbound`), но
ревизию хода открывал только теневой контур
(:func:`apps.orchestrator.dr_shadow.record_turn_safety`) — при выключенном
``DRE_SHADOW_ENABLED`` ревизии не существовало вовсе.

Решение главного окна 08.10: на ходах, затрагивающих план, ревизия
открывается БЕЗУСЛОВНО, независимо от теневого флага.

### Что модуль держит

* **один ход — одна ревизия.** Если теневой контур в этом ходе уже открыл
  ревизию и записал вердикт, берётся его запись (``recorded``); вторая
  ревизия за ход не открывается — иначе вердикт носил бы номер, которого у
  сообщения не было;
* **нет тройки — нет действия.** Любой сбой, ход без разговора, вердикт без
  сырого результата — ``None``. Вызывающий обязан в этом случае НЕ слать
  действие каталогу: ноль или прошлая ревизия вместо настоящей — это
  утверждение «оценено», которого не было;
* **оборванный ход плана не трогает.** Вердикт, на котором ход обрывается
  (кризис, неотложка, блок), до действий с планом не доходит; если всё же
  спросили — ``None``, а не ``STOP`` от нашего имени.

Словарь состояний — каталожный, верхним регистром: ``NORMAL`` / ``CLARIFY``
/ ``CAUTION`` / ``STOP`` / ``UNKNOWN``. Перевод вердикта в состояние делает
:mod:`apps.orchestrator.safety.assessment` — здесь таблицы нет.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PlanTurnSafety:
    """Тройка, с которой действие с планом уходит каталогу."""

    safety_state: str
    safety_policy_version: str
    evaluated_at_revision: int

    def as_wire(self) -> dict[str, Any]:
        return {
            "safety_state": self.safety_state,
            "safety_policy_version": self.safety_policy_version,
            "evaluated_at_revision": self.evaluated_at_revision,
        }


def plan_turn_safety(
    conversation: Any, gate_outcome: Any, *, recorded: Any = None
) -> PlanTurnSafety | None:
    """Тройка безопасности этого хода — или ``None``, если её нет.

    ``gate_outcome`` — результат :func:`evaluate_inbound` этого хода.
    ``recorded`` — то, что вернул в этом же ходе
    :func:`apps.orchestrator.dr_shadow.record_turn_safety` (``Recorded`` или
    ``None``): ревизия уже открыта, второй раз не открываем.

    ``None`` — действие с планом слать нельзя. Никогда не бросает.
    """

    try:
        if conversation is None or gate_outcome is None:
            return None
        if not getattr(gate_outcome, "allowed", False):
            return None
        raw = getattr(gate_outcome, "result", None)
        if raw is None:
            return None

        if recorded is None:
            from apps.orchestrator.dr_shadow import _open_turn_revision
            from apps.orchestrator.safety.record import record_verdict

            conversation_id = str(conversation.id)
            _open_turn_revision(conversation_id)
            recorded = record_verdict(conversation_id, raw, source="pre_check")

        assessment = recorded.assessment
        revision = assessment.evaluated_at_revision
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            return None

        from apps.orchestrator.safety.assessment import policy_version

        version = policy_version()
        if not version:
            return None
        return PlanTurnSafety(
            safety_state=str(assessment.state.value).upper(),
            safety_policy_version=version,
            evaluated_at_revision=revision,
        )
    except Exception:  # noqa: BLE001 — нет тройки → нет действия, ход не падает
        logger.warning(
            "orchestrator.plan_turn_safety.unavailable conversation=%s verdict=%s",
            getattr(conversation, "id", None),
            getattr(gate_outcome, "verdict", None),
            exc_info=True,
        )
        return None


def last_turn_safety(conversation_id: Any) -> PlanTurnSafety | None:
    """Тройка ПОСЛЕДНЕГО хода разговора — для действия с планом вне хода.

    Экран Mini App сохраняет план без реплики в чат (задание владельца, §9),
    а у нажатия на экране своего вердикта нет. Он несёт вердикт последнего
    хода этого разговора вместе с его ревизией: «стоп» минуту назад в чате
    не обходится кнопкой на экране.

    ``None`` — действие слать нельзя:

    * состояния разговора нет или оно истекло (два часа без хода);
    * последний ход вердикта не записал — запись в состоянии старше его
      ревизии. Прежний вердикт за нынешний не выдаётся;
    * вердикт «не оценивалось» или без версии политики.

    Блокирующий вердикт возвращается как есть (``STOP``): отказывает каталог,
    своим словом. Никогда не бросает.
    """
    try:
        from apps.orchestrator.decision_readiness import state as state_mod

        found = state_mod.load(str(conversation_id))
        state = found.state
        if state is None:
            return None
        verdict = state.safety
        revision = verdict.evaluated_at_revision
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            return None
        if revision != state.revision:
            return None
        version = verdict.policy_version
        if not version:
            return None
        return PlanTurnSafety(
            safety_state=str(verdict.state.value).upper(),
            safety_policy_version=str(version),
            evaluated_at_revision=revision,
        )
    except Exception:  # noqa: BLE001 — нет тройки → нет действия
        logger.warning(
            "orchestrator.plan_turn_safety.last_turn_unavailable conversation=%s",
            conversation_id,
            exc_info=True,
        )
        return None


#: Имя атрибута на объекте разговора ЭТОГО хода.
_ATTR = "_plan_turn_safety_provider"


def attach_turn_safety(conversation: Any, provider: Any) -> None:
    """Положить ленивый источник тройки на объект разговора этого хода.

    Путь плана начинается в двух местах — в структурном ходе и в инструменте
    модели, — и до второго источник через аргументы не дотянуть, не меняя
    подписи консьержа. Объект разговора создаётся на ход и доезжает до обоих,
    поэтому источник живёт на нём: вместе с ходом он и исчезает. Глобальной
    переменной здесь нет намеренно — она пережила бы ход в потоке работника
    и отдала бы следующему человеку чужой вердикт.
    """
    if conversation is None:
        return
    try:
        setattr(conversation, _ATTR, provider)
    except Exception:  # noqa: BLE001 — нет источника → нет действия с планом
        logger.warning("orchestrator.plan_turn_safety.attach_failed", exc_info=True)


def turn_safety_of(conversation: Any) -> PlanTurnSafety | None:
    """Тройка этого хода по объекту разговора — или ``None``. Не бросает."""
    provider = getattr(conversation, _ATTR, None)
    if provider is None:
        return None
    try:
        return provider()
    except Exception:  # noqa: BLE001
        logger.warning("orchestrator.plan_turn_safety.provider_failed", exc_info=True)
        return None


__all__ = [
    "PlanTurnSafety",
    "attach_turn_safety",
    "last_turn_safety",
    "plan_turn_safety",
    "turn_safety_of",
]
