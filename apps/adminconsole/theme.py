"""Оформление и словарь админки: шапка, порядок разделов, бейджи.

Три вещи, ради которых модуль существует.

**Шапка.** ``site_header`` / ``site_title`` / ``index_title`` в этом
репозитории не были заданы нигде (проверено grep'ом по всему дереву),
поэтому оператор видел дефолтное «Django administration» и не понимал,
в чей контур он вошёл.

**Порядок разделов.** Django сортирует разделы индекса по алфавиту
названий. При девятнадцати русских заголовках нужные оператору три
(салоны, каталог, записи) утонули бы среди служебных внутренностей.
:func:`install_admin_branding` ставит явный порядок и уводит служебное
вниз — порядок здесь важнее перевода.

**Бейдж состояния.** :func:`badge` печатает ДВА имени: человеческое —
крупно, машинное — мелко рядом. Одного человеческого мало: оператор
прочитает «Требует проверки», а спросить о нём не сможет — в задачах,
логах и в разговоре живёт машинная строка ``REVIEW_REQUIRED``. Одного
машинного тоже мало — это и есть сегодняшняя болезнь экрана.

Ничего из этого не меняет ПОВЕДЕНИЯ: ни прав, ни действий, ни того,
какие поля можно править. Только то, как экран выглядит и как на нём
названы вещи.
"""

from __future__ import annotations

from typing import Literal

from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import SafeString

#: Тон бейджа. Смысл, а не цвет: «ok» — всё хорошо, «wait» — ждём
#: чужого действия, «stop» — состояние, из-за которого что-то не
#: работает, «off» — нейтральное/выключенное.
BadgeTone = Literal["ok", "wait", "stop", "off"]

#: Как выглядит отсутствие значения.
#:
#: Формулировка «нет данных», а не прочерк и не ноль: OPEN_DECISIONS §65
#: запрещает подставлять значение вместо отсутствия. Прочерк в колонке
#: чисел читается как ноль, ноль — как измеренный ноль; ни то, ни другое
#: не правда, когда значения просто нет.
ABSENT_LABEL = "нет данных"


class AylaAdminMedia:
    """Подключает ``ayla-admin.css`` штатным механизмом ``class Media``.

    Общесайтового подключения (переопределение ``admin/base_site.html``)
    здесь быть не может: ``TEMPLATES["DIRS"]`` пуст, ``APP_DIRS`` включён,
    а ``django.contrib.admin`` стоит ПЕРВЫМ в ``INSTALLED_APPS`` — шаблон
    приложения его не перебьёт, а править настройки ради оформления
    задача запрещает. Следствие названо прямо: стили ложатся на списки и
    карточки тех моделей, чей ``ModelAdmin`` подмешал этот класс;
    страница-индекс остаётся штатной (у неё от нас — своя шапка и свой
    порядок разделов).
    """

    class Media:
        css = {"all": ("adminconsole/ayla-admin.css",)}


def badge(tone: BadgeTone, label: str, code: str | None = None) -> SafeString:
    """Человеческая подпись состояния плюс его машинное имя.

    ``code`` не необязательная роскошь, а условие: см. докстринг модуля.
    Опускать его допустимо только там, где машинного имени не
    существует вовсе (вычисленный признак вроде «бронируется»).
    """
    if code:
        return format_html(
            '<span class="ayla-badge ayla-badge--{}">{}'
            '<span class="ayla-badge__code">{}</span></span>',
            tone,
            label,
            code,
        )
    return format_html('<span class="ayla-badge ayla-badge--{}">{}</span>', tone, label)


def absent(what: str = ABSENT_LABEL) -> SafeString:
    """Отсутствие значения, названное словами.

    Не «—» и не «0»: подменять отсутствие значением запрещено
    (OPEN_DECISIONS §65). Экран обязан показывать, что значения НЕТ.
    """
    return format_html('<span class="ayla-absent">{}</span>', what)


def number(value: object) -> SafeString:
    """Число с выравниванием по разряду. ``None`` — это отсутствие, не ноль."""
    if value is None:
        return absent()
    return format_html('<span class="ayla-num">{}</span>', value)


#: Порядок разделов на индексе админки.
#:
#: Сверху — то, ради чего оператор сюда заходит; ниже — справочное;
#: в самом низу — служебные внутренности, которые оператору салона не
#: нужны никогда и потому не должны звать нажимать.
APP_ORDER: tuple[str, ...] = (
    "tenancy",
    "catalog",
    "scheduling",
    "booking",
    "identity",
    "conversations",
    "consent",
    "kb",
    "promotions",
    "handoff",
    "adminconsole",
    "audit",
    "auth",
)


def _ordered_app_list(app_list: list[dict]) -> list[dict]:
    """Разделы по :data:`APP_ORDER`, остальные — следом по алфавиту."""
    rank = {label: i for i, label in enumerate(APP_ORDER)}
    tail = len(rank)
    return sorted(
        app_list,
        key=lambda app: (rank.get(app.get("app_label", ""), tail), app.get("name", "")),
    )


def install_admin_branding() -> None:
    """Шапка и порядок разделов. Идемпотентно — можно звать повторно.

    Порядок ставится обёрткой над ``admin.site.get_app_list``, а не
    подклассом ``AdminSite``: подмена сайта тронула бы регистрацию всех
    двадцати ``admin.py``, то есть поведение. Обёртка меняет ровно одно —
    в каком порядке разделы перечислены на странице.
    """
    site = admin.site
    site.site_header = "Ayla — админка контура"
    site.site_title = "Ayla"
    site.index_title = "Салоны, мастера, услуги и записи"

    if getattr(site, "_ayla_app_order_installed", False):
        return

    original = site.get_app_list

    def get_app_list(request, app_label=None):  # type: ignore[no-untyped-def]
        return _ordered_app_list(original(request, app_label))

    site.get_app_list = get_app_list  # type: ignore[method-assign]
    site._ayla_app_order_installed = True  # noqa: SLF001 — свой флаг на своём объекте
