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
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
#: DRF-2153 (М-5): запись через ассистента — предложение по макету DRF-1187
#: (карточка клиент / услуга / дата / время → «Подтвердить»), создание —
#: тем же сервисом, что стойка и форма мастера (М-2).
ACTION_PREPARE_BOOKING = "prepare_booking"
#: Сколько вариантов рядом предлагать, когда время занято (макет: два).
SLOT_TAKEN_ALTERNATIVES = (2, 4)
_WEEKDAYS_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)

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
    """Действие нельзя ни предложить, ни выполнить. Несёт слаг для HTTP.

    ``verbatim`` — текст показать мастеру как есть (короткий уточняющий
    вопрос макета: «Какая услуга?»), а не «Не смог подготовить действие: …»;
    ``cards`` — структурные карточки к нему (варианты клиентов, дверь в
    форму, день с записями).
    """

    slug = "action_invalid"

    def __init__(
        self,
        detail: str = "",
        *,
        slug: str = "",
        verbatim: bool = False,
        cards: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(detail or self.slug)
        self.detail = detail or self.slug
        if slug:
            self.slug = slug
        self.verbatim = verbatim
        self.cards = cards or []


@dataclass(frozen=True)
class ProposedAction:
    """Предложение: что именно будет сделано и чем это подтвердить."""

    name: str
    summary: str
    confirm_label: str
    token: str
    expires_in_sec: int
    #: Поля карточки макета (DRF-1187): клиент / услуга / длительность /
    #: дата / время — экран рисует строки, а не разбирает summary.
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "action": self.name,
            "summary": self.summary,
            "confirm_label": self.confirm_label,
            "token": self.token,
            "expires_in_sec": self.expires_in_sec,
        }
        if self.details is not None:
            out["details"] = self.details
        return out


@dataclass(frozen=True)
class ExecutedAction:
    """Что выполнено и что об этом сказать мастеру."""

    name: str
    text: str
    target_id: uuid.UUID | None = None
    #: Дверь после результата («Открыть запись» → обычный экран деталей).
    open: dict[str, str] | None = None
    #: Карточки к ответу (варианты времени при «занято»).
    cards: list[dict[str, Any]] = field(default_factory=list)
    #: ``False`` — действие НЕ выполнено (занято / результат неизвестен).
    executed: bool = True
    #: Строки итога для карточки «Запись создана».
    details: dict[str, Any] | None = None


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

ACTION_SPECS.append(
    {
        "name": ACTION_PREPARE_BOOKING,
        "description": (
            "ПРЕДЛОЖИТЬ записать клиента к мастеру. Ничего не создаёт: мастер "
            "увидит карточку (клиент, услуга, дата, время) и подтвердит отдельно. "
            "Вызывай, когда мастер просит записать клиента. Клиента ищи по имени "
            "(client_name) или бери client_id из уточнения мастера; услугу — по "
            "названию из его списка. Не угадывай клиента, услугу или время — "
            "если чего-то нет, всё равно вызови: инструмент задаст вопрос."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "client_name": {"type": "string", "description": "Имя клиента, как сказал мастер."},
                "client_id": {
                    "type": "string",
                    "description": "Id клиента из уточнения мастера («client_id=…»), если было.",
                },
                "service": {"type": "string", "description": "Название услуги, как сказал мастер."},
                "start_at": {
                    "type": "string",
                    "description": "Начало, ISO 8601: ГГГГ-ММ-ДДTЧЧ:ММ.",
                },
            },
            "required": ["start_at"],
        },
    }
)

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


def _visits_in(master, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Записи мастера в окне — карточкой дня (без телефона по построению)."""

    from apps.master_api.services.assistant_cards import visit_card_rows
    from apps.master_api.services.visit_source import master_visits

    rows = master_visits(master, start=start, end=end)
    return visit_card_rows(rows, _tenant_tz(master))


def _block_time_details(start: datetime, end: datetime, tz) -> dict[str, Any]:
    """Строки карточки макета: «Среда, 26 августа» / «Не работаю весь день»."""

    local_start = start.astimezone(tz)
    local_end = end.astimezone(tz)
    day = f"{_WEEKDAYS_RU[local_start.weekday()].capitalize()}, {local_start.day} {_MONTHS_RU[local_start.month - 1]}"
    whole_day = local_start.time() == datetime.min.time() and (
        local_end - local_start
    ) >= timedelta(hours=23)
    change = (
        "Не работаю весь день" if whole_day else f"Не работаю {local_start:%H:%M}–{local_end:%H:%M}"
    )
    return {"kind": "day_off", "title": "Изменить рабочий день", "date": day, "change": change}


_CONFIRM_LABELS = {ACTION_BLOCK_TIME: "Отправить заявку", ACTION_PREPARE_BOOKING: "Подтвердить"}


def _sign(payload: dict[str, Any]) -> str:
    return _signer().sign(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


def propose(name: str, arguments: dict[str, Any], *, master) -> ProposedAction:
    """Собрать предложение. НИЧЕГО не выполняет и ничего не пишет в базу."""

    if name == ACTION_PREPARE_BOOKING:
        return _propose_booking(arguments or {}, master=master)
    if name != ACTION_BLOCK_TIME:
        raise ActionError(f"неизвестное действие {name!r}")
    normalised, summary = _validate_block_time(arguments or {}, master=master)
    tz = _tenant_tz(master)
    start = _parse_dt(normalised["start"], field="start")
    end = _parse_dt(normalised["end"], field="end")
    # Макет DRF-1187: «Если есть записи — будет показан экран конфликта».
    # День с записями не закрывается заявкой — сначала разобраться с ними.
    visits = _visits_in(master, start, end)
    if visits:
        first = visits[0]
        raise ActionError(
            f"На этот день уже есть записи: {first['client']} в {first['time']}. "
            "Сначала разберитесь с ними.",
            verbatim=True,
            cards=[
                {
                    "kind": "day",
                    "date": start.astimezone(tz).date().isoformat(),
                    "count": len(visits),
                    "visits": visits,
                }
            ],
        )
    payload = {
        "action": name,
        "args": normalised,
        "master_id": str(master.id),
        "tenant_id": str(master.tenant_id),
    }
    return ProposedAction(
        name=name,
        summary=summary,
        confirm_label=_CONFIRM_LABELS[name],
        token=_sign(payload),
        expires_in_sec=ACTION_TOKEN_TTL_SECONDS,
        details=_block_time_details(start, end, tz),
    )


# ─── prepare_booking (DRF-2153) ────────────────────────────────────────────


def _master_services(master) -> list[Any]:
    """Услуги мастера — те же, что предлагает форма М-3 (только активные)."""

    from apps.catalog.models import MasterService

    rows = MasterService.all_tenants.filter(
        tenant_id=master.tenant_id, master_id=master.id
    ).select_related("service")
    return [ms.service for ms in rows if ms.service is not None and ms.service.is_active]


def _resolve_service(master, raw: Any) -> Any:
    """Услуга по названию среди своих; пусто/не найдено → вопрос макета."""

    from apps.master_api.services.assistant_cards import service_options

    services = _master_services(master)
    wanted = str(raw or "").strip().lower()
    if not wanted:
        raise ActionError(
            "Какая услуга?",
            verbatim=True,
            cards=[{"kind": "choose_service", "options": service_options(services)}],
        )
    exact = [s for s in services if s.name.lower() == wanted]
    if len(exact) == 1:
        return exact[0]
    partial = [s for s in services if wanted in s.name.lower() or s.name.lower() in wanted]
    if len(partial) == 1:
        return partial[0]
    # Ни одной или несколько похожих — не угадывать (макет: «Ayla не угадывает услугу»).
    raise ActionError(
        "Какая услуга?",
        verbatim=True,
        cards=[{"kind": "choose_service", "options": service_options(partial or services)}],
    )


def _resolve_client(master, arguments: dict[str, Any], *, tz) -> dict[str, Any]:
    """Клиент — по id из уточнения или по имени через поиск М-2.

    0 совпадений — не изобретать: дверь в форму, где нового гостя заводят с
    телефоном; ≥2 — «Кого вы имеете в виду?» с датой последнего визита, без
    телефона (решение владельца 20.09, DRF-1039).
    """

    from apps.admin_api.services.booking import Refusal, search_customers_as
    from apps.integrations.ayla.user_proxy import external_user_id_for
    from apps.master_api.services.bookings import enrich_customer_rows

    client_id = str(arguments.get("client_id") or "").strip()
    client_name = str(arguments.get("client_name") or "").strip()[:80]
    if not client_id and not client_name:
        raise ActionError("Кого записать?", verbatim=True)

    actor_user = getattr(master, "linked_bot_user", None)
    actor = external_user_id_for(actor_user) if actor_user is not None else ""
    query = client_name or client_id
    rows = search_customers_as(
        actor=actor,
        tenant=master.tenant,
        query=query,
        log="master_api.assistant.find_client",
    )
    if isinstance(rows, Refusal):
        raise ActionError("Не удалось проверить клиентов. Попробуйте снова.", verbatim=True)
    enriched = enrich_customer_rows(master, rows)
    if client_id:
        picked = [r for r in enriched if r["id"] == client_id]
        if picked:
            return picked[0]
    if not enriched:
        from apps.master_api.services.assistant_cards import booking_form_url

        raise ActionError(
            "Клиента с таким именем нет. Нового клиента можно добавить в форме записи.",
            verbatim=True,
            cards=[{"kind": "open", "url": booking_form_url(master), "label": "Добавить запись"}],
        )
    if len(enriched) > 1:
        from apps.master_api.services.assistant_cards import client_option_label

        raise ActionError(
            "Кого вы имеете в виду?",
            verbatim=True,
            cards=[
                {
                    "kind": "clarify_client",
                    "options": [
                        {"client_id": r["id"], "label": client_option_label(r)} for r in enriched
                    ],
                }
            ],
        )
    return enriched[0]


def _propose_booking(arguments: dict[str, Any], *, master) -> ProposedAction:
    tz = _tenant_tz(master)
    start = _localise(_parse_dt(arguments.get("start_at"), field="start_at"), tz)
    if start <= dj_timezone.now():
        raise ActionError("Это время уже прошло. На какое время записать?", verbatim=True)
    client = _resolve_client(master, arguments, tz=tz)
    service = _resolve_service(master, arguments.get("service"))
    if not getattr(service, "ayla_service_id", None):
        raise ActionError("Эта услуга пока не подключена к записи.", verbatim=True)

    duration = int(getattr(service, "duration_min", 0) or 0)
    local = start.astimezone(tz)
    end_local = local + timedelta(minutes=duration)
    day = f"{local.day} {_MONTHS_RU[local.month - 1]}"
    details = {
        "client": client["name"],
        "service": service.name,
        "duration_min": duration,
        "date": f"{day}, {_WEEKDAYS_RU[local.weekday()]}",
        "time": f"{local:%H:%M}",
        "time_range": f"{local:%H:%M}–{end_local:%H:%M}",
    }
    payload = {
        "action": ACTION_PREPARE_BOOKING,
        "args": {
            "client_id": client["id"],
            "service_id": str(service.id),
            "start_at": local.isoformat(),
        },
        "details": details,
        "master_id": str(master.id),
        "tenant_id": str(master.tenant_id),
    }
    summary = f"{client['name']} · {service.name} · {duration} мин · {day} · {local:%H:%M}"
    return ProposedAction(
        name=ACTION_PREPARE_BOOKING,
        summary=summary,
        confirm_label=_CONFIRM_LABELS[ACTION_PREPARE_BOOKING],
        token=_sign(payload),
        expires_in_sec=ACTION_TOKEN_TTL_SECONDS,
        details=details,
    )


def _execute_booking(payload: dict[str, Any], *, master, actor) -> ExecutedAction:
    """Создать запись тем же сервисом, что стойка и форма мастера (М-2).

    Одна попытка. «Занято» → варианты рядом, без тихого переноса; нет
    ответа → «Проверяем результат», а не «создана» (макет DRF-1187).
    """

    import uuid as uuid_mod

    from apps.admin_api.services.booking import (
        Refusal,
        bookable_service,
        bookable_starts,
        create_appointment_as,
        slot_payload,
    )
    from apps.integrations.ayla.user_proxy import external_user_id_for
    from apps.master_api.services.assistant_cards import booking_detail_url

    args = payload.get("args") or {}
    details = payload.get("details") or {}
    service = bookable_service(master.tenant_id, str(args.get("service_id") or ""))
    if isinstance(service, Refusal):
        raise ActionError(service.detail, slug="action_rejected")
    start_at = str(args.get("start_at") or "")
    result = create_appointment_as(
        actor=external_user_id_for(actor),
        tenant=master.tenant,
        master=master,
        service=service,
        start_at=start_at,
        idempotency_key=str(uuid_mod.uuid4()),
        client_id=str(args.get("client_id") or "") or None,
        client_name=None,
        client_phone=None,
        log="master_api.assistant.create_booking",
    )
    if result.outcome == "committed":
        appointment_id = str(result.extra.get("appointment_id") or "")
        return ExecutedAction(
            name=ACTION_PREPARE_BOOKING,
            text="Запись создана",
            open={"url": booking_detail_url(master, appointment_id), "label": "Открыть запись"},
            details=details,
        )
    if result.outcome == "conflict":
        tz = _tenant_tz(master)
        try:
            day = datetime.fromisoformat(start_at).astimezone(tz).date()
        except ValueError:
            day = dj_timezone.now().astimezone(tz).date()
        slots = bookable_starts(
            master=master, service=service, day=day, log="master_api.assistant.alternatives"
        )
        alternatives: list[dict[str, Any]] = []
        if not isinstance(slots, Refusal):
            lo, hi = SLOT_TAKEN_ALTERNATIVES
            taken = details.get("time")
            alternatives = [s for s in (slot_payload(x) for x in slots) if s.get("time") != taken][
                :hi
            ]
            if len(alternatives) < lo:
                alternatives = alternatives[:lo]
        return ExecutedAction(
            name=ACTION_PREPARE_BOOKING,
            text="Это время занято",
            executed=False,
            cards=[
                {
                    "kind": "slot_taken",
                    "range": details.get("time_range", ""),
                    "alternatives": [
                        {"time": a["time"], "start_at": a.get("start_at")} for a in alternatives
                    ],
                }
            ],
        )
    if result.outcome == "pending":
        return ExecutedAction(
            name=ACTION_PREPARE_BOOKING, text="Проверяем результат", executed=False
        )
    raise ActionError(result.detail or "не удалось создать запись", slug="action_rejected")


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
    if payload.get("action") == ACTION_PREPARE_BOOKING:
        return _execute_booking(payload, master=master, actor=actor)
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
    "ACTION_PREPARE_BOOKING",
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
