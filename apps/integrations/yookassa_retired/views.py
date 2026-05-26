"""``410 Gone`` view for retired YooKassa webhook URL — #732.

Single view: any POST to ``/api/v1/yookassa/webhook/`` (the old
bot-platform URL still configured in YooKassa Personal Cabinets that
haven't been flipped to Ayla yet) returns HTTP 410 Gone, writes an
audit row, and logs a WARNING.

### Status-code semantics

``410 Gone`` is the only HTTP code that says «permanently moved, do
not retry» without losing the forensic trail. YooKassa's retry policy
treats all 4xx as «do not retry» (good — avoids retry storms) and
410 is semantically correct for «this endpoint is intentionally
gone». A 404 would convey the same retry behavior but no operational
signal — 410 is louder.

### PII discipline

We capture ONLY:

* ``body_bytes`` — payload size (no parsing of YooKassa fields —
  card-tail digits can appear in failure messages).
* ``remote_ip`` — proxy-aware (X-Forwarded-For chain → REMOTE_ADDR).
* ``user_agent_truncated`` — first 120 chars (UA strings can be
  arbitrarily long).

NEVER write request body content. Forensic question is «who is
still hitting us?», not «what payment failed?» — payment details
live on Ayla now.
"""

from __future__ import annotations

import logging
from typing import Final

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.audit.services import write_audit


logger = logging.getLogger(__name__)


AUDIT_ACTION: Final[str] = "yookassa.webhook_received_after_retirement"

# Truncate UA at this length when writing the audit payload. YooKassa's
# real UA is ~50 chars; budget room for forks but cap the row size.
_UA_MAX_LEN: Final[int] = 120


def _client_ip(request: HttpRequest) -> str:
    """Best-effort source IP — proxy-aware.

    Walks the X-Forwarded-For chain (left-most non-proxy entry) and
    falls back to REMOTE_ADDR. Returns empty string if neither is
    populated (treat as «unknown»). This is a forensic capture only;
    we don't gate on it.
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "") or ""
    if xff:
        # First entry is the originator per RFC 7239 conventions.
        first = xff.split(",", 1)[0].strip()
        if first:
            return first
    return request.META.get("REMOTE_ADDR", "") or ""


@csrf_exempt
@require_POST
def gone_410(request: HttpRequest) -> JsonResponse:
    """Returns ``410 Gone`` + writes a forensic audit row.

    Always returns 410 — there is no condition under which we'd want
    YooKassa to retry. Audit row + WARNING log are the operational
    signal that a Cabinet still points here.

    The ``@require_POST`` decorator returns ``405 Method Not Allowed``
    on GET/PUT/etc. — those aren't YooKassa traffic and we want the
    distinction in monitoring (curl probes / scanners look different
    from real retries).
    """
    body = request.body or b""
    remote_ip = _client_ip(request)
    user_agent = (request.META.get("HTTP_USER_AGENT", "") or "")[:_UA_MAX_LEN]

    write_audit(
        action=AUDIT_ACTION,
        target="yookassa.retired",
        payload={
            "body_bytes": len(body),
            "remote_ip": remote_ip,
            "user_agent_truncated": user_agent,
        },
    )

    logger.warning(
        "yookassa.webhook_received_after_retirement remote_ip=%s body_bytes=%d",
        remote_ip,
        len(body),
    )

    return JsonResponse(
        {
            "status": "gone",
            "reason": "yookassa_webhook_retired",
            "info": ("YooKassa webhook URL has moved. Update Personal Cabinet configuration."),
        },
        status=410,
    )
