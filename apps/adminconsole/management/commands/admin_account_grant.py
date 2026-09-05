"""Завести учётную запись админки в роли (DRF-1495).

    python manage.py admin_account_grant --username i.petrova --role viewer

Пароль аргументом не принимается — он виден в ``ps`` и остаётся в
истории оболочки. Команда читает ``AYLA_ADMIN_PASSWORD``; без переменной
запись заводится с непригодным паролем, и владелец задаёт его через
``manage.py changepassword``.

Суперпользователя не выдаёт никогда: ``createsuperuser`` остаётся
отдельной, осознанной операцией владельца.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.adminconsole.accounts import PASSWORD_ENV_VAR, AccountError, grant_admin_account
from apps.adminconsole.roles import ROLE_GROUPS


class Command(BaseCommand):
    help = "Завести (или перевести в роль) учётную запись админки бота."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--username", required=True, help="Логин человека, не роли.")
        parser.add_argument(
            "--role",
            required=True,
            choices=sorted(ROLE_GROUPS),
            help="viewer — смотрящий; editor — правящий.",
        )
        parser.add_argument("--email", default="", help="Необязательно.")
        parser.add_argument(
            "--actor",
            default="",
            help="Кто выдаёт. Попадает в журнал; команду обычно запускает владелец.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            user, created = grant_admin_account(
                username=options["username"],
                role=options["role"],
                email=options["email"],
                actor_username=options["actor"],
            )
        except AccountError as exc:
            raise CommandError(str(exc)) from exc

        verb = "заведена" if created else "обновлена"
        self.stdout.write(
            self.style.SUCCESS(
                f"Учётная запись {user.get_username()!r} {verb}, роль: {options['role']}."
            )
        )
        if not user.has_usable_password():
            self.stdout.write(
                self.style.WARNING(
                    "Пароль не задан — войти нельзя. Задайте его командой "
                    f"`manage.py changepassword {user.get_username()}` "
                    f"или перезапустите с {PASSWORD_ENV_VAR} в окружении."
                )
            )
