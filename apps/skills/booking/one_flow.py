"""Один поток записи: где спрашивать время (DRF-2178, Э-4).

Этап 4 из 4. Макет DRF-1320 описывает ОДИН поток — в приложении. До
этого среза их было два: свой пошаговый выбор даты и части суток в чате
(`skill.py::_render_date_picker` и соседи) и кадры Mini App. Два места,
где у человека спрашивают время, и есть два потока, даже если лежат в
одном модуле.

## Чат остаётся там, где в приложение не войти

Снять чатовый выбор целиком нельзя, и это не осторожность, а факт:
`web_app` и `miniapp_url` берутся из реестра ботов
(`apps.channels.miniapp_config.miniapp_target`) и у бота МОГУТ БЫТЬ
ПУСТЫ. Там кнопке `open_app` взяться неоткуда — и чат единственный путь
записи. Снять его значило бы отнять запись у такого развёртывания.

Поэтому правило звучит так: **чатовый пошаговый выбор — только там, где
в приложение не войти.** Это не отступление от макета, а область за его
границей: он описывает путь с приложением и про развёртывание без него
не говорит ничего.

## Модуль решает ГДЕ, а не КАК

Здесь только предикат и вход в приложение. Рендер чатовых чипов остаётся
там, где был, и не переезжает: этот шов должен быть узким, иначе этап,
и так самый большой по радиусу, перепишет половину навыка заодно.

Решает ОДНО место. Две ветки, каждая со своим «а есть ли приложение»,
разъехались бы на первой же правке; сторож на это стоит отрицательной
парой — подмени предикат, и ответ обязан измениться.
"""

from __future__ import annotations

from typing import Any

#: Текст входа в приложение. Макет кадра 2 говорит с человеком уже ВНУТРИ
#: приложения, а эта строка — приглашение туда, которого на макете нет.
#: Названо отступлением в теле PR; коротко и без обещаний.
#:
#: Имени специалиста здесь нет намеренно. Достать его можно только
#: лишним чтением ростера, а шов этого этапа обязан остаться узким:
#: человек только что сам выбрал специалиста и помнит, кого выбрал.
ENTRY_TEXT = "Открой приложение — там выберешь время."
OPEN_LABEL = "Выбрать время"

#: Payload кнопки: префикс + id специалиста. Двоеточий грамматика
#: `OPEN_APP_PAYLOAD_RE` не знает, отсюда префикс, а не разделитель.
PROVIDER_PAYLOAD_PREFIX = "provider_"


def chat_step_by_step_allowed() -> bool:
    """Спрашивать ли время в чате — единственное место, где это решается.

    ``True`` ровно тогда, когда в приложение не войти: у бота нет ни
    `web_app`, ни адреса Mini App. Тогда прежний пошаговый выбор — не
    дубль, а единственный путь.

    Реестр недоступен — тоже ``True``: остаться без способа записаться
    хуже, чем показать второй способ.
    """
    from apps.channels.miniapp_config import miniapp_target

    try:
        target = miniapp_target()
    except Exception:  # noqa: BLE001 — реестр молчит: чат остаётся путём
        return True
    return not (target.web_app or target.miniapp_url)


def miniapp_entry_button(*, master_id: Any) -> dict[str, str] | None:
    """Кнопка входа в приложение с уже выбранным специалистом.

    ``None`` — войти некуда; вызывающий обязан остаться на чатовом пути.
    Это тот же случай, что делает :func:`chat_step_by_step_allowed`
    истинной, и проверяется он тем же чтением реестра.
    """
    from apps.channels.miniapp_config import miniapp_target
    from apps.skills.welcome.skill import MINIAPP_ROUTES, _miniapp_url

    slug = "open_catalog"
    if slug not in MINIAPP_ROUTES:
        return None
    target = miniapp_target()
    if target.web_app:
        return {
            "label": OPEN_LABEL,
            "callback": f"{PROVIDER_PAYLOAD_PREFIX}{master_id}",
            "web_app": target.web_app,
        }
    if target.miniapp_url:
        return {"label": OPEN_LABEL, "url": _miniapp_url(target.miniapp_url, slug)}
    return None


def miniapp_entry_result(*, master_id: Any) -> Any:
    """`SkillResult` со входом в приложение — или ``None``, если войти некуда.

    Тип тот же, что у чатового рендера, чтобы вызывающая ветка навыка
    возвращала одно и то же и не заводила второй способ ответить.
    """
    from apps.orchestrator.discovery import keyboard_envelope
    from apps.skills.booking.skill import _build_skill_result

    button = miniapp_entry_button(master_id=master_id)
    if button is None:
        return None
    # `action_type` у строителя один — «booking»; заводить второй ради
    # этого входа не стану: тип действия тут тот же, меняется только то,
    # куда ведёт кнопка.
    return _build_skill_result(
        text=ENTRY_TEXT,
        tool_calls_made=[],
        confidence=None,
        action_data=keyboard_envelope([button]),
    )


__all__ = [
    "ENTRY_TEXT",
    "OPEN_LABEL",
    "PROVIDER_PAYLOAD_PREFIX",
    "chat_step_by_step_allowed",
    "miniapp_entry_button",
    "miniapp_entry_result",
]
