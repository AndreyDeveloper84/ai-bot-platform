"""Sprint 8 observability stack — OpenTelemetry + Sentry + structured logs.

The package owns three deliberately small modules:

* :mod:`apps.observability.otel` (DRF-705 / T1) — OTel SDK configuration.
* :mod:`apps.observability.sentry` (DRF-710 / E1) — Sentry SDK init + PII scrubber.
* :mod:`apps.observability.logging` (DRF-713 / J1) — JSON formatter + context filter.

Per Decision 9 in `docs/plans/sprint-8-observability-shadow.md` we ship our own
formatter rather than pulling in ``structlog``; OTel already provides
``trace_id`` / ``span_id`` via its current-span API so the formatter just reads
those off the active context.
"""

default_app_config = "apps.observability.apps.ObservabilityConfig"
