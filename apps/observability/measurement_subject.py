"""Кто ответил на замер: предмет, названный самой машиной.

Перенос ``core/measurement_subject.py`` из каталога (PR
AndreyDeveloper84/beautygo_backend#330, коммит 849e72d) — та же форма,
свои опоры. Один модуль на два репозитория невозможен без общего пакета;
пока его нет, оба держат одинаковую **шапку** и одинаковые **правила**,
и расхождение между ними ловится глазами по одному формату.

Зачем отдельный модуль
----------------------

Правило «замер без названного хоста не принимается» дырявое, и дыру видно
на нашем же случае: **имя хоста — это то, что я помню**, а неверный адрес
называют честно и уверенно. В контуре две машины, у которых совпадает всё,
кроме адреса: путь ``/home/taximeter/ai-bot-platform-dev``, учётка
``taximeter``, имена compose-проектов. Изнутри ни один признак не говорит,
где ты находишься.

Поэтому здесь печатается не то, что я думаю про машину, а то, что она
**говорит о себе сейчас**. Главная строка — не имя, а **время старта
процесса БД**: его нельзя вспомнить неправильно.

Вторая половина — пульс. У брошенной копии контейнер БД остался жив, когда
прикладные вышли: запрос туда не падает, он **отвечает** правдоподобным
числом из замороженного состояния. Доступность живую машину от замороженной
не отличает — отличает **возраст последней записи**.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from django.apps import apps
from django.db import DatabaseError
from django.db import connection as default_connection
from django.db.models import Max
from django.utils import timezone


@dataclass(frozen=True)
class Anchor:
    """Таблица, в которую пишет любое живое использование контура."""

    label: str
    model: str  # "app_label.ModelName"
    field: str
    #: Почему эта опора молчит здесь ЗАКОНОМЕРНО. Заполняется только
    #: тогда, когда молчание объяснено устройством контура, а не
    #: предположением: иначе пометка превращается в глушилку.
    silent_reason: str | None = None
    #: Строки, которые из опоры ВЫЧИТАЮТСЯ, — те, что пишет сам процесс
    #: замера. Опора, которую наполняет измеряющий, не измеряет ничего.
    exclude: tuple[tuple[str, object], ...] = ()


#: Пульс бота. Набор намеренно широкий: отдельная таблица может не
#: использоваться на конкретном стенде, и её тишина сама по себе не значит
#: ничего. Свежесть определяет **самая новая** запись из набора — тишина
#: во всех сразу и есть признак замороженной машины.
#:
#: Первая опора — сообщение в боте: оно пишется каждым ходом диалога, и
#: именно её главное окно назвало опорой для бот-стороны DRF-1661.
PULSE_ANCHORS = (
    Anchor("сообщения диалога", "conversations.Message", "created_at"),
    # `worker.subscriber_audit` пишется ОДНИМ ЛИШЬ фактом запуска процесса
    # (apps/workers/apps.py, «once per process boot») — в том числе
    # запуском `manage.py surface_state`. Без исключения опора всегда
    # свежая на любой машине, включая замороженную: её оживляет сам замер.
    Anchor(
        "события",
        "events.Event",
        "created_at",
        exclude=(("event_name", "worker.subscriber_audit"),),
    ),
    Anchor("активность пользователей", "identity.BotUser", "last_seen"),
    Anchor("заявки на запись", "booking.BookingRequest", "created_at"),
    # Слабая намеренно: пишется один раз на пользователя, поэтому одна
    # она свежести не доказывает. В наборе полезна — лишняя опора пульс
    # не портит: свежесть берётся по самой новой, а у замороженной
    # машины старые все.
    Anchor("первые визиты", "identity.BotUser", "first_seen"),
)


@dataclass(frozen=True)
class Pulse:
    """Последняя запись одной опорной таблицы — или причина, почему её нет."""

    label: str
    model: str
    at: datetime | None
    error: str | None = None
    silent_reason: str | None = None


def gather_pulse(anchors=PULSE_ANCHORS) -> list[Pulse]:
    """Максимальная отметка времени по каждой опоре.

    ``_base_manager``, а не ``objects``: менеджер по умолчанию бывает
    отфильтрован (арендатор, «не удалённые»), и тогда пульс замерил бы
    видимость, а не запись. В боте это не гипотеза: ``BotUser.objects``,
    ``Message.objects`` — ``TenantScopedManager``, вне контекста
    арендатора они отдают пусто.

    Опечатка в имени модели или поля поднимается **исключением**, а не
    превращается в тихий пропуск: набор опор — часть замера, и его поломка
    обязана быть видна. Отсутствие самой таблицы в базе — другой исход, он
    записывается в ``error`` и печатается строкой.
    """
    out: list[Pulse] = []
    for anchor in anchors:
        model = apps.get_model(anchor.model)  # LookupError при опечатке
        model._meta.get_field(anchor.field)  # FieldDoesNotExist при опечатке
        try:
            qs = model._base_manager.all()
            if anchor.exclude:
                qs = qs.exclude(**dict(anchor.exclude))
            value = qs.aggregate(m=Max(anchor.field))["m"]
        except DatabaseError as exc:
            # Первая строка ошибки, а не всё исключение: развёрнутый
            # SQL с указателем на позицию ломает выравнивание блока, и
            # предмет перестаёт читаться с одного взгляда.
            reason = str(exc).strip().splitlines()[0]
            out.append(
                Pulse(
                    anchor.label,
                    anchor.model,
                    None,
                    error=reason,
                    silent_reason=anchor.silent_reason,
                )
            )
            continue
        out.append(
            Pulse(
                anchor.label,
                anchor.model,
                value,
                silent_reason=anchor.silent_reason,
            )
        )
    return out


def newest(pulses: list[Pulse]) -> Pulse | None:
    """Самая свежая из опор. ``None``, если не ответила ни одна."""
    alive = [p for p in pulses if p.at is not None]
    if not alive:
        return None
    return max(alive, key=lambda p: p.at)


def fresh(pulses: list[Pulse], within: timedelta, now: datetime | None = None):
    """Опоры, писавшие не позже ``within`` назад.

    Считать их **числом**, а не «есть или нет»: одна свежая опора может
    оказаться единственной живой в мёртвом наборе, и отличить её от
    заливки или миграции нечем. Две независимые опоры, согласные между
    собой, — другой разговор.
    """
    now = now or timezone.now()
    return [p for p in pulses if p.at is not None and now - p.at <= within]


def db_identity(connection=None) -> dict[str, object]:
    """Что база говорит о себе сама — одной строкой SQL.

    ``pg_postmaster_start_time()`` здесь главный: адрес я могу набрать по
    памяти, а время старта процесса память не подделывает. Восьмидневный
    аптайм при живом контуре — уже ответ.

    На SQLite (локальный запуск без ``POSTGRES_HOST``) этих функций нет:
    печатается имя файла и честное «не Postgres», а не пустая строка.
    """
    conn = connection or default_connection
    if conn.vendor != "postgresql":
        return {
            "addr": f"({conn.vendor})",
            "port": "",
            "database": conn.settings_dict.get("NAME"),
            "user": "",
            "started_at": None,
            "settings_host": conn.settings_dict.get("HOST") or f"(файл, {conn.vendor})",
            "settings_port": conn.settings_dict.get("PORT") or "",
        }
    with conn.cursor() as cur:
        cur.execute(
            "SELECT host(inet_server_addr()), inet_server_port(), "
            "current_database(), current_user, pg_postmaster_start_time()"
        )
        addr, port, database, user, started = cur.fetchone()
    return {
        "addr": addr,
        "port": port,
        "database": database,
        "user": user,
        "started_at": started,
        "settings_host": conn.settings_dict.get("HOST") or "(unix socket)",
        "settings_port": conn.settings_dict.get("PORT") or "",
    }


def container_id() -> str:
    """Идентификатор контейнера из ``/proc``, либо честное «не определён».

    Изнутри контейнера читаются не все признаки: **имя** контейнера и метки
    compose принадлежат демону, а не процессу. Пустая строка на их месте
    прочиталась бы как «всё в порядке», поэтому здесь именно текст про то,
    чего не видно.
    """
    cgroup = Path("/proc/self/cgroup")
    if not cgroup.exists():
        return "не определён (не Linux-контейнер)"
    for line in cgroup.read_text(errors="replace").splitlines():
        for token in line.replace("/", ":").split(":"):
            token = token.removesuffix(".scope").removeprefix("docker-")
            if len(token) == 64 and all(c in "0123456789abcdef" for c in token):
                return token[:12]
    return "не определён (cgroup v2 без id)"


#: То, чего изнутри не видно вовсе, — печатается командой, чтобы читатель
#: не принял отсутствие строки за отсутствие вопроса.
OUTSIDE_HINT = (
    "изнутри НЕ видно имени контейнера и метки compose — спросить снаружи:\n"
    "  docker inspect <контейнер> --format "
    "'{{index .Config.Labels \"com.docker.compose.project.working_dir\"}}'"
)


def _age(value: datetime | None, now: datetime) -> str:
    if value is None:
        return "—"
    delta: timedelta = now - value
    days, rest = divmod(int(delta.total_seconds()), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"возраст {days} дн {hours} ч"
    if hours:
        return f"возраст {hours} ч {minutes} мин"
    return f"возраст {minutes} мин"


def subject_lines(
    connection=None,
    *,
    anchors=PULSE_ANCHORS,
    now=None,
    pulses=None,
    fresh_within: timedelta | None = None,
) -> list[str]:
    """Блок «кто отвечает на этот замер», готовый к печати.

    ``pulses`` передают, когда тот же снимок нужен вызывающему для проверки:
    два отдельных сбора дали бы напечатанный возраст и проверенный возраст
    из разных мгновений, и расхождение между ними некому было бы заметить.
    """
    now = now or timezone.now()
    identity = db_identity(connection)
    pulses = gather_pulse(anchors) if pulses is None else pulses
    freshest = newest(pulses)

    started = identity["started_at"]
    lines = [
        "== ПРЕДМЕТ: кто отвечает на этот замер ==",
        f"хост (hostname процесса)   : {socket.gethostname()}",
        f"контейнер (id из cgroup)   : {container_id()}",
        f"БД по настройкам           : {identity['settings_host']}:{identity['settings_port']}",
        f"БД отвечает с адреса       : {identity['addr']}:{identity['port']} "
        f"({identity['database']} / {identity['user']})",
        "СТАРТ ПРОЦЕССА БД          : "
        + (f"{started}  {_age(started, now)}" if started else "не Postgres — не определён"),
    ]

    if freshest is None:
        lines.append(
            "ПУЛЬС                      : НИ ОДНА опора не ответила — "
            "это поломка набора опор либо пустая база, и в обоих случаях "
            "свежесть НЕ подтверждена"
        )
    else:
        lines.append(
            f"ПУЛЬС (новейшая запись)    : {freshest.at}  "
            f"{_age(freshest.at, now)}  ← {freshest.label}"
        )
    if fresh_within is not None:
        count = len(fresh(pulses, fresh_within, now))
        lines.append(f"СВЕЖИХ ОПОР                : {count} из {len(pulses)} в пределах порога")

    # Возраст печатается у КАЖДОЙ опоры, а не только у самой новой:
    # одна тихая опора в широком наборе прячется за чужой свежестью, и
    # видно её только возрастом рядом.
    for pulse in sorted(pulses, key=lambda p: (p.at is None, -(p.at.timestamp() if p.at else 0))):
        if pulse.error:
            mark = pulse.error
        elif pulse.at is None:
            mark = "пусто"
        else:
            mark = f"{pulse.at.isoformat(timespec='seconds')}  {_age(pulse.at, now)}"
            if fresh_within is not None:
                is_fresh = now - pulse.at <= fresh_within
                if is_fresh and pulse.silent_reason:
                    # Обратная стража к самой пометке: ожившая опора
                    # обязана сказать об этом сама.
                    mark += "  ПОМЕТКА УСТАРЕЛА: опора объявлена молчащей, но пишет"
                elif is_fresh:
                    mark += "  свежая"
                elif pulse.silent_reason:
                    mark += f"  молчит по устройству: {pulse.silent_reason}"
                else:
                    mark += "  ПРОСРОЧЕНА"
        lines.append(f"    {pulse.label:<26}: {mark}")

    lines.append(OUTSIDE_HINT)
    return lines
