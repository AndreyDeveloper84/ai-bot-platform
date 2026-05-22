"""Re-export master_api fixtures into the notifications test scope.

Pytest only collects fixtures from ``conftest.py`` files in the
ancestor chain of the test file. The M7 notification-prefs tests live
under ``apps/notifications/tests/`` but the BotUser / Tenant /
CatalogMaster fixtures they need are defined under
``apps/master_api/tests/conftest.py``. Re-exporting via ``from … import
*`` here keeps the fixture authoring single-sourced.
"""

from apps.master_api.tests.conftest import (  # noqa: F401
    _bot_token,
    accepted_master,
    bot_user,
    other_bot_user,
    other_tenant,
    pending_master,
    tenant,
)
