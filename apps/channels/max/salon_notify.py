"""Уведомления-решения в чате салонного бота — один рендерер (DRF-2118, §50 п.8).

Принцип владельца: «Чат сообщает и просит решения. Mini App используется
для полноценного управления». Чат — для событий, где от человека
требуется решение; остальное — в Admin Mini App.

Один формат на все типы: **заголовок → факты → Было / Станет / затронуто →
кнопки**. Пять типов (шестой, «утренний итог», — отдельный лист):

======  ===========================================  ======================================
kind    событие                                      кнопки
======  ===========================================  ======================================
handoff клиент ждёт ответа (очередь handoff)         Открыть диалог · Вернуть Ayla
schedule мастер просит изменить график               Одобрить · Отклонить · Подробнее
sync    синхронизация каталога с ошибкой             Повторить · Подробнее
master_off мастер перестал продаваться               Открыть карточку
booking запись требует вмешательства                 Открыть запись
======  ===========================================  ======================================

Кнопки-решения — колбэки ``cb:salon:n:<kind>:<ref>:<action>``
(:func:`callback` / :func:`parse_callback`); их принимает
:mod:`apps.channels.max.salon_notify_actions`. Кнопки-двери — ссылки в
Admin Mini App салонного бота.

**Отправитель и адресаты** — :func:`apps.channels.max.staff_outbound.send_to_staff`
(DRF-2128): салонный бот, активные владелец/админ салона; мастеру — только
его события (``extra``: тип 4 про него; ответ на его заявку — отдельный DM
решения, не отсюда).

**Идемпотентность по событию** — :func:`_claim`: ``cache.add`` на ключ
``salon_notify:<tenant>:<kind>:<ref>`` с TTL :data:`DEDUP_TTL_S`. Пределы,
названные честно: (а) кэш общий и не транзакционный — ключ ставится ДО
отправки, поэтому сбой провода после захвата ключа не повторит сообщение
раньше TTL; (б) при сбросе Redis возможен повтор того же события через
семь дней. Два конкурентных вызова на одно событие — одна отправка:
``add`` атомарен у всех наших бэкендов.

**Персональные данные** — телефон клиента в текст не попадает никогда
(DRF-1039, сторож DRF-2129 на общем входе): рендерер знает только имя.
Причины отказов — константы по коду, не свободный текст.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.core.cache import cache

from apps.channels.max.addressing import MaxAddress
from apps.channels.max.staff_outbound import MANAGER, StaffSendResult, send_to_staff

logger = logging.getLogger(__name__)

KINDS: tuple[str, ...] = ("handoff", "schedule", "sync", "master_off", "booking")
CB_PREFIX = "cb:salon:n:"
#: Семь дней: дольше события такого рода не живут (заявка решается за день,
#: синк чинится за часы), короче — повтор beat-задачи прислал бы дубль.
DEDUP_TTL_S = 7 * 24 * 3600

_WEEKDAY_ACC = ("понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье")
_WEEKDAY_PREP = ("в", "во", "в", "в", "в", "в", "в")
_MONTH_GEN = (
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

#: Слово причины «мастер не продаётся» — по коду ``master_state.sale_block``.
SALE_BLOCK_HUMAN: dict[str, str] = {
    "revoked": "доступ отозван",
    "pending": "приглашение не принято",
    "profile_incomplete": "профиль не заполнен",
    "ayla_unlinked": "не связана с аккаунтом Ayla",
    "catalog_unlinked": "не заведена в каталоге",
    "schedule_unconfirmed": "график не подтверждён",
}

#: Слово причины «запись требует вмешательства» — по коду источника.
BOOKING_REASON_HUMAN: dict[str, str] = {
    "cancelled_by_client": "клиент отменил запись",
    "cancelled": "запись отменена",
    "no_show": "клиент не пришёл",
    "conflict": "перенос не завершён — нужен человек",
}


@dataclass(frozen=True)
class Button:
    """Кнопка: колбэк-решение (``action``) или дверь в Mini App (``url``)."""

    label: str
    action: str = ""
    url: str = ""


@dataclass(frozen=True)
class Diff:
    was: str
    becomes: str
    affected: str


@dataclass(frozen=True)
class Impact:
    """Лёгкая форма исхода ``schedule_impact.impact_for_window`` для рендера."""

    state: str
    affected: int | None = None


@dataclass(frozen=True)
class SalonNotice:
    kind: str
    tenant: Any
    #: Идентификатор события — вместе с ``kind`` и тенантом даёт ключ дедупа.
    ref: str
    title: str
    facts: tuple[str, ...] = ()
    diff: Diff | None = None
    buttons: tuple[Button, ...] = ()
    #: Дополнительные адресаты помимо управляющих — мастер о своём событии.
    extra: tuple[MaxAddress, ...] = ()
    #: Лог-контекст (имена полей → значения), без персональных данных.
    log: dict[str, Any] = field(default_factory=dict)


# ── колбэки ──────────────────────────────────────────────────────────


def callback(notice: SalonNotice, action: str) -> str:
    return f"{CB_PREFIX}{notice.kind}:{notice.ref}:{action}"


def parse_callback(payload: str) -> tuple[str, str, str] | None:
    """``cb:salon:n:<kind>:<ref>:<action>`` → ``(kind, ref, action)``; иначе ``None``.

    ``ref`` может содержать ``:`` (тип 4: ``<master_id>:<block>``) — режем
    по первому и последнему двоеточию после префикса.
    """

    if not payload.startswith(CB_PREFIX):
        return None
    body = payload[len(CB_PREFIX) :]
    kind, sep, rest = body.partition(":")
    if not sep or kind not in KINDS:
        return None
    ref, sep, action = rest.rpartition(":")
    if not sep or not ref or not action:
        return None
    return kind, ref, action


# ── рендер ───────────────────────────────────────────────────────────


def render(notice: SalonNotice) -> str:
    lines = [notice.title]
    lines.extend(notice.facts)
    if notice.diff is not None:
        lines.append(f"Было: {notice.diff.was} · Станет: {notice.diff.becomes}")
        lines.append(notice.diff.affected)
    return "\n".join(line for line in lines if line)


def _keyboard(notice: SalonNotice) -> list[dict[str, Any]] | None:
    from apps.channels.max.outbound import make_inline_keyboard_attachment

    buttons: list[dict[str, Any]] = []
    for b in notice.buttons:
        if b.url:
            buttons.append({"label": b.label, "url": b.url})
        else:
            buttons.append({"label": b.label, "callback": callback(notice, b.action)})
    if not buttons:
        return None
    return [make_inline_keyboard_attachment(buttons, columns=1)]


# ── отправка с дедупом ───────────────────────────────────────────────


def _dedup_key(notice: SalonNotice) -> str:
    tenant_id = getattr(notice.tenant, "id", None) or getattr(notice.tenant, "slug", "-")
    return f"salon_notify:{tenant_id}:{notice.kind}:{notice.ref}"


def _claim(key: str) -> bool:
    """Первый, кто поставил ключ, — отправляет. ``add`` атомарен."""

    return bool(cache.add(key, "1", timeout=DEDUP_TTL_S))


def notify(notice: SalonNotice) -> StaffSendResult | None:
    """Отправить уведомление один раз на событие. ``None`` — дубль, не отправлено."""

    if notice.kind not in KINDS:
        raise ValueError(f"unknown salon notice kind {notice.kind!r}")
    key = _dedup_key(notice)
    if not _claim(key):
        logger.info(
            "channels.max.salon_notify.duplicate kind=%s ref=%s tenant=%s",
            notice.kind,
            notice.ref,
            getattr(notice.tenant, "slug", "-"),
        )
        return None

    text = render(notice)
    attachments = _keyboard(notice)
    result = send_to_staff(notice.tenant, MANAGER, text, attachments)
    sent, failed, recipients = result.sent, result.failed, result.recipients

    seen: set[str] = set()
    if notice.extra:
        from apps.channels.max.staff_outbound import manager_recipients

        # Мастер, который сам владелец/админ, уже получил копию выше.
        seen = {a.value for a in manager_recipients(notice.tenant)}
    for address in notice.extra:
        if not address or address.value in seen:
            continue
        seen.add(address.value)
        extra_result = send_to_staff(notice.tenant, address, text, attachments)
        sent += extra_result.sent
        failed += extra_result.failed
        recipients += extra_result.recipients

    logger.info(
        "channels.max.salon_notify.sent kind=%s ref=%s tenant=%s recipients=%d sent=%d failed=%d %s",
        notice.kind,
        notice.ref,
        getattr(notice.tenant, "slug", "-"),
        recipients,
        sent,
        failed,
        " ".join(f"{k}={v}" for k, v in notice.log.items()),
    )
    return StaffSendResult(recipients=recipients, sent=sent, failed=failed)


# ── общие помощники ──────────────────────────────────────────────────


def _tz(tenant: Any) -> ZoneInfo:
    name = getattr(tenant, "timezone", "") or "Europe/Moscow"
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — неверный IANA в настройке не должен ронять уведомление
        return ZoneInfo("Europe/Moscow")


def _hm(value: time | datetime) -> str:
    return value.strftime("%H:%M")


def _day_human(day: date) -> str:
    """«пятницу, 25 сентября» — винительный падеж, как в тексте владельца."""

    return f"{_WEEKDAY_ACC[day.weekday()]}, {day.day} {_MONTH_GEN[day.month - 1]}"


def _day_prep(day: date) -> str:
    return _WEEKDAY_PREP[day.weekday()]


def _first_name(person: Any) -> str:
    """Имя — единственное, что рендерер знает о клиенте (DRF-1039)."""

    for attr in ("client_name", "display_name", "name"):
        value = (getattr(person, attr, "") or "").strip()
        if value:
            return value.split()[0]
    return "клиент"


def _salon_app_link(path: str) -> str:
    from apps.channels.max.salon_links import _salon_app_base

    base = _salon_app_base()
    if not base:
        return ""
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def _door(label: str, path: str) -> tuple[Button, ...]:
    """Кнопка-ссылка в Mini App; без настроенного Mini App двери нет."""

    url = _salon_app_link(path)
    if not url:
        return ()
    return (Button(label=label, url=url),)


# ── тип 2 — мастер просит изменить график ───────────────────────────


def _working_window(master: Any, day: date) -> tuple[time, time] | None:
    from apps.scheduling.models import WorkingHours

    row = (
        WorkingHours.all_tenants.filter(master=master, day_of_week=day.weekday())
        .values("is_working", "start_time", "end_time")
        .first()
    )
    if not row or not row["is_working"] or not row["start_time"] or not row["end_time"]:
        return None
    return row["start_time"], row["end_time"]


def _becomes(window: tuple[time, time] | None, block_start: time, block_end: time) -> str:
    """Рабочие часы дня минус окно блокировки — словами."""

    if window is None:
        return "выходной"
    ws, we = window
    if block_start <= ws and block_end >= we:
        return "выходной"
    if block_start <= ws < block_end < we:
        return f"{_hm(block_end)}–{_hm(we)}"
    if ws < block_start < we <= block_end:
        return f"{_hm(ws)}–{_hm(block_start)}"
    if ws < block_start and block_end < we:
        return f"{_hm(ws)}–{_hm(block_start)} и {_hm(block_end)}–{_hm(we)}"
    return f"{_hm(ws)}–{_hm(we)}"  # окно вне рабочих часов — ничего не меняет


def schedule_diff(request: Any, master: Any, tenant: Any) -> tuple[list[str], str, str]:
    """``(дни, было, станет)`` по ``requested_start/end`` × ``WorkingHours``.

    Несколько дней — по строке на день (до трёх), дальше «и ещё N дней».
    Заявка без типизированного окна (только ``requested_change``) — «по заявке».
    """

    start = getattr(request, "requested_start", None)
    end = getattr(request, "requested_end", None)
    if start is None or end is None:
        return ["по заявке"], "по заявке", "по заявке"
    tz = _tz(tenant)
    start_l, end_l = start.astimezone(tz), end.astimezone(tz)
    days: list[date] = []
    cursor = start_l.date()
    while cursor <= end_l.date() and len(days) < 60:
        days.append(cursor)
        cursor += timedelta(days=1)

    was_parts: list[str] = []
    becomes_parts: list[str] = []
    for day in days:
        window = _working_window(master, day)
        b_start = start_l.time() if day == start_l.date() else time(0, 0)
        b_end = end_l.time() if day == end_l.date() else time(23, 59)
        was = f"{_hm(window[0])}–{_hm(window[1])}" if window else "выходной"
        becomes = _becomes(window, b_start, b_end)
        if len(days) == 1:
            was_parts.append(was)
            becomes_parts.append(becomes)
        else:
            was_parts.append(f"{_day_human(day)}: {was}")
            becomes_parts.append(f"{_day_human(day)}: {becomes}")

    def _join(parts: list[str]) -> str:
        if len(parts) <= 3:
            return "; ".join(parts)
        return "; ".join(parts[:3]) + f"; и ещё {len(parts) - 3} дн."

    day_labels = [f"{_day_prep(d)} {_day_human(d)}" for d in days]
    return day_labels, _join(was_parts), _join(becomes_parts)


def schedule_request_notice(request: Any, *, impact: Impact) -> SalonNotice:
    """Тип 2: «Анна просит изменить график на пятницу: Было … · Станет … · Затронута …»."""

    from apps.admin_api.services import schedule_impact as si

    tenant = request.tenant
    master = request.master
    day_labels, was, becomes = schedule_diff(request, master, tenant)
    if len(day_labels) == 1:
        when = day_labels[0].split(" ", 1)[1]  # без предлога: «на пятницу, 25 сентября»
        title = f"{master.name} просит изменить график на {when}"
    else:
        title = f"{master.name} просит изменить график на {len(day_labels)} дн."
    affected = si.affected_phrase(si.Impact(state=impact.state, affected=impact.affected))
    return SalonNotice(
        kind="schedule",
        tenant=tenant,
        ref=str(request.id),
        title=title,
        diff=Diff(was=was, becomes=becomes, affected=affected),
        buttons=(
            Button("Одобрить", action="approve"),
            Button("Отклонить", action="reject"),
            Button("Подробнее", action="details"),
        ),
        log={"master": getattr(master, "id", "-"), "request": request.id},
    )


def schedule_request_impact(request: Any) -> Impact:
    """Прочитать impact §142 для окна заявки — тем же сервисом, что Mini App."""

    from apps.admin_api.services import schedule_impact as si

    start = getattr(request, "requested_start", None)
    end = getattr(request, "requested_end", None)
    if start is None or end is None:
        return Impact(si.UNAVAILABLE, None)
    tz = _tz(request.tenant)
    result = si.impact_for_window(
        request.tenant,
        request.master,
        start_at=start.astimezone(tz).isoformat(),
        end_at=end.astimezone(tz).isoformat(),
    )
    return Impact(result.state, result.affected)


# ── тип 1 — клиент ждёт ответа ───────────────────────────────────────


def handoff_waiting_notice(task: Any) -> SalonNotice:
    """Тип 1: «Клиент Анна ждёт ответа человека» → Открыть диалог · Вернуть Ayla."""

    from apps.handoff.notify import admin_task_url

    name = _first_name(getattr(task, "bot_user", None))
    priority = (getattr(task, "priority", "") or "").lower()
    facts = ["Ayla передала диалог человеку."]
    if priority in ("high", "urgent"):
        facts.append("Приоритет: срочно.")
    buttons: list[Button] = []
    url = admin_task_url(task.id)
    if url:
        buttons.append(Button("Открыть диалог", url=url))
    buttons.append(Button("Вернуть Ayla", action="return"))
    if not url:
        buttons.append(Button("Открыть диалог", action="open"))
    return SalonNotice(
        kind="handoff",
        tenant=task.tenant,
        ref=str(task.id),
        title=f"Клиент {name} ждёт ответа",
        facts=tuple(facts),
        buttons=tuple(buttons),
        log={"task": task.id},
    )


# ── тип 3 — синхронизация с ошибкой ──────────────────────────────────


def sync_failed_notice(tenant: Any, age: Any) -> SalonNotice:
    """Тип 3: «Каталог салона не синхронизировался N» → Повторить · Подробнее."""

    last_ok = getattr(age, "last_ok_at", None)
    age_human = getattr(age, "age_human", "") or "давно"
    tz = _tz(tenant)
    last_line = (
        f"Последняя удачная синхронизация: {last_ok.astimezone(tz).strftime('%d.%m %H:%M')}"
        if last_ok
        else "Удачной синхронизации ещё не было"
    )
    # Ключ дедупа — час последнего успеха: пока каталог не ожил, повтор
    # beat-проверки — тот же случай, не новое событие.
    ref = f"{getattr(tenant, 'slug', '-')}:{last_ok.strftime('%Y%m%d%H') if last_ok else 'never'}"
    return SalonNotice(
        kind="sync",
        tenant=tenant,
        ref=ref,
        title=f"Синхронизация каталога не проходит уже {age_human}",
        facts=(
            last_line,
            "Пока это длится, бот отвечает клиентам по устаревшему каталогу.",
        ),
        buttons=(Button("Повторить", action="retry"), Button("Подробнее", action="details")),
        log={"age_seconds": getattr(age, "age_seconds", None)},
    )


# ── тип 4 — мастер перестал продаваться ──────────────────────────────


def master_unavailable_notice(master: Any, *, block: str) -> SalonNotice:
    """Тип 4: «Лера больше не доступна для записи: доступ отозван» → Открыть карточку."""

    reason = SALE_BLOCK_HUMAN.get(block, block)
    person = getattr(master, "linked_bot_user", None)
    extra: tuple[MaxAddress, ...] = ()
    # Мастеру — о себе, всегда. В ``MasterNotificationPrefs`` (DRF-1123)
    # переключателя для этого события нет: new_booking / booking_change /
    # personal_message — про клиентов, urgent принудительно включён (§805).
    # Читать чужой переключатель значило бы выдать его за свой; отдельный
    # переключатель «обо мне» — отдельный лист.
    if person is not None and getattr(person, "channel_user_id", ""):
        extra = (MaxAddress(user_id=person.channel_user_id),)
    return SalonNotice(
        kind="master_off",
        tenant=master.tenant,
        ref=f"{master.id}:{block}",
        title=f"{master.name} больше не доступна для записи",
        facts=(f"Причина: {reason}.",),
        buttons=_door("Открыть карточку", f"admin/team/{master.id}"),
        extra=extra,
        log={"master": master.id, "block": block},
    )


# ── тип 5 — запись требует вмешательства ─────────────────────────────


def booking_attention_notice(
    proxy: Any,
    *,
    reason: str,
    master_name: str | None = None,
    service_name: str | None = None,
) -> SalonNotice:
    """Тип 5: «Запись требует вмешательства: клиент отменил» → Открыть запись."""

    tenant = proxy.tenant
    tz = _tz(tenant)
    start = getattr(proxy, "start_at", None)
    when = start.astimezone(tz).strftime("%d.%m %H:%M") if start else "время не указано"
    name = _first_name(getattr(proxy, "bot_user", None))
    master = master_name or getattr(proxy, "master_name", "") or ""
    service = service_name or getattr(proxy, "service_name", "") or ""
    facts = [
        f"Причина: {BOOKING_REASON_HUMAN.get(reason, reason)}.",
        f"Клиент: {name}. Визит: {when}.",
    ]
    if service or master:
        facts.append(" · ".join(part for part in (service, master) if part))
    day_path = f"admin/day?date={start.astimezone(tz).date().isoformat()}" if start else "admin/day"
    return SalonNotice(
        kind="booking",
        tenant=tenant,
        ref=f"{proxy.appointment_id}:{reason}",
        title="Запись требует вмешательства",
        facts=tuple(facts),
        buttons=_door("Открыть запись", day_path),
        log={"appointment": proxy.appointment_id, "reason": reason},
    )


__all__ = [
    "BOOKING_REASON_HUMAN",
    "Button",
    "CB_PREFIX",
    "DEDUP_TTL_S",
    "Diff",
    "Impact",
    "KINDS",
    "SALE_BLOCK_HUMAN",
    "SalonNotice",
    "booking_attention_notice",
    "callback",
    "handoff_waiting_notice",
    "master_unavailable_notice",
    "notify",
    "parse_callback",
    "render",
    "schedule_diff",
    "schedule_request_impact",
    "schedule_request_notice",
    "sync_failed_notice",
]
