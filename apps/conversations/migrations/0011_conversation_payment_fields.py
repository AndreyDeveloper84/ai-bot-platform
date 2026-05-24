"""Add 6 payment-event grounding fields to ``Conversation`` (Gamma #443 handoff).

Gamma's payment consumer (PR #443) populates these from Ayla canonical
``payment.*`` events per ``docs/architecture/event-contract.md`` §3.5-§3.8::

    payment.captured  → last_payment_captured_at + consecutive_payment_failures=0 + pending_payment_id=NULL
    payment.failed    → last_payment_failed_at + last_payment_failure_code (enum) +
                        consecutive_payment_failures += 1 + pending_payment_id=NULL
    payment.refunded  → last_payment_refunded_at
    payment.pending   → pending_payment_id

Pure additive, nullable, no backfill — existing rows stay NULL / zero
until the first ``payment.*`` event arrives. Index on
``pending_payment_id`` is created automatically from ``db_index=True``.

**Concurrency decision:** ``atomic=True`` (default) — regular
``CREATE INDEX`` inside a transaction. ``Conversation`` is not a hot
table at pilot scale; non-concurrent is simpler and rolls back cleanly
on migration failure. ``CONCURRENTLY`` would be warranted only at much
larger row counts on a write-heavy table.

**PII note:** the ``last_payment_failure_code`` field is an enum-only
contract — see field ``help_text``. Free-text YooKassa error messages
can contain card numbers; Gamma's consumer maps them to the enum before
writing this column. This migration ships the schema only; the PII
guarantee is enforced upstream in the consumer.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0010_conversation_last_booking_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="last_payment_captured_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "When the customer's most recent successful payment "
                    "was captured (Ayla canonical payment.captured event). "
                    "Used for AI context grounding."
                ),
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="last_payment_failed_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the customer's most recent payment attempt failed.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="last_payment_failure_code",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Enum code for last failure reason. Values: "
                    "'insufficient_funds' | 'card_expired' | 'card_declined' "
                    "| 'three_d_secure_failed' | 'other'. "
                    "CRITICAL: enum-only — YooKassa error messages may "
                    "contain PII (card numbers). Free-text reasons must be "
                    "mapped to enum in payment consumer."
                ),
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="last_payment_refunded_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the customer's most recent payment was refunded.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="pending_payment_id",
            field=models.UUIDField(
                blank=True,
                db_index=True,
                help_text=(
                    "Ayla canonical payment_id for currently-pending payment "
                    "(authorized but not yet captured/failed). Reset to NULL "
                    "on captured/failed/refunded."
                ),
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="consecutive_payment_failures",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text=(
                    "Counter incremented on payment.failed, reset to 0 on "
                    "payment.captured. Used for human handoff threshold "
                    "(PAYMENT_FAILED_HANDOFF_THRESHOLD env var)."
                ),
            ),
        ),
    ]
