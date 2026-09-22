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

Команда берёт личности владельцев тенанта (``TenantStaff`` с ролью owner →
``BotUser.channel`` + ``channel_user_id``), считает ``_solo_tenant_slug``
заново и печатает слаг, ТОЛЬКО если он сошёлся с настоящим слагом тенанта.
Отбор по префиксу ``solo-`` был бы ровно тем «именем производной», против
которого предостерегает G4: слаг с виду соло-шный мог завести кто угодно, а
сошедшийся пересчёт значит, что строку завёл provisioning по этой личности.

Связь бота и каталога здесь — СЛАГ, не UUID: до G4 каталог заводил строку
через ``ensure_tenant(slug=…)`` со своим ключом.

# Почему владельцы берутся ВСЕ, а не один действующий

Вопрос команды — происхождение, а не нынешние полномочия. ``deactivated_at``
у ``TenantStaff`` — мягкая деактивация, и ограничение
``unique_active_owner_per_tenant`` обещает одного ДЕЙСТВУЮЩЕГО владельца, а
не одну строку: передача владения снимает старую строку и заводит новую.
Требуй команда ровно одной строки — тенант, у которого владельца однажды
передали, молча выпал бы из списка и был бы посчитан «без владельца», то
есть назван неправдой. Поэтому доказательством считается совпадение с
ЛЮБОЙ из личностей владельцев, действующей или снятой: слаг посчитан один
раз при заведении, и снятие роли этого не отменяет.

Тенант у ``BotUser`` не сверяется намеренно: пересчёт идёт от пары
(канал, id в канале) — это один и тот же человек, в какой бы строке он ни
числился, и сверка добавила бы только промахи.

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
  владельца. Названы числом; на бою это число никогда не ноль — служебный
  тенант бота (миграция ``0014_seed_global_bot_tenant``) владельца не имеет.
* **Людей.** В stdout только слаги тенантов, в stderr — заголовок и числа:
  ни имён, ни логинов, ни идентификаторов личности.
"""

from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand

from apps.identity.services.solo_onboarding import _solo_tenant_slug
from apps.tenancy.models import Tenant, TenantStaff

#: Вид слага соло-тенанта. Используется ТОЛЬКО в диагностической строке
#: отчёта и никогда как признак отбора: префикс ничего не доказывает.
SOLO_SHAPE = "solo-"


class Command(BaseCommand):
    help = (
        "Напечатать слаги тенантов, чьё соло-происхождение доказано пересчётом слага "
        "по личности владельца. Только чтение; список годится для каталожной "
        "backfill_solo_tenant_kind."
    )

    def _plain(self, line: str) -> None:
        """Отчёт в stderr без стиля ошибки: удачный прогон не красный."""
        self.stderr.write(line, lambda text: text)

    def _owner_identities(self) -> dict[object, set[tuple[str, str]]]:
        """Личности владельцев по тенантам — одним запросом, без N+1.

        ``all_tenants``: команда ходит по всем тенантам сразу и вне
        арендаторского контекста, а ``objects`` у ``TenantStaff`` —
        ``TenantScopedManager`` и отдал бы пусто. ``select_related``
        достаёт ``BotUser`` тем же запросом и минует его собственный
        арендаторский менеджер.
        """
        identities: dict[object, set[tuple[str, str]]] = defaultdict(set)
        rows = TenantStaff.all_tenants.filter(role=TenantStaff.Role.OWNER).select_related(
            "bot_user"
        )
        for staff in rows.iterator():
            bot_user = staff.bot_user
            identities[staff.tenant_id].add((bot_user.channel, bot_user.channel_user_id))
        return identities

    def handle(self, *args, **options) -> None:
        owners = self._owner_identities()

        proven: list[str] = []
        not_solo = 0
        solo_shaped_refused = 0
        inactive_proven = 0
        ownerless = 0

        for tenant in Tenant.all_objects.all().order_by("slug").iterator():
            identities = owners.get(tenant.id)
            if not identities:
                ownerless += 1
                continue
            if not any(_solo_tenant_slug(*identity) == tenant.slug for identity in identities):
                # Пересчёт не сошёлся: либо салон, либо слаг соло-вида,
                # заведённый не provisioning-ом этих личностей.
                not_solo += 1
                if tenant.slug.startswith(SOLO_SHAPE):
                    solo_shaped_refused += 1
                continue
            if not tenant.is_active:
                inactive_proven += 1
                continue
            proven.append(tenant.slug)

        for slug in proven:
            self.stdout.write(slug)

        self._plain("Список для каталога: backfill_solo_tenant_kind --from-file")
        self._plain(f"доказано пересчётом: {len(proven)}")
        self._plain(f"пересчёт не сошёлся (не соло): {not_solo}")
        # Диагностика, а не критерий: здоровый прогон даёт ноль. Число выше
        # нуля значит, что пересчёт отказал слагам соло-вида — так системная
        # поломка пересчёта (переименованное поле, изменённый хеш) видна
        # оператору, а не прячется за правдоподобным «не соло».
        self._plain(f"  из них слаги соло-вида (ожидается 0): {solo_shaped_refused}")
        self._plain(f"неактивные доказанные, не в списке: {inactive_proven}")
        self._plain(f"без владельца: {ownerless}")
