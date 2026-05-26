"""Orders app — RETIRED in #427+#428 (Phase 0 / Bucket 6).

The app shipped in DRF-843 / Phase 1 with two models:
* ``Order`` — money intent / receipt for certificate sales.
* ``PaymentEvent`` — idempotency ledger for YooKassa webhooks.

Both tables were dropped in migration ``0002_drop_orders_tables.py``
when YooKassa payment lifecycle (create, capture, refund, webhook)
moved to Ayla djangoproject per ADR-0009 §Domain ownership matrix.

The bot-platform side now uses :mod:`apps.integrations.ayla_payments`
to request checkout links from Ayla, and consumes
``payment.{authorized,captured,failed,refunded}`` events to keep
:class:`apps.conversations.models.Conversation` payment-context
fields fresh (see #443 consumer family).

This stub package + migrations dir remain in tree so Django's
migration history stays consistent after the deploy that runs
``0002_drop_orders_tables.py``. A future cleanup PR removes the
``apps.orders`` ``INSTALLED_APPS`` entry + this directory entirely.
"""
