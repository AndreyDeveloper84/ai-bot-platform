"""#442 — RemoteBookingProxy + BookingReminder.ayla_appointment_id.

Unified atomic-by-intent migration that lands the full Ayla bridge on
the ``booking`` app:

* New :class:`apps.booking.models.RemoteBookingProxy` (one row per
  Ayla appointment_id, cross-tenant cache).
* New ``BookingReminder.ayla_appointment_id`` column + partial unique
  index ``unique_ayla_booking_reminder(ayla_appointment_id, kind)``.

### Why ``atomic = False``

The partial unique index is created with ``CREATE UNIQUE INDEX
CONCURRENTLY``. Postgres rejects CONCURRENTLY inside a transaction
block, so the whole migration must run with ``atomic = False`` —
otherwise Django wraps it in BEGIN/COMMIT and Postgres aborts the
operation. We accept the rare "migration crashes mid-way → some ops
applied, some not" failure mode here because:

1. ``AddField`` / ``CreateModel`` are individually transactional on
   modern Postgres (each DDL is atomic in its own MVCC frame).
2. Re-running the migration after a partial failure is safe: Django
   tracks per-operation state in ``django_migrations`` only at the
   migration level, but the operations themselves are idempotent
   enough that we use ``IF NOT EXISTS`` on the RunPython index
   creation.

### Vendor split

CREATE INDEX CONCURRENTLY is Postgres-only. SQLite (used by the test
backend) does not support the keyword. The RunPython hook branches on
``schema_editor.connection.vendor`` so tests still get the partial
unique index (just without the online-creation guarantee).
"""

import django.db.models.deletion
from django.db import migrations, models


def create_partial_unique_index(apps, schema_editor):
    """Create the partial unique index online on Postgres, regular on SQLite.

    ### tenant_id is in the index columns

    Per Round-5 adversarial finding F-Adv2: a global ``(ayla_appointment_id,
    kind)`` unique would let a malicious tenant B spoof an appointment_id
    belonging to tenant A and overwrite A's reminder via the partial
    constraint. Including ``tenant_id`` in the index columns scopes the
    uniqueness per tenant so cross-tenant collisions are impossible.

    ### DROP IF EXISTS first (F-Fri2)

    If a previous CONCURRENTLY build crashed mid-way, Postgres leaves an
    INVALID index that ``IF NOT EXISTS`` skips silently — masking the
    failure. Dropping first guarantees a clean re-build.
    """
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        schema_editor.execute("DROP INDEX IF EXISTS unique_ayla_booking_reminder;")
        schema_editor.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY "
            "unique_ayla_booking_reminder "
            "ON booking_bookingreminder (ayla_appointment_id, tenant_id, kind) "
            "WHERE ayla_appointment_id IS NOT NULL;"
        )
    else:
        # SQLite (test backend) — partial unique syntax is supported
        # but CONCURRENTLY is not. Plain CREATE works.
        schema_editor.execute("DROP INDEX IF EXISTS unique_ayla_booking_reminder;")
        schema_editor.execute(
            "CREATE UNIQUE INDEX "
            "unique_ayla_booking_reminder "
            "ON booking_bookingreminder (ayla_appointment_id, tenant_id, kind) "
            "WHERE ayla_appointment_id IS NOT NULL;"
        )


def drop_partial_unique_index(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        schema_editor.execute("DROP INDEX CONCURRENTLY IF EXISTS unique_ayla_booking_reminder;")
    else:
        schema_editor.execute("DROP INDEX IF EXISTS unique_ayla_booking_reminder;")


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY rejects being inside a transaction block.
    # See module docstring for the trade-off rationale.
    atomic = False

    dependencies = [
        ("booking", "0011_q12a_commercial_identity_snapshot"),
        ("identity", "0005_botuser_ayla_user_id"),
        ("tenancy", "0009_tenantstaff"),
    ]

    operations = [
        migrations.CreateModel(
            name="RemoteBookingProxy",
            fields=[
                (
                    "appointment_id",
                    models.UUIDField(
                        editable=False,
                        help_text="Canonical Ayla djangoproject Appointment.id (UUID).",
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "start_at",
                    models.DateTimeField(
                        db_index=True,
                        help_text=(
                            "Appointment start, salon-local timezone. Reminder "
                            "math (T-24h / T-2h) computes against this."
                        ),
                    ),
                ),
                (
                    "end_at",
                    models.DateTimeField(
                        help_text=(
                            "Appointment end. Round-3 spec §3.3: reschedule "
                            "preserves duration via "
                            "``new_end_at = new_start_at + (old_end_at - old_start_at)`` — "
                            "this field is what the math reads."
                        ),
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("confirmed", "Confirmed"),
                            ("pending_payment", "Pending payment"),
                            ("tentative", "Tentative"),
                            ("cancelled", "Cancelled"),
                            ("completed", "Completed"),
                        ],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("mobile_app", "Mobile app"),
                            ("admin_console", "Admin console"),
                            ("automation", "Automation"),
                            ("yclients_sync", "YClients sync"),
                        ],
                        default="",
                        help_text=(
                            "Where the booking originated. Empty when source "
                            "isn't carried in the event (booking.cancelled / "
                            ".rescheduled / .completed don't repeat the source)."
                        ),
                        max_length=20,
                    ),
                ),
                (
                    "service_id",
                    models.UUIDField(
                        blank=True,
                        db_index=True,
                        help_text=(
                            "Ayla Service.id — looked up via catalog mirror for "
                            "the AI to say 'у тебя стрижка'. Nullable because "
                            "update events (cancelled / rescheduled / completed) "
                            "don't repeat the service reference; the row keeps "
                            "the value set at creation time."
                        ),
                        null=True,
                    ),
                ),
                (
                    "specialist_id",
                    models.UUIDField(
                        blank=True,
                        db_index=True,
                        help_text="Ayla Master.id — analogous to ``service_id``.",
                        null=True,
                    ),
                ),
                (
                    "last_synced_event_id",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text=(
                            "ULID of the last cross-service event that touched "
                            "this row. Forensic trace, not the primary "
                            "idempotency key (that's the IngestDedupe table on "
                            "the ingest side)."
                        ),
                        max_length=26,
                    ),
                ),
                (
                    "synced_at",
                    models.DateTimeField(
                        auto_now=True,
                        help_text="When the platform last updated this row.",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Remote booking proxy",
                "verbose_name_plural": "Remote booking proxies",
                "ordering": ["-start_at"],
            },
        ),
        migrations.AddField(
            model_name="bookingreminder",
            name="ayla_appointment_id",
            field=models.UUIDField(
                blank=True,
                db_index=True,
                help_text=(
                    "Ayla appointment UUID per ADR-0009 event contract. When "
                    "set, replaces yclients_record_id as the idempotency key "
                    "for Ayla-side reminders (#442 booking consumer). "
                    "Coexists with the YClients legacy path: rows from the "
                    "YClients webhook keep yclients_record_id + leave this "
                    "nullable; rows from the Ayla consumer set this + leave "
                    "yclients_record_id blank."
                ),
                null=True,
            ),
        ),
        # F-Fri1 fix: make yclients_record_id nullable so the Ayla
        # consumer can write NULL (not "") and avoid colliding with
        # the legacy unique_together(yclients_record_id, kind) when
        # two Ayla appointments share a kind. NULL ≠ NULL in Postgres
        # uniqueness rules, so empty-yclients rows don't pile up
        # under ("", "day_before").
        migrations.AlterField(
            model_name="bookingreminder",
            name="yclients_record_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text=(
                    "YClients record id (stringified — YClients ints fit "
                    "fine but the API will return larger opaque ids on "
                    "enterprise tenants per their roadmap). Nullable so "
                    "the Ayla consumer path (#442) writes NULL here while "
                    "the legacy unique_together (yclients_record_id, kind) "
                    "stays non-conflicting — NULL ≠ NULL in Postgres "
                    "uniqueness, so two Ayla appointments with the same "
                    "kind don't collide."
                ),
                max_length=64,
                null=True,
            ),
        ),
        # Partial unique index via RunPython so Postgres uses
        # CONCURRENTLY (online creation, no table lock on prod) AND
        # includes tenant_id in the index columns (F-Adv2 fix). See
        # module docstring for vendor split rationale.
        migrations.RunPython(
            code=create_partial_unique_index,
            reverse_code=drop_partial_unique_index,
        ),
        migrations.AddField(
            model_name="remotebookingproxy",
            name="bot_user",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Channel-side user identity. Nullable: an Ayla "
                    "mobile-only customer can have bookings before ever "
                    "opening the bot, so the proxy is written orphan and "
                    "a post_save signal on BotUser backfills the ref when "
                    "the user appears via any channel. CASCADE applies "
                    "only to the linked case: if the BotUser is purged "
                    "(GDPR erasure), linked mirrors go too — orphan rows "
                    "are unaffected (no link to cascade through)."
                ),
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="remote_booking_proxies",
                to="identity.botuser",
            ),
        ),
        migrations.AddField(
            model_name="remotebookingproxy",
            name="tenant",
            field=models.ForeignKey(
                help_text=(
                    "Owning tenant. CASCADE — the proxy is derived data; "
                    "if the tenant goes, the local mirror goes too."
                ),
                on_delete=django.db.models.deletion.CASCADE,
                related_name="remote_booking_proxies",
                to="tenancy.tenant",
            ),
        ),
        migrations.AddIndex(
            model_name="remotebookingproxy",
            index=models.Index(
                fields=["bot_user", "start_at"],
                name="booking_rem_bot_use_949903_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="remotebookingproxy",
            index=models.Index(
                fields=["tenant", "status"],
                name="booking_rem_tenant__d8135c_idx",
            ),
        ),
    ]
