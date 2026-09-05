"""Привести роли админки в соответствие с тем, что зарегистрировано (DRF-1495).

Идемпотентна: гоняется на каждый деплой без вреда. Права выставляются
через ``set()``, поэтому модель, снятая с регистрации, теряет права у
обеих ролей на следующем прогоне.

Учётных записей не создаёт и не трогает — только две группы.

    python manage.py sync_admin_roles
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.adminconsole.roles import sync_admin_roles


class Command(BaseCommand):
    help = "Создать/обновить группы ролей админки (ayla-viewer, ayla-editor)."

    def handle(self, *args: Any, **options: Any) -> None:
        granted = sync_admin_roles()
        for group_name, count in sorted(granted.items()):
            self.stdout.write(self.style.SUCCESS(f"{group_name}: {count} прав"))
