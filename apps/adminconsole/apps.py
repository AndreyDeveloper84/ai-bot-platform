"""AppConfig админки бота (DRF-1495).

До этой задачи — пустой каркас, зарезервированный под «Django admin
chrome» (``config/settings/base.py``). Теперь здесь живёт фундамент
доступа: роли, жизненный цикл учётных записей и журнал действий.
"""

from __future__ import annotations

from django.apps import AppConfig


class AdminconsoleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.adminconsole"
    verbose_name = "Админка бота"

    def ready(self) -> None:
        """Подписать журнал на действия админки.

        Подписка на ``LogEntry``, а не на отдельные ``ModelAdmin``: любой
        экран, заведённый подзадачами 2-6 эпика, попадёт в журнал сам.
        ``dispatch_uid`` держит подписку единственной, если ``ready()``
        позовут дважды (перезагрузка реестра приложений в тестах).
        """
        from django.contrib.admin.models import LogEntry
        from django.db.models.signals import post_save

        from apps.adminconsole.journal import record_admin_action

        post_save.connect(
            record_admin_action,
            sender=LogEntry,
            dispatch_uid="apps.adminconsole.journal.record_admin_action",
        )
