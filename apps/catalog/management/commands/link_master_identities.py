"""Дозвонить связь личности мастерам, которые приняли приглашение ДО DRF-2442.

Приём приглашения теперь сам говорит каталогу «эта личность — тот мастер»
(``master_api.views._link_identity_in_catalog``). Но мастера, принявшие
приглашение раньше, остались без связи: у их прокси-строки в каталоге пуст
``linked_user_id``, и кабинет отвечает 403 ``subject_unresolved``. Замер 24.09 —
четыре мастера ``formula-tela``. Перевыпускать приглашения не нужно: связь
ставится по паре, которая уже лежит в зеркале (``linked_bot_user`` +
``catalog_specialist_id``), и та же дверь идемпотентна.

# Что печатает — три числа и один вердикт

``pending`` — сколько строк подлежит связи (принятые, с личностью и с
``catalog_specialist_id``); ``linked`` — сколько связано этим прогоном
(включая ``replayed``: для каталога связь уже есть, значит цель достигнута);
``left`` — сколько осталось несвязанными, с разбивкой по причинам.

Вердикт один, и ноль без непустого охвата чистотой не считается:

* ``nothing_to_link`` — подлежащих строк нет. Ноль верен и **ничего не
  доказывает**: возможно, ни один мастер ещё не принял приглашение;
* ``linked_all`` — все подлежащие связаны;
* ``partial`` — часть осталась, причины названы;
* ``dry_run`` — сухой прогон: ничего не звонилось, напечатан только охват.

# Границы

По умолчанию — **сухой прогон**: считает и печатает, каталог не зовёт.
``--apply`` делает вызовы. Ничего не удаляет и не перепривязывает: дверь
каталога сама откажет ``identity_already_bound``, если личность уже связана с
другой учёткой, и это попадёт в ``left`` причиной, а не «починится».

Повтор безопасен: ключ идемпотентности детерминирован по паре
(профиль, личность), поэтому второй прогон не создаёт вторых связей — у двери
это узел, и у команды он свой (она ходит пачкой).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from django.core.management.base import BaseCommand

VERDICT_DRY_RUN = "dry_run"
VERDICT_NOTHING = "nothing_to_link"
VERDICT_ALL = "linked_all"
VERDICT_PARTIAL = "partial"


def _pending_rows() -> list[Any]:
    """Строки, которым связь нужна и возможна — принятые, с личностью и профилем.

    Проход по салонам под ``tenant_scope``, а не ``all_tenants``: сквозное
    чтение каталога зарезервировано за ``apps/marketplace/`` (страж
    ``import_boundaries``, MKT1), и подметальщик той же работы
    (``apps.catalog.tasks.link_unlinked_salon_masters``) ходит так же.
    """

    from apps.catalog.master_state import ACCEPTED
    from apps.catalog.models import CatalogMaster
    from apps.catalog.tasks import tenants_in_sync_order
    from apps.tenancy.context import tenant_scope

    rows: list[Any] = []
    for tenant in tenants_in_sync_order():
        with tenant_scope(tenant):
            rows.extend(
                CatalogMaster.objects.filter(
                    invite_status=ACCEPTED,
                    archived_at__isnull=True,
                    linked_bot_user__isnull=False,
                )
                # ``catalog_specialist_id`` — UUIDField: пусто здесь значит NULL,
                # и сравнение с "" падало бы разбором UUID.
                .exclude(catalog_specialist_id__isnull=True)
                .select_related("linked_bot_user")
                .order_by("id")
            )
    return rows


class Command(BaseCommand):
    help = (
        "Дозвонить связь личности мастерам, принявшим приглашение до DRF-2442 "
        "(сухой прогон по умолчанию; --apply зовёт каталог)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Звать дверь каталога. Без него: только охват и вердикт.",
        )

    def handle(self, *args, **options) -> None:
        from apps.identity.services.specialist_identity_link import (
            ACTOR_BACKFILL,
            SpecialistIdentityLinkRefused,
            bind_master_identity_in_catalog,
        )

        rows = _pending_rows()
        pending = len(rows)

        if not options["apply"]:
            verdict = VERDICT_NOTHING if pending == 0 else VERDICT_DRY_RUN
            self.stdout.write(
                f"DRY-RUN pending={pending} linked=0 left={pending} verdict={verdict} "
                "— каталог не вызывался"
            )
            return

        linked = 0
        reasons: Counter[str] = Counter()
        for row in rows:
            try:
                bind_master_identity_in_catalog(
                    specialist_id=row.catalog_specialist_id,
                    bot_user=row.linked_bot_user,
                    actor_label=ACTOR_BACKFILL,
                )
            except SpecialistIdentityLinkRefused as exc:
                reasons[exc.reason] += 1
                continue
            # ``replayed`` тоже считается связанным: для каталога связь есть,
            # а цель команды — состояние, не количество новых записей.
            linked += 1

        left = pending - linked
        if pending == 0:
            verdict = VERDICT_NOTHING
        elif left == 0:
            verdict = VERDICT_ALL
        else:
            verdict = VERDICT_PARTIAL
        detail = ""
        if reasons:
            detail = " reasons=" + ",".join(
                f"{reason}={count}" for reason, count in sorted(reasons.items())
            )
        self.stdout.write(
            f"APPLIED pending={pending} linked={linked} left={left} verdict={verdict}{detail}"
        )
