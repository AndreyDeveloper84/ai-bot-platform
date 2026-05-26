"""YooKassa-retired temporary endpoint — #732 (Phase 0 / Bucket 6 PRE_PILOT).

Per ADR-0009 §Domain ownership matrix, YooKassa payment lifecycle
(create, capture, refund, webhook) moved to Ayla djangoproject. The
old ``apps.integrations.yookassa`` package was deleted in PR #739
(#427+#428) and the ``/api/v1/yookassa/webhook/`` URL mount was
removed at the same time.

### Why this app exists (temporary)

Round-1 adversarial review on #739 (agent ``a0e15c3aba9de55c6``)
caught a webhook downtime hazard:

  Between T1 (YooKassa Personal Cabinet URL switch → Ayla) and T4
  (the #739 deploy that ran ``DROP TABLE orders_order``), any in-
  flight YooKassa retry to the old URL hits Django's default 404.
  Per YooKassa idempotency docs: 5xx triggers retry, 4xx (including
  404) does NOT. Payment event lost forever — no forensic trail.

This app re-installs the URL pattern with a ``410 Gone`` view that:

1. Returns HTTP ``410 Gone`` (only status code that semantically
   says «permanently moved, stop retrying» — YooKassa treats 4xx
   as «do not retry», so no infinite-retry loop).
2. Writes an audit row ``yookassa.webhook_received_after_retirement``
   for forensic capture (body byte count + source IP + UA; **NOT**
   the body content — YooKassa webhooks can carry card-tail PII).
3. Emits a WARNING log so any unexpected hit pages operators.

### Lifecycle — sunset path

This package is **temporary** (issue #732 PRE_PILOT). Cleanup PR
removes it in full after a 30-day soak window post-#739 deploy,
once the audit table confirms zero (or near-zero) hits. The
cleanup PR will:

* Remove ``apps.integrations.yookassa_retired`` from
  ``INSTALLED_APPS``.
* Remove the URL mount in ``config/urls.py``.
* Delete this directory entirely.

### Why a fresh app instead of resurrecting apps.integrations.yookassa

PR #739 explicitly deleted ``apps.integrations.yookassa`` to close
the dead-credential surface + the implicit «this code is owned by
bot-platform» reading. Re-introducing the old name would muddle
ADR-0009 §Domain ownership for any future reader. A separate
``_retired`` package signals intent + scope (just the 410 endpoint,
nothing else) and makes the cleanup PR's surface obvious.
"""
