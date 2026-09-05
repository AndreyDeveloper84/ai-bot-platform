"""Handoff → MAX escalation notifications (DRF-1029).

When an :class:`~apps.handoff.models.AdminTask` is created, the operator
(on the pilot: the owner personally) gets a best-effort push in MAX.
Until this module landed, the only way to notice an escalation was to
manually refresh the admin page — the dialog went silent, the client was
told "someone will answer within 30 minutes", and nobody knew.

### Contract (brief §3, do not weaken)

* **Off by default.** ``HANDOFF_NOTIFY_MAX_CHAT_IDS`` empty → the
  mechanism is fully disabled: no network calls, no warning-level log
  lines. That is the CI / local-dev default.
* **After commit, never inside the transaction.** Callers register
  :func:`notify_admin_task_created` via ``transaction.on_commit`` — a
  rolled-back task must never notify (false alarm: the operator hunts
  for a task that does not exist).
* **Best-effort, hard.** A send failure must never break task creation,
  roll back the HUMAN_HANDOFF flip, or crash inbound-message processing.
  Everything is caught broadly, logged, and audited
  (``handoff.notify_failed``). The worst outcome of DRF-1029 would be
  making escalation *more* fragile than it was.
* **Never block the consumer.** The consumer is single-threaded; one
  hung network call stops the whole pilot (that was DRF-989). Sends are
  synchronous with a short timeout (≤ 5 s) and no retries. Synchronous
  was chosen over Celery deliberately: the mechanism must also work
  when the queue itself is down — which is exactly when escalations
  spike.
* **Minimum PII.** The message lands in an external messenger forever,
  outside our contour and outside 152-FZ deletion. It carries: tenant,
  reason, time, task id, conversation id, and a direct admin link.
  NO transcript, NO client phone — those are one click away in the
  admin and have no place in a messenger.

### Reuse

:func:`send_max_notification` is the channel-agnostic fan-out
primitive; DRF-1030 (master booking push) reuses it instead of writing
a second copy.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from django.conf import settings
from django.utils import timezone

from apps.audit.services import write_audit
from apps.channels.max.outbound import send_message
from apps.handoff.models import AdminTask

logger = logging.getLogger(__name__)

# §3.4 — hard ceiling for the synchronous send; the single-threaded
# consumer must never wait longer than this on a notification.
_SEND_TIMEOUT = 5.0

# Operator-facing reason is truncated so a pathological caller cannot
# push an unbounded blob into a messenger chat.
_MAX_REASON_LEN = 200

_HIGH_PRIORITIES = frozenset({AdminTask.Priority.HIGH.value, AdminTask.Priority.URGENT.value})


def get_notify_chat_ids() -> list[str]:
    """Configured MAX recipient chat_ids; empty list = mechanism off."""

    return [c for c in getattr(settings, "HANDOFF_NOTIFY_MAX_CHAT_IDS", []) if c]


def admin_task_url(task_id: object) -> str:
    """Direct admin change-link for the task; "" when base is unset."""

    base = getattr(settings, "HANDOFF_ADMIN_BASE_URL", "").rstrip("/")
    if not base:
        return ""
    return f"{base}/admin/handoff/admintask/{task_id}/change/"


def build_admin_task_notification(task: AdminTask) -> str:
    """Format the operator-facing text for a fresh AdminTask.

    Minimum-PII by contract: tenant name, reason, time, task id,
    conversation id, admin link. Never the transcript, never the
    client's phone. HIGH/URGENT priorities get a distinct header —
    the operator reads notifications on the run.
    """

    if task.priority in _HIGH_PRIORITIES:
        header = f"🚨 Эскалация к человеку — ПРИОРИТЕТ {task.priority.upper()}"
    else:
        header = "📋 Эскалация к человеку"
    task_type_label = AdminTask.TaskType(task.task_type).label
    reason = (task.reason or "").strip()[:_MAX_REASON_LEN]
    reason_part = f"{task_type_label} — {reason}" if reason else task_type_label
    when = timezone.localtime(task.created_at).strftime("%d.%m.%Y %H:%M")
    lines = [
        header,
        f"Салон: {task.tenant.name}",
        f"Причина: {reason_part}",
        f"Время: {when}",
        f"Задача: {task.id}",
        f"Диалог: {task.conversation_id}",
    ]
    url = admin_task_url(task.id)
    if url:
        lines.append(f"Открыть: {url}")
    return "\n".join(lines)


def send_max_notification(
    *,
    text: str,
    chat_ids: Sequence[str],
    timeout: float = _SEND_TIMEOUT,
    on_failure: Callable[[str, Exception], None] | None = None,
) -> int:
    """Fan out ``text`` to each MAX chat, best-effort. Returns failures.

    Every recipient is isolated: an exception on one chat is logged
    (and reported via ``on_failure``) but never cancels the remaining
    sends. No retries — the sync path stays short by design.
    """

    failures = 0
    for chat_id in chat_ids:
        try:
            send_message(chat_id=chat_id, text=text, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — best-effort by contract
            failures += 1
            logger.warning(
                "handoff.notify.send_failed chat_id=%s exc=%s",
                chat_id,
                exc,
            )
            if on_failure is not None:
                try:
                    on_failure(chat_id, exc)
                except Exception:  # noqa: BLE001 — the hook is best-effort too
                    logger.exception("handoff.notify.on_failure_hook_failed chat_id=%s", chat_id)
    return failures


def notify_admin_task_created(task: AdminTask) -> None:
    """on_commit entry point from ``create_admin_task``. NEVER raises.

    Register via ``transaction.on_commit`` so a rolled-back handoff
    never notifies. With an empty recipient setting this is a silent
    no-op — no network, no warning logs.
    """

    try:
        chat_ids = get_notify_chat_ids()
        if not chat_ids:
            return  # fully disabled (§3.1)
        text = build_admin_task_notification(task)

        def _audit_failure(chat_id: str, exc: Exception) -> None:
            _write_notify_failure_audit(task, chat_id, exc)

        failures = send_max_notification(text=text, chat_ids=chat_ids, on_failure=_audit_failure)
        if failures == 0:
            logger.info(
                "handoff.notify.sent task=%s recipients=%d",
                task.id,
                len(chat_ids),
            )
    except Exception:  # noqa: BLE001 — hard containment (§3.3)
        logger.exception("handoff.notify.unexpected task=%s", getattr(task, "id", None))


def build_unclaimed_notification(task: AdminTask, *, waited_minutes: int) -> str:
    """Format the «nobody took this» nudge (DRF-1488).

    Same minimum-PII contract as the creation notice, plus the two facts
    the creation notice could not carry because they did not exist yet:
    how long the task has been waiting, and who it is addressed to. The
    addressee line is the whole point — the pilot's ten tasks were
    addressed to nobody, so nobody could be reminded.

    The addressee is a STAFF identifier (a Django username or a queue
    label), and it is a class of data §Minimum PII does not list, so the
    decision is made here rather than by default: it goes in. The recipient
    is the operators' own chat, the value names a colleague on shift and
    never a client, and a nudge that cannot say whose task is late asks
    everybody and reaches nobody — which is the failure being fixed. The
    client-facing rules are untouched: nothing about the person on the
    other end of the dialog appears here, and DRF-1039 (never pass the
    client's phone) holds as before.
    """

    task_type_label = AdminTask.TaskType(task.task_type).label
    lines = [
        "⏰ Эскалация без ответа",
        f"Салон: {task.tenant.name}",
        f"Тип: {task_type_label}",
        f"Ждёт: {waited_minutes} мин",
        f"Адресат: {task.addressee or 'НЕ НАЗНАЧЕН'}",
        f"Задача: {task.id}",
        f"Диалог: {task.conversation_id}",
        "Клиенту в это время бот не отвечает.",
    ]
    url = admin_task_url(task.id)
    if url:
        lines.append(f"Открыть: {url}")
    return "\n".join(lines)


def notify_admin_task_unclaimed(task: AdminTask, *, waited_minutes: int) -> None:
    """Push the overdue nudge to the operator chats. NEVER raises.

    Same containment as :func:`notify_admin_task_created`: an unreachable
    messenger must not stop the sweep from stamping the remaining tasks.
    Disabled (silently, no network) when no recipients are configured.
    """

    try:
        chat_ids = get_notify_chat_ids()
        if not chat_ids:
            return  # fully disabled (§3.1)
        text = build_unclaimed_notification(task, waited_minutes=waited_minutes)

        def _audit_failure(chat_id: str, exc: Exception) -> None:
            _write_notify_failure_audit(task, chat_id, exc)

        failures = send_max_notification(text=text, chat_ids=chat_ids, on_failure=_audit_failure)
        if failures == 0:
            logger.info(
                "handoff.notify.unclaimed_sent task=%s recipients=%d waited_minutes=%d",
                task.id,
                len(chat_ids),
                waited_minutes,
            )
    except Exception:  # noqa: BLE001 — hard containment (§3.3)
        logger.exception("handoff.notify.unclaimed_unexpected task=%s", getattr(task, "id", None))


def _write_notify_failure_audit(task: AdminTask, chat_id: str, exc: Exception) -> None:
    """Audit a failed notification so the gap is visible after the fact."""

    try:
        write_audit(
            "handoff.notify_failed",
            target="AdminTask",
            target_id=task.id,
            payload={
                "conversation_id": str(task.conversation_id),
                "chat_id": str(chat_id),
                "error": f"{type(exc).__name__}: {exc}"[:200],
            },
        )
    except Exception:  # noqa: BLE001 — the audit row is best-effort too
        logger.exception("handoff.notify.audit_failed task=%s", task.id)
