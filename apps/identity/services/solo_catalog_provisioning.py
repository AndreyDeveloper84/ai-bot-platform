"""Каталожный workspace соло-мастера из бота — вызов и запись исхода (DRF-1830, M29).

Решения владельца 12.09 (G1 → б, G4 → а): при создании solo workspace каталог
сразу заводит ``Tenant`` с тем же UUID и ``SpecialistProfile(DRAFT)``; ручка —
``POST /internal/tenants/solo-workspaces/`` (DRF-1828, beautygo_backend #432).

Этот модуль — единственное место в боте, которое её зовёт, и единственное,
которое пишет исход на :class:`~apps.identity.models.SoloIdentityLink`:

* успех → ``catalog_specialist_id`` + ``catalog_provisioned_at`` из **ответа**
  каталога (readback, не «послали»), причина очищена;
* неуспех → ``catalog_provisioning_refusal`` машинным именем, **без исключения
  наружу**: кабинет в боте уже создан, и регистрация не имеет права упасть
  из-за того, что Ayla лежит (правило ``solo_link_attempt``). Имена разные,
  потому что чинятся в разных местах:

  ======================  =====================================================
  ``token_missing``       у бота пуст ``AYLA_TENANT_PROVISIONING_TOKEN`` (A3)
  ``refused``             каталог 403: у него токен пуст или не совпадает
  ``conflict:<reason>``   каталог 409: slug/UUID/claim заняты — оператор
  ``client_error``        прочий 4xx — контракт разошёлся
  ``transport_error``     сеть / 5xx / кривой ответ — повтор
  ======================  =====================================================

Уже подтверждённый workspace повторно не заводится — вызов не делается вовсе.
Повтор неуспешного — из повторной регистрации и из
``solo_identity_link.confirm_by_operator``.
"""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from apps.catalog.services.http_client import (
    CatalogClientError,
    CatalogHttpClient,
    CatalogProvisioningRefused,
    CatalogProvisioningTokenMissing,
    CatalogSoloProvisioningRefused,
    CatalogTransportError,
)
from apps.integrations.ayla.user_proxy import external_user_id_for

logger = logging.getLogger(__name__)


def provision_catalog_workspace(
    link: Any,
    *,
    tenant: Any,
    bot_user: Any,
    display_name: str,
    http_client: Any | None = None,
) -> str | None:
    """Завести каталожный workspace для этого соло-кабинета; вернуть причину отказа или ``None``.

    ``None`` — workspace подтверждён каталогом (сейчас или раньше).
    """

    if link.catalog_provisioned_at is not None:
        return None

    refusal: str | None
    try:
        with http_client if http_client is not None else CatalogHttpClient() as http:
            dto = http.provision_solo_workspace(
                tenant_id=tenant.id,
                slug=tenant.slug,
                name=tenant.name,
                city=getattr(tenant, "city", "") or "",
                external_user_id=external_user_id_for(bot_user),
                display_name=display_name,
            )
    except CatalogProvisioningTokenMissing:
        refusal = "token_missing"
    except CatalogProvisioningRefused:
        refusal = "refused"
    except CatalogSoloProvisioningRefused as exc:
        refusal = f"conflict:{exc.reason}"[:64]
    except CatalogClientError:
        refusal = "client_error"
    except CatalogTransportError:
        refusal = "transport_error"
    else:
        link.catalog_specialist_id = dto.specialist_id
        link.catalog_provisioned_at = timezone.now()
        link.catalog_provisioning_refusal = ""
        link.save(
            update_fields=[
                "catalog_specialist_id",
                "catalog_provisioned_at",
                "catalog_provisioning_refusal",
            ]
        )
        logger.info(
            "identity.solo_catalog.provisioned master=%s tenant=%s specialist=%s created=%s",
            link.master_id,
            tenant.id,
            dto.specialist_id,
            dto.created,
        )
        return None

    link.catalog_provisioning_refusal = refusal
    link.save(update_fields=["catalog_provisioning_refusal"])
    logger.warning(
        "identity.solo_catalog.not_provisioned master=%s tenant=%s refusal=%s",
        link.master_id,
        tenant.id,
        refusal,
    )
    return refusal


__all__ = ["provision_catalog_workspace"]
