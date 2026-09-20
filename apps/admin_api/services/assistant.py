"""Раздел «Ayla» для администратора — половина А (DRF-2119, §50 п.5).

«Ayla — помощник администратора». Тот же цикл, что у мастера
(:func:`apps.master_api.services.assistant.run_assistant`), свой субъект:
администратор видит весь салон, а не одного мастера, и его действия — не
заявки «от себя», а подготовка того, что потом делает человек.

Три инструмента (половина А):

* **найти запись** (``find_booking``) — по клиенту / мастеру / дате → карточки
  из того же :func:`~apps.admin_api.services.salon_day.build_salon_day`, что
  экран «Сегодня». Телефона в карточке нет по построению (DRF-1039): у
  ``DayVisit`` такого поля не существует, а сторож 2129 стоит на форме
  карточки, не только на тексте;
* **подготовить запись** (``prepare_booking``) — черновик: модель собирает
  мастера, услугу, время и клиента, а ответ — ссылка на
  ``/admin/booking/new`` с предзаполнением. **Ничего не пишет.** Запись
  создаёт человек в форме, кнопкой; ассистент не зовёт ``bookings/`` POST;
* **подготовить изменение графика** (``prepare_schedule_change``) —
  предложение с подписанным талоном; подтверждение → заявка ОТ ИМЕНИ
  АДМИНА (``requested_by`` = его ``BotUser``, источник
  ``assistant_admin_confirmed``) и немедленное одобрение теми же сервисами,
  что у Admin Mini App (``request_availability_change`` +
  ``approve_availability_request``) одной транзакцией; мастеру уходит ровно
  одно уведомление — DM решения тем же путём, что при обычном одобрении.

**Половина Б — «проверить свободное время» — не объявляется модели** до
единого вычислителя (DRF-1637): три расходящихся ответа хуже отсутствия
инструмента. Сторож на состав — ``test_admin_assistant_2119`` p1.

**Роль** — владелец / админ (``require_admin_role`` во вьюхах; ресепшн и
мастер — 403, DRF-2115). **PII** — телефон в ответе администратору не
появляется: первично — данных с телефоном у инструментов нет; последний
рубеж — :func:`mask_phones` на тексте ответа.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db import transaction
from django.utils import timezone as dj_timezone

from apps.master_api.services.assistant import AssistantReply, MAX_REPLY_CHARS, run_assistant
from apps.master_api.services.assistant_actions import (
    _MONTHS_RU,
    ActionError,
    ExecutedAction,
    ProposedAction,
    _human_window,
    _localise,
    _parse_dt,
)
from apps.master_api.services.assistant_tools import ToolError, ToolOutcome

logger = logging.getLogger(__name__)

ACTION_TOKEN_SALT = "admin_api.assistant_action"
ACTION_TOKEN_TTL_SECONDS = 15 * 60
TOOL_FIND_BOOKING = "find_booking"
ACTION_PREPARE_BOOKING = "prepare_booking"
ACTION_PREPARE_SCHEDULE = "prepare_schedule_change"
MAX_ROWS = 30
PHONE_MASK = "[номер скрыт]"
_REASON_CLASSES = ("vacation", "sick", "personal", "other")
_REASON_LABELS = {
    "vacation": "отпуск",
    "sick": "болезнь",
    "personal": "личные дела",
    "other": "другое",
}
_PHONE_RE = re.compile(r"(?:\+?\d[\s().-]*){7,}")

ADMIN_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": TOOL_FIND_BOOKING,
        "description": (
            "Найти записи салона на дату: по имени клиента, по мастеру или все. "
            "Возвращает карточки (время, клиент, мастер, услуга, статус). "
            "Вызывай на вопросы «кто записан», «когда у Анны», «что у Ольги сегодня»."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Дата ГГГГ-ММ-ДД. По умолчанию — сегодня.",
                },
                "client": {"type": "string", "description": "Имя клиента (часть имени)."},
                "master": {"type": "string", "description": "Имя мастера (часть имени)."},
            },
            "required": [],
        },
    },
]

ADMIN_ACTION_SPECS: list[dict[str, Any]] = [
    {
        "name": ACTION_PREPARE_BOOKING,
        "description": (
            "ПОДГОТОВИТЬ новую запись клиента: собрать мастера, услугу, время и "
            "клиента в черновик. Ничего не создаёт — администратор откроет форму "
            "с этими полями и создаст запись сам. Вызывай, когда просят записать "
            "клиента."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "master": {"type": "string", "description": "Имя мастера."},
                "service": {"type": "string", "description": "Название услуги."},
                "start_at": {
                    "type": "string",
                    "description": "Начало, ISO 8601: ГГГГ-ММ-ДДTЧЧ:ММ.",
                },
                "client_name": {"type": "string", "description": "Имя клиента (новый клиент)."},
                "client_id": {
                    "type": "string",
                    "description": "Идентификатор клиента, если известен.",
                },
            },
            "required": ["master", "start_at"],
        },
    },
    {
        "name": ACTION_PREPARE_SCHEDULE,
        "description": (
            "ПОДГОТОВИТЬ изменение графика мастера: отметить период как нерабочий "
            "(отпуск, болезнь, личные дела). Ничего не выполняет: администратор "
            "увидит, что именно изменится, и подтвердит отдельно."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "master": {"type": "string", "description": "Имя мастера."},
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
                "reason_text": {"type": "string", "description": "Уточнение, до 200 символов."},
            },
            "required": ["master", "start", "end"],
        },
    },
]
ACTION_NAMES = frozenset(spec["name"] for spec in ADMIN_ACTION_SPECS)


# ── PII — последний рубеж ────────────────────────────────────────────


def mask_phones(text: str) -> str:
    """Цепочки ≥7 цифр (в любом форматировании) → «[номер скрыт]».

    Первичная защита — у инструментов нет данных с телефоном; это —
    последний рубеж на случай, когда модель принесла номер из истории
    или из ввода администратора.
    """

    if not text:
        return text

    def _sub(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return PHONE_MASK if len(digits) >= 7 else match.group(0)

    masked = _PHONE_RE.sub(_sub, text)
    if masked != text:
        logger.info("admin_assistant.phone_masked")
    return masked


# ── резолв по имени ──────────────────────────────────────────────────


def _tz(tenant: Any) -> ZoneInfo:
    try:
        return ZoneInfo(getattr(tenant, "timezone", "") or "Europe/Moscow")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Moscow")


def _resolve_master(tenant: Any, name: Any):
    """Мастер салона по части имени — из зеркала каталога; неоднозначность — отказ."""

    from apps.catalog.master_state import available_q
    from apps.catalog.models import CatalogMaster

    needle = str(name or "").strip().lower()
    if not needle:
        raise ActionError("не назван мастер")
    rows = list(
        CatalogMaster.all_tenants.filter(tenant=tenant, archived_at__isnull=True)
        .filter(available_q())
        .order_by("name")
    )
    hits = [m for m in rows if needle in (m.name or "").lower()]
    if not hits:
        raise ActionError(f"мастер «{name}» в салоне не найден")
    if len(hits) > 1:
        names = ", ".join(m.name for m in hits[:5])
        raise ActionError(f"мастеров несколько: {names} — уточните")
    return hits[0]


def _resolve_service(tenant: Any, master: Any, name: Any):
    """Услуга мастера по части названия — только продаваемые рёбра; ``None`` — не названа."""

    from apps.catalog.models import MasterService

    needle = str(name or "").strip().lower()
    if not needle:
        return None
    edges = list(
        MasterService.all_tenants.filter(tenant=tenant, master=master)
        .sellable()
        .select_related("service")
    )
    hits = [e.service for e in edges if needle in (e.service.name or "").lower()]
    if not hits:
        raise ActionError(f"услуга «{name}» у {master.name} не найдена")
    if len(hits) > 1:
        names = ", ".join(s.name for s in hits[:5])
        raise ActionError(f"услуг несколько: {names} — уточните")
    return hits[0]


# ── инструмент: найти запись ─────────────────────────────────────────


def _salon_day(tenant: Any, day: date, now: datetime):
    from apps.admin_api.services.salon_day import build_salon_day

    return build_salon_day(tenant, day=day, now=now)


def _parse_day(raw: Any, *, default: date) -> date:
    text = str(raw or "").strip()
    if not text:
        return default
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ToolError(f"не понимаю дату {text!r}, нужна ГГГГ-ММ-ДД") from exc


def find_booking(tenant: Any, arguments: dict[str, Any], *, now: datetime) -> ToolOutcome:
    tz = _tz(tenant)
    local_now = now.astimezone(tz)
    day = _parse_day(arguments.get("date"), default=local_now.date())
    client = str(arguments.get("client") or "").strip().lower()
    master = str(arguments.get("master") or "").strip().lower()
    try:
        salon_day = _salon_day(tenant, day, local_now)
    except Exception as exc:  # noqa: BLE001 — источник не ответил: словами, не пустым списком
        logger.warning(
            "admin_assistant.find_booking.day_unavailable tenant=%s", tenant.slug, exc_info=True
        )
        raise ToolError("день салона сейчас не читается — попробуйте позже") from exc

    rows: list[dict[str, Any]] = []
    for dm in [*salon_day.masters, _Orphans(salon_day.orphan_visits)]:
        if master and master not in (dm.name or "").lower():
            continue
        for visit in dm.visits:
            client_label = f"{visit.client_first_name} {visit.client_last_initial}".strip()
            if client and client not in client_label.lower():
                continue
            rows.append(
                {
                    "id": visit.id,
                    "time": visit.start_at.astimezone(tz).strftime("%H:%M"),
                    "client": client_label or "клиент",
                    "master": dm.name,
                    "service": visit.service_name,
                    "status": visit.status,
                }
            )
    rows.sort(key=lambda r: r["time"])
    return ToolOutcome(
        name=TOOL_FIND_BOOKING,
        data={"date": day.isoformat(), "bookings": rows[:MAX_ROWS], "total": len(rows)},
    )


class _Orphans:
    """Визиты без карточки мастера — тоже записи дня."""

    name = "мастер не указан"

    def __init__(self, visits: list[Any]) -> None:
        self.visits = visits


def run_admin_tool(
    name: str, arguments: dict[str, Any], *, tenant: Any, now: datetime
) -> ToolOutcome:
    if name == TOOL_FIND_BOOKING:
        return find_booking(tenant, arguments or {}, now=now)
    raise ToolError(f"неизвестный инструмент {name!r}")


# ── действия: предложение → подтверждение ────────────────────────────


@dataclass(frozen=True)
class AdminProposal(ProposedAction):
    """Предложение администратору: талон (подтверждение на сервере) ИЛИ дверь (ссылка)."""

    confirm_kind: str = "token"
    open_url: str = ""

    def as_dict(self) -> dict[str, Any]:
        out = super().as_dict()
        out["confirm_kind"] = self.confirm_kind
        out["open_url"] = self.open_url
        return out


def _signer() -> TimestampSigner:
    from django.conf import settings

    key = getattr(settings, "MASTER_SESSION_SECRET", "") or settings.SECRET_KEY
    return TimestampSigner(key=key, salt=ACTION_TOKEN_SALT)


def propose_admin_action(
    name: str, arguments: dict[str, Any], *, tenant: Any, bot_user: Any
) -> AdminProposal:
    """Собрать предложение. НИЧЕГО не выполняет и ничего не пишет в базу."""

    if name == ACTION_PREPARE_BOOKING:
        return _propose_booking(arguments or {}, tenant=tenant)
    if name == ACTION_PREPARE_SCHEDULE:
        return _propose_schedule(arguments or {}, tenant=tenant, bot_user=bot_user)
    raise ActionError(f"неизвестное действие {name!r}")


def _propose_booking(arguments: dict[str, Any], *, tenant: Any) -> AdminProposal:
    tz = _tz(tenant)
    master = _resolve_master(tenant, arguments.get("master"))
    service = _resolve_service(tenant, master, arguments.get("service"))
    start = _localise(_parse_dt(arguments.get("start_at"), field="start_at"), tz)
    client_id = str(arguments.get("client_id") or "").strip()
    client_name = str(arguments.get("client_name") or "").strip()[:80]
    if not client_id and not client_name:
        raise ActionError("не назван клиент")

    local = start.astimezone(tz)
    query: dict[str, str] = {
        "date": local.date().isoformat(),
        "master_id": str(master.id),
        "start_at": local.strftime("%Y-%m-%dT%H:%M"),
        "return": "today",
    }
    if service is not None:
        query["service_id"] = str(service.id)
    if client_id:
        query["client_id"] = client_id
    else:
        query["client_name"] = client_name
    who = client_name or "клиент"
    what = f"{service.name}, " if service is not None else ""
    day = f"{local.day} {_MONTHS_RU[local.month - 1]}"
    summary = (
        f"Черновик записи: {who} к {master.name}, {what}{day} в {local:%H:%M}. "
        "Откройте форму — там всё уже заполнено; запись создастся только после "
        "вашего подтверждения."
    )
    return AdminProposal(
        name=ACTION_PREPARE_BOOKING,
        summary=summary,
        confirm_label="Открыть форму записи",
        token="",
        expires_in_sec=0,
        confirm_kind="open",
        open_url=f"/admin/booking/new?{urlencode(query)}",
    )


def _propose_schedule(arguments: dict[str, Any], *, tenant: Any, bot_user: Any) -> AdminProposal:
    tz = _tz(tenant)
    master = _resolve_master(tenant, arguments.get("master"))
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

    payload = {
        "action": ACTION_PREPARE_SCHEDULE,
        "args": {
            "master_id": str(master.id),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "reason_class": reason_class,
            "reason_text": reason_text,
        },
        "tenant_id": str(tenant.id),
        "bot_user_id": str(bot_user.id),
    }
    token = _signer().sign(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    summary = (
        f"Собираюсь закрыть время у {master.name}: {_human_window(start, end, tz)}. "
        f"Причина: {_REASON_LABELS[reason_class]}{f' ({reason_text})' if reason_text else ''}. "
        "Пока вы не подтвердите, график не меняется."
    )
    return AdminProposal(
        name=ACTION_PREPARE_SCHEDULE,
        summary=summary,
        confirm_label="Изменить график",
        token=token,
        expires_in_sec=ACTION_TOKEN_TTL_SECONDS,
        confirm_kind="token",
    )


def _decode(token: str, *, tenant: Any, bot_user: Any) -> dict[str, Any]:
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
    if str(payload.get("tenant_id")) != str(tenant.id):
        raise ActionError("это подтверждение выписано не вам", slug="action_not_yours")
    if str(payload.get("bot_user_id")) != str(bot_user.id):
        raise ActionError("это подтверждение выписано не вам", slug="action_not_yours")
    return payload


def execute_admin_action(
    token: str, *, tenant: Any, bot_user: Any, role: str = "admin"
) -> ExecutedAction:
    """Выполнить подтверждённое действие. Раньше подтверждения — никак.

    Заявка — от имени администратора (``requested_by`` = он), одобрение — им
    же, одной транзакцией, теми же сервисами, что у Mini App. Мастеру — DM
    решения тем же путём, что при обычном одобрении (``_approve_dispatch``),
    ровно одно; уведомление владельцу о «новой заявке» не шлётся — заявку
    подал он сам.
    """

    from apps.admin_api.services.availability import (
        AvailabilityDecisionError,
        approve_availability_request,
    )
    from apps.audit.services import write_audit
    from apps.master_api.services.schedule import (
        AvailabilityRequestError,
        request_availability_change,
    )
    from apps.catalog.models import CatalogMaster

    payload = _decode(token, tenant=tenant, bot_user=bot_user)
    if payload.get("action") != ACTION_PREPARE_SCHEDULE:
        raise ActionError(f"неизвестное действие {payload.get('action')!r}")
    args = payload.get("args") or {}
    master = CatalogMaster.all_tenants.filter(
        tenant=tenant, id=str(args.get("master_id") or "")
    ).first()
    if master is None:
        raise ActionError("мастер не найден", slug="not_found")
    tz = _tz(tenant)
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
                actor=bot_user,
            )
            write_audit(
                "admin_api.assistant.schedule_change_requested",
                target="scheduling.ScheduleChangeRequest",
                target_id=req.id,
                payload={
                    "tenant_id": str(tenant.id),
                    "master_id": str(master.id),
                    "request_id": str(req.id),
                    "bot_user_id": str(bot_user.id),
                    "source": "assistant_admin_confirmed",
                },
                actor_id=bot_user.id,
            )
            approve_availability_request(
                request_id=req.id,
                tenant_id=tenant.id,
                actor=None,
                actor_bot_user_id=bot_user.id,
                actor_bot_user=bot_user,
                actor_role=role,
            )
    except AvailabilityRequestError as exc:
        raise ActionError(getattr(exc, "detail", str(exc)), slug=getattr(exc, "slug", "")) from exc
    except AvailabilityDecisionError as exc:
        raise ActionError(getattr(exc, "detail", str(exc)), slug=getattr(exc, "slug", "")) from exc

    return ExecutedAction(
        name=ACTION_PREPARE_SCHEDULE,
        text=f"Готово. График {master.name} изменён: {_human_window(start, end, tz)} — нерабочее время. "
        "Мастер получит уведомление.",
        target_id=req.id,
    )


# ── субъект ──────────────────────────────────────────────────────────


@dataclass
class AdminSubject:
    """Администратор салона: весь салон, три инструмента, телефонов не знает."""

    tenant: Any
    bot_user: Any
    role_ctx: Any
    now: datetime | None = None

    @property
    def limit_key(self) -> Any:
        return self.bot_user.id

    @property
    def log_label(self) -> str:
        return f"admin={self.bot_user.id} tenant={getattr(self.tenant, 'slug', '-')}"

    @property
    def addressee(self) -> str:
        return "администратору"

    @property
    def tool_specs(self) -> list[dict[str, Any]]:
        return list(ADMIN_TOOL_SPECS)

    @property
    def action_specs(self) -> list[dict[str, Any]]:
        return list(ADMIN_ACTION_SPECS)

    def system_prompt(self, *, today: date, tz_label: str) -> str:
        from apps.master_api.services.assistant import _WEEKDAYS_RU
        from apps.persona.voice import SURFACE_SALON, assistant_identity

        identity = assistant_identity(SURFACE_SALON)
        who = (getattr(self.bot_user, "display_name", "") or "").split()[0:1]
        name = who[0] if who else "администратор"
        salon = getattr(self.tenant, "name", "") or "салон"
        return "\n\n".join(
            [
                f"Ты — «{identity.name}», помощник администратора салона «{salon}». "
                f"Отвечаешь администратору {name}; это сотрудник, а не клиент.",
                f"Сегодня {today.isoformat()} ({_WEEKDAYS_RU[today.weekday()]}), "
                f"часовой пояс {tz_label}. Относительные даты («завтра», «в четверг») "
                "считай от этой даты и передавай инструментам в формате ГГГГ-ММ-ДД.",
                "Отвечай коротко и по делу. Без приветствий и без «чем ещё могу помочь».",
                "У тебя есть инструменты: найти записи салона на дату; подготовить "
                "новую запись (черновик формы); подготовить изменение графика мастера "
                "(предложение на подтверждение). Если вопрос про записи — вызови "
                "инструмент, не угадывай. Ты НЕ создаёшь записи и НЕ меняешь график "
                "сам: только готовишь, а администратор подтверждает.",
                "Свободные окна мастеров ты не считаешь — направь в раздел «Расписание».",
                "Границы:\n"
                "- Ты видишь только ЭТОТ салон.\n"
                "- Ты не врач: не ставишь диагнозов, не оцениваешь здоровье клиентов.\n"
                "- Ты не обещаешь за салон: ни скидок, ни возвратов.\n"
                "- Никогда не называй телефоны и контакты клиентов.",
                f"Ответ не длиннее {MAX_REPLY_CHARS} символов.",
            ]
        )

    def run_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        return run_admin_tool(
            name, arguments, tenant=self.tenant, now=self.now or dj_timezone.now()
        )

    def propose(self, name: str, arguments: dict[str, Any]) -> AdminProposal:
        return propose_admin_action(name, arguments, tenant=self.tenant, bot_user=self.bot_user)

    def is_action(self, name: str) -> bool:
        return name in ACTION_NAMES

    def postprocess(self, text: str) -> str:
        return mask_phones(text)


def answer_admin_question(
    *,
    tenant: Any,
    bot_user: Any,
    role_ctx: Any,
    text: str,
    history=None,
    now: datetime | None = None,
) -> AssistantReply:
    """Один вопрос администратора — один ответ. Никогда не бросает."""

    subject = AdminSubject(tenant=tenant, bot_user=bot_user, role_ctx=role_ctx, now=now)
    return run_assistant(subject, text=text, history=history, now=now)


__all__ = [
    "ACTION_PREPARE_BOOKING",
    "ACTION_PREPARE_SCHEDULE",
    "ADMIN_ACTION_SPECS",
    "ADMIN_TOOL_SPECS",
    "ActionError",
    "AdminProposal",
    "AdminSubject",
    "TOOL_FIND_BOOKING",
    "ToolError",
    "answer_admin_question",
    "execute_admin_action",
    "find_booking",
    "mask_phones",
    "propose_admin_action",
    "run_admin_tool",
]
