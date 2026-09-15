"""``manage.py ayla_erasure_requeue <job_id> [--apply]`` — вернуть задание удаления в Ayla в очередь.

DRF-1950 (решение В3). Алерт об исчерпании повторов удаления в Ayla называет
эту команду оператору. По умолчанию — сухой прогон: печатает, какие задания
были бы затронуты, ничего не меняя. С ``--apply`` — исчерпанное (``failed``) или
ожидающее (``pending``) задание получает ``attempts = 0``, ``next_attempt_at =
сейчас`` и снятую отметку алерта; ближайший проход подметальщика повторит
DELETE и readback. Закрытые задания (``completed``,
``superseded_by_account_deletion``) не трогаются — это ошибка команды.
Состояния «закрыто вручную» у задания нет: в очередь возвращается только
``failed`` / ``pending``.

Печатает число затронутых заданий, их номера, состояние, источник, попытки и
класс причины — без внешних идентификаторов и без субъекта Ayla.

На пилоте ``--apply`` выполняет главное окно по слову владельца.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils import timezone

from apps.audit.services import write_audit

PILOT_NOTE = "на пилоте --apply выполняет главное окно по слову владельца"


class Command(BaseCommand):
    help = "Вернуть задание удаления в Ayla в очередь (сухой прогон без --apply)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("job_id", help="Номер задания AylaErasureJob.")
        parser.add_argument(
            "--apply",
            action="store_true",
            help=f"Записать изменение. Без флага — только показать, что будет сделано ({PILOT_NOTE}).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from apps.identity.models import AylaErasureJob

        try:
            job_id = uuid.UUID(str(options["job_id"]))
        except ValueError as exc:
            raise CommandError("job_id — не номер задания") from exc

        requeueable = {AylaErasureJob.Status.FAILED, AylaErasureJob.Status.PENDING}
        with transaction.atomic():
            job = AylaErasureJob.objects.select_for_update().filter(pk=job_id).first()
            if job is None:
                raise CommandError(f"Задание {job_id} не найдено")
            summary = (
                f"состояние={job.status} источник={job.source} "
                f"попыток={job.attempts} причина={job.last_error_kind or '-'}"
            )
            if job.status not in requeueable:
                raise CommandError(
                    f"Задание {job.pk}: {summary} — закрыто, повторная постановка не нужна"
                )
            if not options["apply"]:
                self.stdout.write(
                    "\n".join(
                        [
                            "сухой прогон: затронуто было бы заданий: 1",
                            str(job.pk),
                            summary,
                            "будет attempts=0, следующая попытка — сейчас.",
                            f"Для записи добавьте --apply ({PILOT_NOTE}).",
                        ]
                    )
                )
                return
            previous = job.status
            job.status = AylaErasureJob.Status.PENDING
            job.attempts = 0
            job.next_attempt_at = timezone.now()
            job.alerted_at = None
            job.save(
                update_fields=["status", "attempts", "next_attempt_at", "alerted_at", "updated_at"]
            )
        write_audit(
            "identity.ayla_erasure.requeued",
            target="AylaErasureJob",
            target_id=job.pk,
            payload={"previous_status": previous, "source": job.source},
        )
        self.stdout.write(
            "\n".join(["затронуто заданий: 1", str(job.pk), summary, "поставлено в очередь."])
        )
