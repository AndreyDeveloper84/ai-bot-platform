"""``410 Gone`` view for retired YooKassa webhook URL — #732.

Single view: any POST to ``/api/v1/yookassa/webhook/`` (the old
bot-platform URL still configured in YooKassa Personal Cabinets that
haven't been flipped to Ayla yet) returns HTTP 410 Gone, writes a
sampled audit row, and logs a WARNING.

### Status-code semantics — truthful retry behavior

Per YooKassa public documentation
(https://yookassa.ru/developers/using-api/webhooks +
/developers/using-api/response-handling/http-codes), **ANY** non-200
response — including 410 Gone — triggers retry for 24 hours from the
original event (first retry at 1 min, then up to 5 retries at 5-30
min intervals). YooKassa does NOT distinguish 4xx from 5xx in its
retry policy — only HTTP 200 stops retries.

So why 410 and not 404? Two reasons:

1. **Operational loudness.** 410 Gone is the only HTTP code that
   semantically says «permanently moved» — easier to dashboard +
   grep than the generic 404 noise.
2. **Eventual conformance.** RFC 9110 §15.5.11 says 410 SHOULD NOT
   be retried; if YooKassa changes its retry policy to honor the
   spec, 410 will gracefully stop the retry storm. Until then, this
   is a forensic-capture endpoint, NOT a retry-stopper.

Operational implication: real audit-row volume is
``events × ~6 retry attempts``, NOT 1 row per event. Rate-limit +
audit sampling (mirrors sibling
``apps/eventbus/views.py::InternalEventsIngestView``) bound the
AuditLog table writes under both legitimate retry volume AND scanner
DoS (Round-1 adversarial P1 on PR #768).

### PII discipline (§7) + adversarial hardening

Captures ONLY:

* ``body_bytes`` — payload size. NEVER body content; YooKassa
  webhook bodies can carry card-tail digits in failure messages.
* ``remote_ip`` — proxy-aware via :func:`apps.eventbus.ingest_ip.
  get_remote_ip` (trusted XFF chain depth + ``X-Real-IP`` gating),
  then **validated** as a parseable IP via
  :mod:`ipaddress`. Header sources are attacker-controlled; without
  validation an attacker could inject card-tail digits / log-
  injection payloads / arbitrary strings into the audit row.
  Round-1 adversarial P2+P3 on PR #768.
* ``user_agent_truncated`` — first 120 chars, with ASCII control
  characters (incl. ``\\r\\n`` log-injection + ``\\x00`` DB-mangling
  + escape sequences) stripped. Round-1 adversarial F3 / P3.

### Strict-mode opt-out

YooKassa has no concept of ``X-Tenant`` (external webhook). The
URL prefix ``/api/v1/yookassa/`` is opted out of strict-tenant
enforcement in :mod:`apps.tenancy.middleware` — without that, the
2026-05-28 strict-mode flip would return 400 ``TENANT_REQUIRED``
before this view runs, defeating the design. Round-1 adversarial
M1 on PR #768.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from typing import Final

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from django_ratelimit.decorators import ratelimit  # type: ignore[import-untyped]

from apps.audit.services import write_audit
from apps.eventbus.ingest_ip import get_remote_ip
from apps.eventbus.ingest_rate_audit_sampler import (
    should_audit_yookassa_retired_410,
    should_audit_yookassa_retired_429,
)


logger = logging.getLogger(__name__)


AUDIT_ACTION: Final[str] = "yookassa.webhook_received_after_retirement"
AUDIT_RATE_LIMITED_ACTION: Final[str] = "yookassa.webhook_received_after_retirement.rate_limited"

# UA cap — YooKassa's real UA is ~50 chars; budget room for forks
# but cap the audit-row size.
_UA_MAX_LEN: Final[int] = 120

# Control characters: ASCII 0-31 (incl. \r\n which would otherwise
# enable log-injection through the formatted WARNING message) + DEL
# (127). High control bytes also matter for downstream JSON / DB
# encoders that may reject them. Replace with '?' so the audit field
# is still grep-able for forensic triage.
_UA_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# Rate-limit ceiling — reuse ``EVENT_INGEST_RATE_LIMIT`` because the
# operational profile is identical (unauthenticated external POST;
# expected steady-state events/sec; scanner DoS surface). The same
# setting being absent in old YOOKASSA_ envs is fine because the
# default 100/m is well above any pre-flip leftover Cabinet's
# ``events × 6 retries`` volume.
_RATE_LIMIT_DEFAULT: Final[str] = "100/m"


def _rate_limit_key(group, request) -> str:
    """Proxy-aware rate-limit key — mirror eventbus pattern."""
    return get_remote_ip(request) or "_unknown_"


def _sanitize_ip(raw: str) -> str:
    """Validate ``raw`` as a parseable IP; return canonical form or ''.

    Header sources (``X-Real-IP`` / ``X-Forwarded-For``) are
    attacker-controlled. An attacker can stuff ``X-Forwarded-For:
    4242424242424242`` and that string would otherwise land in the
    audit row's ``remote_ip`` slot, violating the «no PII in audit»
    contract. Strict IP parsing closes the surface.
    """
    if not raw:
        return ""
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except (ValueError, TypeError):
        return ""


def _sanitize_ua(raw: str) -> str:
    """Strip ASCII control characters + truncate."""
    if not raw:
        return ""
    cleaned = _UA_CONTROL_RE.sub("?", raw)
    return cleaned[:_UA_MAX_LEN]


def _write_sampled_audit(
    *,
    action: str,
    sampler,
    body_bytes: int,
    remote_ip: str,
    user_agent: str,
) -> None:
    """Write an audit row only if the per-slug sampler admits the IP.

    Mirrors the sibling
    ``apps.eventbus.views.InternalEventsIngestView`` pattern: per-IP
    +60s windows bound a single source from flooding the audit table
    while preserving the forensic signal. ``sampler`` is one of the
    slug-distinct samplers
    (:func:`should_audit_yookassa_retired_410`,
    :func:`should_audit_yookassa_retired_429`) so the 410 + 429
    streams stay separable per dashboard.
    """
    if not sampler(remote_ip or "_unknown_"):
        return
    write_audit(
        action=action,
        target="yookassa.retired",
        payload={
            "body_bytes": body_bytes,
            "remote_ip": remote_ip,
            "user_agent_truncated": user_agent,
        },
    )


@csrf_exempt
@require_POST
@ratelimit(
    key=_rate_limit_key,
    rate=lambda group, request: getattr(settings, "EVENT_INGEST_RATE_LIMIT", _RATE_LIMIT_DEFAULT),
    method="POST",
    block=False,
)
def gone_410(request: HttpRequest) -> JsonResponse:
    """Returns ``410 Gone`` + writes a sampled forensic audit row.

    Status code matrix:

    | Outcome                            | Status | Audit       |
    |------------------------------------|--------|-------------|
    | Rate-limit exceeded (per-IP)       | 429    | SAMPLED     |
    | All other POSTs                    | 410    | SAMPLED     |
    | GET / PUT / etc. (require_POST)    | 405    | NONE        |
    """
    body = request.body or b""
    remote_ip = _sanitize_ip(get_remote_ip(request))
    user_agent = _sanitize_ua(request.META.get("HTTP_USER_AGENT", "") or "")
    body_bytes = len(body)

    # Rate-limit gate — django-ratelimit sets ``limited=True`` when
    # the per-IP bucket is exhausted (block=False form). 429 + sampled
    # audit + distinct action slug so 429 + 410 forensic streams stay
    # separable in dashboards.
    if getattr(request, "limited", False):
        _write_sampled_audit(
            action=AUDIT_RATE_LIMITED_ACTION,
            sampler=should_audit_yookassa_retired_429,
            body_bytes=body_bytes,
            remote_ip=remote_ip,
            user_agent=user_agent,
        )
        logger.warning(
            "yookassa.webhook_received_after_retirement.rate_limited "
            "remote_ip=%s body_bytes=%d ua=%s",
            remote_ip,
            body_bytes,
            user_agent,
        )
        return JsonResponse(
            {"status": "rate_limited", "reason": "rate_limit_exceeded"},
            status=429,
        )

    _write_sampled_audit(
        action=AUDIT_ACTION,
        sampler=should_audit_yookassa_retired_410,
        body_bytes=body_bytes,
        remote_ip=remote_ip,
        user_agent=user_agent,
    )

    logger.warning(
        "yookassa.webhook_received_after_retirement remote_ip=%s body_bytes=%d ua=%s",
        remote_ip,
        body_bytes,
        user_agent,
    )

    return JsonResponse(
        {
            "status": "gone",
            "reason": "yookassa_webhook_retired",
            "info": ("YooKassa webhook URL has moved. Update Personal Cabinet configuration."),
        },
        status=410,
    )
