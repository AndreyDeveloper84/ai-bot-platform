"""Что Ayla может ПРЕДЛОЖИТЬ сделать мастеру — и почему сама не делает.

Решение владельца, эпик DRF-1180, дословно:

    «Если Ayla предлагает выполнить действие, которое изменит данные —
    например, создать запись или изменить рабочий день — сначала она
    должна показать, что именно собирается сделать, получить
    подтверждение пользователя и только после этого выполнять действие.»

### Почему предложение и исполнение — два разных запроса

Самый короткий способ дать ассистенту право писать — положить пишущий
инструмент в `TOOL_SPECS` рядом с читающими. Тогда `run_tool` выполнит
его ровно так же, как `my_day`: молча, по одному ответу модели, на
данных, которыми управляет текст, набранный человеком. Мастер узнает о
снятом рабочем дне из фразы «готово».

Здесь пишущее действие физически не может выполниться внутри петли
ответа. Первый запрос возвращает ПРЕДЛОЖЕНИЕ: разобранные аргументы,
человеческую сводку и подписанный талон. Исполнение — отдельный
`POST /assistant/confirm` с этим талоном, то есть отдельное нажатие
человека. Между ними нет пути, по которому модель прошла бы сама.

### Почему сводку пишем мы, а не модель

Сводка — это то, что человек читает перед тем, как согласиться. Если её
сочиняет модель, подтверждается пересказ, а исполняется payload, и
разойтись они могут молча. Поэтому :func:`propose` рендерит сводку из
УЖЕ разобранных и провалидированных аргументов — из тех самых, что
поедут в талон и оттуда в исполнение.

### Почему талон подписан, а не лежит в базе

Предложение живёт минуты и умирает вместе с намерением. Строка в базе
ради этого — миграция, уборка протухших строк и ещё одно место, где
состояние расходится с экраном. `TimestampSigner` даёт то же самое без
хранения: подпись, срок и привязка к мастеру внутри самого талона.
Талон, выписанный одному мастеру, у другого не сработает — это
проверяется явно, а не только тем, что он «его не увидит».

### Единственное действие на сегодня

`block_time` — заявка на нерабочее время, тот же
:func:`~apps.master_api.services.schedule.request_availability_change`,
который стоит за кнопкой «Помечу как недоступно» в расписании.
Создание записи (`create_booking`) сюда не добавлено: у мастерской
поверхности нет ручки создания записи, и выдумывать её здесь значило бы
проектировать, а не выставлять.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db import transaction
from django.utils import timezone as dj_timezone

logger = logging.getLogger(__name__)

#: Соль подписи талона. Отдельная от сессионной
#: (`apps.master_api.auth.SESSION_TOKEN_SALT`): компрометация одной не
#: должна давать вторую.
ACTION_TOKEN_SALT = "master_api.assistant_action"

#: Сколько живёт предложение. Человек читает сводку и жмёт «Подтвердить»
#: за секунды; всё, что старше пятнадцати минут, — это вкладка, забытая
#: со вчера, и подтверждать её вслепую нельзя.
ACTION_TOKEN_TTL_SECONDS = 15 * 60

ACTION_BLOCK_TIME = "block_time"

#: Классы причины — те же, что принимает `POST /availability`.
_REASON_CLASSES = ("vacation", "sick", "personal", "other")

_REASON_LABELS = {
    "vacation": "отпуск",
    "sick": "болезнь",
    "personal": "личное",
    "other": "другое",
}

_MONTHS_RU = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

#: Текст, который видит мастер после успешного подтверждения.
DONE_TEXT = "Готово. Заявка отправлена администратору салона."


class ActionError(Exception):
    """Действие нельзя ни предложить, ни выполнить. Несёт слаг для HTTP."""

    slug = "action_invalid"

    def __init__(self, detail: str = "", *, slug: str = "") -> None:
        super().__init__(detail or self.slug)
        self.detail = detail or self.slug
        if slug:
            self.slug = slug


@dataclass(frozen=True)
class ProposedAction:
    """Предложение: что именно будет сделано и чем это подтвердить."""

    name: str
    summary: str
    confirm_label: str
    token: str
    expires_in_sec: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.name,
            "summary": self.summary,
            "confirm_label": self.confirm_label,
            "token": self.token,
            "expires_in_sec": self.expires_in_sec,
        }


@dataclass(frozen=True)
class ExecutedAction:
    """Что выполнено и что об этом сказать мастеру."""

    name: str
    text: str
    target_id: uuid.UUID | None = None


#: Спецификации пишущих действий для модели. ОТДЕЛЬНЫЙ список: он
#: попадает в запрос только когда вызывающая поверхность умеет показать
#: карточку подтверждения (`allow_actions=True`). Салонный бот её пока
#: не умеет и этих спецификаций не видит — там ассистент остаётся ровно
#: таким, каким работает сегодня.
ACTION_SPECS: list[dict[str, Any]] = [
    {
        "name": ACTION_BLOCK_TIME,
        "description": (
            "ПРЕДЛОЖИТЬ отметить время как нерабочее (отпуск, болезнь, личные "
            "дела). Ничего не выполняет: мастер увидит, что именно будет "
            "сделано, и подтвердит отдельно. Вызывай, когда мастер просит "
            "освободить время, взять выходной или отметить, что не работает."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start": {
                    "type": "string",
                    "description": "Начало периода, ISO 8601: ГГГГ-ММ-ДДTЧЧ:ММ.",
                },
                "end": {
                    "type": "string",
                    "description": "Конец периода, ISO 8601: ГГГГ-ММ-ДДTЧЧ:ММ.",
                },
                "reason_class": {
                    "type": "string",
                    "enum": list(_REASON_CLASSES),
                    "description": "Класс причины.",
                },
                "reason_text": {
                    "type": "string",
                    "description": "Уточнение причины, до 200 символов. Необязательно.",
                },
            },
            "required": ["start", "end", "reason_class"],
        },
    },
]

ACTION_NAMES = frozenset(spec["name"] for spec in ACTION_SPECS)


def is_action(name: str) -> bool:
    """Это пишущее действие, а не читающий инструмент?"""

    return name in ACTION_NAMES


def _signer() -> TimestampSigner:
    from django.conf import settings

    key = getattr(settings, "MASTER_SESSION_SECRET", "") or settings.SECRET_KEY
    return TimestampSigner(key=key, salt=ACTION_TOKEN_SALT)


def _tenant_tz(master):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(getattr(getattr(master, "tenant", None), "timezone", "") or "Europe/Moscow")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Moscow")


def _parse_dt(raw: Any, *, field: str) -> datetime:
    text = str(raw or "").strip()
    if not text:
        raise ActionError(f"{field}: время не указано")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ActionError(f"{field}: не понимаю время {text!r}") from exc
    return parsed


def _localise(value: datetime, tz) -> datetime:
    """Наивное время — местное для салона, а не UTC.

    Модель отдаёт «2026-09-12T14:00» без зоны. Прочитать это как UTC
    значит снять мастеру не тот кусок дня и не сказать об этом.
    """

    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value


def _human_window(start: datetime, end: datetime, tz) -> str:
    local_start = start.astimezone(tz)
    local_end = end.astimezone(tz)
    day = f"{local_start.day} {_MONTHS_RU[local_start.month - 1]}"
    if local_start.date() == local_end.date():
        return f"{day}, {local_start:%H:%M}–{local_end:%H:%M}"
    end_day = f"{local_end.day} {_MONTHS_RU[local_end.month - 1]}"
    return f"с {day} {local_start:%H:%M} по {end_day} {local_end:%H:%M}"


def _validate_block_time(arguments: dict[str, Any], *, master) -> tuple[dict[str, Any], str]:
    """Разобрать аргументы `block_time` и собрать сводку по ним же."""

    tz = _tenant_tz(master)
    start = _localise(_parse_dt(arguments.get("start"), field="start"), tz)
    end = _localise(_parse_dt(arguments.get("end"), field="end"), tz)
    if start >= end:
        raise ActionError("начало периода должно быть раньше конца")
    if end <= dj_timezone.now():
        raise ActionError("этот период уже прошёл")

    reason_class = str(arguments.get("reason_class") or "personal").strip()
    if reason_class not in _REASON_CLASSES:
        reason_class = "other"
    reason_text = str(arguments.get("reason_text") or "").strip()[:200]

    normalised = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "reason_class": reason_class,
        "reason_text": reason_text,
    }
    summary = (
        "Собираюсь отправить администратору заявку на нерабочее время: "
        f"{_human_window(start, end, tz)}. Причина: {_REASON_LABELS[reason_class]}"
        f"{f' ({reason_text})' if reason_text else ''}. "
        "Пока вы не подтвердите, ничего не меняется."
    )
    return normalised, summary


_CONFIRM_LABELS = {ACTION_BLOCK_TIME: "Отправить заявку"}


def propose(name: str, arguments: dict[str, Any], *, master) -> ProposedAction:
    """Собрать предложение. НИЧЕГО не выполняет и ничего не пишет в базу."""

    if name != ACTION_BLOCK_TIME:
        raise ActionError(f"неизвестное действие {name!r}")

    normalised, summary = _validate_block_time(arguments or {}, master=master)
    payload = {
        "action": name,
        "args": normalised,
        "master_id": str(master.id),
        "tenant_id": str(master.tenant_id),
    }
    token = _signer().sign(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return ProposedAction(
        name=name,
        summary=summary,
        confirm_label=_CONFIRM_LABELS[name],
        token=token,
        expires_in_sec=ACTION_TOKEN_TTL_SECONDS,
    )


def _decode(token: str, *, master) -> dict[str, Any]:
    try:
        raw = _signer().unsign(token, max_age=ACTION_TOKEN_TTL_SECONDS)
    except SignatureExpired as exc:
        raise ActionError(
            "подтверждение устарело — спросите заново", slug="action_expired"
        ) from exc
    except BadSignature as exc:
        raise ActionError("подтверждение не читается", slug="action_invalid") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ActionError("подтверждение не читается", slug="action_invalid") from exc

    # Талон выписан конкретному мастеру. Проверяется здесь, а не только
    # тем, что чужой его «не увидит»: увидеть можно по-разному —
    # переслать, скопировать из логов, поймать в прокси.
    if str(payload.get("master_id")) != str(master.id):
        raise ActionError("это подтверждение выписано не вам", slug="action_not_yours")
    if str(payload.get("tenant_id")) != str(master.tenant_id):
        raise ActionError("это подтверждение выписано не вам", slug="action_not_yours")
    return payload


def execute(token: str, *, master, actor) -> ExecutedAction:
    """Выполнить подтверждённое действие. Раньше подтверждения — никак.

    ``actor`` — :class:`~apps.identity.models.BotUser`, от чьего имени
    пишется аудит: заявку подаёт человек, а не модель.
    """

    from apps.audit.services import write_audit
    from apps.events.services import emit
    from apps.events.vocabulary import MASTER_AVAILABILITY_CHANGE_REQUESTED
    from apps.master_api.services.schedule import (
        AvailabilityRequestError,
        notify_manager_of_availability_request,
        request_availability_change,
    )

    payload = _decode(token, master=master)
    if payload.get("action") != ACTION_BLOCK_TIME:
        raise ActionError(f"неизвестное действие {payload.get('action')!r}")

    args = payload.get("args") or {}
    tz = _tenant_tz(master)
    start = _localise(_parse_dt(args.get("start"), field="start"), tz)
    end = _localise(_parse_dt(args.get("end"), field="end"), tz)

    try:
        with transaction.atomic():
            req = request_availability_change(
                master,
                start=start,
                end=end,
                reason_class=str(args.get("reason_class") or "other"),
                reason_text=str(args.get("reason_text") or ""),
                actor=actor,
            )
            audit_payload = {
                "tenant_id": str(master.tenant_id),
                "master_id": str(master.id),
                "request_id": str(req.id),
                "bot_user_id": str(actor.id),
                "requested_start": (
                    req.requested_start.isoformat() if req.requested_start else None
                ),
                "requested_end": (req.requested_end.isoformat() if req.requested_end else None),
                "reason_class": req.reason_class,
                "source": "assistant_confirmed",
            }
            write_audit(
                MASTER_AVAILABILITY_CHANGE_REQUESTED,
                target="scheduling.ScheduleChangeRequest",
                target_id=req.id,
                payload=audit_payload,
                actor_id=actor.id,
            )
            emit(MASTER_AVAILABILITY_CHANGE_REQUESTED, properties=audit_payload)
            request_id = req.id
            tenant = master.tenant
            transaction.on_commit(
                lambda: notify_manager_of_availability_request(
                    tenant=tenant,
                    master=master,
                    request_id=request_id,
                )
            )
    except AvailabilityRequestError as exc:
        raise ActionError(str(getattr(exc, "detail", "") or exc), slug="action_rejected") from exc

    return ExecutedAction(name=ACTION_BLOCK_TIME, text=DONE_TEXT, target_id=req.id)


__all__ = [
    "ACTION_BLOCK_TIME",
    "ACTION_NAMES",
    "ACTION_SPECS",
    "ACTION_TOKEN_TTL_SECONDS",
    "DONE_TEXT",
    "ActionError",
    "ExecutedAction",
    "ProposedAction",
    "execute",
    "is_action",
    "propose",
]
