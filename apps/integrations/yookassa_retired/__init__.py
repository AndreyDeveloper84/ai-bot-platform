"""YooKassa-retired temporary endpoint — #732 (Phase 0 / Bucket 6 PRE_PILOT).

Per ADR-0009 §Domain ownership matrix, YooKassa payment lifecycle
(create, capture, refund, webhook) moved to Ayla djangoproject. The
old ``apps.integrations.yookassa`` package was deleted in PR #739
(#427+#428) and the ``/api/v1/yookassa/webhook/`` URL mount was
removed at the same time.

### Why this app exists (temporary)

Round-1 adversarial review on #739 (agent ``a0e15c3aba9de55c6``)
caught a webhook downtime hazard: any in-flight YooKassa retry to
the old URL falls through to Django's default 404. We restore the
URL with a 410-returning view to capture the forensic signal — but
note the truthful retry semantics:

  Per YooKassa public docs (https://yookassa.ru/developers/using-api/
  webhooks + /response-handling/http-codes), **any** non-200 response
  triggers retry for 24 hours from the original event. YooKassa does
  NOT distinguish 4xx from 5xx — only HTTP 200 stops retries. So 410
  ALSO gets retried for 24h, just like a 500 would. 410 is chosen
  over 404 ONLY for operational loudness (easier to dashboard) and
  for eventual conformance with RFC 9110 §15.5.11.

The view (``apps.integrations.yookassa_retired.views.gone_410``):

1. Returns HTTP ``410 Gone``.
2. Writes an audit row ``yookassa.webhook_received_after_retirement``
   sampled per-IP-per-60s window. Volume planning: real load is
   ``Cabinets × events × ~6 retries in 24h``; sampling bounds the
   audit-table writes under both legitimate retry volume AND scanner
   DoS (Round-1 adversarial P1).
3. Audit captures ONLY: ``body_bytes`` (no body content — PII §7),
   ``remote_ip`` (proxy-aware + IP-validated; attacker-controlled
   header sources can't inject card-tail digits into the audit row),
   ``user_agent_truncated`` (control chars stripped — no log-
   injection via ``\\r\\n`` in the WARNING format string).
4. Emits a WARNING log so on-call paging rules trigger.
5. Rate-limited per-IP (mirrors
   ``apps.eventbus.views.InternalEventsIngestView``) — over-limit
   gets HTTP 429 with a sampled audit row using a distinct action
   slug so 429 + 410 streams stay separable in dashboards.

### Strict-mode opt-out

YooKassa has no ``X-Tenant`` header (external webhook). The URL
prefix ``/api/v1/yookassa/`` is added to ``STRICT_OPT_OUT_PREFIXES``
in :mod:`apps.tenancy.middleware`. Without that, the 2026-05-28
strict-mode flip (per memory ``project_strict_tenant_refuse_soak``)
returns 400 ``TENANT_REQUIRED`` before this view runs, defeating the
forensic-capture design entirely. Round-1 adversarial M1 on #768.

### Lifecycle — sunset path

This package is **temporary** (issue #732 PRE_PILOT). Cleanup PR
removes it after a 30-day soak window post-#739 deploy. The cleanup
will:

* Remove the URL mount in ``config/urls.py``.
* Remove ``"/api/v1/yookassa/"`` from
  ``apps.tenancy.middleware.STRICT_OPT_OUT_PREFIXES``.
* Remove ``should_audit_yookassa_retired`` from
  ``apps.eventbus.ingest_rate_audit_sampler``.
* Delete this directory entirely.

(No ``INSTALLED_APPS`` entry to remove — this package is NOT
registered as a Django app, matching siblings
``apps.integrations.yclients`` + ``apps.integrations.ayla_payments``.
URL routing works via ``include(...)`` without app registration; the
package contains no models / migrations / admin / signals.)

30-day soak rationale: maximum YooKassa retry window is 24h per
event; 30 days is ~30× safety margin for slowly-flipping Cabinets +
operator reaction time. NOT tied to ``AuditLog`` retention — those
rows persist past cleanup, and a string in ``audit/retired_actions.py``
(see follow-up) keeps the slug grep-able after the package is gone.

### Why a fresh app instead of resurrecting apps.integrations.yookassa

PR #739 explicitly deleted ``apps.integrations.yookassa`` to close
the dead-credential surface + the implicit «this code is owned by
bot-platform» reading. Re-introducing the old name would muddle
ADR-0009 §Domain ownership for any future reader. A separate
``_retired`` package signals intent + scope (just the 410 endpoint,
nothing else) and makes the cleanup PR's surface obvious.
"""
