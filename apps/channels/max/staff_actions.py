"""What the salon bot's buttons actually do (DRF-1061).

Two answers, both read-only, both built from data that already exists:

* **the day** — who is coming, when, to whom. The salon had no way to see
  this at all: the admin surface returns an empty queryset for a
  non-specialist actor, so «кто сегодня придёт» was answered by asking the
  owner (audit §4.3).
* **pending requests** — masters asking to change their schedule. The
  approve/reject endpoints exist and work; what was missing was any way for
  the admin to learn a request had been filed. Nothing notified them.

Both read the mirror that actually holds pilot data — ``RemoteBookingProxy``
via ``apps.master_api.services.visit_source`` — not the local
``BookingRequest``, which on the pilot has four rows and no master on any of
them (DRF-1085).

Client phone numbers are never included, by any path (DRF-1039).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone as dt_timezone

from django.utils import timezone
from apps.tenancy.timezones import salon_zone

logger = logging.getLogger(__name__)

MAX_LISTED = 12
"""Cap on lines in one reply. A salon day beyond this is a Mini App job —
a chat message with forty rows is not readable on a phone."""

#: Ответ, когда источник записей (зеркало ``RemoteBookingProxy`` через
#: ``visit_source``) не ответил. Отдельное состояние, а не «записей нет»:
#: пустой день и упавший источник — разные факты, и путать их опасно
#: (мастер решил бы, что он свободен, когда зеркало просто молчит). Текст
#: зовёт попробовать снова; меню под ответом оставляет выход в кабинет, так
#: что состояние ошибки не запирает человека (инцидент М-6b: единое
#: состояние ошибки съело единственный выход). Без этого исключение из
#: ``master_visits`` всплывало выше ``_handle_button`` и, при уже занятом
#: ключе идемпотентности, гасило ответ целиком — немой тупик на нажатии.
DAY_UNAVAILABLE = (
    "Не удалось загрузить записи — источник временно недоступен. Попробуйте ещё раз чуть позже."
)


# ─── DRF-2759 — пустой день мастера говорит, ПОЧЕМУ он пуст ────────────────
#
# Было одно «На DD.MM записей нет.» на четыре разных факта: источник дня знал
# только визиты, не график. Мастер в выходной, мастер без графика и мастер,
# к которому сегодня просто никто не записался, читали одно и то же.
#
# Тексты — владельца, дословно (решение 02.10.2026 в DRF-2759). Не править
# ради стиля; обращение к персоналу — на «вы».
MASTER_DAY_OFF = "Сегодня у вас выходной по графику."
MASTER_SCHEDULE_NOT_SET = (
    "Ваш рабочий график ещё не настроен. Обратитесь к администратору салона, чтобы его настроить."
)
MASTER_NO_VISITS = "Сегодня у вас пока нет записей."
MASTER_SCHEDULE_UNAVAILABLE = "Не удалось получить информацию о вашем графике. Попробуйте позже."

EMPTY_DAY_OFF = "day_off"
EMPTY_NOT_SET = "schedule_not_set"
EMPTY_WORKING = "working_no_visits"
EMPTY_UNKNOWN = "schedule_unavailable"

EMPTY_DAY_TEXT = {
    EMPTY_DAY_OFF: MASTER_DAY_OFF,
    EMPTY_NOT_SET: MASTER_SCHEDULE_NOT_SET,
    EMPTY_WORKING: MASTER_NO_VISITS,
    EMPTY_UNKNOWN: MASTER_SCHEDULE_UNAVAILABLE,
}

#: Сколько секунд держать прочитанное состояние графика. При включённом
#: ``BOOKING_VIA_AYLA_REST`` рамка — три REST-чтения каталога, синхронно в
#: единственном потоке консьюмера; приветствие и «Мой день» подряд не должны
#: платить за них дважды. «Не удалось прочитать» НЕ кешируется: следующий
#: вопрос обязан спросить заново.
EMPTY_DAY_CACHE_SECONDS = 120


def _day_bounds(now: datetime, tz) -> tuple[datetime, datetime]:
    local = now.astimezone(tz)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(dt_timezone.utc), (
        start_local + timedelta(days=1) - timedelta(microseconds=1)
    ).astimezone(dt_timezone.utc)


def salon_day(tenant, *, now: datetime | None = None) -> str:
    """Today across every master of the salon.

    Grouped by master, because that is how a salon reads its day: the
    question is "who is busy when", not a flat chronological list.
    """

    from apps.catalog.models import CatalogMaster
    from apps.master_api.services.visit_source import master_visits

    now = now or timezone.now()
    tz = salon_zone(tenant)
    start, end = _day_bounds(now, tz)

    # `.objects` — the callers run inside tenant_scope (the consumer enters
    # it for the bot's tenant), so the scoped manager applies and a
    # cross-tenant read is impossible rather than just unintended.
    masters = list(
        CatalogMaster.objects.filter(archived_at__isnull=True, is_active=True).order_by("name")
    )
    if not masters:
        return "В салоне пока нет мастеров."

    blocks: list[str] = []
    total = 0
    try:
        for master in masters:
            visits = master_visits(master, start=start, end=end)
            if not visits:
                continue
            total += len(visits)
            lines = [f"*{master.name}*"]
            for visit in visits[:MAX_LISTED]:
                when = visit.visit_at.astimezone(tz).strftime("%H:%M") if visit.visit_at else "—"
                service = visit.service_name or "услуга не указана"
                lines.append(f"  {when} · {visit.client_name} · {service}")
            if len(visits) > MAX_LISTED:
                lines.append(f"  …и ещё {len(visits) - MAX_LISTED}")
            blocks.append("\n".join(lines))
    except Exception:  # noqa: BLE001 — источник недоступен ≠ «записей нет»; не 500
        logger.warning("staff_actions.salon_day.source_unavailable", exc_info=True)
        return DAY_UNAVAILABLE

    date_label = now.astimezone(tz).strftime("%d.%m")
    if not blocks:
        return f"На {date_label} записей нет."

    plural = "запись" if total == 1 else ("записи" if 2 <= total <= 4 else "записей")
    return f"*{date_label}* — {total} {plural}.\n\n" + "\n\n".join(blocks)


def master_day(master, *, now: datetime | None = None) -> str:
    """Today for one master."""

    from apps.master_api.services.visit_source import master_visits

    now = now or timezone.now()
    tz = salon_zone(master.tenant)
    start, end = _day_bounds(now, tz)

    try:
        visits = master_visits(master, start=start, end=end)
    except Exception:  # noqa: BLE001 — источник недоступен ≠ «записей нет»; не 500
        logger.warning("staff_actions.master_day.source_unavailable", exc_info=True)
        return DAY_UNAVAILABLE
    if not visits:
        # DRF-2759 — записей нет, и только теперь нужен график: почему их нет.
        # Есть записи — график не спрашивается вовсе, они показываются при
        # любом его состоянии (решение владельца: записи видны и при конфликте).
        return EMPTY_DAY_TEXT[empty_day_state(master, now=now)]

    date_label = now.astimezone(tz).strftime("%d.%m")
    lines = [f"*{date_label}* — {len(visits)}:"]
    for visit in visits[:MAX_LISTED]:
        when = visit.visit_at.astimezone(tz).strftime("%H:%M") if visit.visit_at else "—"
        service = visit.service_name or "услуга не указана"
        lines.append(f"{when} · {visit.client_name} · {service}")
    if len(visits) > MAX_LISTED:
        lines.append(f"…и ещё {len(visits) - MAX_LISTED}")
    return "\n".join(lines)


def empty_day_state(master, *, now: datetime | None = None) -> str:
    """Почему у мастера сегодня нет записей — одно из четырёх ``EMPTY_*``.

    Вызывается ТОЛЬКО когда записей нет. Один источник на приветствие и на
    кнопку «Мой день» — иначе они разойдутся с первой правкой.

    График читается не из локальных таблиц ``apps/scheduling``: это копия,
    которую боту никто не обновляет (DRF-2014; замер 15.09 — 28 строк у 4
    мастеров против 63 у 9 в каталоге). Рамку даёт
    ``master_api.services.dashboard._working_block_today_ex`` — то же правило
    «исключение дня → неделя», которым дашборд мастера в мини-приложении уже
    различает «выходной» и «часы не заданы» (DRF-2152, DRF-2200). Второго
    правила здесь нет намеренно.

    * рамка не прочитана (каталог не ответил; у салона нет владельца или
      администратора, от чьего имени читать; у строки мастера нет профиля в
      каталоге) → :data:`EMPTY_UNKNOWN`. «Не знаю» не становится ни выходным,
      ни «не настроен»: утверждать что-либо о графике, который не прочитан,
      нельзя;
    * рабочий блок на сегодня есть → :data:`EMPTY_WORKING`;
    * блока нет, а в недельном шаблоне нет ни одного рабочего дня →
      :data:`EMPTY_NOT_SET`;
    * блока нет, шаблон задан → :data:`EMPTY_DAY_OFF`. Сюда попадает и
      исключение на весь день (отпуск, больничный, отгул): провод каталога
      вида не называет, а текст владельца на этот случай один.
    """

    from django.core.cache import cache

    now = now or timezone.now()
    tz = salon_zone(master.tenant)
    today = now.astimezone(tz).date()
    key = f"staff:empty_day:{master.id}:{today.isoformat()}"
    try:
        cached = cache.get(key)
    except Exception:  # noqa: BLE001 — нет кеша: читаем источник, ход не падает
        cached = None
    if cached in (EMPTY_DAY_OFF, EMPTY_NOT_SET, EMPTY_WORKING):
        return str(cached)

    try:
        from apps.master_api.services.dashboard import _working_block_today_ex

        block, readable, hours_set = _working_block_today_ex(master, today, tz=tz)
    except Exception:  # noqa: BLE001 — любой отказ источника = «не знаю», не 500
        logger.warning("staff_actions.empty_day.frame_failed master=%s", master.id, exc_info=True)
        return EMPTY_UNKNOWN
    if not readable:
        return EMPTY_UNKNOWN

    if block is not None:
        state = EMPTY_WORKING
    elif not hours_set:
        state = EMPTY_NOT_SET
    else:
        state = EMPTY_DAY_OFF
    try:
        cache.set(key, state, EMPTY_DAY_CACHE_SECONDS)
    except Exception:  # noqa: BLE001
        pass
    return state


def is_empty_day_text(text: str) -> bool:
    """True для четырёх ответов пустого дня — под ними рисуется «Расписание»."""

    return text in EMPTY_DAY_TEXT.values()


def pending_request_rows(tenant) -> list[tuple[str, str]]:
    """``(request_id, label)`` for each pending request, capped.

    Returned separately from the text so the caller can attach one
    approve button per request without re-querying.
    """

    from apps.scheduling.models import ScheduleChangeRequest

    tz = salon_zone(tenant)
    rows = list(
        ScheduleChangeRequest.objects.filter(
            status=ScheduleChangeRequest.Status.PENDING,
        )
        .select_related("master")
        .order_by("created_at")[:MAX_LISTED]
    )
    out: list[tuple[str, str]] = []
    for row in rows:
        master_name = getattr(row.master, "name", "мастер")
        when = row.created_at.astimezone(tz).strftime("%d.%m") if row.created_at else ""
        out.append((str(row.id), f"✅ {master_name} · {when}"))
    return out


def approve_request(*, tenant, request_id: str, actor) -> str:
    """Approve one pending request from the chat. Returns what to reply.

    Only approval is available here, deliberately. Rejection requires a
    reason the master will read (`rejection_reason` is mandatory in the
    service and surfaced in their DM), and asking for free text in chat
    would mean an FSM — a "now send me the reason" state to get stuck in.
    Approval needs no text, so it is the tap-sized half; rejection stays
    where the person can type and see what they are refusing.
    """

    from uuid import UUID

    from apps.admin_api.services.availability import (
        AvailabilityDecisionError,
        approve_availability_request,
    )

    try:
        parsed = UUID(str(request_id))
    except (ValueError, AttributeError):
        return "Заявка не найдена."

    try:
        approve_availability_request(
            request_id=parsed,
            tenant_id=tenant.id,
            # No Django User exists for a MAX-only owner; the service
            # accepts None and records the BotUser as the audit actor,
            # same as the Mini App path does.
            actor=None,
            actor_bot_user_id=getattr(actor, "id", None),
            actor_bot_user=actor,
            actor_role="admin",
        )
    except AvailabilityDecisionError as exc:
        slug = getattr(exc, "slug", "")
        if slug == "already_decided":
            return "Эту заявку уже рассмотрели."
        if slug == "not_found":
            return "Заявка не найдена."
        logger.warning("staff_actions.approve_failed slug=%s request=%s", slug, request_id)
        return "Не получилось одобрить заявку. Попробуйте из кабинета салона."
    except Exception:  # noqa: BLE001 — a chat tap must not raise
        logger.exception("staff_actions.approve_crashed request=%s", request_id)
        return "Не получилось одобрить заявку. Попробуйте из кабинета салона."

    return "Заявка одобрена. Мастер получит уведомление."


def salon_readiness(tenant) -> str:
    """«Проверить готовность» (DRF-2117): поимённый список того, что мешает записи.

    Каталог + зеркало — :mod:`apps.admin_api.services.salon_readiness`;
    источник недоступен → «не удалось проверить», не «готов».
    """

    from apps.admin_api.services.salon_readiness import check_salon_readiness, render

    return render(check_salon_readiness(tenant))


def pending_requests(tenant) -> str:
    """Schedule-change requests waiting on an admin.

    The approve/reject endpoints have existed and worked all along; what
    was missing was any way for an admin to find out a request was filed
    (nothing notified them). This is that missing half — read-only for now:
    deciding still happens in the Mini App, where the confirmation and the
    audit trail already live.
    """

    from apps.scheduling.models import ScheduleChangeRequest

    tz = salon_zone(tenant)
    rows = list(
        ScheduleChangeRequest.objects.filter(
            status=ScheduleChangeRequest.Status.PENDING,
        )
        .select_related("master")
        .order_by("created_at")[: MAX_LISTED + 1]
    )
    if not rows:
        return "Заявок от мастеров нет."

    lines = ["*Заявки от мастеров*"]
    for row in rows[:MAX_LISTED]:
        master_name = getattr(row.master, "name", "мастер")
        when = row.created_at.astimezone(tz).strftime("%d.%m") if row.created_at else ""
        lines.append(f"• {master_name} · {when}")
    if len(rows) > MAX_LISTED:
        lines.append("…и ещё")
    # Approve is a button below this message; rejection needs a written
    # reason the master will read, so it stays where they can type it.
    lines.append("\nОдобрить — кнопкой ниже. Отклонить с причиной — в кабинете салона.")
    return "\n".join(lines)
