"""Admin REST view — find a returning customer (UX contract §13).

``GET /api/v1/admin/customers/?q=…``

Thin shell over :func:`apps.admin_api.services.booking.search_customers_as`
(DRF-2154: the master surface searches through the same code under its own
identity). Ayla owns the customer record; nothing here reads or caches one.

### Why an unreachable search is never an empty list

§13: «A failed search is not proof that the customer does not exist.»
The two answers look identical on screen and mean opposite things — one
says «book them as a new guest», the other says «try again in a moment».
Collapsing them produces a duplicate customer record for somebody who is
already in the salon's book, and duplicates are the one thing a front
desk cannot easily undo.

So every failure path here answers with a status the client turns into
«поиск недоступен», and `results` is returned only when Ayla actually
answered with a list.

### Why the phone never comes back

Ayla's lookup takes a phone as input and never returns one (DRF-1039,
and the view's own docstring says so). This endpoint does not add one:
the administrator disambiguates by name, having searched by a number
they already had.
"""

from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.admin_api.services.booking import (
    Refusal,
    public_customer_row,
    search_customers_as,
)
from apps.identity.models import BotUser
from apps.integrations.ayla.user_proxy import external_user_id_for

logger = logging.getLogger(__name__)

MAX_QUERY_LEN = 100

#: The registry (DRF-2094) cites the row reducer by this name.
_public = public_customer_row


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


@require_http_methods(["GET"])
@require_admin_role
def search_customers(request: HttpRequest) -> HttpResponse:
    """Search this salon's customers on behalf of the calling administrator."""

    tenant = request.tenant  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    query = (request.GET.get("q") or "").strip()
    if len(query) > MAX_QUERY_LEN:
        return _error("bad_request", "query too long", 400)

    actor = external_user_id_for(bot_user)
    rows = search_customers_as(
        actor=actor,
        tenant=tenant,
        query=query,
        log="admin_api.search_customers",
        journal=logger,
    )
    if isinstance(rows, Refusal):
        return _error(rows.slug, rows.detail, rows.status)

    results = [_public(row) for row in rows]
    logger.info(
        "admin_api.search_customers tenant=%s actor=%s hits=%d",
        tenant.id,
        actor,
        len(results),
    )
    return JsonResponse({"results": results})


__all__ = ["search_customers"]
