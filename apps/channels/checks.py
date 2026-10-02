"""Boot-time statement: обещание поддержки названо адресом (DRF-2393).

### Что было молча

Салонный бот рисует кнопку «Обратиться в поддержку», а отвечает на неё
``_support_text()`` (``apps/channels/max/salon_handler.py``): при пустом
``AYLA_SUPPORT_CONTACT`` ответ — «Напишите в поддержку Ayla.» То есть на
вопрос «как связаться с поддержкой» человек получает «свяжитесь с
поддержкой». Тавтология вместо адреса.

Три обстоятельства делали это невидимым:

* умолчание настройки — пустая строка (``config/settings/base.py``);
* переменной не было ни в ``.env.example``, ни в
  ``.env.staging.template`` — поднимающий контур о ней не узнавал;
* проверки этого вида не выполняются там, **где идёт запрос**: ``web``
  стенда поднимается ``uvicorn config.asgi:application``, а
  ``get_asgi_application()`` не зовёт ``run_checks()`` никогда. На
  выкладке они всё же бежали — побочным свойством ``migrate``; этот лист
  делает шаг явным, чтобы сторож не зависел от чужого поведения.
  (Первая редакция этого текста говорила «на стенде не выполнялись
  вовсе» — неверно, поймано ревью.)

Замер стенда 24.09.2026: пусто.

### Warning, а не Error, и это решение

Пустой контакт — законное состояние местной разработки и CI: кнопку там
никто не нажимает. Ошибка остановила бы выкладку из-за настройки, без
которой контур работает. Предмет сторожа — **молчание**, а не неверное
значение: до него никто и нигде не говорил, что обещание осталось без
адресата.

### Только там, где это не отладка

``DEBUG`` истинен ровно на тех контурах, где тавтология безвредна.
Предупреждение в каждом зелёном прогоне учит читателя пропускать строку
``System check identified``, и следующее предупреждение той же формы —
настоящее — уйдёт тем же путём. Это решение DRF-2021, и ``0 silenced``
обязано оставаться нулём.

Две оговорки, обе найдены ревью и обе стоит знать.

**CI молчит не потому, что он CI.** ``config/settings/local.py`` жёстко
задаёт ``DEBUG = True``, и это перекрывает переменную ``DJANGO_DEBUG``
задания. Сделает кто-нибудь ``local.py`` послушным переменной — и этот
сторож (вместе с ``payments.W001`` и ``admin_api.W001``) заговорит в
каждом прогоне.

**Под pytest сторож ГОВОРИТ.** ``pytest-django`` выставляет
``settings.DEBUG = False``, поэтому в наборе он звучит — рядом с двумя
такими же, уже существовавшими. Набор от этого не краснеет
(``call_command("check")`` падает только на ошибке), но утверждать, будто
«локально и в CI тихо», было бы неточно.

### Чего сторож НЕ проверяет

Что по указанному адресу кто-то отвечает. Он видит строку настройки, а не
человека за ней. Это предел, названный вслух.

### Одно слово, и ничего больше

Вывод ``manage.py check`` копируется в логи выкладки и в тикеты. Сообщение
называет настройку и последствие; **самого значения в тексте нет** — адрес
поддержки не секрет, но привычка печатать значения настроек в логи
заводится один раз и потом печатает токены.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.checks import Error as CheckError, Warning as CheckWarning, register

#: Идентификатор сторожа — по нему его ищут в логах выкладки.
SUPPORT_CONTACT_CHECK_ID = "support.W001"


@register()
def check_support_contact_named(app_configs: Any, **kwargs: Any) -> list[CheckWarning]:
    """support.W001 — контур обещает поддержку, но не называет адреса."""

    contact = str(getattr(settings, "AYLA_SUPPORT_CONTACT", "") or "").strip()
    if contact:
        return []
    if settings.DEBUG:
        # Местная разработка и CI — см. выше.
        return []
    return [
        CheckWarning(
            "Support is promised without an address.",
            hint=(
                "AYLA_SUPPORT_CONTACT is empty on a contour that is not "
                "DEBUG. The salon bot's «Обратиться в поддержку» button "
                "then answers «Напишите в поддержку Ayla.» — the question "
                "«how do I reach support» gets «reach support» back. Set "
                "it to the address people should write to, or take the "
                "promise off the screen."
            ),
            id=SUPPORT_CONTACT_CHECK_ID,
        )
    ]


#: DRF-2751 — адрес поддержки задан, но это не клиентский бот.
SUPPORT_CONTACT_INVALID_CHECK_ID = "support.E002"


@register()
def check_support_contact_is_the_client_bot(app_configs: Any, **kwargs: Any) -> list[CheckError]:
    """support.E002 — заданный адрес поддержки не ведёт в клиентского бота.

    Решение владельца 02.10.2026: поддержка клиента идёт через клиентский
    MAX-бот; внутренний чат сотрудников клиенту не показывается. Сторож —
    белый список (:mod:`apps.channels.support_contact`): проходит только
    ссылка на клиентского бота из реестра.

    ОШИБКА, а не предупреждение, — в отличие от ``support.W001``. Пустой
    адрес — молчание, с ним контур работает. Неверный адрес — это адрес,
    который будет показан человеку: показать не то хуже, чем не показать.
    Сам показ закрыт и без этой проверки (``shown_support_contact`` не
    отдаёт недопустимое значение), так что ошибка здесь — чтобы выкладка
    сказала о нём вслух, а не чтобы удержать его.

    Только там, где это не отладка, — по той же причине, что ``W001``.
    Значения в тексте нет: называется причина отказа, а не адрес.
    """

    from apps.channels.support_contact import support_contact_problem

    contact = str(getattr(settings, "AYLA_SUPPORT_CONTACT", "") or "").strip()
    if not contact or settings.DEBUG:
        return []
    problem = support_contact_problem(contact)
    if problem is None:
        return []
    return [
        CheckError(
            f"The support address is not the client bot ({problem}).",
            hint=(
                "AYLA_SUPPORT_CONTACT must be the public link of the client bot from "
                "the bot registry (MAX_BOT_<SLUG>_LINK of the max_global entry), "
                "optionally with ?start=support — nothing else is accepted: not a "
                "chat invitation, not a recipient id, not the salon bot, not free "
                "text. Leave it empty rather than point it elsewhere."
            ),
            id=SUPPORT_CONTACT_INVALID_CHECK_ID,
        )
    ]
