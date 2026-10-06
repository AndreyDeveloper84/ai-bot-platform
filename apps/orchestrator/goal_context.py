"""Цель человека — в промпт консьержа на каждом ходе (DRF-2808).

Когда человек называет цель словами, бот отвечает «Поняла. Буду учитывать:
{goal}» (``goal_capture.CONFIRMATION``, слова владельца 28.09). До этого листа
обещание не исполнялось: строка цели жила только внутри блока питания, а тот
подключается лишь на ходах про еду и при включённом коуче питания. На пилоте
цель до модели не доходила никогда.

Этот блок — та же строка, но в каждом ходе консьержа, рядом с памятью: цель —
подтверждённый факт о человеке, заявленный им самим (не вывод Ф4), и едет под
тем же согласием ``memory_green`` и тем же выключателем памяти.

### Правила, которые блок держит намеренно

* **Только слова человека** (``goal_text``). Голый курируемый ключ
  (``more_energy``) в промпт не идёт: это наш идентификатор, а не слова
  человека, и модель, увидев слоган, начнёт его цитировать. Цель, выбранная
  кнопкой без слов, здесь не рисуется — подпись выбранной опции решает
  владелец, не этот блок.
* **Медицинская цель** (``nutrition_wellness.goal_is_medical``) — блока нет:
  консьерж записывает к мастеру, а не ведёт к медицинской цели.
* **Чувствительный периметр** (беременность, ГВ, РПП, снятый дефицит —
  ``render.remarks_suppressed``) — блока нет. Тот же предикат, что у блока
  питания: там цели в этом периметре тоже нет (DRF-2766 фаза 1в).

Профиль для периметра запрашивается только когда слова цели есть — иначе
каждый ход платил бы поход в Ayla за ничего. Периметр применяется к
ПРОЧИТАННОМУ профилю: анкеты питания у большинства бьюти-клиентов нет, и
«нет анкеты» — не периметр. Цена названа: если чтение профиля человека в
периметре сорвалось, его собственные слова цели (уже прошедшие проверку
здоровья при записи) попадут в промпт — это слова, а не совет.

Сбой любого шага — пустой блок: ход дороже картины.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def render_goal_line(goal: Any) -> str:
    """Цель → строка промпта. ``""``, когда слов человека нет.

    Одна функция на оба места, где цель попадает в промпт (этот блок и блок
    питания): строки обязаны совпадать до символа — по этому совпадению
    вызывающий убирает дубль.
    """
    text = (getattr(goal, "text", None) or "").strip() if goal is not None else ""
    if not text:
        return ""
    return f"Цель клиента своими словами: {text}"


def build_goal_block(bot_user: Any) -> str:
    """Строка цели для системного промпта консьержа, или ``""``.

    ``""`` — во всех закрытых случаях: выключатель памяти, нет согласия
    ``memory_green`` или связки с Ayla, нет цели или её слов, медицинская
    цель, чувствительный периметр, любой сбой.
    """
    try:
        from apps.identity.services.personal_context import memory_green_open
        from apps.orchestrator.memory_block import concierge_memory_enabled

        if not concierge_memory_enabled() or not memory_green_open(bot_user):
            return ""

        from apps.orchestrator.nutrition_context import _fetch_goal, _fetch_profile
        from apps.orchestrator.nutrition_wellness import goal_is_medical

        goal = _fetch_goal(bot_user)
        line = render_goal_line(goal)
        if not line or goal_is_medical(goal):
            return ""

        from apps.nutrition_proactive.render import remarks_suppressed

        # Периметр — по ПРОЧИТАННОМУ профилю. У ``remarks_suppressed`` ``None``
        # — «закрыто» (там он сторожит советы о еде), а ``None`` здесь и «анкеты
        # нет» — обычное состояние бьюти-клиента. Читать его как периметр
        # значило бы не показать цель почти никому.
        profile = _fetch_profile(bot_user)
        if profile is not None and remarks_suppressed(profile):
            logger.info("orchestrator.goal_context.skip reason=sensitive_perimeter")
            return ""
        return line
    except Exception:  # noqa: BLE001 — ход дороже картины
        logger.exception("orchestrator.goal_context.failed")
        return ""


def merge_goal_into_memory(memory_block: str, goal_block: str, nutrition_block: str) -> str:
    """Положить строку цели рядом с памятью — если блок питания её уже не несёт.

    Блок питания ставит цель первой как рамку для чисел (там она нужна у
    чисел), и на ходах про еду строка была бы в промпте дважды. Совпадение
    точное: обе строки рисует :func:`render_goal_line`.
    """
    if not goal_block or goal_block in nutrition_block:
        return memory_block
    return "\n\n".join(part for part in (memory_block, goal_block) if part)
