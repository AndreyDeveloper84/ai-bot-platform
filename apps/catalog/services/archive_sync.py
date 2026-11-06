"""Архив мастера бота → каталог (DRF-2984, PR-B2b).

Деактивация и возврат мастера в боте (``admin_api.services.master_deactivation``)
до этого модуля в каталог не доходили: архивный мастер оставался в пуле
каталога — продавался по прямой ссылке и фиду и входил бы в счёт подписки
(В-3: считаем только активных). Теперь после коммита бот сообщает каталогу
``PATCH internal/specialists/{id}/archive/`` (ayla-2c, C3).

Свойства:

* **За своим тумблером** ``CATALOG_ARCHIVE_SYNC_ENABLED`` (по умолчанию
  выключен, не ``BILLING_ENABLED``): включение меняет выдачу каталога, это
  решение владельца, а не следствие биллинга (главное окно 21.10).
* **После коммита.** Решение салона уже записано; сбой каталога его не
  откатывает — только журнал ``catalog.archive_sync.failed`` с причиной.
* **Субъект — админ салона, который нажал**, а не сам мастер: у мастера без
  привязки своей личности нет, а слать её за владельца — подмена субъекта.

Чего здесь нет: Django-админка бота (``unarchive_masters`` и др.) каталогу
архив не сообщает — у оператора платформы нет личности админа салона, а
служебного пути без субъекта каталог не открывает. Оператор правит
``SpecialistProfile.is_available`` в админке каталога.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction

from apps.catalog.models import CatalogMaster
from apps.catalog.specialist_ref import CatalogSpecialistUnresolved, catalog_specialist_id
from apps.identity.models import BotUser

logger = logging.getLogger(__name__)


def archive_sync_enabled() -> bool:
    return bool(getattr(settings, "CATALOG_ARCHIVE_SYNC_ENABLED", False))


def sync_master_archived_on_commit(
    master: CatalogMaster, *, archived: bool, actor: BotUser
) -> None:
    """После коммита сообщить каталогу, что ``master`` архивирован / возвращён."""

    if not archive_sync_enabled():
        return
    # Те же объекты, что записала дверь: перечитывать после коммита нечего
    # (и межтенантное чтение all_tenants вне marketplace запрещено, MKT1).
    transaction.on_commit(lambda: _push(master, archived=archived, actor=actor))


def _push(master: CatalogMaster, *, archived: bool, actor: BotUser) -> None:
    from apps.integrations.ayla.booking_client import get_ayla_booking_client
    from apps.integrations.ayla.user_proxy import external_user_id_for

    master_id = master.pk
    try:
        client = get_ayla_booking_client()
        client.set_specialist_archived(
            specialist_id=catalog_specialist_id(master),
            external_user_id=external_user_id_for(actor),
            archived=archived,
        )
    except CatalogSpecialistUnresolved:
        # Мастер без каталожной личности в пуле каталога и не стоит.
        logger.info("catalog.archive_sync.skipped master=%s reason=no_catalog_identity", master_id)
        return
    except Exception as exc:  # noqa: BLE001 — сбой каталога не откатывает решение салона
        logger.warning(
            "catalog.archive_sync.failed master=%s archived=%s error=%s",
            master_id,
            archived,
            type(exc).__name__,
        )
        return
    logger.info("catalog.archive_sync.sent master=%s archived=%s", master_id, archived)


__all__ = ["archive_sync_enabled", "sync_master_archived_on_commit"]
