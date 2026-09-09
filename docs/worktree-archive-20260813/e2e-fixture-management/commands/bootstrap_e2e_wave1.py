"""Create the deterministic bot-platform half of the Wave 1 E2E fixture."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.booking.models import BookingReminder, RemoteBookingProxy
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


BOT_USER_ID = UUID("20000000-0000-4000-8000-000000000001")
CONVERSATION_ID = UUID("20000000-0000-4000-8000-000000000002")
REMINDER_IDS = {
    BookingReminder.Kind.DAY_BEFORE: UUID("20000000-0000-4000-8000-000000000011"),
    BookingReminder.Kind.TWO_HOURS: UUID("20000000-0000-4000-8000-000000000012"),
}
CHANNEL_USER_ID = "e2e-wave1-max-user-1001"


class Command(BaseCommand):
    help = "Create/reset the bot half of e2e-wave1 from the backend manifest."

    def add_arguments(self, parser):
        parser.add_argument("--backend-manifest", required=True)
        parser.add_argument("--output", help="Optional path for the merged JSON manifest.")

    @transaction.atomic
    def handle(self, *args, **options):
        if not getattr(settings, "BOOKING_VIA_AYLA_REST", False):
            raise CommandError("BOOKING_VIA_AYLA_REST must be ON for the e2e-wave1 fixture")

        source_path = Path(options["backend_manifest"])
        try:
            manifest = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Cannot read backend manifest: {exc}") from exc
        if manifest.get("fixture") != "e2e-wave1" or manifest.get("schema_version") != 1:
            raise CommandError("Unsupported backend manifest")

        tenant_id = UUID(manifest["tenant_id"])
        customer_id = UUID(manifest["backend_customer_id"])
        anchor = datetime.fromisoformat(manifest["anchor"])

        tenant, _ = Tenant.all_objects.update_or_create(
            id=tenant_id,
            defaults={
                "slug": "e2e-wave1", "name": "E2E Wave 1", "is_active": True,
                "timezone": "Europe/Moscow", "locale": "ru-RU", "shadow_mode": False,
                "features": {"BOOKING_VIA_AYLA_REST": True},
            },
        )
        bot_user, _ = BotUser.all_tenants.update_or_create(
            id=BOT_USER_ID,
            defaults={
                "tenant": tenant, "ayla_user_id": customer_id, "channel": "max",
                "channel_user_id": CHANNEL_USER_ID, "chat_id": CHANNEL_USER_ID,
                "phone": "+79990001001", "display_name": "E2E Customer",
                "client_name": "E2E Customer", "timezone": "Europe/Moscow",
                "deleted_at": None, "context": {"fixture": "e2e-wave1"},
            },
        )

        Conversation.all_tenants.filter(
            tenant=tenant, bot_user=bot_user, is_active=True, is_shadow=False
        ).exclude(id=CONVERSATION_ID).update(is_active=False)
        conversation, _ = Conversation.all_tenants.update_or_create(
            id=CONVERSATION_ID,
            defaults={
                "tenant": tenant, "bot_user": bot_user, "state": Conversation.State.IDLE,
                "is_active": True, "is_shadow": False, "deleted_at": None, "outcome": "",
                "skill_state": {}, "last_booking_at": anchor - timedelta(days=30),
                "tier": Conversation.Tier.AI_CONTINUITY, "tier_reason_class": "",
                "tier_locked_at": None, "tier_locked_by_master": None,
            },
        )

        proxies = {}
        for key, data in manifest["appointments"].items():
            appointment_id = UUID(data["appointment_id"])
            proxy, _ = RemoteBookingProxy.all_tenants.update_or_create(
                appointment_id=appointment_id,
                defaults={
                    "tenant": tenant, "bot_user": bot_user,
                    "start_at": datetime.fromisoformat(data["starts_at"]),
                    "end_at": datetime.fromisoformat(data["ends_at"]),
                    "status": data["status"], "source": RemoteBookingProxy.Source.AUTOMATION,
                    "service_id": UUID(manifest["service_id"]),
                    "specialist_id": UUID(manifest["specialist_id"]),
                    "last_synced_event_id": "", "last_applied_appointment_version": data["version"],
                },
            )
            proxies[key] = str(proxy.appointment_id)

        happy = manifest["appointments"]["happy_path"]
        happy_id = UUID(happy["appointment_id"])
        visit_at = datetime.fromisoformat(happy["starts_at"])
        reminder_manifest = []
        for kind, delta in (
            (BookingReminder.Kind.DAY_BEFORE, timedelta(hours=24)),
            (BookingReminder.Kind.TWO_HOURS, timedelta(hours=2)),
        ):
            reminder, _ = BookingReminder.all_tenants.update_or_create(
                id=REMINDER_IDS[kind],
                defaults={
                    "tenant": tenant, "bot_user": bot_user, "booking_request": None,
                    "yclients_record_id": None, "chat_id": bot_user.chat_id,
                    "ayla_appointment_id": happy_id, "visit_at": visit_at, "kind": kind,
                    "status": BookingReminder.Status.PENDING, "scheduled_at": visit_at - delta,
                    "sent_at": None, "replied_at": None, "master_name": "E2E Master",
                    "service_name": "E2E Massage",
                },
            )
            reminder_manifest.append({
                "reminder_id": str(reminder.id), "appointment_id": str(happy_id),
                "kind": reminder.kind, "scheduled_at": reminder.scheduled_at.isoformat(),
                "status": reminder.status,
            })

        manifest.update({
            "bot_tenant_id": str(tenant.id), "bot_user_id": str(bot_user.id),
            "channel": bot_user.channel, "channel_user_id": bot_user.channel_user_id,
            "channel_user_id_label": "channel/external user ID", "conversation_id": str(conversation.id),
            "conversation": {
                "state": conversation.state, "is_active": conversation.is_active,
                "skill_state": conversation.skill_state,
                "last_booking_at": conversation.last_booking_at.isoformat(),
            },
            "remote_booking_proxies": proxies, "reminders": reminder_manifest,
            "booking_via_ayla_rest": True,
        })
        payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        if options.get("output"):
            Path(options["output"]).write_text(payload + "\n", encoding="utf-8")
        self.stdout.write(payload)
