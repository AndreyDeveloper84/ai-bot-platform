"""Соло-мастер в салонном боте видит мастерскую витрину (§69, вариант «в»).

Решение владельца 08.09.2026 (``docs/OPEN_DECISIONS.md`` §69): соло-мастер
— одновременно мастер и владелица своего кабинета; он видит **мастерское
меню**, а владельческое открывается **одной кнопкой «Салон»**. Отклонены
объединённое меню и **меню по старшей роли** — а именно его бот и строил:
``RoleContext`` признака «соло» не несёт, и владелица в правах побеждала
мастера (ручной проход на стенде 13.11.2026, DRF-1503: после регистрации —
«Салон «Студия …».», «Сегодня», «Заявки от мастеров», «Записать клиента»).

Кто «соло» для витрины:

* владелица и мастер одновременно (дешёвый отсев: остальной персонал не
  платит ни одного запроса);
* кабинет он завёл сам — ``SoloIdentityLink`` у его карточки
  (:func:`solo_link_retry.solo_workspace_link`): её заводит только
  соло-регистрация, у владелицы-мастера обычного салона её нет;
* и в кабинете он один (:func:`solo_onboarding.is_solo_provider` — его же
  Mini App берёт для выбора поверхности соло; бот вдобавок требует связь
  кабинета, поэтому владелица салона из одного человека, кабинет которой
  заведён не соло-регистрацией, в Mini App видит соло, а в боте — меню
  владелицы). Появилась команда — это уже салон, и витрина снова
  владельческая. Названо: уже одно приглашение второму человеку, даже не
  принятое, считается командой — так же и в Mini App.

Права не меняются: соло по-прежнему владелица (запись клиента в чате,
«Визит состоялся?» в дне салона, готовность). Меняется только витрина.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def is_solo_master(role_ctx: Any) -> bool:
    """Показывать ли этому человеку мастерскую витрину соло (§69, «в»).

    ``role_ctx`` — ``RoleContext`` из ``resolve_role``; читается через
    ``getattr``: заглушки ролей в тестах не несут ``master_id``/``tenant_id``,
    и для них ответ — «нет», то есть прежнее поведение. Запросы — только у
    владелицы-мастера (связь кабинета по ``master_id``, затем «один человек»
    по тенанту). Сбой чтения — «нет»: витрина откатывается к меню владелицы,
    а не роняет ответ бота (ключ идемпотентности хода к этому моменту занят,
    и упавший ответ — это тишина).
    """

    if not (getattr(role_ctx, "is_owner", False) and getattr(role_ctx, "is_master", False)):
        return False
    master_id = getattr(role_ctx, "master_id", None)
    tenant_id = getattr(role_ctx, "tenant_id", None)
    if not master_id or not tenant_id:
        return False
    try:
        from apps.identity.models import SoloIdentityLink
        from apps.identity.services.solo_onboarding import is_solo_provider
        from apps.tenancy.models import Tenant

        if not SoloIdentityLink.objects.filter(master_id=master_id).exists():
            return False
        tenant = Tenant.all_objects.filter(pk=tenant_id).first()
        return tenant is not None and is_solo_provider(tenant)
    except Exception:  # noqa: BLE001 — витрина не должна ронять ответ бота
        logger.exception("identity.solo_view.unknown")
        return False
