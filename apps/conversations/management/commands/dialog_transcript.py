"""Диалог с пилота вместе со служебным следом каждой реплики бота (DRF-1754).

### Зачем это существует

Разбор диалога владельца 12.09 (03:01–03:09 UTC) шёл по ``tools/ops/
dialog_transcript.sh`` — голый ``SELECT`` из ``conversations_message``. Он
показывал, ЧТО сказал бот, и ничего о том, ПОЧЕМУ: какая ветка ответила,
какой инструмент выбрала модель, что вернул инструмент, какой вердикт дал
сторож. Замер до кода (DRF-1754) показал, что у каждой реплики есть
``trace_id``, и по нему в базе лежат:

* ``AIRequestMetric`` — по строке на вызов модели (``skill_selected``,
  ``llm_pass_index``, ``llm_model``, ``outcome``, ``fallback_triggered``);
* ``Event`` — семь общих событий на ход и, когда сработал, вердикт
  ``pre_check``;
* ``ReplayTrace`` — ветка, вердикты pre/post, skill, трасса инструментов —
  ТОЛЬКО когда включён ``REPLAY_LIVE_CAPTURE_ENABLED``;
* ``Message.action_type`` — последний инструмент, который выбрала модель.

Команда собирает эти четыре источника у каждой реплики бота и печатает их
одной строкой ``след:`` под репликой. Чего в базе нет — печатается ИМЕНЕМ
пропуска и причиной («safety_state: нет — с живого пути не пишется»), а не
пустотой: отсутствие обязано доезжать отсутствием, иначе читатель примет
молчание за «всё в порядке».

### Договор безопасности

* **Только чтение.** Ни одной записи в базу.
* **Телефоны, почта, карты маскируются** тем же ``Redactor`` (``regex_v3``),
  что и у ReplayTrace; идентификаторы канала и ``bot_user`` показываются
  первыми восемью знаками ``md5`` — их хватает, чтобы различить людей, и
  не хватает, чтобы найти человека.
* ``--out DIR`` пишет файл ``<hash>_<дата>.txt``; ``docs/dialogs/`` в
  ``.gitignore`` — расшифровки не попадают в репозиторий.

### Использование

    manage.py dialog_transcript --list [--since 24h]
    manage.py dialog_transcript --conv <uuid> [--since 3h] [--out docs/dialogs]
"""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Max, Min
from django.utils import timezone

from apps.conversations.models import Conversation, Message
from apps.events.models import Event
from apps.observability.models import AIRequestMetric
from apps.replay.models import ReplayTrace
from apps.replay.redactor import Redactor

#: События, которые пишутся на КАЖДОМ ходу и ничего не говорят о решении —
#: их в след не печатаем, иначе семь одинаковых имён заслонят один
#: содержательный вердикт.
GENERIC_EVENTS = frozenset(
    {
        "worker.consumed",
        "worker.handler_started",
        "worker.handler_completed",
        "identity.bot_user.resolved",
        "channels.max.global.received",
        "channels.max.received",
        "conversations.message.stored",
        "replay_captured",
    }
)

#: Ключи payload события, которые безопасно печатать рядом с именем: это
#: коды решения, не текст человека. Всё остальное — не печатается.
EVENT_PAYLOAD_KEYS = ("verdict", "branch", "outcome", "reason", "skill", "matched_count")

#: Что команда НЕ может показать и почему. Печатается у каждой реплики бота,
#: чтобы отсутствие читалось как отсутствие, а не как «ничего не случилось».
KNOWN_ABSENCES = (
    ("safety_state", "нет — с живого пути не пишется (record_verdict не вызывается)"),
    ("dr_verdict", "нет — движок DecisionReadiness в живой ход не подключён"),
)

_SINCE_RE = re.compile(r"^(\d+)\s*([mhd])$")


def parse_since(value: str) -> timedelta:
    """``24h`` / ``30m`` / ``2d`` → ``timedelta``; иное — ``CommandError``."""

    match = _SINCE_RE.match(value.strip())
    if match is None:
        raise CommandError(f"--since: ожидается вид 24h / 30m / 2d, получено {value!r}")
    amount, unit = int(match.group(1)), match.group(2)
    return {
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }[unit]


def short_hash(value: Any) -> str:
    """Первые восемь знаков md5 — различает, но не идентифицирует."""

    return hashlib.md5(str(value).encode("utf-8")).hexdigest()[:8]  # noqa: S324 — не для безопасности


def format_metric(row: AIRequestMetric) -> str:
    pass_index = "" if row.llm_pass_index is None else f"#{row.llm_pass_index}"
    parts = [f"{row.skill_selected or '?'}{pass_index}"]
    if row.llm_model:
        parts.append(row.llm_model)
    parts.append(row.outcome or "?")
    if row.fallback_triggered:
        parts.append("fallback")
    if row.latency_total_ms is not None:
        parts.append(f"{row.latency_total_ms}ms")
    return " ".join(parts)


def format_event(row: Event) -> str:
    payload = row.payload if isinstance(row.payload, dict) else {}
    details = [f"{key}={payload[key]}" for key in EVENT_PAYLOAD_KEYS if key in payload]
    return row.event_name + (f"({', '.join(details)})" if details else "")


def format_replay(row: ReplayTrace) -> str:
    """Шаги ReplayTrace одной строкой: ветка, вердикты, инструменты."""

    by_step: dict[str, dict[str, Any]] = {}
    for step in row.pipeline_steps or []:
        if isinstance(step, dict) and isinstance(step.get("payload"), dict):
            by_step[str(step.get("step"))] = step["payload"]
    routing = by_step.get("routing", {})
    parts = []
    if routing.get("branch"):
        parts.append(f"branch={routing['branch']}")
    if routing.get("skill"):
        parts.append(f"skill={routing['skill']}")
    tools = routing.get("tool_trace")
    if isinstance(tools, list) and tools:
        names = []
        for entry in tools:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("tool", "?"))
            if entry.get("result"):
                name += f"→{entry['result']}"
            names.append(name)
        parts.append("tools=" + ",".join(names))
    pre = by_step.get("pre_check", {}).get("verdict")
    post = by_step.get("post_check", {}).get("verdict")
    if pre is not None or post is not None:
        parts.append(f"pre={pre} post={post}")
    return " ".join(parts) or "есть, без routing/pre/post"


def trace_line(message: Message, *, replay_flag: bool) -> str:
    """Служебный след ОДНОЙ реплики бота — всё, что база знает по её trace_id."""

    parts: list[str] = []
    parts.append(f"action={message.action_type or '—'}")
    if message.trace_id is None:
        parts.append("trace_id: нет — след не привязать")
        for name, why in KNOWN_ABSENCES:
            parts.append(f"{name}: {why}")
        return " | ".join(parts)

    trace_str = str(message.trace_id)
    metrics = list(
        AIRequestMetric.all_tenants.filter(request_id=message.trace_id).order_by(
            "llm_pass_index", "created_at"
        )
    )
    parts.append(
        "llm: " + "; ".join(format_metric(m) for m in metrics)
        if metrics
        else "llm: нет строк AIRequestMetric"
    )
    events = [
        e
        for e in Event.objects.filter(trace_id=trace_str).order_by("created_at")
        if e.event_name not in GENERIC_EVENTS
    ]
    parts.append(
        "события: " + ", ".join(format_event(e) for e in events)
        if events
        else "события: только общие"
    )
    replays = list(ReplayTrace.all_tenants.filter(trace_id=trace_str).order_by("captured_at"))
    if replays:
        parts.append("replay: " + "; ".join(format_replay(r) for r in replays))
    elif replay_flag:
        parts.append("replay: нет строки (флаг включён — не попала в выборку или упала запись)")
    else:
        parts.append("replay: нет — REPLAY_LIVE_CAPTURE_ENABLED выключен")
    for name, why in KNOWN_ABSENCES:
        parts.append(f"{name}: {why}")
    return " | ".join(parts)


class Command(BaseCommand):
    help = "Диалог с пилота вместе со служебным следом каждой реплики бота (только чтение)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--list", action="store_true", help="список диалогов с активностью")
        parser.add_argument("--conv", default="", help="uuid диалога для расшифровки")
        parser.add_argument("--since", default="24h", help="окно активности: 24h / 30m / 2d")
        parser.add_argument("--out", default="", help="каталог для файла расшифровки")

    def handle(self, *args: Any, **options: Any) -> None:
        since = timezone.now() - parse_since(options["since"])
        if options["list"]:
            self._list(since)
            return
        if not options["conv"]:
            raise CommandError("нужен --conv <uuid> (см. --list) или --list")
        text = self._transcript(options["conv"], since)
        self.stdout.write(text)
        if options["out"]:
            path = self._write_out(Path(options["out"]), options["conv"], text)
            self.stderr.write(f"записано: {path}")

    def _list(self, since: Any) -> None:
        # cast — django-stubs выводит для values().annotate() тип,
        # который не индексируется; строки здесь — обычные dict.
        rows = cast(
            list[dict[str, Any]],
            list(
                Message.all_tenants.filter(created_at__gte=since)
                .values("conversation_id", "conversation__tenant__slug")
                .annotate(n=Count("id"), first=Min("created_at"), last=Max("created_at"))
                .order_by("-last")[:50]
            ),
        )
        self.stdout.write("conv_id | tenant | n | first | last | hash")
        for row in rows:
            self.stdout.write(
                f"{row['conversation_id']} | {row['conversation__tenant__slug']} | {row['n']} | "
                f"{row['first']:%Y-%m-%d %H:%M:%S} | {row['last']:%Y-%m-%d %H:%M:%S} | "
                f"{short_hash(row['conversation_id'])}"
            )

    def _transcript(self, conv_id: str, since: Any) -> str:
        from django.conf import settings

        try:
            conversation = Conversation.all_tenants.select_related("tenant").get(id=conv_id)
        except (Conversation.DoesNotExist, ValueError, TypeError) as exc:
            raise CommandError(f"диалог {conv_id!r} не найден") from exc
        redactor = Redactor()
        replay_flag = bool(getattr(settings, "REPLAY_LIVE_CAPTURE_ENABLED", False))
        messages = list(
            Message.all_tenants.filter(conversation=conversation, created_at__gte=since).order_by(
                "created_at"
            )
        )
        lines = [
            f"# диалог {short_hash(conversation.id)}  tenant={conversation.tenant.slug}  "
            f"bot_user={short_hash(conversation.bot_user_id)}  state={conversation.state}  "
            f"реплик={len(messages)} с {since:%Y-%m-%d %H:%M:%S %Z}",
            f"# replay-захват на этом процессе: {'вкл' if replay_flag else 'выкл'}",
        ]
        for message in messages:
            body = redactor.redact_text(message.rendered_text or message.content or "")
            body = body.replace("\n", "\n" + " " * 21)
            lines.append(f"{message.created_at:%H:%M:%S}  {message.role:<9}  {body}")
            if message.role == Message.Role.ASSISTANT:
                lines.append(" " * 10 + "след: " + trace_line(message, replay_flag=replay_flag))
        return "\n".join(lines) + "\n"

    @staticmethod
    def _write_out(directory: Path, conv_id: str, text: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{short_hash(conv_id)}_{timezone.now():%Y-%m-%d_%H%M}.txt"
        path.write_text(text, encoding="utf-8")
        return path
