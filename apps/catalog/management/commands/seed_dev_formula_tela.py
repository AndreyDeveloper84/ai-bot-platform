"""Seed the 'formula-tela' tenant with a minimum catalog for Mini App dev testing.

Idempotent: re-running updates display fields but keeps existing IDs.
Used to bootstrap ai_bot_platform_dev on app.penza.taxi so the customer
Mini App auth + catalog screens render against real data without doing a
full mysite migration first.

Usage::

    python manage.py seed_dev_formula_tela
    python manage.py seed_dev_formula_tela --max-user-id 1234567890

The optional ``--max-user-id`` creates a BotUser row so a specific MAX
account can pass ``/auth/verify`` without first DMing the bot.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.scheduling.models import Weekday, WorkingHours
from apps.tenancy.models import Tenant

TENANT_SLUG = "formula-tela"
TENANT_NAME = "Формула тела"

# `Any` because the seed dicts mix str/int/Decimal/bool/list values.
# TypedDicts would be more precise but this is one-shot dev seed data.
SERVICES: list[dict[str, Any]] = [
    {
        "external_id": 1001,
        "slug": "lpg-massage",
        "name": "LPG-массаж",
        "short_description": "Аппаратный массаж против целлюлита",
        "description": "Вакуумно-роликовый массаж тела. Курс 10 процедур.",
        "price_from": Decimal("2500.00"),
        "duration_min": 45,
        "is_popular": True,
    },
    {
        "external_id": 1002,
        "slug": "body-wrap",
        "name": "Обёртывание",
        "short_description": "Лимфодренаж и детокс",
        "description": "Водорослевое обёртывание с термоодеялом.",
        "price_from": Decimal("3200.00"),
        "duration_min": 60,
        "is_popular": False,
    },
    {
        "external_id": 1003,
        "slug": "presso-therapy",
        "name": "Прессотерапия",
        "short_description": "Аппаратный лимфодренаж",
        "description": "Прессотерапия в костюме на всё тело.",
        "price_from": Decimal("1800.00"),
        "duration_min": 40,
        "is_popular": True,
    },
]

MASTERS: list[dict[str, Any]] = [
    {
        "external_id": 2001,
        "name": "Анна Иванова",
        "specialization": "Аппаратные методики",
        "bio": "8 лет в эстетике тела. Сертификация LPG.",
        "experience": "8 лет",
        "service_external_ids": [1001, 1002, 1003],
    },
    {
        "external_id": 2002,
        "name": "Мария Петрова",
        "specialization": "Массаж и обёртывания",
        "bio": "5 лет работы в студии. Любимая процедура — обёртывание.",
        "experience": "5 лет",
        "service_external_ids": [1002, 1003],
    },
]

# Mon–Fri 10:00–20:00 with 14:00–15:00 lunch; Sat 11:00–18:00; Sun off.
WORKING_HOURS = [
    (Weekday.MONDAY, True, time(10, 0), time(20, 0), time(14, 0), time(15, 0)),
    (Weekday.TUESDAY, True, time(10, 0), time(20, 0), time(14, 0), time(15, 0)),
    (Weekday.WEDNESDAY, True, time(10, 0), time(20, 0), time(14, 0), time(15, 0)),
    (Weekday.THURSDAY, True, time(10, 0), time(20, 0), time(14, 0), time(15, 0)),
    (Weekday.FRIDAY, True, time(10, 0), time(20, 0), time(14, 0), time(15, 0)),
    (Weekday.SATURDAY, True, time(11, 0), time(18, 0), None, None),
    (Weekday.SUNDAY, False, None, None, None, None),
]


class Command(BaseCommand):
    help = "Seed the formula-tela tenant with minimum catalog + schedule for Mini App dev."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--max-user-id",
            default=None,
            help="MAX channel_user_id to pre-register as a BotUser, so the "
            "Mini App can call /auth/verify without DMing the bot first.",
        )
        parser.add_argument(
            "--max-display-name",
            default="Dev tester",
            help="display_name for the seeded BotUser. Ignored without --max-user-id.",
        )

    @transaction.atomic
    def handle(self, *, max_user_id, max_display_name, **opts) -> None:
        now = timezone.now()

        tenant, created = Tenant.objects.get_or_create(
            slug=TENANT_SLUG,
            defaults={"name": TENANT_NAME, "timezone": "Europe/Moscow"},
        )
        self.stdout.write(
            self.style.SUCCESS(f"{'created' if created else 'reused'} Tenant({tenant.slug})")
        )

        services_by_ext: dict[int, CatalogService] = {}
        for spec in SERVICES:
            svc, svc_created = CatalogService.all_tenants.update_or_create(
                tenant=tenant,
                external_id=spec["external_id"],
                defaults={
                    "slug": spec["slug"],
                    "name": spec["name"],
                    "short_description": spec["short_description"],
                    "description": spec["description"],
                    "price_from": spec["price_from"],
                    "duration_min": spec["duration_min"],
                    "is_active": True,
                    "is_popular": spec["is_popular"],
                    "external_updated_at": now,
                },
            )
            services_by_ext[spec["external_id"]] = svc
            self.stdout.write(f"  {'+' if svc_created else '='} Service {svc.slug}")

        masters_by_ext: dict[int, CatalogMaster] = {}
        for spec in MASTERS:
            mst, mst_created = CatalogMaster.all_tenants.update_or_create(
                tenant=tenant,
                external_id=spec["external_id"],
                defaults={
                    "name": spec["name"],
                    "specialization": spec["specialization"],
                    "bio": spec["bio"],
                    "experience": spec["experience"],
                    "is_active": True,
                    "invite_status": CatalogMaster.InviteStatus.ACCEPTED,
                    "mode": CatalogMaster.Mode.CATALOG_ONLY,
                    "external_updated_at": now,
                },
            )
            masters_by_ext[spec["external_id"]] = mst
            self.stdout.write(f"  {'+' if mst_created else '='} Master {mst.name}")

            for svc_ext in spec["service_external_ids"]:
                MasterService.all_tenants.update_or_create(
                    tenant=tenant,
                    master=mst,
                    service=services_by_ext[svc_ext],
                )

            for day, is_working, start, end, lunch_start, lunch_end in WORKING_HOURS:
                WorkingHours.all_tenants.update_or_create(
                    tenant=tenant,
                    master=mst,
                    day_of_week=day,
                    defaults={
                        "is_working": is_working,
                        "start_time": start,
                        "end_time": end,
                        "lunch_start": lunch_start,
                        "lunch_end": lunch_end,
                    },
                )

        if max_user_id:
            bot_user, was_created = BotUser.all_tenants.update_or_create(
                tenant=tenant,
                channel="max",
                channel_user_id=max_user_id,
                defaults={
                    "display_name": max_display_name,
                    "chat_id": max_user_id,
                    "timezone": "Europe/Moscow",
                },
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"{'created' if was_created else 'reused'} BotUser(max:{bot_user.channel_user_id})"
                )
            )

        self.stdout.write(self.style.SUCCESS("seed complete"))
