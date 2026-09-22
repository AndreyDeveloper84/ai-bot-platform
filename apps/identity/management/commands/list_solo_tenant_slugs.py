"""Кого бот доказал соло-мастером — список слагов для каталога (DRF-2325, №31).

# Зачем

Признак `Tenant.kind` появился в каталоге с G4 (DRF-1828) и пишется только
provisioning solo-workspace; миграция 0007 поставила всем прежним строкам
``salon`` по умолчанию. Из-за этого соло-мастер, заведённый раньше G4,
открывает «Место работы» и получает отказ «место ведёт владелец салона»,
хотя владелец — он сам (замер DRF-2254).

Каталог происхождение таких строк не знает. Знает бот: соло-тенант здесь
заводит ``solo_onboarding.create_solo_provider``, и слаг он считает по
личности владельца — ``solo-{канал}-{8 hex sha256(канал:id)}``.

# Доказательство — пересчёт, а не префикс

Эта команда берёт личность владельца тенанта (``TenantStaff`` с ролью
owner → ``BotUser.channel`` + ``channel_user_id``), считает
``_solo_tenant_slug`` заново и печатает слаг, ТОЛЬКО если он сошёлся с
настоящим слагом тенанта. Отбор по префиксу ``solo-`` был бы ровно тем
«именем производной», против которого предостерегает G4: слаг с виду
соло-шный мог завести кто угодно, а сошедшийся пересчёт значит, что строку
завёл provisioning по этой самой личности.

Связь бота и каталога здесь — СЛАГ, не UUID: до G4 каталог заводил строку
через ``ensure_tenant(slug=…)`` со своим ключом.

# Как пользоваться

Команда только читает — ничего не пишет ни здесь, ни в каталоге. Слаги идут
в stdout по одному в строке, отчёт — в stderr; поэтому список подаётся
каталожной команде прямо в канал::

    python manage.py list_solo_tenant_slugs > slugs.txt
    # в каталоге, сухой прогон:
    python manage.py backfill_solo_tenant_kind --from-file slugs.txt

Решение «перевести» принимает каталог и владелец: бот отвечает только за
доказательство происхождения. Мастеров бот НЕ считает — счёт мастеров ведёт
каталог, и вторая копия этого условия разошлась бы с первой молча.

# Чего в списке нет

* **Неактивных тенантов** (``is_active=False``) — они названы числом в
  отчёте. Перевод замороженного тенанта заранее взвёл бы самообслуживание
  на день его разморозки; это решение владельца, а не умолчание команды.
* **Тенантов без владельца** — доказывать нечем: пересчёт берут от личности
  владельца. Названы числом.
* **Людей.** В stdout только слаги тенантов, в stderr — слаги и числа: ни
  имён, ни логинов, ни идентификаторов личности.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.identity.services.solo_onboarding import _solo_tenant_slug
from apps.tenancy.models import Tenant, TenantStaff


class Command(BaseCommand):
    help = (
        "Напечатать слаги тенантов, чьё соло-происхождение доказано пересчётом слага "
        "по личности владельца. Только чтение; список годится для каталожной "
        "backfill_solo_tenant_kind."
    )

    def _owner_identity(self, tenant: Tenant) -> tuple[str, str] | None:
        """Личность владельца тенанта — (канал, id в канале), если он один.

        ``all_tenants``: команда ходит по всем тенантам сразу и вне
        арендаторского контекста, а ``objects`` у ``TenantStaff`` —
        ``TenantScopedManager`` и отдал бы пусто.
        """
        owners = list(
            TenantStaff.all_tenants.filter(
                tenant=tenant, role=TenantStaff.Role.OWNER
            ).select_related("bot_user")[:2]
        )
        if len(owners) != 1:
            return None
        bot_user = owners[0].bot_user
        return bot_user.channel, bot_user.channel_user_id

    def handle(self, *args, **options) -> None:
        proven: list[str] = []
        not_solo = 0
        inactive_proven = 0
        ownerless = 0

        for tenant in Tenant.all_objects.all().order_by("slug"):
            identity = self._owner_identity(tenant)
            if identity is None:
                ownerless += 1
                continue
            if _solo_tenant_slug(*identity) != tenant.slug:
                # Пересчёт не сошёлся: либо салон, либо слаг соло-вида,
                # заведённый не provisioning-ом этой личности.
                not_solo += 1
                continue
            if not tenant.is_active:
                inactive_proven += 1
                continue
            proven.append(tenant.slug)

        for slug in proven:
            self.stdout.write(slug)

        self.stderr.write("Список для каталога: backfill_solo_tenant_kind --from-file")
        self.stderr.write(f"доказано пересчётом: {len(proven)}")
        self.stderr.write(f"пересчёт не сошёлся (не соло): {not_solo}")
        self.stderr.write(f"неактивные, не в списке: {inactive_proven}")
        self.stderr.write(f"без владельца: {ownerless}")
