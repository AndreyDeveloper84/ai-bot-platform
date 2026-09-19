"""Приветствия персонала по роли с живой сводкой (DRF-2114, §50 п.4).

После пре-чека (DRF-2113) человек со STAFF-вердиктом слышит не «Салон «X».»,
а приветствие владельца — дословно из ``docs/modules/меню салонный
бот.md`` — с числами из живых источников:

* записи дня — :func:`apps.admin_api.services.salon_day.build_salon_day`
  (зеркало ``RemoteBookingProxy``, тот же источник, что у экрана «День»);
  мастеру — его ``DayMaster``, «ближайшая» — первый предстоящий визит после
  «сейчас» в поясе салона (это ЗАПИСЬ, не свободное окно DRF-2014);
* «работают N мастера» — ``AVAILABLE`` (:mod:`apps.catalog.master_state`)
  в тенанте;
* «ситуация требует внимания» — заявки на график ``PENDING`` +
  handoff-очередь (``AdminTask`` OPEN / IN_PROGRESS) тенанта; ноль —
  «ожидающих ответа нет».

**Ни одного числа без источника (§103):** источник недоступен — строка
опускается, а не печатается «0». Каждый счётчик независим.

Первое приветствие (после первого успешного подключения) — по
``BotUser.welcomed_at`` рабочей строки (на салонном стриме поле пусто:
клиентский S1 здесь не идёт); штампуется после первого приветствия. До
DRF-2117 «Проверить готовность» ведёт на «Сегодня», и текст говорит это
прямо; спокойная сводка печатается без «графики и услуги настроены» —
утверждения, у которого до DRF-2117 нет источника (решение главного окна:
§103 важнее дословности).

Склонение имени салона («в «Формулу тела»», «В «Формуле тела»») из
``Tenant.name`` не выводится — печатается форма «для салона «{имя}»» /
«В салоне «{имя}»» с именем в именительном падеже. Названо пределом.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.utils import timezone

logger = logging.getLogger(__name__)
NL = chr(10)  # перевод строки — одной константой, чтобы тексты читались как у владельца

# ─── тексты владельца (§50 п.4), дословно ─────────────────────────────────

MASTER_HELLO = "Здравствуйте, {name}!"
MASTER_ROLE_LINE = "Вы вошли в Ayla для салона «{salon}» как мастер."
MASTER_TODAY_LINE = "Сегодня у вас {records}."
MASTER_NEXT_LINE = "Ближайшая — {client}, {service} {minutes} минут, в {time}."

ADMIN_HELLO = "Здравствуйте, {name}!"
ADMIN_ROLE_LINE = "Вы вошли в Ayla для салона «{salon}» как {role}."
ADMIN_TODAY_HEAD = "Сегодня:"
#: Строки сводки — «7 записей;» / «работают 3 мастера;» / «одна ситуация требует
#: внимания.» — каждая со своим знаком: «;» между, «.» у последней.
ADMIN_NO_WAITING = "ожидающих ответа нет"
ADMIN_CALM_LINE = "В салоне «{salon}» всё в порядке: сегодня {records}; ожидающих ответа нет."
#: После DRF-2117 к спокойной строке возвращается «; графики и услуги настроены.»

FIRST_GREETING = (
    "Здравствуйте, {name}!\n\n"
    "Вы вошли в Ayla для салона «{salon}».\n"
    "Ваша роль — {role}.\n\n"
    "Здесь можно управлять записями, диалогами с клиентами, расписанием, услугами "
    "и командой. Ayla будет писать сюда, когда потребуется ваше решение.\n\n"
    "Сначала проверим, готов ли салон принимать записи."
)
#: До DRF-2117 (салонная готовность) — честная строка о том, куда ведёт кнопка.
FIRST_GREETING_READINESS_PENDING = (
    "Проверка готовности появится здесь позже — пока откроется «Сегодня»."
)

ROLE_WORDS = {"owner": "владелец", "admin": "администратор"}

BUTTON_OPEN_CABINET = "Открыть кабинет"
BUTTON_OPEN_SALON = "Открыть салон"
BUTTON_TODAY = "Сегодня"
BUTTON_SCHEDULE = "Расписание"
BUTTON_ASK_AYLA = "Спросить Ayla"
BUTTON_NEW_BOOKING = "＋ Новая запись"
BUTTON_CHECK_READINESS = "Проверить готовность"

MASTER_SLUGS = {
    "today": "open_master_today",
    "schedule": "open_master_schedule",
    "ayla": "open_master_ayla",
}
ADMIN_SLUGS = {
    "today": "open_admin_today",
    "schedule": "open_admin_schedule",
    "ayla": "open_admin_ayla",
    "new_booking": "open_admin_booking_new",
}


# ─── склонения ────────────────────────────────────────────────────────────


def _plural(n: int, one: str, few: str, many: str) -> str:
    n_abs = abs(n)
    if n_abs % 10 == 1 and n_abs % 100 != 11:
        return one
    if 2 <= n_abs % 10 <= 4 and not 12 <= n_abs % 100 <= 14:
        return few
    return many


def records_phrase(n: int) -> str:
    return f"{n} {_plural(n, 'запись', 'записи', 'записей')}"


def masters_phrase(n: int) -> str:
    verb = "работает" if (abs(n) % 10 == 1 and abs(n) % 100 != 11) else "работают"
    return f"{verb} {n} {_plural(n, 'мастер', 'мастера', 'мастеров')}"


def attention_phrase(n: int) -> str:
    if n == 1:
        return "одна ситуация требует внимания"
    return f"{n} {_plural(n, 'ситуация требует', 'ситуации требуют', 'ситуаций требуют')} внимания"


# ─── живые данные ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NextVisit:
    client: str
    service: str
    minutes: int
    time: str


@dataclass(frozen=True)
class GreetingData:
    """Что нашлось по источникам. ``None`` — источник недоступен, строка опускается."""

    records: int | None = None
    masters_available: int | None = None
    attention: int | None = None
    my_records: int | None = None
    next_visit: NextVisit | None = None
    missing: tuple[str, ...] = field(default_factory=tuple)


def _tenant_now(tenant: Any) -> datetime:
    try:
        return timezone.now().astimezone(ZoneInfo(getattr(tenant, "timezone", "") or "UTC"))
    except Exception:  # noqa: BLE001 — незнакомый пояс не должен ронять приветствие
        return timezone.now()


# Три источника — три функции, чтобы подмена источника в тесте меняла ЧИСЛО,
# а отказ источника опускал строку (§103). Вызываются в tenant_scope.


def _salon_day(tenant: Any, now: datetime):
    from apps.admin_api.services.salon_day import build_salon_day

    return build_salon_day(tenant, day=now.date(), now=now)


def _masters_available() -> int:
    from apps.catalog.master_state import AVAILABLE
    from apps.catalog.models import CatalogMaster

    return CatalogMaster.objects.filter(AVAILABLE).count()


def _attention() -> int:
    from apps.handoff.models import AdminTask
    from apps.scheduling.models import ScheduleChangeRequest

    pending = ScheduleChangeRequest.objects.filter(
        status=ScheduleChangeRequest.Status.PENDING
    ).count()
    waiting = AdminTask.objects.filter(
        status__in=(AdminTask.Status.OPEN, AdminTask.Status.IN_PROGRESS)
    ).count()
    return pending + waiting


def gather(tenant: Any, role_ctx: Any, *, now: datetime | None = None) -> GreetingData:
    """Собрать сводку по живым источникам — каждый независимо, отказ = None."""

    from apps.tenancy.context import tenant_scope

    now = now or _tenant_now(tenant)
    missing: list[str] = []
    records = masters_available = attention = my_records = None
    next_visit = None

    with tenant_scope(tenant):
        try:
            day = _salon_day(tenant, now)
        except Exception:  # noqa: BLE001 — §103: без источника строка опускается
            logger.warning("channels.max.salon.greeting.day_unavailable", exc_info=True)
            day = None
            missing.append("records")
        if day is not None:
            records = day.summary.total - day.summary.released
            if getattr(role_ctx, "is_master", False):
                my_records, next_visit = _master_day(day, role_ctx, now)

        try:
            masters_available = _masters_available()
        except Exception:  # noqa: BLE001
            logger.warning("channels.max.salon.greeting.masters_unavailable", exc_info=True)
            missing.append("masters")

        try:
            attention = _attention()
        except Exception:  # noqa: BLE001
            logger.warning("channels.max.salon.greeting.attention_unavailable", exc_info=True)
            missing.append("attention")

    return GreetingData(
        records=records,
        masters_available=masters_available,
        attention=attention,
        my_records=my_records,
        next_visit=next_visit,
        missing=tuple(missing),
    )


def _master_day(day: Any, role_ctx: Any, now: datetime) -> tuple[int | None, NextVisit | None]:
    from apps.master_api.services.visit_source import RELEASED_STATUSES, UPCOMING_STATUSES

    master_id = str(getattr(role_ctx, "master_id", "") or "")
    mine = next((m for m in day.masters if str(m.master_id) == master_id), None)
    if mine is None:
        return None, None
    standing = [v for v in mine.visits if v.status not in RELEASED_STATUSES]
    upcoming = sorted(
        (v for v in standing if v.status in UPCOMING_STATUSES and v.start_at > now),
        key=lambda v: v.start_at,
    )
    nxt = None
    if upcoming:
        v = upcoming[0]
        local = v.start_at.astimezone(now.tzinfo) if now.tzinfo else v.start_at
        nxt = NextVisit(
            client=v.client_first_name or "клиент",
            service=v.service_name or "услуга",
            minutes=int(v.duration_min or 0),
            time=local.strftime("%H:%M"),
        )
    return len(standing), nxt


# ─── рендер ───────────────────────────────────────────────────────────────


def _display_name(bot_user: Any, role_ctx: Any) -> str:
    if getattr(role_ctx, "is_master", False) and getattr(role_ctx, "master_id", None):
        try:
            from apps.catalog.models import CatalogMaster

            name = (
                CatalogMaster.objects.filter(pk=role_ctx.master_id)
                .values_list("name", flat=True)
                .first()
            )
            if name:
                return str(name).split()[0]
        except Exception:  # noqa: BLE001
            pass
    raw = str(getattr(bot_user, "display_name", "") or "").strip()
    return raw.split()[0] if raw else "коллега"


def admin_role_word(role_ctx: Any) -> str:
    if getattr(role_ctx, "is_owner", False):
        return ROLE_WORDS["owner"]
    return ROLE_WORDS["admin"]


def render_master(name: str, salon: str, data: GreetingData) -> str:
    lines = [MASTER_HELLO.format(name=name), MASTER_ROLE_LINE.format(salon=salon)]
    if data.my_records is not None:
        lines.append(MASTER_TODAY_LINE.format(records=records_phrase(data.my_records)))
    if data.next_visit is not None:
        n = data.next_visit
        lines.append(
            MASTER_NEXT_LINE.format(
                client=n.client, service=n.service, minutes=n.minutes, time=n.time
            )
        )
    return NL.join(lines)


def render_admin(name: str, salon: str, role_word: str, data: GreetingData) -> str:
    head = [ADMIN_HELLO.format(name=name), ADMIN_ROLE_LINE.format(salon=salon, role=role_word)]
    if data.attention == 0 and data.records is not None:
        calm = ADMIN_CALM_LINE.format(salon=salon, records=records_phrase(data.records))
        return NL.join(head + [calm])
    items: list[str] = []
    if data.records is not None:
        items.append(records_phrase(data.records))
    if data.masters_available is not None:
        items.append(masters_phrase(data.masters_available))
    if data.attention is not None:
        items.append(attention_phrase(data.attention) if data.attention else ADMIN_NO_WAITING)
    if not items:
        return NL.join(head)
    body = [f"{item};" for item in items[:-1]] + [f"{items[-1]}."]
    return NL.join(head + [ADMIN_TODAY_HEAD] + body)


def render_first(name: str, salon: str, role_word: str) -> str:
    return (
        FIRST_GREETING.format(name=name, salon=salon, role=role_word)
        + NL
        + FIRST_GREETING_READINESS_PENDING
    )


# ─── кнопки ───────────────────────────────────────────────────────────────


def _app_button(entry: Any, label: str, slug: str) -> dict[str, str] | None:
    """``open_app`` на экран тройки; без ``web_app`` — ссылка на маршрут; без обоих — ничего."""

    from apps.skills.welcome.skill import MINIAPP_ROUTES

    if entry is None or slug not in MINIAPP_ROUTES:
        return None
    web_app = getattr(entry, "web_app", "") or ""
    if web_app:
        return {"label": label, "callback": slug, "web_app": web_app}
    base = getattr(entry, "miniapp_url", "") or ""
    if base:
        return {"label": label, "url": f"{base.rstrip('/')}/{MINIAPP_ROUTES[slug].lstrip('/')}"}
    return None


def master_buttons(entry: Any) -> list[dict[str, str]]:
    from apps.channels.max.staff_menu import _miniapp_button

    buttons = [
        _miniapp_button(entry, BUTTON_OPEN_CABINET),
        _app_button(entry, BUTTON_TODAY, MASTER_SLUGS["today"]),
        _app_button(entry, BUTTON_SCHEDULE, MASTER_SLUGS["schedule"]),
        _app_button(entry, BUTTON_ASK_AYLA, MASTER_SLUGS["ayla"]),
    ]
    return [b for b in buttons if b]


def admin_buttons(entry: Any) -> list[dict[str, str]]:
    from apps.channels.max.staff_menu import _miniapp_button

    buttons = [
        _miniapp_button(entry, BUTTON_OPEN_SALON),
        _app_button(entry, BUTTON_TODAY, ADMIN_SLUGS["today"]),
        _app_button(entry, BUTTON_SCHEDULE, ADMIN_SLUGS["schedule"]),
        _app_button(entry, BUTTON_ASK_AYLA, ADMIN_SLUGS["ayla"]),
        _app_button(entry, BUTTON_NEW_BOOKING, ADMIN_SLUGS["new_booking"]),
    ]
    return [b for b in buttons if b]


def first_buttons(entry: Any) -> list[dict[str, str]]:
    from apps.channels.max.staff_menu import _miniapp_button

    buttons = [
        _app_button(entry, BUTTON_CHECK_READINESS, ADMIN_SLUGS["today"]),
        _miniapp_button(entry, BUTTON_OPEN_SALON),
    ]
    return [b for b in buttons if b]


# ─── вход ─────────────────────────────────────────────────────────────────


def is_first_entry(bot_user: Any) -> bool:
    return getattr(bot_user, "welcomed_at", None) is None


def mark_greeted(bot_user: Any) -> None:
    """Первое приветствие прозвучало — следующие входы обычные."""

    if getattr(bot_user, "welcomed_at", None) is not None:
        return
    try:
        bot_user.welcomed_at = timezone.now()
        bot_user.save(update_fields=["welcomed_at"])
    except Exception:  # noqa: BLE001 — худший случай: первое приветствие повторится
        logger.warning("channels.max.salon.greeting.mark_failed", exc_info=True)


def build_greeting(
    *, bot_user: Any, role_ctx: Any, tenant: Any, entry: Any, now: datetime | None = None
) -> tuple[str, list[dict[str, str]]] | None:
    """(текст, кнопки) по роли — или ``None``, если для роли приветствия нет (ресепшн).

    Мастер → мастерское; владелец / администратор → первое или обычное.
    """

    salon = getattr(tenant, "name", "") or getattr(tenant, "slug", "")
    name = _display_name(bot_user, role_ctx)
    is_admin_side = getattr(role_ctx, "is_owner", False) or getattr(role_ctx, "is_admin", False)
    if is_admin_side:
        role_word = admin_role_word(role_ctx)
        if is_first_entry(bot_user):
            return render_first(name, salon, role_word), first_buttons(entry)
        data = gather(tenant, role_ctx, now=now)
        return render_admin(name, salon, role_word, data), admin_buttons(entry)
    if getattr(role_ctx, "is_master", False):
        data = gather(tenant, role_ctx, now=now)
        return render_master(name, salon, data), master_buttons(entry)
    return None


__all__ = [
    "ADMIN_SLUGS",
    "MASTER_SLUGS",
    "GreetingData",
    "NextVisit",
    "attention_phrase",
    "build_greeting",
    "gather",
    "is_first_entry",
    "mark_greeted",
    "masters_phrase",
    "records_phrase",
    "render_admin",
    "render_first",
    "render_master",
]
