"""Sentry SDK configuration with PII scrubbing (DRF-710 / Sprint 8 / E1).

Sentry is our last line of defence for production errors. The risk a
crash carries with it is a structured payload containing untrusted user
text — phone numbers in stack-trace frame ``locals``, an email in an
exception ``message``, a Russian passport number in a Sentry breadcrumb.

This module wires Sentry such that:

1. Init is idempotent + no-ops when ``SENTRY_DSN`` is empty. Local dev
   + tests never accidentally ship events upstream.
2. Every event passes through :func:`scrub_event` before transport — a
   recursive walk that delegates to Sprint 5's
   :class:`apps.replay.redactor.Redactor`. **One source of truth** for
   PII patterns; bumping Sprint 5's allow-list automatically tightens
   Sentry redaction.
3. Trace + tenant context attached via Sentry tags so the dashboard
   filters work out of the box. trace_id is pulled from the current
   OTel span (T2 + T3 wire it).

### Why scrub at ``before_send`` rather than at exception-raise

Raising a custom redacted exception type would force every call site to
sanitize — error-prone. ``before_send`` is the single chokepoint Sentry
guarantees runs against every event. Forgetting one call site is
impossible.

### Production fail-fast

When ``DJANGO_ENV=production`` AND ``SENTRY_DSN`` is empty, callers
should explode at settings boot — :mod:`config.settings.production`
owns that check (analogous to ``CHROMA_AUTH_TOKEN`` / catalog token).
This module's :func:`configure_sentry` only refuses to send events;
the fail-fast lives one layer up.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


_CONFIGURE_LOCK = threading.Lock()
_CONFIGURED = False


# Decision 3 — 5% trace sampling. Errors always sampled at 100%; only
# transaction-style traces are subject to ``traces_sample_rate``.
_DEFAULT_TRACES_SAMPLE_RATE = 0.05


def configure_sentry() -> bool:
    """Wire the Sentry SDK. Idempotent.

    Returns ``True`` when configuration just ran, ``False`` when an
    earlier call already did the work or when ``SENTRY_DSN`` is empty.

    Empty DSN = no-op (events never leave the process). This is how
    local dev + CI avoid noisy upstream reports without any conditional
    import-skip dance at call sites.
    """
    global _CONFIGURED

    if _CONFIGURED:
        return False

    with _CONFIGURE_LOCK:
        if _CONFIGURED:
            return False

        dsn = str(getattr(settings, "SENTRY_DSN", "") or "")
        if not dsn:
            logger.info("sentry.configure noop reason=no_dsn")
            _CONFIGURED = True
            return False

        try:
            import sentry_sdk
            from sentry_sdk.integrations.celery import CeleryIntegration
            from sentry_sdk.integrations.django import DjangoIntegration
            from sentry_sdk.integrations.redis import RedisIntegration
        except ImportError:  # pragma: no cover — optional dependency
            logger.warning("sentry.configure skipped reason=sentry_sdk_not_installed")
            _CONFIGURED = True
            return False

        environment = str(getattr(settings, "SENTRY_ENVIRONMENT", "local") or "local")
        traces_sample_rate = float(
            getattr(settings, "SENTRY_TRACES_SAMPLE_RATE", _DEFAULT_TRACES_SAMPLE_RATE)
        )
        release = str(getattr(settings, "SERVICE_VERSION", "0.0.0") or "0.0.0")

        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=release,
            traces_sample_rate=traces_sample_rate,
            # Errors always sampled at 100% (sample_rate default = 1.0).
            # Only transaction traces obey traces_sample_rate.
            send_default_pii=False,
            integrations=[
                DjangoIntegration(),
                CeleryIntegration(),
                RedisIntegration(),
            ],
            before_send=scrub_event,  # type: ignore[arg-type]
        )
        logger.info(
            "sentry.configure ok env=%s release=%s traces_sample_rate=%s",
            environment,
            release,
            traces_sample_rate,
        )
        _CONFIGURED = True
        return True


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """``before_send`` hook — walk the event payload through the Sprint 5
    redactor, then layer trace + tenant tags on top.

    Sentry passes ``event`` as a ``dict`` (already serialised at this
    point — opaque dataclasses have been turned into JSON-friendly
    mappings). The Sprint 5 redactor's :meth:`Redactor.redact_value`
    recursion handles the exact shape Sentry produces.

    ``hint`` carries the original exception when available; we don't use
    it for redaction (the message string is already in ``event["message"]``
    or under ``event["exception"]``) but it's part of the SDK contract.
    """
    from apps.replay.redactor import Redactor

    redactor = Redactor()
    # The recursive walk only touches str leaves — dict keys (Sentry
    # uses stable, non-PII keys like "message" / "exception") stay
    # intact. The result is the same shape Sentry expects.
    scrubbed = redactor.redact_value(event)

    # Tag layer — trace + tenant. Done AFTER redaction because tag
    # values are short identifiers (UUID hex) that look nothing like
    # PII patterns; redacting them would corrupt the dashboard filter.
    _attach_context_tags(scrubbed)
    return scrubbed


def _attach_context_tags(event: dict[str, Any]) -> None:
    """Inject `tenant_id` + `trace_id` + `span_id` tags from the
    current OTel span (when present) into the Sentry event.

    Defensive: missing OTel context never raises — tags just get
    omitted. Sentry tolerates absent tags gracefully.
    """
    tags = event.setdefault("tags", {})
    try:
        from opentelemetry import trace
    except ImportError:  # pragma: no cover
        return
    span = trace.get_current_span()
    if span is None:
        return
    ctx = span.get_span_context()
    if not getattr(ctx, "is_valid", False):
        return
    trace_id_hex = format(ctx.trace_id, "032x")
    span_id_hex = format(ctx.span_id, "016x")
    tags.setdefault("trace_id", trace_id_hex)
    tags.setdefault("span_id", span_id_hex)

    try:
        from apps.tenancy.context import current_tenant
    except ImportError:  # pragma: no cover
        return
    tenant = current_tenant()
    if tenant is not None:
        tags.setdefault("tenant_id", str(tenant.id))


def reset_sentry_for_tests() -> None:
    """Test-only — pretend Sentry was never configured."""
    global _CONFIGURED
    _CONFIGURED = False
