"""Structured JSON log formatter (DRF-713 / Sprint 8 / J1).

Single-class module that produces one JSON line per ``LogRecord`` —
machine-parseable output for the platform's prod log shipper (whatever
that ends up being; Loki, Datadog, ELK all consume the same shape).

### Why a stdlib formatter and not structlog

Decision 9 in `docs/plans/sprint-8-observability-shadow.md`: ~40 LOC
of stdlib code beats pulling in another dependency. OTel already gives
us `trace_id` + `span_id` via the current span; tenancy already exposes
`current_tenant()` via the ContextVar wired in Sprint 2. The formatter
just reads from those primitives — no new abstractions.

### Output shape

```
{"ts":"2026-05-13T13:55:00.123Z","level":"INFO","logger":"apps.skills.faq",
 "msg":"faq.handle hits=3 confidence=0.82","tenant_id":"...","trace_id":"...",
 "span_id":"...","pipeline_step":"skill","extra":{"chunks":3}}
```

* `ts` — UTC, ISO 8601 with millisecond precision, suffix ``Z``.
* `msg` — formatted message (after ``record.getMessage()``); placeholder
  args already merged. Sentry redaction (E1) runs **before** Sentry
  send — the JSON log layer doesn't redact, since logs already go to
  isolated stores and Phase 1 will add a transport-level scrubber.
* `tenant_id` / `trace_id` / `span_id` — empty string when unavailable
  (J2's ContextFilter populates them; this formatter just reads them
  off the record).
* `extra` — any non-standard attribute on the LogRecord. We dump it
  rather than flatten so a stray ``message`` key in extras doesn't
  collide with the top-level field.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Any

# Fields on every LogRecord that we either emit as named keys
# (`level`, `logger`, `msg`, `ts`) or omit deliberately (stdlib bookkeeping
# like `args`, `pathname`, etc.). Everything else on the record becomes
# part of `extra`.
_STANDARD_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        # ContextFilter (J2) populates these — emitted as top-level fields,
        # so they shouldn't be re-dumped into `extra`.
        "tenant_id",
        "trace_id",
        "span_id",
        "pipeline_step",
        # uvicorn / gunicorn injected fields we tolerate but don't expose
        "taskName",
        "color_message",
    }
)


class JsonFormatter(logging.Formatter):
    """Render a ``LogRecord`` as a single JSON line.

    Compatible with Django's `LOGGING` dict-config:

    ```python
    LOGGING = {
        "version": 1,
        "formatters": {"json": {"()": "apps.observability.logging.JsonFormatter"}},
        ...
    }
    ```
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _iso8601_utc(record.created),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "tenant_id": getattr(record, "tenant_id", "") or "",
            "trace_id": getattr(record, "trace_id", "") or "",
            "span_id": getattr(record, "span_id", "") or "",
            "pipeline_step": getattr(record, "pipeline_step", "") or "",
        }

        if record.exc_info:
            # The Formatter base class caches the formatted text in
            # `exc_text` after the first call — re-use it across multiple
            # handlers so we don't re-render the traceback per output.
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            payload["exc_info"] = record.exc_text

        extra = _extract_extra(record)
        if extra:
            payload["extra"] = extra

        # ensure_ascii=False keeps russian text readable; default=str is a
        # belt-and-braces fallback so unexpected types (Path, UUID) become
        # strings rather than crash the handler.
        return json.dumps(payload, ensure_ascii=False, default=str)


def _iso8601_utc(epoch_seconds: float) -> str:
    """Format a UNIX timestamp as ISO 8601 UTC with millisecond precision.

    Standard `datetime.isoformat()` defaults to microsecond precision and
    no `Z` suffix; we normalise both for consistency with the org's other
    log producers.
    """
    dt = _dt.datetime.fromtimestamp(epoch_seconds, tz=_dt.timezone.utc)
    # ``%f`` is microseconds; slice to milliseconds and append `Z`.
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _extract_extra(record: logging.LogRecord) -> dict[str, Any]:
    """Pull non-stdlib attributes off the record for the `extra` block."""
    out: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_FIELDS or key.startswith("_"):
            continue
        out[key] = value
    return out


# ---------------------------------------------------------------------------
# ContextFilter (J2 / DRF-714)
# ---------------------------------------------------------------------------


class ContextFilter(logging.Filter):
    """Enrich every LogRecord with tenant + OTel-trace context.

    Zero-touch design — existing ``logger.info("x")`` call sites
    don't change. The filter runs on every record produced under the
    Django LOGGING config and stamps `tenant_id` / `trace_id` /
    `span_id` attributes so :class:`JsonFormatter` can surface them as
    named keys (instead of relegating them to ``extra``).

    Defensive on every read — if OTel is unconfigured or the tenancy
    ContextVar is empty, the field becomes ``""``. Filter MUST NOT
    raise: a noisy filter would crash production logging which would
    cascade through every Django request.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Tenant context — read via the existing tenancy.context helper.
        # Import lazily so this module can be imported during Django
        # AppConfig.ready() before apps.tenancy itself is fully loaded.
        #
        # ``LogRecord`` has no statically-declared `tenant_id` / `trace_id`
        # / `span_id` attributes; we attach dynamically. ``setattr`` keeps
        # mypy happy without per-line ignores.
        trace_id, span_id = _read_otel_ids()
        setattr(record, "tenant_id", _read_tenant_id())  # noqa: B010
        setattr(record, "trace_id", trace_id)  # noqa: B010
        setattr(record, "span_id", span_id)  # noqa: B010
        # Always pass — filter is for enrichment, not gatekeeping.
        return True


def _read_tenant_id() -> str:
    try:
        from apps.tenancy.context import current_tenant
    except ImportError:  # pragma: no cover
        return ""
    try:
        tenant = current_tenant()
    except Exception:  # noqa: BLE001 — filter must never raise
        return ""
    if tenant is None:
        return ""
    return str(getattr(tenant, "id", "") or "")


def _read_otel_ids() -> tuple[str, str]:
    """Return ``(trace_id_hex, span_id_hex)`` — empty strings when no
    span is active.

    Thin wrapper over the canonical implementation in
    :func:`apps.observability.otel.get_current_span_ids` (Sprint 8 P1-1
    extraction). Kept as a module-private function for clarity at the
    filter's call site, but the actual logic lives once.
    """
    from apps.observability.otel import get_current_span_ids

    return get_current_span_ids()
