"""Главное меню ВИТРИНЫ — клиентского (глобального) бота (DRF-1491).

Это новый экран, а не починка старого. Существующие
:data:`apps.skills.menu.replies.HELP_TEXT` и
:data:`~apps.skills.menu.replies.FALLBACK_TEXT` остаются на месте и
принадлежат САЛОННОМУ пути: их текст назван салоном «Формула тела» и
перечисляет ровно то, что умеет один салон. Витрина — другой бот с
другим составом возможностей, и переименовать салонную копию значило бы
сломать салон ради витрины.

### Почему меню живёт здесь, а не в реестре навыков

Реестр навыков (:mod:`apps.skills.registry`) диспетчеризуется только на
арендаторском пути (``handler.py``, ``SURFACE_PER_TENANT``); клиентский
бот идёт по ``SURFACE_GLOBAL`` и зовёт консьержа. Затащить весь реестр на
глобальный путь ради одной точки меню несоразмерно: реестр тянет
tenant-scoped навыки, у которых на витрине нет ни тенанта, ни каталога.
Поэтому здесь только ЧИСТЫЕ построители (текст + кнопки), а ветка в
лестнице глобального обработчика зовёт их напрямую — тем же приёмом,
которым ``global_onboarding`` переиспользует ``WelcomeSkill`` без
диспетчера.

### Смешанное меню (решение владельца, ``docs/OPEN_DECISIONS.md`` §25)

**В боте отвечает разговорное** — найти услугу, показать салоны,
записаться, свои записи, перенос и отмена. Всё это уже работает на
глобальном пути: тап переводится в каноническую фразу
(:data:`apps.skills.menu.matching.MENU_CALLBACK_TEXT`), а фраза едет по
той же лестнице, что и набранный текст.

**В миниапп уводит экранное** — профиль, цель, полный каталог, история
визитов, питание. У этих пунктов нет разговорного эквивалента: это
экраны, а не реплики.

### Вырождение конфигурации

Образец взят у приветствия
(:func:`apps.skills.welcome.skill._welcome_buttons`): есть
``MAX_BOT_WEB_APP`` — кнопка ``open_app``; нет, но есть
``MAX_MINIAPP_URL`` — внешняя ссылка; нет ничего — кнопки НЕТ, а ботовая
часть меню живёт. Отличие одно и намеренное: перечень в ТЕКСТЕ
собирается из тех же списков, что и кнопки, поэтому меню без миниаппа не
обещает экранов, которых человек не откроет.

### Питание — двое ворот, а не одни

``NUTRITION_ENABLED`` и согласие ``HEALTH`` — разные условия
(§25 п.6, решение владельца 05.09.2026 дословно: «видит и попадает на
запрос согласия»):

======================  ===================  ===================================
``NUTRITION_ENABLED``   согласие ``HEALTH``  что видит человек
======================  ===================  ===================================
не задан                любое                пунктов питания нет вовсе
задан                   нет                  пункт есть, тап на ЗАПРОС согласия
задан                   есть                 пункт есть, тап в поверхность
======================  ===================  ===================================

Мёртвой кнопки не бывает ни в одной строке: либо пункта нет, либо тап
куда-то ведёт. По этому же признаку из состава снята панель самочувствия
— см. комментарий у :data:`NUTRITION_ITEMS`.

Ворота проверяются ДВАЖДЫ: при отрисовке (что человек видит) и на тапе
(``handler._route_health_callback``). Клавиатура живёт в истории чата
дольше, чем флаг в окружении, и тап по кнопке, нарисованной вчера, не
должен просить согласие на особую категорию персданных ради поверхности,
выключенной сегодня.

Отказ уважается (канон 2.5 «без понуканий», 2.6 «автономия клиента
абсолютна»): «Не сейчас» возвращает человека в меню, и повторного ЗАПРОСА
в том же диалоге он не получает — следующий тап по пищевому пункту
отвечает объяснением без кнопок согласия
(:data:`HEALTH_DECLINED_EARLIER_TEXT`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from apps.orchestrator.discovery import CALLBACK_CATALOG_SALONS
from apps.skills.menu.matching import (
    CALLBACK_MENU_BOOK,
    CALLBACK_MENU_CANCEL,
    CALLBACK_MENU_MY_BOOKINGS,
    CALLBACK_MENU_RESCHEDULE,
)

# ---------------------------------------------------------------------------
# Колбэки согласия на медданные
# ---------------------------------------------------------------------------

#: Своё семейство, а НЕ ``cb:menu:``. ``resolve_tap_text`` перехватывает
#: весь ``cb:menu:*`` и переводит незнакомый слаг в «Что ты умеешь?»
#: (DRF-1051) — пищевой пункт превратился бы в тап по «Помощи», и человек
#: никогда бы не увидел запроса согласия. Ни одна ветка лестницы
#: ``cb:health:`` сегодня не занимает.
HEALTH_CALLBACK_PREFIX = "cb:health:"

#: «Пункт питания, на который нет согласия»: ``cb:health:need:{surface}``.
CALLBACK_HEALTH_NEED_PREFIX = "cb:health:need:"

#: «Не сейчас» на экране запроса согласия.
CALLBACK_HEALTH_DECLINE = "cb:health:decline"

#: ``action_type`` отказа. По его наличию в ЭТОМ диалоге определяется,
#: что запрос уже был и повторять его нельзя. Таблица сообщений, а не
#: короткая память: у короткой памяти TTL, а «тот же диалог» живёт
#: дольше. Влезает в ``Message.action_type`` (max_length=32).
HEALTH_DECLINE_ACTION_TYPE = "menu_health_declined"

#: ``action_type`` самого экрана запроса — чтобы оператор, читающий
#: переписку, отличал служебный экран от реплики бота.
HEALTH_REQUEST_ACTION_TYPE = "menu_health_request"

#: ``action_type`` меню и ветки «не поняла».
MENU_ACTION_TYPE = "marketplace_menu"
FALLBACK_ACTION_TYPE = "marketplace_fallback"


# ---------------------------------------------------------------------------
# Состав меню
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MenuItem:
    """Один пункт меню и то, ГДЕ он отвечает.

    ``callback`` — payload ботовой кнопки (``where="bot"``) либо слаг
    маршрута мини-приложения (``where="miniapp"``). Разделение не
    косметическое: ботовый payload едет в лестницу глобального
    обработчика, слаг — в ``MINIAPP_ROUTES``, и перепутать их значит
    нарисовать кнопку, ведущую в никуда.

    ``line`` — строка перечня в тексте меню. Живёт рядом с кнопкой
    нарочно: собирать текст отдельно значит однажды пообещать пункт,
    которого на клавиатуре нет.
    """

    label: str
    callback: str
    line: str
    where: str


#: Разговорная половина. Каждый payload УЖЕ принимается глобальным путём:
#: четыре ``cb:menu:*`` переводятся в каноническую фразу
#: (``quick_actions.resolve_tap_text`` — ``MENU_CALLBACK_TEXT``), а
#: ``cb:catalog:salons`` — безрефовый колбэк списка салонов (DRF-1492,
#: ``execute_catalog_callback``). Ничего нового принимать не требуется —
#: это и есть свойство «принятое совпадает с рисуемым».
BOT_ITEMS: tuple[MenuItem, ...] = (
    MenuItem(
        label="📅 Записаться",
        callback=CALLBACK_MENU_BOOK,
        line="записаться к мастеру — подберу по услуге и времени",
        where="bot",
    ),
    MenuItem(
        label="🔍 Показать салоны",
        callback=CALLBACK_CATALOG_SALONS,
        line="показать салоны и мастеров",
        where="bot",
    ),
    MenuItem(
        label="📋 Мои записи",
        callback=CALLBACK_MENU_MY_BOOKINGS,
        line="показать ваши ближайшие визиты",
        where="bot",
    ),
    MenuItem(
        label="🔄 Перенести запись",
        callback=CALLBACK_MENU_RESCHEDULE,
        line="перенести запись на другое время",
        where="bot",
    ),
    MenuItem(
        label="❌ Отменить запись",
        callback=CALLBACK_MENU_CANCEL,
        line="отменить запись",
        where="bot",
    ),
)

#: Экранная половина. ``callback`` здесь — слаг из
#: :data:`apps.skills.welcome.skill.MINIAPP_ROUTES`; тест
#: ``apps/skills/welcome/tests/test_miniapp_routes.py`` не даёт слагу
#: разъехаться с маршрутом SPA и с ``_ROUTE_MAP`` мини-приложения.
MINIAPP_ITEMS: tuple[MenuItem, ...] = (
    MenuItem(
        label="👤 Профиль",
        callback="open_profile",
        line="профиль и настройки",
        where="miniapp",
    ),
    MenuItem(
        label="🎯 Моя цель",
        callback="open_goal_select",
        line="выбрать цель — к чему идём",
        where="miniapp",
    ),
    MenuItem(
        label="📖 Каталог услуг",
        callback="open_catalog",
        line="каталог услуг целиком",
        where="miniapp",
    ),
    MenuItem(
        label="🗓 История визитов",
        callback="open_visits",
        line="история визитов",
        where="miniapp",
    ),
)

#: Пищевая половина — те же экраны, но за двумя воротами (см. докстринг
#: модуля). ``callback`` — слаг маршрута; когда согласия нет, кнопка
#: строится не по слагу, а по ``cb:health:need:{surface}``.
NUTRITION_ITEMS: tuple[MenuItem, ...] = (
    MenuItem(
        label="📸 Сканер еды",
        callback="open_food_scan",
        line="сканер еды — снять тарелку и увидеть состав",
        where="miniapp",
    ),
    MenuItem(
        label="📔 Дневник питания",
        callback="open_food_diary",
        line="дневник питания",
        where="miniapp",
    ),
)

# ПАНЕЛИ САМОЧУВСТВИЯ здесь намеренно НЕТ, хотя §25 её называет.
#
# Замер, а не осторожность: `/customer/wellness` в прод-сборке первым же
# действием возвращает `PilotComingSoonScreen`
# (``apps/miniapp/src/screens/CustomerWellnessDashboardScreen.tsx``,
# сторож ``STUB_SURFACES_ENABLED = import.meta.env.DEV`` из
# ``apps/miniapp/src/lib/feature-flags.ts``). Собственный комментарий
# флага называет это «hidden until S4/post-pilot» — то есть экран не
# строится прямо сейчас, а отложен.
#
# Пункт вёл бы человека к плейтхолдеру — и не даром: пищевые пункты
# стоят за согласием на медданные, самым чувствительным, какое бот умеет
# просить. §25 п.6 запрещает вести тап в мёртвую кнопку; взять за неё
# согласие на особую категорию персданных — тем более.
#
# Прецедент в этом же репозитории тот же: комментарий в
# ``apps.skills.welcome.skill.MINIAPP_ROUTES`` объясняет, что кнопку
# «📸 Сфотографировать еду» сняли с первого экрана ровно потому, что
# «the screen it opens fails at the moment of use».
#
# Сканер и дневник оставлены: их экраны настоящие и не загорожены
# флагом, а незаведённые ручки (``guardProd`` в
# ``apps/miniapp/src/lib/food-scanner.ts``) — работа в текущей неделе, и
# честный текст «ещё не подключён» человек читает вместо выдуманных
# данных. Граница проведена по одному признаку: отложено — снимаем,
# делается — оставляем. Вернуть пункт — одна запись в этом кортеже.


# ---------------------------------------------------------------------------
# Тексты
# ---------------------------------------------------------------------------

_INTRO = (
    "Я Ayla 👋 Помогаю найти мастера, записаться — и поддерживаю между визитами.\n\n"
    "Вот что можно прямо здесь, в переписке:"
)

_MINIAPP_HEADER = "А это открывается отдельным экраном:"

_OUTRO = (
    "Можно нажать кнопку — или просто написать своими словами, "
    "например «хочу массаж спины» или «когда у меня запись?».\n"
    "Нужен живой человек — напишите «оператор»."
)

#: Хвост КОРОТКОЙ рамки — той, что показывается после промаха.
#:
#: Отдельный от :data:`_OUTRO` не ради краткости самой по себе. Реплики
#: платформы держат потолок длины (``voice_check.max_length`` золотых
#: фикстур ``apps/replay/fixtures/golden``, 300 символов на негативных
#: случаях), и экран промаха под него обязан влезать: человек, которого
#: не поняли, читает извинение и хочет действия, а не второй абзац. Всё,
#: что не поместилось, при этом никуда не делось — оно на кнопках, и
#: клавиатура у промаха та же, что у меню.
_FALLBACK_OUTRO = "Нажмите кнопку или напишите своими словами."

#: Ветка «не поняла» ВИТРИНЫ. Отличается от салонной
#: :data:`apps.skills.menu.replies.FALLBACK_TEXT` не только рамкой: там
#: перечень услуг одного салона, здесь — возможности маркетплейса.
#: Сказанное человеком не повторяется никогда (U-5).
FALLBACK_INTRO = "Я пока не поняла 🤔\n\nВот с чем точно помогу — прямо здесь, в переписке:"

#: Запрос согласия на медданные. Не сама поверхность и не мёртвая кнопка:
#: экран объясняет, ЗАЧЕМ нужно согласие, и даёт оба выхода.
#:
#: Формулировка держит два свойства :mod:`apps.consent.health`:
#: раздельность (это НЕ то согласие, что дано на первом контакте) и
#: обратимость (сказано, что отозвать можно там же).
HEALTH_REQUEST_TEXT = (
    "Это про еду и самочувствие — а такие данные закон относит к особой "
    "категории (152-ФЗ, ст. 10).\n\n"
    "Поэтому нужно отдельное согласие: то, что вы дали при знакомстве, его не "
    "заменяет.\n"
    "Отозвать можно в любой момент — там же, где дали.\n\n"
    "Открыть профиль и решить?"
)

#: Ответ на «Не сейчас». Возврат назад, без уговоров и без второго
#: захода (канон 2.5 / 2.6).
HEALTH_DECLINED_TEXT = (
    "Хорошо, оставляем как есть — больше не спрошу.\nВсё остальное работает по-прежнему."
)

#: Повторный тап по пищевому пункту после отказа В ЭТОМ ЖЕ ДИАЛОГЕ.
#: Объяснение без кнопок согласия: спросить второй раз нельзя, а не
#: сделать ничего — значит вернуть мёртвую кнопку.
HEALTH_DECLINED_EARLIER_TEXT = (
    "Сюда пока не пущу: без согласия на данные о здоровье эти экраны не работают.\n"
    "Захотите передумать — согласие живёт в профиле, я про него больше не напоминаю."
)

#: Не удалось прочитать, спрашивали ли человека раньше.
#:
#: Отдельная строка, а не :data:`HEALTH_DECLINED_EARLIER_TEXT`. Сторож
#: при сбое чтения толкует сомнение в пользу «не спрашивать» — это
#: правильно, — но сказать при этом «я про него больше не напоминаю»
#: человеку, который НИКОГДА не отказывался, значит утверждать про него
#: неправду. Осторожность в поведении не обязана быть враньём в словах.
HEALTH_CHECK_FAILED_TEXT = "Сейчас не получилось это открыть — попробуйте, пожалуйста, чуть позже."


# ---------------------------------------------------------------------------
# Распознавание «покажи меню»
# ---------------------------------------------------------------------------

#: Целые фразы, а не подстроки. Порог намеренно высокий: ветка стоит
#: ВЫШЕ консьержа, и всё, что она заберёт лишнего, человек получит
#: оглавлением вместо ответа на свой вопрос. «Помоги мне выбрать массаж»
#: здесь не матчится и уезжает к консьержу, как и раньше.
_MENU_PHRASES: frozenset[str] = frozenset(
    {
        "что ты умеешь",
        "что вы умеете",
        "что ты можешь",
        "что вы можете",
        "что умеешь",
        "что можешь",
        "чем ты можешь помочь",
        "чем можешь помочь",
        "какие у тебя возможности",
        "твои возможности",
        "помощь",
        "help",
        "меню",
        "главное меню",
        "menu",
        "/help",
        "/menu",
    }
)

#: Снимается ТОЛЬКО хвостовая пунктуация. Полная нормализация
#: (``matching.normalize``) схлопывает все знаки — а «/help» и «/menu»
#: именно знаком и опознаются, так что она бы их обезличила.
_PUNCT_TAIL = re.compile(r"[!?.…,\s]+$")


def matches_menu_request(text: str) -> bool:
    """Целиком ли это сообщение — просьба показать возможности.

    True только для сообщения, КОТОРОЕ ЦЕЛИКОМ является такой просьбой.
    Это не строгость ради строгости: ветка стоит перед консьержем, и
    подстрочный матч («…а ещё что ты умеешь по маникюру?») подменил бы
    человеку содержательный ответ оглавлением.
    """
    stripped = (text or "").strip().lower().replace("ё", "е")
    stripped = _PUNCT_TAIL.sub("", stripped)
    if not stripped:
        return False
    return stripped in _MENU_PHRASES


# ---------------------------------------------------------------------------
# Разбор тапа семейства ``cb:health:``
# ---------------------------------------------------------------------------


#: Строгая форма payload'а семейства: сегменты из ``[a-z0-9_]``.
#: Форма, а не префикс — по той же причине, что у приветствия
#: (``global_onboarding._WELCOME_CALLBACK_RE``): человек может набрать
#: «cb:health: …» руками, и это его слова, а не тап.
_HEALTH_CALLBACK_RE = re.compile(r"^cb:health:[a-z0-9_]+(?::[a-z0-9_]+)?$")


def is_health_callback(text: str) -> bool:
    """True для тапа семейства ``cb:health:`` — по ФОРМЕ, не по префиксу.

    Маршрутизация и персистенс обязаны решать одинаково: если ветка
    лестницы заберёт ход по префиксу, а резолвер истории откажется по
    форме, набранная человеком строка «cb:health: что это?» уедет в
    экран согласия, а в переписке останется дословно — два разных ответа
    на один ход.
    """
    return bool(_HEALTH_CALLBACK_RE.match((text or "").strip()))


def health_need_surface(text: str) -> str | None:
    """Слаг поверхности из ``cb:health:need:{surface}``; None — не он.

    Незнакомый слаг возвращает None НАМЕРЕННО: снятый пункт меню, чей
    payload остался в истории чата, не должен открывать экран согласия
    ради поверхности, которой больше нет.
    """
    stripped = (text or "").strip()
    if not stripped.startswith(CALLBACK_HEALTH_NEED_PREFIX):
        return None
    slug = stripped[len(CALLBACK_HEALTH_NEED_PREFIX) :]
    known = {_surface_slug(item) for item in NUTRITION_ITEMS}
    return slug if slug in known else None


def _surface_slug(item: MenuItem) -> str:
    """``open_food_scan`` — ``food_scan``. Payload короче и читаемее."""
    return item.callback.removeprefix("open_")


@dataclass(frozen=True)
class HealthTap:
    """Разбор тапа ``cb:health:`` глазами ИСТОРИИ диалога.

    Та же форма, что у :class:`apps.channels.max.global_onboarding.WelcomeTap`
    и у анкеты: ``history_text`` — фраза, которой тап является как реплика,
    либо ``None``, если подставить нечего и в историю не идёт ничего.
    """

    history_text: str | None


#: Что тап семейства значит как РЕПЛИКА человека.
#:
#: Выбран перевод в фразу, а не молчание, и довод тот же, которым он
#: выдан приветствию (``global_onboarding``): бот отвечает на эти тапы
#: экраном про медданные и обещанием «больше не спрошу», и без реплики
#: человека рядом это читается как решение, принятое ботом самим. Метка
#: нажатой кнопки И ЕСТЬ то, чем тап был как высказывание.
#:
#: Без этой таблицы сырой ``cb:health:need:food_scan`` ложился бы в
#: ``Message`` и в короткую память с ролью ``user`` — то есть в промпт
#: консьержа, у которого есть нутриционные инструменты. Модель охотно
#: истолковала бы это как «человек просил про еду» и подняла бы тему
#: медданных сразу после обещания её не поднимать.
HEALTH_TAP_TEXT: dict[str, str] = {
    CALLBACK_HEALTH_DECLINE: "Не сейчас",
}


def health_tap_text() -> dict[str, str]:
    """Полная таблица «payload — фраза», включая пищевые пункты.

    Собирается из :data:`NUTRITION_ITEMS`, а не переписывается рядом:
    добавленный пункт обязан получить свою фразу вместе с кнопкой, иначе
    его payload уедет в историю сырым.
    """
    return {
        **HEALTH_TAP_TEXT,
        **{
            f"{CALLBACK_HEALTH_NEED_PREFIX}{_surface_slug(item)}": item.label
            for item in NUTRITION_ITEMS
        },
    }


def resolve_health_tap(text: str) -> HealthTap | None:
    """Разобрать тап ``cb:health:``; ``None`` — «это не тап семейства».

    ``None`` означает «обычное сообщение»: вызывающий не трогает ни текст
    хода, ни персистенс. Разбирается ФОРМА, а не префикс — человек может
    НАБРАТЬ «cb:health: …» руками, и подменять ему его собственные слова
    нельзя (правило C01).

    Снятый пункт правильной формы даёт ``HealthTap(None)``: чем кнопка
    была, восстановить нечем, а сырой ``cb:`` в истории — ровно тот
    дефект, который здесь и закрывается.
    """
    stripped = (text or "").strip()
    if not _HEALTH_CALLBACK_RE.match(stripped):
        return None
    return HealthTap(history_text=health_tap_text().get(stripped))


# ---------------------------------------------------------------------------
# Построение клавиатуры
# ---------------------------------------------------------------------------


def _miniapp_button(item: MenuItem, *, web_app: str, miniapp_url: str) -> dict[str, str] | None:
    """Кнопка экрана — или None, когда мини-приложение не настроено.

    Лестница вырождения ровно приветственная
    (``welcome.skill._welcome_buttons``): ``open_app``, внешняя ссылка,
    ничего. Третий случай не ошибка: ботовая половина меню от него не
    зависит и продолжает работать.
    """
    if web_app:
        return {"label": item.label, "callback": item.callback, "web_app": web_app}
    if miniapp_url:
        from apps.skills.welcome.skill import _miniapp_url

        return {"label": item.label, "url": _miniapp_url(miniapp_url, item.callback)}
    return None


def _config() -> tuple[str, str]:
    from django.conf import settings

    return (
        getattr(settings, "MAX_BOT_WEB_APP", "") or "",
        getattr(settings, "MAX_MINIAPP_URL", "") or "",
    )


def nutrition_enabled() -> bool:
    """Первые ворота: мастер-выключатель питания.

    Читается тем же способом, что и в ``apps.skills.food_scanner.skill``
    — ``getattr`` с ``False`` по умолчанию, — чтобы «не задан» на обеих
    сторонах значило одно и то же.
    """
    from django.conf import settings

    return bool(getattr(settings, "NUTRITION_ENABLED", False))


def health_granted(bot_user: Any) -> bool:
    """Вторые ворота: действующее согласие ``HEALTH`` у ЭТОГО человека.

    Тот же предикат, которым ходит сторож нутриционной поверхности
    (:func:`apps.consent.health.is_granted`), — чтобы меню не могло
    показать вход туда, куда поверхность откажет.

    Fail-closed: любая ошибка чтения — это «согласия нет», то есть тап
    приведёт на запрос согласия. Ошибиться в другую сторону значило бы
    открыть медданные по сбою БД.
    """
    from apps.consent.health import is_granted

    try:
        return bool(is_granted(bot_user))
    except Exception:  # noqa: BLE001 — сторож согласия обязан быть fail-closed
        return False


def marketplace_menu_buttons(*, bot_user: Any) -> list[dict[str, str]]:
    """Клавиатура главного меню витрины для КОНКРЕТНОГО человека.

    Состав зависит от трёх вещей и ни от чего больше: настроено ли
    мини-приложение, задан ли ``NUTRITION_ENABLED``, есть ли согласие
    ``HEALTH``.
    """
    web_app, miniapp_url = _config()
    buttons: list[dict[str, str]] = [
        {"label": item.label, "callback": item.callback} for item in BOT_ITEMS
    ]
    for item in MINIAPP_ITEMS:
        button = _miniapp_button(item, web_app=web_app, miniapp_url=miniapp_url)
        if button is not None:
            buttons.append(button)
    buttons.extend(_nutrition_buttons(bot_user=bot_user, web_app=web_app, miniapp_url=miniapp_url))
    return buttons


def _nutrition_buttons(*, bot_user: Any, web_app: str, miniapp_url: str) -> list[dict[str, str]]:
    """Пищевые пункты по таблице §25 п.6.

    Первая строка таблицы («флаг не задан») отдаёт пустой список — пункта
    нет вовсе. Вторая («флаг задан, согласия нет») отдаёт ОБЫЧНУЮ
    callback-кнопку в ``cb:health:need:*``: тап ведёт на запрос согласия,
    а не в поверхность и не в пустоту. Третья отдаёт ту же кнопку, что и
    любой другой экранный пункт.

    Без настроенного мини-приложения пищевых пунктов нет ни в одной
    строке: и согласие, и сами поверхности живут в мини-приложении, так
    что кнопка была бы обещанием экрана, который негде открыть.
    """
    if not nutrition_enabled():
        return []
    if not (web_app or miniapp_url):
        return []
    if health_granted(bot_user):
        out: list[dict[str, str]] = []
        for item in NUTRITION_ITEMS:
            button = _miniapp_button(item, web_app=web_app, miniapp_url=miniapp_url)
            if button is not None:
                out.append(button)
        return out
    return [
        {
            "label": item.label,
            "callback": f"{CALLBACK_HEALTH_NEED_PREFIX}{_surface_slug(item)}",
        }
        for item in NUTRITION_ITEMS
    ]


def _menu_action_data(buttons: list[dict[str, str]], *, kind: str) -> dict[str, Any]:
    """Плоская форма ``action_data`` — та, что несёт ключи кнопок целиком.

    НЕ платформенный конверт ``attachments`` (его строит
    ``matching.main_menu_action_data`` для салонного пути): конверт
    прогоняется через ``make_inline_keyboard_attachment`` без колонок, а
    половина этого меню — кнопки с ``web_app`` / ``url``, которым нужен
    полный словарь. Плоскую форму читает ветка (2)
    ``handler._build_attachments`` и передаёт словари в ``_button_to_max``
    как есть.
    """
    return {"buttons": buttons, "button_columns": 2, "kind": kind}


def _lines(items: tuple[MenuItem, ...]) -> list[str]:
    return [f"• {item.line}" for item in items]


def marketplace_menu_text(*, intro: str = _INTRO, compact: bool = False) -> str:
    """Текст меню, собранный из ТЕХ ЖЕ списков, что и клавиатура.

    Перечень строится по фактически нарисованным пунктам, а не по
    константе: на развёртывании без мини-приложения меню не обещает
    экранов, которых человек не откроет, — и наоборот, добавленный пункт
    нельзя забыть упомянуть.

    ``compact`` — рамка промаха: перечень экранов и длинный хвост
    опускаются, чтобы реплика влезала в потолок длины (см.
    :data:`_FALLBACK_OUTRO`). Клавиатура при этом не урезается, так что
    ни один пункт не пропадает — он просто не пересказан словами.
    """
    web_app, miniapp_url = _config()
    parts = [intro, "\n".join(_lines(BOT_ITEMS))]

    if compact:
        parts.append(_FALLBACK_OUTRO)
        return "\n\n".join(parts)

    screen_items: list[MenuItem] = []
    if web_app or miniapp_url:
        screen_items.extend(MINIAPP_ITEMS)
        if nutrition_enabled():
            screen_items.extend(NUTRITION_ITEMS)
    if screen_items:
        parts.append(f"{_MINIAPP_HEADER}\n" + "\n".join(_lines(tuple(screen_items))))

    parts.append(_OUTRO)
    return "\n\n".join(parts)


def marketplace_menu_reply(*, bot_user: Any) -> tuple[str, dict[str, Any]]:
    """Экран «вот что я умею» — текст и клавиатура одним куском."""
    buttons = marketplace_menu_buttons(bot_user=bot_user)
    return marketplace_menu_text(), _menu_action_data(buttons, kind="marketplace_menu")


def marketplace_fallback_reply(*, bot_user: Any) -> tuple[str, dict[str, Any]]:
    """Ветка «я пока не поняла» — тот же состав, другая рамка.

    Состав тот же намеренно: человек, которого не поняли, и человек,
    спросивший «что ты умеешь», нуждаются в одном и том же — в списке
    того, что сработает. Разная только первая строка, и она честная: во
    втором случае человеку не сообщают, что его не поняли.
    """
    buttons = marketplace_menu_buttons(bot_user=bot_user)
    return (
        marketplace_menu_text(intro=FALLBACK_INTRO, compact=True),
        _menu_action_data(buttons, kind="marketplace_fallback"),
    )


def health_request_action_data() -> dict[str, Any]:
    """Клавиатура экрана запроса согласия.

    «Открыть профиль» ведёт туда, где согласие ВЫДАЁТСЯ
    (``apps.consent.health.GRANT_SOURCE`` = ``miniapp:profile_health_consent``),
    а не в саму пищевую поверхность: поверхность без согласия откажет, и
    это была бы ровно та мёртвая кнопка, которую §25 п.6 запрещает.

    Остаётся одна «Не сейчас», когда мини-приложения нет. На практике так
    сюда не попадают — пищевые пункты в такой конфигурации не рисуются, —
    но экран достижим по payload'у из истории чата, и оставлять человека
    без выхода нельзя.
    """
    web_app, miniapp_url = _config()
    buttons: list[dict[str, str]] = []
    grant_item = MenuItem(
        label="✅ Открыть профиль",
        callback="open_profile",
        line="",
        where="miniapp",
    )
    button = _miniapp_button(grant_item, web_app=web_app, miniapp_url=miniapp_url)
    if button is not None:
        buttons.append(button)
    buttons.append({"label": "Не сейчас", "callback": CALLBACK_HEALTH_DECLINE})
    return {"buttons": buttons, "button_columns": 1, "kind": "health_consent_request"}
