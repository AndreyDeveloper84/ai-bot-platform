"""GET /api/v1/admin/staff/ — who works at this salon, and what are they.

### Why this exists

Access could be granted (``staff/invite/``) and taken back
(``staff/revoke/``) before anything could *show* who held it. The team
screen lists masters; nothing anywhere listed administrators, and so the
question «who can get into my salon's admin panel» had no answer short of
a psql session. The owner asked for the list, and asked for the list
only — no role changes, no revoke button, no bulk actions.

### Why the owner and not every admin

``require_admin_role`` admits owner and admin; this view narrows to owner,
the same way ``masters/<id>/deactivate/`` does.

The owner ruled that *changing* roles is owner-only. Reading the full map
of who holds which administrative role is the reconnaissance half of that
same act: it is the one place that says how many administrators exist,
who they are, and which of them arrived by a code somebody could ask for
again. Nobody asked for that to be wider than the decision it supports,
and a view is far cheaper to widen later than to narrow after a salon has
seen it.

Nothing is lost to the people not admitted. Masters already appear on the
Команда screen, which stays owner+admin; what this view adds on top is
exactly the administrative half the owner reserved.

### Why the merge is not visible in the response shape

One row per person, with a list of roles — never one row per role. The
alternative reads fine until the pilot's own shape arrives: the owner who
is also a master would occupy two rows, and the reader would have to know
that two rows can be one human. See ``services/staff_roster.py`` for the
identity rule and for why it is deliberately identical to the one
``is_solo_provider`` uses.

### No phone number

``DRF-1039`` forbids handing a client's phone to an executor. It does not
speak about staff, and no other owner decision does. This view therefore
does not show one, and the service beneath it never loads the column, so
the absence is a property of the query rather than of the serialiser.
"""

from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.admin_api.services.staff_roster import build_staff_roster

logger = logging.getLogger(__name__)


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


@require_http_methods(["GET"])
@require_admin_role
def staff_roster(request: HttpRequest) -> HttpResponse:
    """List every person who holds — or held — a role at this salon.

    Returns 200 with::

        {
          "items": [{
            "id":           "bot:<uuid>" | "master:<uuid>",
            "bot_user_id":  <uuid> | null,
            "master_id":    <uuid> | null,
            "name":         str,
            "has_account":  bool,     # a MAX account is attached
            "is_active":    bool,     # at least one live role
            "roles": [{
              "role":   "owner" | "admin" | "receptionist" | "master",
              "active": bool,         # per role, not per person
              "source": "access_code" | "master_invite" | "direct",
              "since":  <iso8601> | null
            }]
          }],
          "total_count": int,
          "truncated":   bool
        }

    403 ``forbidden`` for an admin, receptionist, master or customer.
    """

    role_ctx = request.role_context  # type: ignore[attr-defined]
    tenant = request.tenant  # type: ignore[attr-defined]

    if not role_ctx.is_owner:
        return _error(
            "forbidden",
            "only the salon owner can see the list of people and their roles",
            403,
        )

    people, total, truncated = build_staff_roster(tenant)

    return JsonResponse(
        {
            "items": [p.to_payload() for p in people],
            "total_count": total,
            "truncated": truncated,
        }
    )
