"""Кого мы адресуем и каким ключом — один ответ на весь репозиторий (DRF-1559).

### Зачем отдельный модуль

DRF-1558 перевёл на ``user_id`` все отправки, чей адрес приходит из **нашей
базы**: у ``BotUser`` есть ``channel_user_id``, и он есть всегда. Остались
восемь отправок, чей адрес приходит **из конфига** — ``Tenant.manager_chat_id``
и ``settings.HANDOFF_NOTIFY_MAX_CHAT_IDS``. Там идентификатора человека у нас
не было вовсе, поэтому правкой отправителя они не чинились.

Здесь появляются вторые поля (``Tenant.manager_user_id``,
``HANDOFF_NOTIFY_MAX_USER_IDS``) и **одно** правило выбора между ними. Правило
одно на всех умышленно: восемь мест, каждое со своим ``if``, — это восемь
мест, где следующая правка забудет одно.

### Правило

**Заполнен идентификатор человека — адресуем им. Пуст — откатываемся на
диалоговый и продолжаем работать так же, как вчера.**

Откат, а не отказ: настроенные салоны не должны замолчать в день выкладки.
Но откат — это именно прежнее поведение со всеми его ограничениями:
диалоговый идентификатор верен **только для того бота, из переписки с которым
его скопировали**, и попытка отправить туда вторым ботом отвечает
404 ``dialog.not.found``. Замер 07.09.2026, один человек и один салонный бот
(`docs/OPEN_DECISIONS.md` §55, §56)::

    POST /messages?user_id=260237491   → 200, доставлено
    POST /messages?chat_id=518410834   → 404 dialog.not.found

### Почему новые поля, а не переосмысление старых

В старых полях лежат настоящие ``chat_id``. Молчаливая смена их смысла
сэкономила бы миграцию и сделала бы неверную настройку **необнаружимой**:
адрес есть, отправка уходит, ошибки нет, сообщение не доходит — ровно тот
способ, которым нашёлся исходный дефект, то есть случайно.

Пустое новое поле обнаруживается сразу: :attr:`MaxAddress.key` говорит, каким
ключом ушло, и лог называет его же. Перенести значения нельзя ни скриптом, ни
миграцией — ``chat_id`` человека не пересчитывается в его ``user_id``; MAX
отдаёт ``recipient.user_id`` в ответе на успешную отправку, оттуда его и
берут руками.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.conf import settings


@dataclass(frozen=True)
class MaxAddress:
    """Адрес получателя MAX и то, каким ключом он уходит на провод.

    Ровно один из двух заполнен: :meth:`resolve` сама выбирает, и выбор
    больше нигде не повторяется. Пустой адрес (оба поля пусты) — это
    «получателя нет», и он ложен в булевом контексте, чтобы вызывающий
    писал ``if not address:``, а не сравнивал строки.
    """

    user_id: str = ""
    chat_id: str = ""

    @classmethod
    def resolve(cls, *, user_id: object = "", chat_id: object = "") -> "MaxAddress":
        """Человек, если он задан; иначе диалог; иначе пустой адрес."""

        person = str(user_id or "").strip()
        if person:
            return cls(user_id=person)
        dialog = str(chat_id or "").strip()
        if dialog:
            return cls(chat_id=dialog)
        return cls()

    def __bool__(self) -> bool:
        return bool(self.user_id or self.chat_id)

    @property
    def key(self) -> str:
        """``"user_id"`` или ``"chat_id"`` — чем адресуемся."""

        return "user_id" if self.user_id else "chat_id"

    @property
    def value(self) -> str:
        """Само значение адреса, без ключа."""

        return self.user_id or self.chat_id

    @property
    def is_person(self) -> bool:
        """Адресуемся человеком, а не диалогом — то есть любым ботом."""

        return bool(self.user_id)

    def send_kwargs(self) -> dict[str, Any]:
        """``**address.send_kwargs()`` в :func:`~apps.channels.max.outbound.send_message`.

        Разворачивается ровно в один ключ: ``send_message`` отказывается и
        без адреса, и с обоими сразу, поэтому «забыть развести» здесь
        нельзя — это ошибка вызова, а не тихо не тот получатель.
        """

        return {self.key: self.value}

    def __str__(self) -> str:
        return f"{self.key}={self.value}" if self else "no_address"


def manager_address(tenant: Any) -> MaxAddress:
    """Адрес менеджера салона: ``manager_user_id``, иначе ``manager_chat_id``.

    ``getattr`` с умолчанием, а не прямой доступ: в тестах и в паре
    сервисов сюда приходит не строка ``Tenant``, а лёгкий двойник, и
    отсутствие поля должно читаться как «не настроено», а не падать.
    """

    return MaxAddress.resolve(
        user_id=getattr(tenant, "manager_user_id", ""),
        chat_id=getattr(tenant, "manager_chat_id", ""),
    )


def operator_addresses() -> tuple[MaxAddress, ...]:
    """Настроенный запасной канал операторов, по одному адресу на получателя.

    Списки не смешиваются: непустой ``HANDOFF_NOTIFY_MAX_USER_IDS``
    **вытесняет** ``HANDOFF_NOTIFY_MAX_CHAT_IDS`` целиком, а не дополняет
    его. Объединение выглядело бы безопаснее, но на время переноса это тот
    же человек, записанный дважды, — и он получил бы два сообщения на
    каждое событие. Перенос делается одним переключением: заполнили новую
    настройку — старая перестала читаться.
    """

    people = _from_settings("HANDOFF_NOTIFY_MAX_USER_IDS")
    if people:
        return tuple(MaxAddress(user_id=value) for value in people)
    return tuple(
        MaxAddress(chat_id=value) for value in _from_settings("HANDOFF_NOTIFY_MAX_CHAT_IDS")
    )


def _from_settings(name: str) -> list[str]:
    raw: Sequence[Any] = getattr(settings, name, []) or []
    return [stripped for stripped in (str(item or "").strip() for item in raw) if stripped]


def split_addresses(addresses: Sequence[MaxAddress]) -> tuple[list[str], list[str]]:
    """``(chat_ids, user_ids)`` — форма, которую принимает фан-аут DRF-1558."""

    return (
        [a.chat_id for a in addresses if a and not a.is_person],
        [a.user_id for a in addresses if a and a.is_person],
    )
