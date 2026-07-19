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

### The specialist-id seam (resolved by AMD-005)

C2/C3 key on the Ayla **User UUID** (AMD-005 — ``specialist_id`` in the
contracts is the User id, NOT ``SpecialistProfile.id``; resolution into
the profile happens inside W1/W2). The mirror carries exactly that id
as ``CatalogMaster.ayla_user_id``, so
:func:`specialist_id_for_master` is a plain field read. Masters without
an Ayla link yet get a fail-closed **503
``specialist_mapping_unavailable``** — never a guessed id.
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
    """Resolve the master key for Ayla billing/payout calls (AMD-005).

    C2/C3/C4 key on the Ayla **User UUID**, which the mirror already
    carries as ``CatalogMaster.ayla_user_id`` — no specialist-enrichment
    sync needed (the variant rejected by the orchestrator in AMD-005).
    Returns ``None`` for an unlinked master (no Ayla id yet) — callers
    fail closed (503) rather than guess."""  # noqa: E501
    if master.ayla_user_id:
        return str(master.ayla_user_id)
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
