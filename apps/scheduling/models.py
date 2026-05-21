"""Master scheduling — working hours, exceptions, time blocks, change requests.

Owns the salon's first-party answer to *"when can this master see a client?"*.
Aligns with ``docs/design/handoffs/2026-05-18-schedule-management-handoff.md``
spec; revised from the Phase 0a draft after the handoff review surfaced
schema gaps (weekday indexing, lunch breaks, exception types,
TimeBlock, ScheduleChangeRequest, SlotConfig). Replaces the previous
2-model draft (WorkingHours + ScheduleException with two ``kind`` values)
with the 5-model shape the handoff requires.

### Five models

* :class:`WorkingHours` — recurring weekly template per master. One row per
  ``(master, day_of_week)``. ``is_working=False`` represents a regular
  weekly day off without requiring start/end times. Optional embedded
  lunch break (``lunch_start``/``lunch_end``) — common pattern for nail /
  hair salons.
* :class:`ScheduleException` — date-specific override with a 5-value
  ``type`` (vacation / sick_leave / day_off / custom_hours / event). Unique
  per ``(master, date)`` — new exception OVERWRITES the previous one for
  that date (handoff §1 line 100).
* :class:`TimeBlock` — sub-day operational block (lunch on a specific date,
  cleanup, "просто занято"). Distinct from :class:`ScheduleException` —
  blocks have arbitrary duration < day and use ``DateTimeField`` for start
  and end, not date + time.
* :class:`ScheduleChangeRequest` — master proposes a working-hours diff;
  owner approves/rejects. Per Q-M6 lock — approval happens via MAX
  manager-bot DM with inline buttons, but the record itself is stored
  here for audit + 72h auto-escalation.
* :class:`SlotConfig` — tenant-wide slot parameters (buffer between
  bookings, customer lead time, max advance). Salon-wide for MVP per
  Q-SC4; per-master deferred to v1.1.

### Slot resolution (lives in :mod:`apps.scheduling.services.resolver`)

For ``(master, date, service_duration_min)``:

1. Get exceptions for that ``(master, date)``. If type is full-day off
   (vacation / sick_leave / day_off / event with no times) → no slots.
2. Otherwise: if type is ``custom_hours`` → use exception's ``(start, end)``.
3. Otherwise: use :class:`WorkingHours` for that ``day_of_week``,
   skipping ``is_working=False``.
4. Split the working block by lunch break (when set) into pre- and
   post-lunch sub-blocks.
5. Subtract :class:`TimeBlock` overlaps for that date.
6. Subtract existing booking occupancy (still via
   :class:`apps.booking.models.BookingReminder.visit_at` until Phase 1
   adds ``visit_at`` + ``duration_min`` to :class:`BookingRequest`).
7. Apply :class:`SlotConfig` knobs: buffer_min on either side of
   occupied intervals, lead_time_min cutoff, max_advance_days cap,
   slot_granularity_min as grid step.

### Weekday indexing — 0=Mon … 6=Sun

This is intentional and load-bearing. The handoff (§1 line 78) uses
this convention; Python's :meth:`datetime.date.weekday` returns the
same. The Phase 0a draft used ISO 1–7 — a silent corruption bug
waiting to happen on staging. The resolver also calls ``.weekday()``
(not ``.isoweekday()``) for consistency.

### ``created_by`` audit

A nullable FK to ``settings.AUTH_USER_MODEL`` recording the admin who
created the row. Null is allowed for:

- Migration backfill rows
- System-generated defaults (e.g. WorkingHours seeded at master invite)
- Master self-actions via M0 onboarding (master is not yet a Django
  ``User``; Q-SC5 sick-mark flow will need to be revisited when
  master-mobile auth lands)

The handoff (§1 line 99) specifies ``ForeignKey(User)`` without nullable;
we soften to nullable to admit the system / migration cases. Audit-
significant writes (admin endpoints) MUST populate this field.

### Tenant scoping

Every row carries ``tenant`` directly (not transitively via
``CatalogMaster``). CASCADE on tenant deletion — schedule has no value
without its tenant.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.tenancy.managers import TenantScopedManager


class Weekday(models.IntegerChoices):
    """Days of week — Mon=0 … Sun=6.

    Matches :meth:`datetime.date.weekday` and the
    schedule-management handoff §1 line 78. Phase 0a draft used ISO
    1–7; that was a silent-bug risk caught in handoff review.
    """

    MONDAY = 0, "Monday"
    TUESDAY = 1, "Tuesday"
    WEDNESDAY = 2, "Wednesday"
    THURSDAY = 3, "Thursday"
    FRIDAY = 4, "Friday"
    SATURDAY = 5, "Saturday"
    SUNDAY = 6, "Sunday"


# Default slot-config values — referenced by the SlotConfig factory and
# by tests that synthesise unconfigured tenants. Match the handoff defaults
# (§4 Q-SC3 and §4 line 277–285).
DEFAULT_SLOT_GRANULARITY_MIN = 15
DEFAULT_BUFFER_MIN = 5
DEFAULT_LEAD_TIME_MIN = 60
DEFAULT_MAX_ADVANCE_DAYS = 60


class WorkingHours(models.Model):
    """Recurring weekly working-hours template for a single master.

    One row per ``(master, day_of_week)``. When ``is_working=False`` the
    row represents a recurring weekly day off; ``start_time``,
    ``end_time``, ``lunch_start`` and ``lunch_end`` MUST be null in that
    case (CHECK constraint).

    Optional ``lunch_start`` / ``lunch_end`` represent a recurring daily
    lunch break — the resolver splits the working block around them.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="working_hours",
    )
    master = models.ForeignKey(
        "catalog.CatalogMaster",
        on_delete=models.CASCADE,
        related_name="working_hours",
    )
    day_of_week = models.SmallIntegerField(
        choices=Weekday.choices,
        help_text="0=Monday … 6=Sunday — matches Python date.weekday().",
    )
    is_working = models.BooleanField(
        default=True,
        help_text="False = recurring weekly day off. When False, all time fields must be null.",
    )
    start_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Block start in tenant-local TZ. Inclusive. Required when is_working=True.",
    )
    end_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Block end in tenant-local TZ. Exclusive (last bookable "
        "start is end_time minus service duration). Required when "
        "is_working=True.",
    )
    lunch_start = models.TimeField(
        null=True,
        blank=True,
        help_text="Optional recurring lunch start. The resolver splits "
        "the working block around lunch_start..lunch_end.",
    )
    lunch_end = models.TimeField(
        null=True,
        blank=True,
        help_text="Optional recurring lunch end. Must be set together with lunch_start.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Admin who created this row. Null for system / migration backfills.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Working hours"
        verbose_name_plural = "Working hours"
        ordering = ["master_id", "day_of_week"]
        # Handoff §1 line 82: unique (master, day_of_week).
        constraints = [
            models.UniqueConstraint(
                fields=["master", "day_of_week"],
                name="working_hours_one_row_per_master_day",
            ),
            # Working day must have start + end times.
            models.CheckConstraint(
                condition=models.Q(is_working=False, start_time__isnull=True, end_time__isnull=True)
                | models.Q(is_working=True, start_time__isnull=False, end_time__isnull=False),
                name="working_hours_times_match_is_working",
            ),
            # When set, end_time > start_time.
            models.CheckConstraint(
                condition=models.Q(end_time__isnull=True)
                | models.Q(end_time__gt=models.F("start_time")),
                name="working_hours_end_after_start",
            ),
            # Lunch is either both-null or both-set.
            models.CheckConstraint(
                condition=(
                    models.Q(lunch_start__isnull=True, lunch_end__isnull=True)
                    | models.Q(lunch_start__isnull=False, lunch_end__isnull=False)
                ),
                name="working_hours_lunch_both_or_neither",
            ),
            # When set, lunch_end > lunch_start.
            models.CheckConstraint(
                condition=models.Q(lunch_end__isnull=True)
                | models.Q(lunch_end__gt=models.F("lunch_start")),
                name="working_hours_lunch_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        if not self.is_working:
            return f"WorkingHours[{self.master_id} {self.get_day_of_week_display()} OFF]"
        return (
            f"WorkingHours[{self.master_id} {self.get_day_of_week_display()} "
            f"{self.start_time:%H:%M}-{self.end_time:%H:%M}]"
        )


class ScheduleException(models.Model):
    """Date-specific override of the recurring weekly template.

    One row per ``(master, date)`` — UniqueConstraint enforces this.
    New exception OVERWRITES the previous one for that date (handoff
    §1 line 100 explicitly says "new exception overwrites"). Multi-day
    vacations are represented as N rows (one per date); UI aggregates
    contiguous ones for display.

    Type semantics:

    * ``vacation`` / ``sick_leave`` / ``day_off`` / ``event`` — full-day
      absences. ``start_time`` and ``end_time`` MUST be null.
    * ``custom_hours`` — master works that day with non-standard hours.
      ``start_time`` and ``end_time`` MUST both be set; weekday template
      is ignored entirely for that date.

    The 5-value enum is the canonical type list from handoff §1 line 89.
    """

    class Type(models.TextChoices):
        VACATION = "vacation", "Отпуск"
        SICK_LEAVE = "sick_leave", "Больничный"
        DAY_OFF = "day_off", "Выходной"
        CUSTOM_HOURS = "custom_hours", "Изменён график"
        EVENT = "event", "Корпоратив / обучение"

    # Full-day types — start_time / end_time MUST be null.
    FULL_DAY_TYPES = {Type.VACATION, Type.SICK_LEAVE, Type.DAY_OFF, Type.EVENT}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="schedule_exceptions",
    )
    master = models.ForeignKey(
        "catalog.CatalogMaster",
        on_delete=models.CASCADE,
        related_name="schedule_exceptions",
    )
    date = models.DateField(
        help_text="Calendar date in tenant-local timezone.",
    )
    type = models.CharField(
        max_length=16,
        choices=Type.choices,
        help_text="5-value enum per schedule-management handoff §1.",
    )
    start_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Required for type=custom_hours, must be NULL for all other types.",
    )
    end_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Required for type=custom_hours, must be NULL for all other types.",
    )
    reason = models.TextField(
        blank=True,
        default="",
        help_text="Free-form note visible to the salon team only.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Schedule exception"
        verbose_name_plural = "Schedule exceptions"
        ordering = ["-date", "master_id"]
        indexes = [
            models.Index(fields=["tenant", "master", "date"]),
            models.Index(fields=["tenant", "type", "date"]),
        ]
        constraints = [
            # One exception per (master, date) — new overwrites (handoff line 100).
            models.UniqueConstraint(
                fields=["master", "date"],
                name="schedule_exception_one_per_master_date",
            ),
            # type=custom_hours ⇒ both times set; all other types ⇒ both null.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        type="custom_hours",
                        start_time__isnull=False,
                        end_time__isnull=False,
                    )
                    | (
                        ~models.Q(type="custom_hours")
                        & models.Q(start_time__isnull=True, end_time__isnull=True)
                    )
                ),
                name="schedule_exception_times_match_type",
            ),
            # When custom_hours, end_time > start_time.
            models.CheckConstraint(
                condition=models.Q(end_time__isnull=True)
                | models.Q(end_time__gt=models.F("start_time")),
                name="schedule_exception_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        if self.type == self.Type.CUSTOM_HOURS:
            return (
                f"ScheduleException[{self.master_id} {self.date} "
                f"custom {self.start_time:%H:%M}-{self.end_time:%H:%M}]"
            )
        return f"ScheduleException[{self.master_id} {self.date} {self.type}]"


class TimeBlock(models.Model):
    """One-off operational block of a master's time.

    Distinct from :class:`ScheduleException`: a TimeBlock has arbitrary
    sub-day duration and uses ``DateTimeField`` start/end so it can span
    midnight or be reused across days via separate rows. Common
    examples: «Анна обедает 13:00–14:00 сегодня», «уборка кабинета»,
    «просто занято до закрытия».

    The resolver subtracts every TimeBlock that intersects the queried
    date+master pair.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="time_blocks",
    )
    master = models.ForeignKey(
        "catalog.CatalogMaster",
        on_delete=models.CASCADE,
        related_name="time_blocks",
    )
    start_at = models.DateTimeField(
        help_text="Block start instant (timezone-aware, stored UTC).",
    )
    end_at = models.DateTimeField(
        help_text="Block end instant (timezone-aware, stored UTC). "
        "Exclusive — a candidate slot starting exactly at end_at is "
        "allowed.",
    )
    reason = models.CharField(
        max_length=200,
        help_text="Human-readable note for the salon team. Common: "
        "«обед», «уборка», «подготовка», «просто занято».",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Time block"
        verbose_name_plural = "Time blocks"
        ordering = ["master_id", "start_at"]
        indexes = [
            models.Index(fields=["tenant", "master", "start_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_at__gt=models.F("start_at")),
                name="time_block_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"TimeBlock[{self.master_id} {self.start_at:%Y-%m-%d %H:%M}-{self.end_at:%H:%M}]"


class ScheduleChangeRequest(models.Model):
    """Master proposes a schedule diff; owner approves via bot DM (Q-M6).

    Per Q-M6 lock — owner approval happens via MAX manager-bot DM with
    inline buttons. The record itself lives here so the bot DM is
    rendered from durable state, and the owner's tap mutates this row
    + applies the diff to :class:`WorkingHours` / :class:`ScheduleException`
    atomically.

    72h no decision → ``status`` flips to AUTO_ESCALATED by a periodic
    Celery beat (out of scope of Phase 0a — landing alongside the
    master-mobile §M3 implementation).

    ``requested_change`` is intentionally a free-form JSONField — the
    diff shape varies by request type (e.g. recurring weekday change vs
    one-off date custom hours). Writers populate per their domain.

    ### M3 typed availability-request fields (PR Tier1.2)

    Master-mobile §M3 line 475 specifies a typed `POST
    /api/master/availability` endpoint shape — start/end datetimes +
    a closed-set reason_class (vacation/sick/personal/other) + free-form
    reason_text. Rather than smuggle the typed payload through the
    free-form ``requested_change`` JSON blob (which the admin approve/
    reject endpoint would then need to parse defensively), we add
    first-class columns:

    * ``requested_start`` / ``requested_end`` — UTC instants describing
      the off-time window. Nullable so legacy ``requested_change``-only
      rows (the pre-M3 path) keep working.
    * ``reason_class`` — closed set vacation/sick/personal/other.
      Optional/blank for legacy rows.
    * ``reason_text`` — short user-supplied note. The free-form
      ``reason`` field above stays as a long-form fallback to preserve
      back-compat.

    Admin approve/reject endpoint (separate PR) reads
    ``requested_start`` / ``requested_end`` + ``reason_class`` directly;
    no JSON parsing.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled by master"
        AUTO_ESCALATED = "auto_escalated", "Auto-escalated (72h timeout)"

    class ReasonClass(models.TextChoices):
        VACATION = "vacation", "Отпуск"
        SICK = "sick", "Больничный"
        PERSONAL = "personal", "Личные дела"
        OTHER = "other", "Другое"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="schedule_change_requests",
    )
    master = models.ForeignKey(
        "catalog.CatalogMaster",
        on_delete=models.CASCADE,
        related_name="schedule_change_requests",
        help_text="Master who submitted the request.",
    )
    requested_change = models.JSONField(
        default=dict,
        help_text="Diff structure — writer-defined shape. Common keys: "
        "type (working_hours_change / exception_add), day_of_week, "
        "new_start, new_end, date, exception_type, reason.",
    )
    # M3 typed columns (PR Tier1.2 / master-mobile §M3 line 475). Nullable
    # so legacy ``requested_change``-only rows still validate.
    requested_start = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Start of the proposed off-time window (UTC). Set by "
        "M3 POST /api/master/availability; null for legacy "
        "requested_change-only rows.",
    )
    requested_end = models.DateTimeField(
        null=True,
        blank=True,
        help_text="End of the proposed off-time window (UTC). Set by "
        "M3 POST /api/master/availability; null for legacy rows. Always "
        "> requested_start when both are set.",
    )
    reason_class = models.CharField(
        max_length=16,
        choices=ReasonClass.choices,
        blank=True,
        default="",
        help_text="Closed-set classification of the master's reason. "
        "Empty for legacy rows that pre-date the M3 typed endpoint.",
    )
    reason_text = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Short free-form note from the master (≤200 chars). "
        "The longer ``reason`` text field stays as the legacy free-form "
        "fallback for callers that pre-date the M3 endpoint.",
    )
    reason = models.TextField(
        blank=True,
        default="",
        help_text="Master's free-form rationale (e.g. «утренние йога»).",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    resolution_note = models.TextField(
        blank=True,
        default="",
        help_text="Owner's note on approval/rejection.",
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    # Snapshot of the BotUser who submitted the request. Distinct from
    # ``master`` (the CatalogMaster row) and from ``resolved_by`` (the
    # Django User who approved/rejected). Lets the admin endpoint render
    # «requested by Анна» without re-joining through CatalogMaster →
    # linked_bot_user (which may have been swapped on re-onboarding).
    requested_by = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="BotUser who submitted via the M3 endpoint. NULL for "
        "system-generated or legacy rows.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Schedule change request"
        verbose_name_plural = "Schedule change requests"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status", "-created_at"]),
            models.Index(fields=["tenant", "master", "-created_at"]),
        ]
        constraints = [
            # When both window endpoints are set, end must be after start.
            # Nullable allowed so legacy ``requested_change``-only rows
            # don't break the constraint.
            models.CheckConstraint(
                condition=(
                    models.Q(requested_start__isnull=True)
                    | models.Q(requested_end__isnull=True)
                    | models.Q(requested_end__gt=models.F("requested_start"))
                ),
                name="schedule_change_request_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"ChangeRequest[{self.master_id} {self.status} {self.created_at:%Y-%m-%d}]"


class SlotConfig(models.Model):
    """Tenant-wide slot parameters consumed by the resolver.

    Salon-wide for MVP per Q-SC4. Per-master overrides deferred to v1.1.

    All fields have safe defaults that match the handoff (§4 line
    277–285 + Q-SC3 buffer default). A missing row for a tenant is
    treated by the resolver factory as "use defaults" — but the admin
    UI surfaces this row explicitly so the salon can tune.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.OneToOneField(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="slot_config",
    )
    slot_granularity_min = models.PositiveSmallIntegerField(
        default=DEFAULT_SLOT_GRANULARITY_MIN,
        help_text="Grid step in minutes for candidate slot starts. Default 15. Range 5–60.",
    )
    buffer_min = models.PositiveSmallIntegerField(
        default=DEFAULT_BUFFER_MIN,
        help_text="Minimum gap (minutes) before AND after every "
        "occupied interval. Lets the master prep / clean the station. "
        "Default 5. Range 0–60.",
    )
    lead_time_min = models.PositiveSmallIntegerField(
        default=DEFAULT_LEAD_TIME_MIN,
        help_text="Minimum minutes between now and the earliest "
        "bookable start. Default 60. Range 0–2880 (48h).",
    )
    max_advance_days = models.PositiveSmallIntegerField(
        default=DEFAULT_MAX_ADVANCE_DAYS,
        help_text="Don't surface slots farther than this many days ahead. Default 60. Range 7–180.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Slot config"
        verbose_name_plural = "Slot configs"

    def __str__(self) -> str:
        return f"SlotConfig[tenant={self.tenant_id}]"


__all__ = [
    "DEFAULT_BUFFER_MIN",
    "DEFAULT_LEAD_TIME_MIN",
    "DEFAULT_MAX_ADVANCE_DAYS",
    "DEFAULT_SLOT_GRANULARITY_MIN",
    "ScheduleChangeRequest",
    "ScheduleException",
    "SlotConfig",
    "TimeBlock",
    "Weekday",
    "WorkingHours",
]
