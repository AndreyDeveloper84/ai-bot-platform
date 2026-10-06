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
каждый ход платил бы поход в Ayla за ничего. «Анкеты нет» (у большинства
бьюти-клиентов) — не периметр, цель показывается; «прочитать не удалось» —
состояние неизвестно, и блока нет (fail-closed): цель в промпте рулит
советом модели.

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

        from apps.orchestrator.nutrition_context import _fetch_goal
        from apps.orchestrator.nutrition_wellness import goal_is_medical

        goal = _fetch_goal(bot_user)
        line = render_goal_line(goal)
        if not line or goal_is_medical(goal):
            return ""

        from apps.nutrition_proactive.render import remarks_suppressed

        # Периметр — два разных «нет профиля», и они НЕ одно:
        # * ``None`` — анкеты нет (обычный бьюти-клиент): флагов нет, это вне
        #   периметра, цель показываем;
        # * чтение сорвалось — состояние неизвестно: fail-closed, цели нет.
        #   Цель в промпте рулит советом модели, и человек в периметре
        #   (беременность, РПП) не должен получить разговор к противопоказанной
        #   цели только потому, что каталог не ответил.
        try:
            profile = _read_profile(bot_user)
        except Exception:  # noqa: BLE001 — не знаем состояние → ограничительное
            logger.info("orchestrator.goal_context.skip reason=profile_unreadable")
            return ""
        if profile is not None and remarks_suppressed(profile):
            logger.info("orchestrator.goal_context.skip reason=sensitive_perimeter")
            return ""
        return line
    except Exception:  # noqa: BLE001 — ход дороже картины
        logger.exception("orchestrator.goal_context.failed")
        return ""


def _read_profile(bot_user: Any) -> Any | None:
    """Анкета питания: профиль, ``None`` — анкеты нет; исключение — не прочитали.

    В отличие от ``nutrition_context._fetch_profile`` сбой НЕ сводится к
    ``None``: там ``None`` и так значит «закрыто», здесь — «анкеты нет», и
    смешать его со сбоем значило бы впустить цель вслепую. Ayla отвечает
    ``None`` ровно на отсутствие (404 / ``exists=false``); неподключённое
    окружение и недоступность — исключения.
    """
    import asyncio

    from apps.integrations.ayla import external_user_id_for, get_nutrition_client

    client = get_nutrition_client()
    external_id = external_user_id_for(bot_user)
    return asyncio.run(client.get_profile(external_user_id=external_id))


def merge_goal_into_memory(memory_block: str, goal_block: str, nutrition_block: str) -> str:
    """Положить строку цели рядом с памятью — если блок питания её уже не несёт.

    Блок питания ставит цель первой как рамку для чисел (там она нужна у
    чисел), и на ходах про еду строка была бы в промпте дважды. Совпадение
    точное: обе строки рисует :func:`render_goal_line`.
    """
    if not goal_block or goal_block in nutrition_block:
        return memory_block
    return "\n\n".join(part for part in (memory_block, goal_block) if part)
