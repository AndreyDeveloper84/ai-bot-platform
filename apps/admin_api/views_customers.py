"""Admin REST view — find a returning customer (UX contract §13).

``GET /api/v1/admin/customers/?q=…``

Thin shell over :meth:`AylaSalonClient.search_customers`. Ayla owns the
customer record; nothing here reads or caches one.

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
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.identity.models import BotUser
from apps.integrations.ayla.user_proxy import external_user_id_for

logger = logging.getLogger(__name__)

MAX_QUERY_LEN = 100


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

    from apps.integrations.ayla.salon_client import (
        SalonAPIError,
        SalonForbidden,
        SalonNotConfigured,
        SalonNotFound,
        SalonUnauthorized,
        SalonUnavailable,
        SalonValidationError,
        get_salon_client,
    )

    actor = external_user_id_for(bot_user)

    try:
        rows = get_salon_client().search_customers(
            actor_external_id=actor,
            tenant_slug=tenant.slug,
            query=query,
        )
    except SalonValidationError as exc:
        # Includes the two-character floor, which the client checks
        # locally. A 400 here is about the query, not about the salon.
        return _error("bad_request", str(exc), 400)
    except SalonNotConfigured as exc:
        logger.error("admin_api.search_customers.not_configured err=%s", exc)
        return _error("unavailable", "customer search is not configured", 503)
    except SalonUnauthorized as exc:
        # Our credential, not this person's rights (DRF-1231 until it
        # ships). Loud in the log, neutral on screen.
        logger.error(
            "admin_api.search_customers.upstream_unauthorized tenant=%s err=%s",
            tenant.id,
            exc,
        )
        return _error("unavailable", "customer search is unavailable", 503)
    except SalonForbidden as exc:
        logger.warning(
            "admin_api.search_customers.forbidden actor=%s tenant=%s err=%s",
            actor,
            tenant.id,
            exc,
        )
        return _error("forbidden", "not permitted in this salon", 403)
    except (SalonNotFound, SalonUnavailable, SalonAPIError) as exc:
        # Grouped on purpose: from the front desk's point of view these
        # are one situation — «we could not ask» — and none of them is
        # «this customer does not exist».
        logger.warning("admin_api.search_customers.unavailable err=%s", exc)
        return _error("unavailable", "customer search is unavailable", 503)

    results = [_public(row) for row in rows]
    logger.info(
        "admin_api.search_customers tenant=%s actor=%s hits=%d",
        tenant.id,
        actor,
        len(results),
    )
    return JsonResponse({"results": results})


def _public(row: dict[str, Any]) -> dict[str, Any]:
    """Reduce one upstream row to what the picker may show.

    Two upstream shapes need handling rather than passing through, both
    read out of Ayla's ``_client_name`` (2026-08-21):

    * **an empty name** — deliberate upstream («inventing "Client #4"
      would make the journal look more certain than it is»), but a blank
      row in a picker is unusable, so it becomes a neutral placeholder;
    * **a username as the name** — for accounts that arrived through the
      bot, ``_client_name`` falls back to ``username``, which for those
      is the channel handle ``bot:max:83146139``. That is an internal
      identifier, not a name, and showing it to a receptionist is a leak
      of plumbing into an operator surface.

    Both are reported upstream as follow-ups; this keeps the screen
    honest meanwhile. Neither is silently hidden — an unnamed customer
    reads as unnamed, which is the truth.
    """

    name = str(row.get("name") or "").strip()
    if ":" in name and name.split(":", 1)[0].isalnum() and " " not in name:
        # Channel handle shape (`bot:max:123`), never a human name.
        name = ""
    return {
        "id": str(row.get("id") or ""),
        "name": name or "Без имени",
        # Lets the picker mark a placeholder as one instead of letting it
        # pass for a customer actually called «Без имени».
        "named": bool(name),
    }


__all__ = ["search_customers"]
