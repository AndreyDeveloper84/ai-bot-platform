"""Backfill the ``memory_green`` half of the welcome-S2 consent (DRF-1311).

The S2 welcome text users accepted is, verbatim, a memory disclosure
(«Я буду помнить о тебе только то, что поможет рекомендовать точнее…»,
expanded by S2a «Запоминаю: твои сообщения мне, выбранные цели, питание и
вода…»), and MEMORY_FOUNDATION_DESIGN §8 q.2 decided the pilot activates
``personal_data`` **+ ``memory_green``**. Only ``personal_data`` was ever
written, so ``has_memory_consent(..., "green")`` was False for every user
and the whole memory READ side stayed ``BLOCKED_CONSENT`` — the pilot's
first stored fact («я веган», 2026-08-23) could neither be read back nor
bridged into the Ayla declared prefs.

``global_onboarding`` now records both types at the tap. This migration
repairs the users who already tapped and will never tap again.

**Deliberately narrow.** Only rows whose ``source`` is the global welcome
S2 flow are mirrored: that is the only flow whose disclosure text covers
memory. A ``personal_data`` grant captured anywhere else (registration
form, admin override) showed no memory copy and is left alone — a consent
backfill must never assert a scope the user was not shown.

The mirrored row copies ``document_version`` and ``captured_at`` from the
source grant, so it states what actually happened (this text, at this
moment) rather than "granted at deploy time". ``source`` marks the
provenance so the legal trail shows it was derived, not re-tapped.
"""

from __future__ import annotations

from django.db import migrations

_WELCOME_S2_SOURCE = "global_onboarding:welcome_s2"
_BACKFILL_SOURCE = "backfill:drf1311:welcome_s2"


def backfill_memory_green(apps, schema_editor):
    ConsentRecord = apps.get_model("consent", "ConsentRecord")

    already = set(
        ConsentRecord.objects.filter(
            consent_type="memory_green",
            granted=True,
            withdrawn_at__isnull=True,
        ).values_list("bot_user_id", flat=True)
    )
    sources = ConsentRecord.objects.filter(
        consent_type="personal_data",
        granted=True,
        withdrawn_at__isnull=True,
        source=_WELCOME_S2_SOURCE,
    ).exclude(bot_user_id__in=already)

    for grant in sources:
        mirrored = ConsentRecord.objects.create(
            tenant_id=grant.tenant_id,
            bot_user_id=grant.bot_user_id,
            consent_type="memory_green",
            granted=True,
            source=_BACKFILL_SOURCE,
            document_version=grant.document_version,
        )
        # captured_at is auto_now_add — restate the ACTUAL capture moment.
        ConsentRecord.objects.filter(pk=mirrored.pk).update(captured_at=grant.captured_at)


def unbackfill_memory_green(apps, schema_editor):
    """Reverse: drop only the rows this migration minted (by source slug)."""
    ConsentRecord = apps.get_model("consent", "ConsentRecord")
    ConsentRecord.objects.filter(consent_type="memory_green", source=_BACKFILL_SOURCE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("consent", "0002_alter_consentrecord_consent_type"),
    ]

    operations = [
        migrations.RunPython(backfill_memory_green, unbackfill_memory_green),
    ]
