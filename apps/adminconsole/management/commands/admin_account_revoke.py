"""Отозвать доступ к админке (DRF-1495).

    python manage.py admin_account_revoke --username i.petrova

Гасит вход (``is_active=False``, ``is_staff=False``), снимает роли и
удаляет живые сессии этого человека — без последнего отзыв не отзывает:
у уже вошедшего в куке лежит валидный ключ, и он работал бы до
истечения срока.

Строку пользователя не удаляет: журнал ссылается на автора, и удаление
превратило бы прошлые записи в «кто-то».
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.adminconsole.accounts import AccountError, revoke_admin_account


class Command(BaseCommand):
    help = "Отозвать доступ к админке бота у учётной записи."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--username", required=True)
        parser.add_argument(
            "--actor",
            default="",
            help="Кто отзывает. Попадает в журнал.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            flushed = revoke_admin_account(
                username=options["username"],
                actor_username=options["actor"],
            )
        except AccountError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Доступ {options['username']!r} отозван; "
                f"погашено сессий: {flushed}. Строка пользователя оставлена "
                "намеренно — на неё ссылается журнал."
            )
        )
