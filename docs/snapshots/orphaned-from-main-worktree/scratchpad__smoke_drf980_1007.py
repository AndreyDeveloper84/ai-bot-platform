"""DRF-980/1007 post-deploy smoke (read-only effective).

Everything mutating runs inside a transaction that is rolled back at the
end — no rows persist in the pilot DB. Never touches the pilot tenant's
real data: the pilot tenant row is only READ (id), synthetic objects use
a throwaway tenant.
"""

import uuid

from django.contrib import admin as dj_admin
from django.db import transaction

from apps.conversations.models import Conversation
from apps.handoff.admin import AdminTaskAdmin
from apps.handoff.models import AdminTask
from apps.handoff.services import create_admin_task
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


class _Req:
    user = None


def run():
    # ── DRF-1007: setting visibility + helper semantics (pure reads) ──
    from django.conf import settings

    from apps.skills.booking.tools import _resolve_payment_required

    print("SMOKE1007 setting =", getattr(settings, "BOOKING_NO_PREPAYMENT_TENANTS", None))
    pilot = Tenant.objects.get(id="b32a057a-56c7-4bf0-ae50-e11e76ab44be")
    other = Tenant.objects.exclude(id=pilot.id).first()
    print("SMOKE1007 pilot =", _resolve_payment_required(pilot, {}), "(expect False)")
    print(
        "SMOKE1007 other =",
        _resolve_payment_required(other, {}),
        "(expect True)",
        f"[{other.slug}]",
    )
    print(
        "SMOKE1007 explicit_true_overrides =",
        _resolve_payment_required(pilot, {"payment_required": True}),
        "(expect True)",
    )

    # ── DRF-980: admin close path on synthetic objects, rolled back ──
    class _Rollback(Exception):
        pass

    try:
        with transaction.atomic():
            t = Tenant.objects.create(slug=f"smoke-{uuid.uuid4().hex[:8]}", name="smoke")
            bu = BotUser.all_tenants.create(tenant=t, channel="max", channel_user_id="smoke-bu")
            conv = Conversation.all_tenants.create(tenant=t, bot_user=bu)
            with tenant_scope(t):
                task = create_admin_task(conv, task_type=AdminTask.TaskType.HANDOFF)
            conv.refresh_from_db()
            assert conv.state == "human_handoff", conv.state

            adm = AdminTaskAdmin(AdminTask, dj_admin.site)

            def admin_close(pk, status):
                obj = AdminTask.all_tenants.get(pk=pk)
                obj.status = status
                adm.save_model(_Req(), obj, form=None, change=True)

            # 1. RESOLVED via admin path returns the bot.
            admin_close(task.pk, "resolved")
            conv.refresh_from_db()
            print("SMOKE980 resolve state =", conv.state, "(expect idle)")

            # 2. Re-save of the closed task heals an out-of-band re-mute.
            Conversation.all_tenants.filter(pk=conv.pk).update(state="human_handoff")
            admin_close(task.pk, "resolved")
            conv.refresh_from_db()
            print("SMOKE980 resave state =", conv.state, "(expect idle)")

            # 3. Second open task keeps the bot muted until it closes.
            with tenant_scope(t):
                t2 = create_admin_task(conv, task_type=AdminTask.TaskType.HANDOFF)
                t3 = create_admin_task(conv, task_type=AdminTask.TaskType.COMPLAINT)
            admin_close(t2.pk, "resolved")
            conv.refresh_from_db()
            print("SMOKE980 first_of_two state =", conv.state, "(expect human_handoff)")
            admin_close(t3.pk, "cancelled")
            conv.refresh_from_db()
            print("SMOKE980 last_of_two state =", conv.state, "(expect idle)")

            raise _Rollback
    except _Rollback:
        print("SMOKE980 rollback OK — no synthetic rows persisted")


run()
