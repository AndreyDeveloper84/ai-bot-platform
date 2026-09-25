"""Periodic Celery task: dispatch due reminders.

DRF-844 / Phase 1 / R1. Runs every 15 minutes via Celery beat (see
``CELERY_BEAT_SCHEDULE`` in ``config/settings/base.py``). Picks
PENDING reminders whose ``scheduled_at`` has passed, transitions them
atomically, then dispatches via the channel outbound adapter.

### Idempotency / race-safety

Two workers can hit the same beat tick under load (especially with
Celery's ``acks_late`` retention semantics). The compare-and-set on
status is what guarantees "exactly one send" per reminder:

    rows = BookingReminder.all_tenants.filter(
        pk=row.pk, status=PENDING
    ).update(status=SENT_NO_REPLY)
    if rows == 0:
        # Another worker grabbed it; skip.
        continue

The :meth:`QuerySet.update` returns the rowcount; ``0`` means the
``WHERE status=PENDING`` predicate didn't match (because the other
worker already flipped it). The losing worker silently moves on. The
winning worker proceeds to send.

### Why no retry of FAILED rows

A reminder that failed to send is operationally interesting (the
operator inspects the audit log + the FAILED row), but auto-retrying
risks hammering a flaky channel or spamming the user when the channel
recovers hours later and our queue catches up. Beats fire every 15
minutes; if a transient failure clears, the operator manually flips
the row back to PENDING. Phase 2 may add a bounded retry with
exponential backoff; out of scope for R1.

### Batch limit

The 200-row batch is a soft cap on per-tick work. At the design
cadence (1 message → 2 reminders per booking, ~50 bookings/day across
all tenants combined for Phase 1), the system never fills the batch;
the cap is defensive against backlog-after-outage scenarios where a
single tick would otherwise process thousands of rows and block beat.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task  # type: ignore[import-untyped]
from django.utils import timezone

from apps.audit.services import write_audit
from apps.booking.models import BookingReminder
from apps.channels.max.outbound import MaxAPIError, send_message
from apps.bookings.keyboards import day_before_keyboard

# Кто закрыл визит — один предикат и одно имя системного закрывающего
# на весь контур (решение владельца 30.08).
from apps.booking.completion import SYSTEM_ACTOR

# Shared send-time booking-state classifier — extracted to its own module
# to break the tasks ↔ escalation import cycle (escalation needs the
# classifier; tasks re-exports escalation for Celery autodiscover).
from apps.bookings.recheck import (  # noqa: F401
    _ACTION_DEFER,
    _ACTION_DROP,
    _ACTION_SEND,
    _recheck_booking_state,
)

# Re-export R2's escalation task so Celery's autodiscover_tasks() finds
# it. autodiscover walks installed apps for a top-level ``tasks`` module
# and registers every ``@shared_task`` decorator it encounters during
# import. The escalation task lives in a sibling module
# (``apps.bookings.escalation``) for readability, so we trigger the
# import here. Module import is idempotent — Celery doesn't double-
# register the task.
from apps.bookings.escalation import escalate_stale_reminders  # noqa: F401

# Re-export R3's post-visit follow-up task — same autodiscover rationale.
from apps.bookings.followups import send_post_visit_followups  # noqa: F401

# DRF-2346 — подметание просроченных окон возврата живёт у владельца строки
# (``apps/booking/``): читать ``BookingRequest`` из соседнего приложения
# запрещает сторож границ G9, и запрещает по делу. Здесь только ввоз, чтобы
# имя задачи нашлось там же, где остальные беты записи.
from apps.booking.expired_cancels import commit_expired_cancels  # noqa: F401

logger = logging.getLogger(__name__)


# Tunables — kept at module scope so tests can monkeypatch.
BATCH_LIMIT = 200


def _salon_address_line(reminder: BookingReminder) -> str:
    """DRF-1952 — адрес салона записи (FK ``reminder.tenant``, он в ``select_related``)."""
    from apps.tenancy.visit_address import tenant_address_line

    return tenant_address_line(reminder.tenant)


def _format_day_before_text(reminder: BookingReminder) -> str:
    """Render the T-24h reminder body.

    Voice mirrors :mod:`apps.skills.booking.prompts` (Russian, salon
    brand voice — warm but compact). The ``visit_at`` is formatted in
    the project's configured timezone (Moscow per settings); the
    auto-tz cast happens at format time because ``visit_at`` is stored
    as a tz-aware datetime in UTC.
    """
    visit_local = timezone.localtime(reminder.visit_at)
    return (
        "Здравствуйте! Напоминаю о записи завтра:\n"
        f"{reminder.service_name or '—'} к мастеру "
        f"{reminder.master_name or '—'}\n"
        f"{visit_local.strftime('%d.%m в %H:%M')}\n"
        f"{_salon_address_line(reminder)}\n\n"
        "Подтвердите, пожалуйста:"
    )


def _format_two_hours_text(reminder: BookingReminder) -> str:
    """Render the T-2h reminder body.

    Softer than T-24h — no confirmation ask, just a "see you soon"
    nudge. Matches the legacy mysite copy.
    """
    visit_local = timezone.localtime(reminder.visit_at)
    return (
        "Через 2 часа жду вас на приём:\n"
        f"{reminder.service_name or '—'} к мастеру "
        f"{reminder.master_name or '—'}\n"
        f"в {visit_local.strftime('%H:%M')}\n"
        f"{_salon_address_line(reminder)}\n\n"
        "Если планы изменились — напишите, постараемся помочь."
    )


def _build_attachments(reminder: BookingReminder) -> list[dict[str, Any]] | None:
    """Build the channel-agnostic keyboard attachment for the reminder.

    Returns ``None`` for the T-2h kind (text-only nudge — see
    :func:`apps.bookings.keyboards.two_hours_keyboard`). Returns a
    single-element list wrapping the ``{label, callback}`` button row
    for T-24h; the channel adapter renders this per its native widget.

    The MAX outbound adapter accepts attachments as a pass-through
    ``list[dict]``; the channel adapter mapping from the
    platform-canonical button row to MAX's ``InlineKeyboard`` format
    is the channel adapter's responsibility (the platform contract,
    not this task's concern).
    """
    if reminder.kind == BookingReminder.Kind.DAY_BEFORE:
        buttons = day_before_keyboard(str(reminder.pk))
        # Wrap in the platform UI envelope expected by skill
        # action_data — keeps the shape uniform with the cb:food:*
        # family. Channel adapter unpacks ``payload.buttons``.
        return [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]
    return None


def _target_status(kind: str) -> str:
    """Return the post-dispatch status for a given reminder kind.

    Per the spec:

    * ``DAY_BEFORE`` → ``SENT_NO_REPLY`` (waiting for confirm/cancel/
      reschedule callback within the 22h window before T-2h fires)
    * ``TWO_HOURS`` → ``SENT`` (terminal — no buttons, no expected
      reply)
    """
    if kind == BookingReminder.Kind.DAY_BEFORE:
        return BookingReminder.Status.SENT_NO_REPLY
    return BookingReminder.Status.SENT


def _handoff_silenced(row: Any) -> bool:
    """Занят ли этим человеком живой оператор прямо сейчас (DRF-2342).

    Жалоба владельца: администратор переносит запись руками, а боту в этот
    же момент наступает T-2, и человек получает напоминание о СТАРОМ
    времени — о том самом, что сейчас меняют.

    Отдельная проверка, а не расширение соседних, потому что обе соседние
    про другое и обе легко принять за эту:

    * :func:`_recheck_booking_state` откладывает ход при
      ``CANCEL_REQUESTED`` / ``RESCHEDULE_REQUESTED`` — окно отмены в
      секунды; пока оператор работает руками, статус остаётся
      ``CONFIRMED``;
    * :func:`_reminders_muted` — личный выключатель человека, он и должен
      действовать независимо от того, занят ли им оператор.

    **Срок здесь пока не стоит, и это не забывчивость.** Задача
    администратора сама не закрывается (DRF-2345), то есть «молчать, пока
    открыта» без срока — неограниченная тишина. Каким он будет (потолок
    ожидания, отсечка по SLA подхвата, отправка с оговоркой), решает
    владелец; когда решит, условие встанет ЗДЕСЬ, в одном месте, а не
    рассыплется по ходу отправки.
    """
    from apps.orchestrator.handoff import person_handoff_muted

    bot_user = row.bot_user
    return person_handoff_muted(channel=bot_user.channel, channel_user_id=bot_user.channel_user_id)


def _reminders_muted(row: Any) -> bool:
    """Has the person switched booking reminders off?

    ``UserPreferences.notify_reminders`` — default ``True``; a missing
    preferences row means «never touched the switch», i.e. not muted.
    One indexed lookup by primary key per row that reaches the send
    path; nothing is cached across rows, so a flip lands the same tick.
    """
    from apps.identity.models import UserPreferences

    value = (
        UserPreferences.all_tenants.filter(bot_user_id=row.bot_user_id)
        .values_list("notify_reminders", flat=True)
        .first()
    )
    return value is False


@shared_task(name="bookings.send_due_reminders")
def send_due_reminders() -> dict[str, int]:
    """Dispatch every reminder whose ``scheduled_at`` has passed.

    Picks up to :data:`BATCH_LIMIT` rows where
    ``status=PENDING AND scheduled_at <= now()``, in scheduled_at
    order (earliest first — keeps the queue draining FIFO under load).

    Per-row flow:

    1. Compare-and-set ``status`` from PENDING → target via a single
       UPDATE. ``rowcount == 0`` → another worker won the race; skip.
    2. Build the message text + (T-24h only) keyboard attachment.
    3. Call :func:`apps.channels.max.outbound.send_message`.
    4. On success: stamp ``sent_at``. Audit + (canonical-vocab) emit
       are deferred to Phase 2 — Sprint 11 has no canonical event for
       reminders yet, and emitting non-canonical only adds a logger
       warning. Audit row IS written via ``write_audit`` which has no
       vocabulary constraint.
    5. On send failure: catch, flip to FAILED, audit. Don't requeue —
       see module docstring.

    Returns a dict
    ``{"sent": int, "failed": int, "skipped": int, "stale": int, "deferred": int,
    "muted": int}``
    for telemetry / test visibility.

    ``stale`` counts reminders dropped at dispatch because the underlying
    booking changed state (P0 PRE_PILOT send-time re-check invariant).
    ``deferred`` counts rows left PENDING because booking is in an
    interim reversible state (CANCEL_REQUESTED / RESCHEDULE_REQUESTED) —
    the next 15-min tick re-checks.
    """
    now = timezone.now()
    due_qs = (
        BookingReminder.all_tenants.filter(
            status=BookingReminder.Status.PENDING,
            scheduled_at__lte=now,
        )
        .select_related("tenant", "bot_user", "booking_request")
        .order_by("scheduled_at")[:BATCH_LIMIT]
    )

    sent = 0
    failed = 0
    skipped = 0
    stale = 0
    deferred = 0
    muted = 0
    for row in due_qs:
        # Send-time re-check invariant (P0 PRE_PILOT). See
        # ``_recheck_booking_state`` docstring + module-level rationale
        # block above the helper для full state-mapping table.
        action, reason = _recheck_booking_state(row)
        if action == _ACTION_DEFER:
            # Interim reversible state OR unknown status. Don't touch
            # the row — leave PENDING for next 15-min tick. No CAS, no
            # send, no audit (defer is normal flow, not a finding).
            logger.info(
                "bookings.dispatch.deferred pk=%s kind=%s reason=%s",
                row.pk,
                row.kind,
                reason,
            )
            deferred += 1
            continue
        if action == _ACTION_DROP:
            # Booking became invalid между schedule + dispatch. CAS PENDING
            # → STALE_DROPPED. Loser of the race silently skips.
            rowcount = BookingReminder.all_tenants.filter(
                pk=row.pk,
                status=BookingReminder.Status.PENDING,
            ).update(status=BookingReminder.Status.STALE_DROPPED)
            if rowcount == 0:
                logger.info(
                    "bookings.dispatch.race_lost_on_stale pk=%s kind=%s",
                    row.pk,
                    row.kind,
                )
                skipped += 1
                continue
            logger.info(
                "bookings.dispatch.stale_dropped pk=%s kind=%s reason=%s",
                row.pk,
                row.kind,
                reason,
            )
            # Audit is forensic best-effort here — row already CAS'd к
            # STALE_DROPPED, status enum carries the signal. If audit
            # raises (DB blip / payload-validation glitch), don't kill
            # the batch loop. CR #851 suggestion #6 hardening.
            try:
                write_audit(
                    action="bookings.reminder.stale_dropped",
                    target="BookingReminder",
                    target_id=row.pk,
                    payload={
                        "kind": row.kind,
                        "yclients_record_id": row.yclients_record_id,
                        "reason": reason,
                        "booking_request_id": (
                            str(row.booking_request_id) if row.booking_request_id else None
                        ),
                    },
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "bookings.dispatch.stale_audit_failed pk=%s reason=%s",
                    row.pk,
                    reason,
                )
            stale += 1
            continue

        # action == _ACTION_SEND — the booking is still worth reminding
        # about. Now the person's own switch. ``notify_reminders`` is the
        # ONE toggle the model promises applies here («only soft
        # reminders mute», apps/identity/models.py), and until DRF-1833's
        # measurement nobody read it — a promise on a settings screen
        # with no reader behind it. Read at send time, not at schedule
        # time: the toggle can flip between the two, and the row is the
        # same either way. Service class (DRF-1833 registry): no consent
        # gate here — this IS the person's own booking — but their own
        # «no» is honoured.
        # DRF-2342 — человеком занят живой оператор: напоминание ЖДЁТ, а не
        # сгорает. Строка остаётся PENDING, следующий тик проверит заново:
        # задача закроется — напоминание уйдёт, если визит ещё впереди.
        # Стоит ПЕРЕД личным выключателем намеренно: выключатель меняет
        # статус безвозвратно, и проверив его первым, мы сожгли бы строку,
        # которую всего лишь надо придержать.
        if _handoff_silenced(row):
            deferred += 1
            logger.info("bookings.dispatch.deferred_handoff pk=%s kind=%s", row.pk, row.kind)
            continue

        if _reminders_muted(row):
            rowcount = BookingReminder.all_tenants.filter(
                pk=row.pk,
                status=BookingReminder.Status.PENDING,
            ).update(status=BookingReminder.Status.MUTED)
            if rowcount == 0:
                skipped += 1
                continue
            muted += 1
            logger.info("bookings.dispatch.muted pk=%s kind=%s", row.pk, row.kind)
            write_audit(
                action="bookings.reminder.muted",
                target="BookingReminder",
                target_id=row.pk,
                payload={
                    "kind": row.kind,
                    "yclients_record_id": row.yclients_record_id,
                    "reason": "notify_reminders_off",
                },
            )
            continue

        target_status = _target_status(row.kind)
        # Compare-and-set: only proceed if we are the first to claim
        # this row. .update() returns the affected rowcount; 0 means
        # the WHERE didn't match (another worker beat us).
        rowcount = BookingReminder.all_tenants.filter(
            pk=row.pk,
            status=BookingReminder.Status.PENDING,
        ).update(status=target_status)
        if rowcount == 0:
            logger.info("bookings.dispatch.race_lost pk=%s kind=%s", row.pk, row.kind)
            skipped += 1
            continue

        # We own the row. Compose + send.
        if row.kind == BookingReminder.Kind.DAY_BEFORE:
            text = _format_day_before_text(row)
        else:
            text = _format_two_hours_text(row)
        attachments = _build_attachments(row)

        try:
            send_message(
                # DRF-1558 — напоминание пишет человеку первым. Снимок
                # ``row.chat_id`` — диалог с тем ботом, который его завёл;
                # адресуем самого человека через живую строку BotUser
                # (она уже в ``select_related``, лишнего запроса нет).
                user_id=(getattr(row.bot_user, "channel_user_id", "") or "").strip(),
                text=text,
                attachments=attachments,
            )
        except MaxAPIError as exc:
            logger.warning(
                "bookings.dispatch.send_failed pk=%s kind=%s status=%s err=%s",
                row.pk,
                row.kind,
                exc.status_code,
                exc.body[:200] if exc.body else "",
            )
            BookingReminder.all_tenants.filter(pk=row.pk).update(
                status=BookingReminder.Status.FAILED,
            )
            write_audit(
                action="bookings.reminder.send_failed",
                target="BookingReminder",
                target_id=row.pk,
                payload={
                    "kind": row.kind,
                    "yclients_record_id": row.yclients_record_id,
                    "status_code": exc.status_code,
                },
            )
            failed += 1
            continue
        except Exception as exc:  # noqa: BLE001 — defensive belt-and-braces
            # The adapter only raises MaxAPIError per its contract, but
            # we wrap defensively: a regression that raised a different
            # exception type would leave the row stuck in
            # SENT_NO_REPLY/SENT with no actual send and nothing in the
            # audit log. FAIL LOUDLY in that case.
            logger.exception("bookings.dispatch.send_unexpected pk=%s", row.pk)
            BookingReminder.all_tenants.filter(pk=row.pk).update(
                status=BookingReminder.Status.FAILED,
            )
            write_audit(
                action="bookings.reminder.send_failed",
                target="BookingReminder",
                target_id=row.pk,
                payload={
                    "kind": row.kind,
                    "yclients_record_id": row.yclients_record_id,
                    "exception_type": type(exc).__name__,
                },
            )
            failed += 1
            continue

        # Success — stamp sent_at.
        BookingReminder.all_tenants.filter(pk=row.pk).update(sent_at=timezone.now())
        write_audit(
            action="bookings.reminder.sent",
            target="BookingReminder",
            target_id=row.pk,
            payload={
                "kind": row.kind,
                "yclients_record_id": row.yclients_record_id,
            },
        )
        sent += 1

    if sent or failed or skipped or stale or deferred or muted:
        logger.info(
            "bookings.dispatch.summary sent=%d failed=%d skipped=%d stale=%d deferred=%d muted=%d",
            sent,
            failed,
            skipped,
            stale,
            deferred,
            muted,
        )
    return {
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
        "stale": stale,
        "deferred": deferred,
        "muted": muted,
    }


# ─── booking.completed producer ───────────────────────────────────────────
# Phase 2.3 — emits taxonomy §3.1 booking.completed when the periodic scan
# finds a confirmed booking whose visit time has passed. Without this
# producer LoyaltySubscriber (apps/loyalty, Phase 1.a) has nothing to
# subscribe to. YClients webhook port (Q-ATT-IMPL7) will be the more
# accurate signal later; this scan is the unblocking MVP until then.

# Buffer after visit_at + duration_min before we declare the visit done.
# 30 min covers «service overran» without delaying loyalty point credit
# unreasonably. Tunable via monkeypatch in tests.
COMPLETED_GRACE_MINUTES = 30
# Soft cap on rows per tick. Catches up over multiple ticks if backlog
# accumulates (post-outage). Same defensive pattern as BATCH_LIMIT above.
COMPLETED_BATCH_LIMIT = 200


@shared_task(name="bookings.detect_completed_bookings")
def detect_completed_bookings() -> dict[str, int]:
    """Закрыть визиты, чьё время прошло **и** чей канон не возражает.

    Штамп ``completed_at`` + ``completed_by=system`` и событие
    ``booking.completed`` — ровно по одному разу на строку.

    ### Чем доказывается «состоялся» (DRF-2454)

    Раньше единственным условием был ``status == CONFIRMED``. Это **не
    состояние визита**: ни одно входящее событие эту колонку не двигает, а
    отмены канона доезжают до зеркала ``RemoteBookingProxy``. Стенд 24.09: три
    отменённых визита и один неоплаченный были объявлены состоявшимися.

    Теперь перед штампом спрашивается зеркало
    (:func:`apps.bookings.completion_evidence.mirror_evidence`), и **отсутствие
    свидетельства — отказ**: не поставить штамп обратимо, поставить ложный —
    нет. Цена этого выбора названа там же: строки без зеркала (устаревший путь
    YClients) автоматически больше не закрываются.

    Returns:
      Counters: ``{scanned, emitted, raced, emit_failed, skipped}``.
      - ``scanned``: rows matched the time predicate
      - ``emitted``: rows where this worker won the CAS and emitted
      - ``raced``: rows another worker had already stamped (lost the CAS)
      - ``emit_failed``: emit raised, stamp rolled back for the next tick
      - ``skipped``: свидетельства нет либо оно против; причины — в логе сводки

    ### Race-safety

    The CAS on ``completed_at IS NULL`` is the «exactly-once emit»
    guarantee. Two workers hitting the same row do an UPDATE; only one
    rowcount comes back 1. The winning worker emits to eventbus; the
    losing worker's rowcount=0 path is silent.

    ### Tenant-less query intentional

    We use ``BookingRequest.all_tenants`` and rely on the eventbus emit
    to carry ``tenant`` from the row itself. The task itself runs from
    system context (Celery beat — no request, no current_tenant).

    ### Why a Celery task, not a model signal

    Visit completion is a *temporal* event («the clock ran out»), not a
    data-mutation event. Nothing in the booking record actually changes
    at completion time. Signal-based detection would require either a
    polling sentinel or a stored timer; the Celery beat IS the timer.

    ### Закрытие по часам подписывается своим именем (владелец, 30.08)

    Часы, досчитавшие до конца, ничего не знают о том, пришёл ли человек.
    Раньше этот факт жил только в строке события (``marked_by=system``) и
    исчезал вместе с ней: строка ``BookingRequest`` оставалась с одним
    ``completed_at``, по которому «часы досчитали» и «мастер подтвердил»
    неразличимы — и запрос отзыва уходил ровно потому, что часы досчитали.

    Теперь тот же факт стоит и в строке (``completed_by='system'``), и в
    событии. Последствия, которым нужно подтверждение визита, гейтятся по
    нему; сам визит закрывается как закрывался — иначе он висит вечно и
    ломает расписание.
    """

    from apps.booking.models import BookingRequest
    from apps.bookings.completion_evidence import mirror_evidence
    from apps.eventbus import services as eventbus_services

    now = timezone.now()
    # Coalesce duration_min (NULL means «unknown, assume 60»). Apply the
    # buffer at query time so we don't pull millions of just-finished rows.
    from datetime import timedelta

    default_duration_min = 60

    candidates = list(
        BookingRequest.all_tenants.filter(
            status=BookingRequest.Status.CONFIRMED,
            completed_at__isnull=True,
            visit_at__isnull=False,
            visit_at__lte=now - timedelta(minutes=COMPLETED_GRACE_MINUTES),
        ).order_by("visit_at")[:COMPLETED_BATCH_LIMIT]
    )

    counters = {"scanned": 0, "emitted": 0, "raced": 0, "emit_failed": 0, "skipped": 0}
    # Причины отказов — по имени: «не штамповали» без причины неотличимо от
    # «нечего было штамповать», и ровно это скрывало дефект DRF-2454.
    skipped_by_reason: dict[str, int] = {}

    for booking in candidates:
        duration = booking.duration_min or default_duration_min
        # Per-row recheck: visit_at + duration + grace ≤ now. The query-
        # level filter is `visit_at + grace`, conservative without
        # duration. Filter out rows where the duration pushes completion
        # into the future.
        #
        # ``visit_at`` is guaranteed non-NULL by the query filter
        # (visit_at__isnull=False) — assert for mypy and as a defensive
        # invariant against future filter drift.
        assert booking.visit_at is not None
        if booking.visit_at + timedelta(minutes=duration + COMPLETED_GRACE_MINUTES) > now:
            continue

        counters["scanned"] += 1

        # DRF-2454 — штамп только по ПОЛОЖИТЕЛЬНОМУ свидетельству канона.
        #
        # ``status == CONFIRMED`` в запросе выше — не состояние визита: ни одно
        # входящее событие эту колонку не двигает (``followups`` говорит это
        # прямо), а отмены канона доезжают до зеркала. На стенде из-за этого три
        # ОТМЕНЁННЫХ визита и один НЕОПЛАЧЕННЫЙ оказались «состоявшимися».
        #
        # Несимметричность цены: не поставить штамп — обратимо (следующий тик),
        # поставить ложный — необратимо после разбора очереди. Поэтому
        # отсутствие свидетельства здесь ОТКАЗ.
        may_stamp, reason = mirror_evidence(booking)
        if not may_stamp:
            counters["skipped"] += 1
            skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1
            continue

        # CAS: win the right to emit. WHERE completed_at IS NULL guards
        # against two workers stamping the same row twice.
        rowcount = BookingRequest.all_tenants.filter(
            pk=booking.pk,
            completed_at__isnull=True,
            status=BookingRequest.Status.CONFIRMED,
        ).update(completed_at=now, completed_by=SYSTEM_ACTOR)
        if rowcount == 0:
            counters["raced"] += 1
            continue

        try:
            eventbus_services.emit(
                "booking.completed",
                {
                    "booking_id": str(booking.pk),
                    "completed_at": now.isoformat(),
                    # «marked_by=system» because no human/yclients signal
                    # triggered completion — the Celery scanner detected it.
                    # When Q-ATT-IMPL7 lands, YClients webhook becomes the
                    # producer for status-driven completion with
                    # marked_by=external.
                    "marked_by": SYSTEM_ACTOR,
                    # То же значение под именем, которым его зовёт Ayla.
                    # Подписчики читают ОДИН ключ на обоих путях, вместо
                    # того чтобы каждый помнил, какое из двух имён у него
                    # в конверте (apps.booking.completion.ACTOR_KEYS).
                    # Дублирование дешевле, чем переименование поля в
                    # контракте перед пилотом.
                    "completed_by": SYSTEM_ACTOR,
                },
                actor_type="system",
                tenant=booking.tenant,
            )
            counters["emitted"] += 1
        except Exception:  # noqa: BLE001 — emit failure should NOT block other rows
            logger.exception(
                "bookings.detect_completed.emit_failed booking=%s",
                booking.pk,
            )
            # Roll back the completed_at stamp so the next tick retries.
            # ``completed_by`` goes back with it: a row that is not closed
            # must not name a closer, or the next tick's CAS wins against
            # a row that already looks system-closed to every reader.
            BookingRequest.all_tenants.filter(pk=booking.pk).update(
                completed_at=None, completed_by=""
            )
            # Telemetry: track emit-failure separately. Earlier code
            # decremented `scanned` on rollback which lied about work
            # done — ops dashboards now see {scanned, emitted, raced,
            # emit_failed} where scanned == emitted + raced + emit_failed.
            counters["emit_failed"] += 1

    if counters["emitted"] or counters["raced"] or counters["emit_failed"] or counters["skipped"]:
        logger.info(
            "bookings.detect_completed.summary scanned=%d emitted=%d raced=%d "
            "emit_failed=%d skipped=%d reasons=%s",
            counters["scanned"],
            counters["emitted"],
            counters["raced"],
            counters["emit_failed"],
            counters["skipped"],
            # Каждый отказ назван: «пропустили N» без причин — то же молчание,
            # из которого вырос этот лист.
            ",".join(f"{k}={v}" for k, v in sorted(skipped_by_reason.items())) or "-",
        )
    return counters
