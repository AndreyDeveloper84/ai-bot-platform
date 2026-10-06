"""«✍️ Записать клиента» — a new booking from the salon bot's chat (DRF-2786).

The owner or an administrator books a client without opening the Mini App
(«＋ Новая запись» stays beside it). Same services as the admin Mini App —
``apps.admin_api.services.booking``: ``bookable_service``, ``bookable_starts``,
``search_customers_as``, ``create_appointment_as`` — so the chat and the
screen cannot disagree about what is bookable or what Ayla answered.

### Steps

master → the master's service → day → a free start (from the schedule; a
start without a full timestamp is not offered, as on the screen — the bot
never picks a timezone) → client (typed: name or phone → matches as buttons,
or «Новый клиент» → name → phone) → «Проверьте запись» → «Создать запись».

### The one piece of memory

The salon bot is built without an FSM (``salon_handler``: «nothing to get
stuck in»). The client step needs typed text, so this flow keeps a DRAFT in
the cache — and only that:

* 15 minutes, then it is gone; a tap on a gone draft says so and offers to
  start again;
* any other staff button drops it, so a line typed later to the assistant is
  never swallowed as a client search;
* its id is the idempotency key of the create: a second «Создать запись»
  (after «расписание не ответило») cannot make a second booking.

Words — the Mini App's «Новая запись» (``NewBookingForm.tsx``); the few new
ones are marked. Staff register («вы»).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from django.core.cache import cache
from django.utils import timezone

from apps.channels.max.staff_menu import (
    CB_BK_CLIENT_PREFIX,
    CB_BK_CREATE,
    CB_BK_DATE_PREFIX,
    CB_BK_MASTER_PREFIX,
    CB_BK_NEW,
    CB_BK_NEW_CLIENT,
    CB_BK_SERVICE_PREFIX,
    CB_BK_SLOT_PREFIX,
    CB_DAY,
)
from apps.tenancy.timezones import salon_zone

logger = logging.getLogger(__name__)

LOG = "channels.max.staff_booking"
DRAFT_TTL_SECONDS = 15 * 60
DAYS_OFFERED = 7
MAX_CHOICES = 12
MAX_NAME_LEN = 150
MAX_PHONE_LEN = 20

# ── words: the Mini App's «Новая запись» unless marked NEW ──────────────────
TITLE = "Новая запись"
REVIEW_TITLE = "Проверьте запись"
LABEL_MASTER = "Мастер"
LABEL_SERVICE = "Услуга"
LABEL_WHEN = "Дата и время"
LABEL_CLIENT = "Клиент"
CHOOSE = "выбрать"
LABEL_NEW_CLIENT = "Новый клиент"
LABEL_CREATE = "Создать запись"
LABEL_LATER = "Не сейчас"
ASK_QUERY = "Поиск клиента — напишите имя или телефон."
ASK_NAME = "Имя клиента"
ASK_PHONE = "Телефон клиента"
NO_MATCHES = "Совпадений нет. Возможно, клиент записан под другим именем или телефоном."
SCHEDULE_DOWN = (
    "Расписание сейчас недоступно — свободное время не показать. Попробуйте через минуту."
)
SLOTS_FAILED = "Не удалось получить свободное время. Это не значит, что его нет."
CREATED = "Запись создана."
TAKEN = "Это время уже занято. Выберите другое."
KEPT = "Введённые данные сохранены."
NO_ANSWER = (
    "Ответ от расписания не пришёл. Запись могла быть создана — обновите день, "
    "прежде чем пробовать снова."
)
FORBIDDEN = "Недостаточно прав для записи. Обратитесь к владельцу салона."
# NEW (not on the screen — the chat has states the screen does not):
DRAFT_GONE = "Черновик записи устарел — начните заново."
NO_MASTERS = "В салоне пока нет мастеров."
NO_SERVICES = "У этого мастера нет услуг для записи."
NO_FREE_TIME = "На этот день свободного времени нет. Выберите другой день."
SEARCH_FAILED = "Поиск клиента сейчас недоступен. Попробуйте через минуту."
TOO_LONG = "Слишком длинно — проверьте и напишите ещё раз."
FAILED = "Не получилось создать запись. Попробуйте ещё раз чуть позже."

_WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


@dataclass(frozen=True)
class Reply:
    text: str
    rows: list[list[dict[str, str]]] = field(default_factory=list)
    #: True — the menu goes under the text instead of ``rows`` (the flow ended).
    menu: bool = False


def _key(tenant: Any, bot_user: Any) -> str:
    return f"staff:bkdraft:{tenant.id}:{getattr(bot_user, 'id', '')}"


def _load(tenant: Any, bot_user: Any) -> dict[str, Any] | None:
    try:
        draft = cache.get(_key(tenant, bot_user))
    except Exception:  # noqa: BLE001 — no cache = no draft, never a crash
        return None
    return draft if isinstance(draft, dict) else None


def _save(tenant: Any, bot_user: Any, draft: dict[str, Any]) -> None:
    cache.set(_key(tenant, bot_user), draft, DRAFT_TTL_SECONDS)


def drop(tenant: Any, bot_user: Any) -> None:
    """Forget the draft — another staff button was tapped."""

    try:
        cache.delete(_key(tenant, bot_user))
    except Exception:  # noqa: BLE001
        pass


def awaits_text(tenant: Any, bot_user: Any) -> bool:
    draft = _load(tenant, bot_user)
    return bool(draft and draft.get("awaiting"))


# ── rendering ──────────────────────────────────────────────────────────────


def _later() -> list[dict[str, str]]:
    return [{"label": LABEL_LATER, "callback": CB_DAY}]


def _summary(draft: dict[str, Any], *, title: str = TITLE) -> str:
    lines = [f"*{title}*"]
    lines.append(f"{LABEL_MASTER}: {draft.get('master_name') or CHOOSE}")
    lines.append(f"{LABEL_SERVICE}: {draft.get('service_name') or CHOOSE}")
    lines.append(f"{LABEL_WHEN}: {draft.get('when') or CHOOSE}")
    lines.append(f"{LABEL_CLIENT}: {draft.get('client_label') or CHOOSE}")
    return "\n".join(lines)


def _gone() -> Reply:
    return Reply(DRAFT_GONE, [[{"label": START_LABEL, "callback": CB_BK_NEW}], _later()])


#: The chat entry's label — approved by the main window on 05.10.2026.
START_LABEL = "✍️ Записать клиента"


def _grid(buttons: list[dict[str, str]], width: int) -> list[list[dict[str, str]]]:
    return [buttons[i : i + width] for i in range(0, len(buttons), width)]


# ── steps ──────────────────────────────────────────────────────────────────


def start(*, tenant: Any, bot_user: Any) -> Reply:
    """A fresh draft and the master step."""

    from apps.catalog.models import CatalogMaster

    draft: dict[str, Any] = {"id": str(uuid.uuid4())}
    masters = list(
        CatalogMaster.objects.filter(archived_at__isnull=True, is_active=True).order_by("name")[
            :MAX_CHOICES
        ]
    )
    if not masters:
        return Reply(NO_MASTERS, menu=True)
    _save(tenant, bot_user, draft)
    buttons = [{"label": m.name, "callback": f"{CB_BK_MASTER_PREFIX}{m.id}"} for m in masters]
    return Reply(_summary(draft), [*_grid(buttons, 1), _later()])


def choose_master(*, tenant: Any, bot_user: Any, ref: str) -> Reply:
    from apps.admin_api.views import _get_master_or_404
    from apps.catalog.models import MasterService

    draft = _load(tenant, bot_user)
    if draft is None:
        return _gone()
    master = _get_master_or_404(tenant.id, ref)
    if master is None:
        return start(tenant=tenant, bot_user=bot_user)
    services = list(
        MasterService.objects.filter(master=master)
        .sellable()
        .select_related("service")
        .order_by("service__name")[:MAX_CHOICES]
    )
    draft.update(master_id=str(master.id), master_name=master.name, awaiting=None)
    for key in ("service_id", "service_name", "when", "start_at", "slots", "day"):
        draft.pop(key, None)
    _save(tenant, bot_user, draft)
    if not services:
        return Reply(f"{_summary(draft)}\n\n{NO_SERVICES}", [_later()])
    buttons = [
        {"label": ms.service.name, "callback": f"{CB_BK_SERVICE_PREFIX}{ms.service.id}"}
        for ms in services
    ]
    return Reply(_summary(draft), [*_grid(buttons, 1), _later()])


def _date_rows(tenant: Any) -> list[list[dict[str, str]]]:
    today = timezone.now().astimezone(salon_zone(tenant)).date()
    buttons = []
    for offset in range(DAYS_OFFERED):
        day = today + timedelta(days=offset)
        buttons.append(
            {
                "label": f"{day:%d.%m} {_WEEKDAYS[day.weekday()]}",
                "callback": f"{CB_BK_DATE_PREFIX}{day.isoformat()}",
            }
        )
    return [*_grid(buttons, 3), _later()]


def choose_service(*, tenant: Any, bot_user: Any, ref: str) -> Reply:
    from apps.admin_api.services.booking import Refusal, bookable_service

    draft = _load(tenant, bot_user)
    if draft is None or not draft.get("master_id"):
        return _gone()
    service = bookable_service(tenant.id, ref, log=LOG)
    if isinstance(service, Refusal):
        return Reply(f"{_summary(draft)}\n\n{SLOTS_FAILED}", [_later()])
    draft.update(service_id=str(service.id), service_name=service.name, awaiting=None)
    for key in ("when", "start_at", "slots", "day"):
        draft.pop(key, None)
    _save(tenant, bot_user, draft)
    return Reply(_summary(draft), _date_rows(tenant))


def _slots_reply(tenant: Any, bot_user: Any, draft: dict[str, Any], day: date) -> Reply:
    from apps.admin_api.services.booking import Refusal, bookable_service, bookable_starts
    from apps.admin_api.views import _get_master_or_404

    master = _get_master_or_404(tenant.id, draft["master_id"])
    service = bookable_service(tenant.id, draft["service_id"], log=LOG)
    if master is None or isinstance(service, Refusal):
        return Reply(f"{_summary(draft)}\n\n{SLOTS_FAILED}", [_later()])
    slots = bookable_starts(master=master, service=service, day=day, log=LOG)
    if isinstance(slots, Refusal):
        text = SCHEDULE_DOWN if slots.slug == "schedule_unavailable" else SLOTS_FAILED
        return Reply(f"{_summary(draft)}\n\n{text}", _date_rows(tenant))
    # Only starts the schedule stamped in full: the bot never picks a zone.
    offered = [
        (str(getattr(s, "time", "") or ""), str(getattr(s, "datetime", "") or ""))
        for s in slots
        if getattr(s, "datetime", None)
    ][: MAX_CHOICES * 2]
    draft.update(day=day.isoformat(), slots=offered, awaiting=None)
    draft.pop("when", None)
    draft.pop("start_at", None)
    _save(tenant, bot_user, draft)
    if not offered:
        text = NO_FREE_TIME if not slots else SLOTS_FAILED
        return Reply(f"{_summary(draft)}\n\n{text}", _date_rows(tenant))
    buttons = [
        {"label": time_label, "callback": f"{CB_BK_SLOT_PREFIX}{i}"}
        for i, (time_label, _) in enumerate(offered)
    ]
    return Reply(_summary(draft), [*_grid(buttons, 4), _later()])


def choose_date(*, tenant: Any, bot_user: Any, ref: str) -> Reply:
    draft = _load(tenant, bot_user)
    if draft is None or not draft.get("service_id"):
        return _gone()
    try:
        day = date.fromisoformat(ref)
    except ValueError:
        return Reply(_summary(draft), _date_rows(tenant))
    return _slots_reply(tenant, bot_user, draft, day)


def _when_label(tenant: Any, start_at: str, fallback: str) -> str:
    try:
        moment = datetime.fromisoformat(start_at)
    except ValueError:
        return fallback
    if moment.tzinfo is None:
        return fallback
    return moment.astimezone(salon_zone(tenant)).strftime("%d.%m в %H:%M")


def choose_slot(*, tenant: Any, bot_user: Any, ref: str) -> Reply:
    draft = _load(tenant, bot_user)
    if draft is None or not draft.get("slots"):
        return _gone()
    try:
        time_label, start_at = draft["slots"][int(ref)]
    except (ValueError, IndexError, TypeError):
        return _gone()
    draft.update(start_at=start_at, when=_when_label(tenant, start_at, time_label))
    if draft.get("client_label"):
        draft["awaiting"] = None
        _save(tenant, bot_user, draft)
        return review(draft)
    draft["awaiting"] = "query"
    _save(tenant, bot_user, draft)
    return Reply(
        f"{_summary(draft)}\n\n{ASK_QUERY}",
        [[{"label": LABEL_NEW_CLIENT, "callback": CB_BK_NEW_CLIENT}], _later()],
    )


def new_client(*, tenant: Any, bot_user: Any) -> Reply:
    draft = _load(tenant, bot_user)
    if draft is None or not draft.get("start_at"):
        return _gone()
    draft.update(awaiting="name", client_id=None, client_label=None)
    _save(tenant, bot_user, draft)
    return Reply(f"{_summary(draft)}\n\n{ASK_NAME}", [_later()])


def choose_client(*, tenant: Any, bot_user: Any, ref: str) -> Reply:
    draft = _load(tenant, bot_user)
    if draft is None or not draft.get("start_at"):
        return _gone()
    found = {row["id"]: row["name"] for row in draft.get("matches") or []}
    if ref not in found:
        return _gone()
    draft.update(client_id=ref, client_label=found[ref], awaiting=None)
    draft.pop("client_name", None)
    draft.pop("client_phone", None)
    _save(tenant, bot_user, draft)
    return review(draft)


def take_text(*, tenant: Any, bot_user: Any, text: str) -> Reply | None:
    """A typed line while the draft waits for one; None — not ours."""

    from apps.admin_api.services.booking import (
        Refusal,
        public_customer_row,
        search_customers_as,
    )
    from apps.integrations.ayla.user_proxy import external_user_id_for

    draft = _load(tenant, bot_user)
    if not draft or not draft.get("awaiting"):
        return None
    line = (text or "").strip()
    step = draft["awaiting"]

    if step == "name":
        if len(line) > MAX_NAME_LEN or not line:
            return Reply(TOO_LONG if line else ASK_NAME, [_later()])
        draft.update(client_name=line, awaiting="phone")
        _save(tenant, bot_user, draft)
        return Reply(f"{_summary(draft)}\n\n{ASK_PHONE}", [_later()])
    if step == "phone":
        if len(line) > MAX_PHONE_LEN or not line:
            return Reply(TOO_LONG if line else ASK_PHONE, [_later()])
        draft.update(
            client_phone=line,
            client_id=None,
            client_label=f"{draft.get('client_name')} (новый)",
            awaiting=None,
        )
        _save(tenant, bot_user, draft)
        return review(draft)

    # step == "query"
    rows = search_customers_as(
        actor=external_user_id_for(bot_user), tenant=tenant, query=line, log=LOG
    )
    new_row = [{"label": LABEL_NEW_CLIENT, "callback": CB_BK_NEW_CLIENT}]
    if isinstance(rows, Refusal):
        text_ = ASK_QUERY if rows.slug == "bad_request" else SEARCH_FAILED
        return Reply(text_, [new_row, _later()])
    matches = [public_customer_row(r) for r in rows][:MAX_CHOICES]
    draft["matches"] = [{"id": m["id"], "name": m["name"]} for m in matches if m["id"]]
    _save(tenant, bot_user, draft)
    if not draft["matches"]:
        return Reply(NO_MATCHES, [new_row, _later()])
    buttons = [
        {"label": m["name"], "callback": f"{CB_BK_CLIENT_PREFIX}{m['id']}"}
        for m in draft["matches"]
    ]
    return Reply(_summary(draft), [*_grid(buttons, 1), new_row, _later()])


def review(draft: dict[str, Any]) -> Reply:
    return Reply(
        _summary(draft, title=REVIEW_TITLE),
        [[{"label": LABEL_CREATE, "callback": CB_BK_CREATE}], _later()],
    )


def create(*, tenant: Any, bot_user: Any) -> Reply:
    """«Создать запись» — the draft's id is the idempotency key."""

    from apps.admin_api.services.booking import (
        Refusal,
        bookable_service,
        create_appointment_as,
    )
    from apps.admin_api.views import _get_master_or_404
    from apps.integrations.ayla.user_proxy import external_user_id_for

    draft = _load(tenant, bot_user)
    if (
        draft is None
        or not draft.get("start_at")
        or not (draft.get("client_id") or draft.get("client_phone"))
    ):
        return _gone()
    master = _get_master_or_404(tenant.id, draft["master_id"])
    service = bookable_service(tenant.id, draft["service_id"], log=LOG)
    if master is None or isinstance(service, Refusal):
        return Reply(FAILED, [_later()])

    outcome = create_appointment_as(
        actor=external_user_id_for(bot_user),
        tenant=tenant,
        master=master,
        service=service,
        start_at=draft["start_at"],
        idempotency_key=draft["id"],
        client_id=draft.get("client_id"),
        client_name=None if draft.get("client_id") else draft.get("client_name"),
        client_phone=None if draft.get("client_id") else draft.get("client_phone"),
        log=LOG,
        journal=logger,
    )
    if outcome.outcome == "committed":
        drop(tenant, bot_user)
        return Reply(CREATED, menu=True)
    if outcome.outcome == "conflict" and outcome.status == 409:
        # Taken: the draft stays, fresh starts of the same day are offered.
        day = date.fromisoformat(draft["day"])
        again = _slots_reply(tenant, bot_user, draft, day)
        return Reply(f"{TAKEN} {KEPT}\n\n{again.text}", again.rows)
    if outcome.outcome == "pending":
        # The same key on a retry: a second tap cannot make a second booking.
        return Reply(NO_ANSWER, review(draft).rows)
    if outcome.outcome == "blocked" and outcome.status == 403:
        return Reply(FORBIDDEN, menu=True)
    if outcome.outcome == "blocked" and _is_russian(outcome.detail):
        # Ayla's own refusal worded for staff (health check, unsellable offer).
        return Reply(outcome.detail, [_later()])
    logger.warning("%s.create_refused outcome=%s status=%s", LOG, outcome.outcome, outcome.status)
    return Reply(FAILED, [_later()])


def _is_russian(text: str) -> bool:
    return any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in str(text or ""))


__all__ = [
    "DRAFT_TTL_SECONDS",
    "Reply",
    "START_LABEL",
    "awaits_text",
    "choose_client",
    "choose_date",
    "choose_master",
    "choose_service",
    "choose_slot",
    "create",
    "drop",
    "new_client",
    "start",
    "take_text",
]
