"""Master billing status (C2) + payout preview (C3) proxy services.

Pilot 2026-08-15, frozen contracts ``PILOT_CONTRACTS_2026-08-15.md``
§3/§4. The master Mini App (W4) never calls Ayla directly — it reads
these two bot-side proxies, which forward to Ayla's internal endpoints
via :class:`apps.integrations.ayla.billing_client.AylaBillingClient`
(Bearer ``AYLA_INTERNAL_API_TOKEN``).

### Pass-through, no invention

The ``data`` payload is returned **verbatim** — the contracts freeze the
field set (money as Decimal strings with exactly two places, RUB, ISO
dates), and W4 consumes only the approved schema. This layer adds
nothing and reshapes nothing; additive upstream fields ride through.

### The specialist-id seam (OPEN CONTRACT GAP)

C2/C3 key on Ayla ``SpecialistProfile.id``. The bot's
:class:`~apps.catalog.models.CatalogMaster` mirror keys masters on the
Ayla **User** UUID (``ayla_user_id``) — a *different* id
(``SpecialistProfile.id`` is its own uuid4, verified against
``beautygo_backend/users/models.py`` 2026-07-18). The bot stores no
SpecialistProfile id today, so :func:`specialist_id_for_master` returns
``None`` and the endpoints answer **503 ``specialist_mapping_unavailable``**
(fail-closed, observable) instead of guessing a wrong id. Escalated to
the orchestrator; closes when W1's specialist-enrichment sync lands the
mapping on the mirror (or the contract is amended to key on user id).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from apps.catalog.models import CatalogMaster
from apps.integrations.ayla.billing_client import (
    AylaBillingClient,
    BillingAuthError,
    BillingNotFoundError,
    BillingProxyError,
    BillingTransportError,
)

logger = logging.getLogger(__name__)


class ProxyStatus(str, Enum):
    OK = "ok"
    MAPPING_UNAVAILABLE = "mapping_unavailable"  # no specialist_id on the mirror
    NOT_FOUND = "not_found"  # upstream 404 (SPECIALIST_NOT_FOUND)
    UPSTREAM_ERROR = "upstream_error"  # 5xx / network / auth misconfig


@dataclass(frozen=True)
class BillingProxyResult:
    """Outcome of one proxy call. ``payload`` is the verbatim contract
    ``data`` on OK, empty otherwise."""

    status: ProxyStatus
    payload: dict[str, Any] = field(default_factory=dict)


def specialist_id_for_master(master: CatalogMaster) -> str | None:
    """Resolve the Ayla ``SpecialistProfile.id`` for a session master.

    Returns ``None`` today — see the module docstring (open contract
    gap). The mirror field that will carry it lands with W1's
    specialist-enrichment sync; this is the ONE function to update then.
    """
    return None


def billing_status_for_master(
    master: CatalogMaster,
    *,
    client: AylaBillingClient | None = None,
) -> BillingProxyResult:
    """C2: subscription status + fees + last invoice for the master."""
    return _proxy(
        master,
        client=client,
        call="get_billing_status",
        log_slug="billing_status",
    )


def payout_preview_for_master(
    master: CatalogMaster,
    *,
    client: AylaBillingClient | None = None,
) -> BillingProxyResult:
    """C3: pending payout amount + per-appointment breakdown."""
    return _proxy(
        master,
        client=client,
        call="get_payout_preview",
        log_slug="payout_preview",
    )


def _proxy(
    master: CatalogMaster,
    *,
    client: AylaBillingClient | None,
    call: str,
    log_slug: str,
) -> BillingProxyResult:
    specialist_id = specialist_id_for_master(master)
    if specialist_id is None:
        logger.warning(
            "master_api.billing.%s.mapping_unavailable master_id=%s tenant_id=%s",
            log_slug,
            master.pk,
            master.tenant_id,
        )
        return BillingProxyResult(status=ProxyStatus.MAPPING_UNAVAILABLE)

    owns_client = client is None
    client = client or AylaBillingClient()
    try:
        payload = getattr(client, call)(specialist_id=specialist_id)
        return BillingProxyResult(status=ProxyStatus.OK, payload=payload)
    except BillingNotFoundError:
        logger.warning(
            "master_api.billing.%s.specialist_not_found master_id=%s specialist_id=%s",
            log_slug,
            master.pk,
            specialist_id,
        )
        return BillingProxyResult(status=ProxyStatus.NOT_FOUND)
    except BillingAuthError:
        # Misconfig on OUR side — loud, ops-visible, no payload detail.
        logger.exception("master_api.billing.%s.auth_error", log_slug)
        return BillingProxyResult(status=ProxyStatus.UPSTREAM_ERROR)
    except (BillingTransportError, BillingProxyError):
        logger.exception("master_api.billing.%s.upstream_error", log_slug)
        return BillingProxyResult(status=ProxyStatus.UPSTREAM_ERROR)
    finally:
        if owns_client:
            client.close()
