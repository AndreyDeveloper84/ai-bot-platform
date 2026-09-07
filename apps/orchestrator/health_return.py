"""Возврат к тому, ради чего человек давал согласие на медданные (DRF-1547).

Решение владельца §37 п.5, дословно:

    Если требуется согласие на медицинские данные — он сначала объясняет
    причину и ведёт в профиль; **после согласия возвращает человека к
    дневнику**.

### Что было

Поток согласия оставлял человека В ПРОФИЛЕ. Он нажимал «Дневник питания»,
читал, зачем нужно отдельное согласие по 152-ФЗ, открывал профиль,
соглашался — и оставался на экране согласий. Чтобы увидеть дневник, ради
которого он всё это проделал, ему нужно было САМОМУ вспомнить, зачем он
шёл, закрыть приложение, вернуться в чат и нажать кнопку заново.

### Почему это нельзя починить в мини-приложении

Согласие выдаётся HTTP-запросом (``POST miniapp_api:health_consent``), а
дневник живёт В БОТЕ: чтение из Ayla, вода, уточнение, коррекция, «что я
ел» за день и за неделю. Мини-приложению возвращать некуда — экрана
дневника в нём нет вовсе, ручек ``customer/food/*`` не существует. Вернуть
можно только сообщением в чат.

### Как узнаётся, куда возвращать

Не по догадке и не по последней кнопке. Экран запроса согласия
записывается в переписку с меткой, НЕСУЩЕЙ ПОВЕРХНОСТЬ
(:func:`apps.skills.menu.marketplace.health_request_action_type` —
``menu_health_req:food_diary``). Эта метка и есть единственный след,
связывающий выдачу согласия с местом, откуда человек ушёл.

Читается ПОСЛЕДНЯЯ такая метка, и только если после неё в этом диалоге не
было ни отказа, ни уже случившегося возврата. Иначе повторный POST —
а он идемпотентен и приходит от любого повторного тапа «согласиться» —
присылал бы дневник второй раз.

### Границы

**Никогда не роняет выдачу согласия.** Согласие уже записано к моменту
вызова; исключение здесь означало бы 500 на запросе, который УСПЕЛ
сделать своё дело, и человек нажал бы «согласиться» ещё раз, думая, что
не получилось.

**Ничего не решает про согласие.** Дневник рендерится своим обычным
путём (:func:`apps.orchestrator.personal_surface.render_diary`) со своими
собственными воротами; этот модуль только доставляет ответ в чат.

**Одно исключение из «обычного пути» — и оно про лимиты, не про ворота.**
Дневник просится с :attr:`apps.orchestrator.coach_observation.Cadence.
UNTRACKED` (решение владельца §39): строка наблюдения диетолога, если она
здесь положена, показывается, но суточный слот не тратит.

Причина: человек не планировал этот заход как открытие дневника — он
нажал «согласиться». Потратить на приветствие слот значило бы отнять
наблюдение у захода, который человек спланирует сам, и он через час
получил бы тишину не потому, что сказать нечего.

Проверять это надо ВТОРЫМ заходом в те же сутки: первый зелёный и при
верной реализации, и при нарушении. См.
``apps/orchestrator/tests/test_coach_observation.py``,
``TestWelcomeOutsideLimits``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Метка доставленного возврата. Ставится на сообщение бота, чтобы второй
#: (идемпотентный) POST согласия не прислал дневник ещё раз.
HEALTH_RETURN_ACTION_TYPE = "menu_health_returned"

#: Какая поверхность чем возвращается. Таблица, а не ``if``: сканер еды
#: вернётся сюда строкой, когда у него появится рабочий экран, и вместе с
#: возвратом — а не через неделю после него.
_SURFACE_RENDERERS: dict[str, str] = {"food_diary": "diary"}


def resume_after_health_consent(bot_user: Any) -> bool:
    """Дослать в чат то, ради чего человек давал согласие. ``True`` — дослали.

    ``bot_user`` — строка, которой аутентифицировано мини-приложение. Она
    почти наверняка НЕ та, что ведёт чат: в пилоте у человека несколько
    ``BotUser`` (мини-апп резолвит свою, чат свою), и согласие поэтому
    person-level. Значит и адресата надо искать по человеку, а не по
    строке.

    Никогда не бросает.
    """
    try:
        return _resume(bot_user)
    except Exception:  # noqa: BLE001 — возврат не смеет ронять выдачу согласия
        logger.exception(
            "orchestrator.health_return.failed bot_user=%s", getattr(bot_user, "id", None)
        )
        return False


def _resume(bot_user: Any) -> bool:
    from apps.conversations.models import Message
    from apps.conversations.services import (
        record_global_message,
        resolve_active_global_conversation,
    )
    from apps.skills.menu.marketplace import (
        HEALTH_DECLINE_ACTION_TYPE,
        HEALTH_REQUEST_ACTION_TYPE_PREFIX,
    )

    for chat_user in _chat_bot_users(bot_user):
        conversation = resolve_active_global_conversation(chat_user, create_if_missing=False)
        if conversation is not None:
            break
    else:
        return False

    # Последние ходы бота, новейшие первыми. Потолок, а не весь диалог:
    # запрос согласия — это ход, за которым человек уходит в приложение и
    # возвращается минутами позже, а не через сотню реплик.
    rows = list(
        Message.all_tenants.filter(conversation=conversation, role="assistant")
        .order_by("-created_at")
        .values_list("action_type", flat=True)[:20]
    )
    surface = ""
    for action_type in rows:
        action_type = action_type or ""
        if action_type in {HEALTH_RETURN_ACTION_TYPE, HEALTH_DECLINE_ACTION_TYPE}:
            # Возврат уже был, либо человек отказался и передумал позже —
            # в обоих случаях досылать нечего: первый уже доставлен, а во
            # втором «Не сейчас» было последним словом об этом.
            return False
        if action_type.startswith(HEALTH_REQUEST_ACTION_TYPE_PREFIX):
            surface = action_type[len(HEALTH_REQUEST_ACTION_TYPE_PREFIX) :]
            break
    if _SURFACE_RENDERERS.get(surface) != "diary":
        return False

    from apps.orchestrator.coach_observation import Cadence
    from apps.orchestrator.personal_surface import render_diary

    # §39: приветственное слово показывается, но лимитов НЕ тратит —
    # суточный слот остаётся целым для захода, который человек сделает
    # сам. Категория, а не флаг: второй лимит, когда появится, ляжет в
    # тот же участок каданса, и приветствие окажется вне него без правок
    # здесь.
    reply = render_diary(chat_user, cadence=Cadence.UNTRACKED)
    text = (reply.text or "").strip()
    if not text:
        return False

    chat_id = str(getattr(chat_user, "chat_id", "") or "").strip()
    if not chat_id:
        return False

    from apps.channels.max.handler import _build_attachments
    from apps.channels.max.outbound import send_message

    send_message(chat_id=chat_id, text=text, attachments=_build_attachments(reply.action_data))
    record_global_message(
        conversation,
        role="assistant",
        content=text,
        action_type=HEALTH_RETURN_ACTION_TYPE,
        action_data=reply.action_data,
    )
    logger.info(
        "orchestrator.health_return.delivered surface=%s bot_user=%s",
        surface,
        getattr(chat_user, "id", None),
    )
    return True


def _chat_bot_users(bot_user: Any) -> list[Any]:
    """Строки ЧЕЛОВЕКА, у которых есть чат MAX, — своя первой.

    Резолв личности — тот же примитив, которым ходит выдача person-level
    согласия (``apps.identity.services.privacy.person_shell_ids``), чтобы
    «кто такой этот человек» имело в платформе один ответ.

    Берутся все строки с непустым ``chat_id`` (без него отправлять
    некуда), а не одна: аутентифицированная мини-приложением строка и та,
    что ведёт чат, — в пилоте разные, и угадать, какая из них несёт
    открытый диалог, отсюда нечем. Вызывающий перебирает их и берёт
    первую, у которой диалог есть.
    """
    from apps.identity.models import BotUser
    from apps.identity.services.privacy import person_shell_ids

    ids = person_shell_ids(bot_user)
    rows = list(BotUser.all_tenants.filter(id__in=ids, channel="max").exclude(chat_id=""))
    # Своя строка первой: чаще всего мини-приложение и чат это она же.
    rows.sort(key=lambda row: row.id != getattr(bot_user, "id", None))
    return rows
