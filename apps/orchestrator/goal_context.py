"""Цель человека — в промпт консьержа на каждом ходе (DRF-2808).

Когда человек называет цель словами, бот отвечает «Поняла. Буду учитывать:
{goal}» (``goal_capture.CONFIRMATION``, слова владельца 28.09). До этого листа
обещание не исполнялось: строка цели жила только внутри блока питания, а тот
подключается лишь на ходах про еду и при включённом коуче питания. На пилоте
цель до модели не доходила никогда.

Этот блок — цель в каждом ходе консьержа, рядом с памятью: цель — заявленный
самим человеком факт (не вывод Ф4), и едет под тем же согласием
``memory_green`` и тем же выключателем памяти.

### Два вида цели — два происхождения

* **Слова человека** (``goal_text``) — «Цель клиента своими словами: …».
  Приоритетнее всего: если слова есть, подпись опции не рисуется.
* **Выбрано кнопкой** (``goal_key`` без слов) — решение владельца 06.10,
  вариант (Б): курируемая подпись опции (``GoalOption.label``) с явным
  происхождением «выбрано из списка». Это НАША формулировка, не речь
  клиента, и модели прямо сказано не цитировать её как его слова — иначе
  она начнёт повторять слоган. Голый ключ (``more_energy``) не рисуется
  никогда: подписи нет — блока нет.

К любой цели — одно правило рулёжки: цель — ориентир, текущий запрос клиента
её уточняет или заменяет; выводов о здоровье и диагнозов из цели не делать.

### Что блок не пропускает

* **Медицинская цель** (``nutrition_wellness.goal_is_medical``): консьерж
  записывает к мастеру, а не ведёт к медицинской цели.
* **Чувствительный периметр** (беременность, ГВ, РПП, снятый дефицит —
  ``render.remarks_suppressed``). Два «нет профиля» разведены: «анкеты нет»
  (большинство бьюти-клиентов) — не периметр, цель показывается; «прочитать
  не удалось» — состояние неизвестно, блока нет (fail-closed): цель в промпте
  рулит советом модели.

Профиль запрашивается только когда рисовать есть что. Сбой любого шага —
пустой блок: ход дороже картины.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Правило рулёжки при любой цели (решение владельца 06.10, вариант Б).
GOAL_STEERING_RULE = (
    "Цель — ориентир, а не правило: если клиент сейчас просит другое или уточняет "
    "цель, следуй его текущим словам. Не делай из цели выводов о здоровье и не "
    "называй диагнозов."
)


def render_goal_line(goal: Any) -> str:
    """Цель словами человека → строка промпта. ``""``, когда слов нет.

    Одна функция на оба места, где слова цели попадают в промпт (этот блок и
    блок питания): строки обязаны совпадать до символа — по этому совпадению
    вызывающий убирает дубль.
    """
    text = (getattr(goal, "text", None) or "").strip() if goal is not None else ""
    if not text:
        return ""
    return f"Цель клиента своими словами: {text}"


def render_chosen_goal_line(goal: Any) -> str:
    """Цель, выбранная кнопкой → строка с происхождением. ``""`` без подписи.

    Только курируемая подпись опции (``Goal.label``), никогда ключ.
    """
    label = (getattr(goal, "label", None) or "").strip() if goal is not None else ""
    if not label:
        return ""
    return (
        f"Цель клиента выбрана им из списка: «{label}». Это наша формулировка, а не "
        "его слова — не цитируй её как слова клиента."
    )


def build_goal_block(bot_user: Any) -> str:
    """Блок цели для системного промпта консьержа, или ``""``.

    ``""`` — во всех закрытых случаях: выключатель памяти, нет согласия
    ``memory_green`` или связки с Ayla, нет цели (ни слов, ни подписи),
    медицинская цель, чувствительный периметр, сбой чтения профиля, любой
    сбой.
    """
    try:
        from apps.identity.services.personal_context import memory_green_open
        from apps.orchestrator.memory_block import concierge_memory_enabled

        if not concierge_memory_enabled() or not memory_green_open(bot_user):
            return ""

        from apps.orchestrator.nutrition_context import _fetch_goal
        from apps.orchestrator.nutrition_wellness import goal_is_medical

        goal = _fetch_goal(bot_user)
        # Слова человека приоритетнее подписи опции.
        line = render_goal_line(goal) or render_chosen_goal_line(goal)
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
        return f"{line}\n{GOAL_STEERING_RULE}"
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
    """Положить блок цели рядом с памятью — если блок питания его строку уже несёт, нет.

    Блок питания ставит слова цели первыми как рамку для чисел, и на ходах про
    еду строка была бы в промпте дважды. Сравнивается первая строка блока —
    сама цель; совпадение точное: слова рисует :func:`render_goal_line` в обоих
    местах. Подпись выбранной опции блок питания не рисует — её дубля не бывает.
    """
    if not goal_block:
        return memory_block
    if goal_block.splitlines()[0] in nutrition_block:
        return memory_block
    return "\n\n".join(part for part in (memory_block, goal_block) if part)
