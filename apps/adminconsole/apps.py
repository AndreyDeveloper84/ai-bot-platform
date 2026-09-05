"""AppConfig админки бота (DRF-1495).

До этой задачи — пустой каркас, зарезервированный под «Django admin
chrome» (``config/settings/base.py``). Теперь здесь живёт фундамент
доступа: роли, жизненный цикл учётных записей, журнал действий,
политика сокрытия полей-секретов и — с DRF-1514 — область видимости
данных клиента вместе с журналом доступа к ним.
"""

from __future__ import annotations

from django.apps import AppConfig


class AdminconsoleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.adminconsole"
    verbose_name = "Админка бота"

    def ready(self) -> None:
        """Поставить журнал и политику секретов на уже собранную админку.

        Порядок безопасен: ``django.contrib.admin`` стоит первым в
        ``INSTALLED_APPS``, его ``ready()`` выполняет ``autodiscover()``,
        так что к нашему ``ready()`` все ``apps/*/admin.py`` уже
        импортированы и ``admin.site`` заполнен.

        Все три установки идемпотентны — повторный ``ready()``
        (перезагрузка реестра приложений в тестах) ничего не удваивает.
        """
        from apps.adminconsole.client_scope import install_client_data_scope
        from apps.adminconsole.journal import install_admin_journal
        from apps.adminconsole.secrets_policy import install_secret_field_policy

        install_admin_journal()
        install_secret_field_policy()
        install_client_data_scope()
