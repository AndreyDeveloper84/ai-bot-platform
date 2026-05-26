"""Drop apps/orders tables — #427+#428 YooKassa lifecycle consolidation.

Per ADR-0009 §Domain ownership matrix, YooKassa payment lifecycle
(create, capture, refund, webhook) lives in Ayla djangoproject only.
bot-platform's ``apps/orders`` mirror + ``apps/integrations/yookassa``
client are being removed.

### What this migration does

* ``DeleteModel("PaymentEvent")`` — drops ``orders_paymentevent`` table.
* ``DeleteModel("Order")`` — drops ``orders_order`` table.

**Operation order is load-bearing.** Django's migration executor runs
``operations`` in the order declared — it does NOT auto-reorder
``DeleteModel`` to respect FK dependencies. ``PaymentEvent`` is dropped
first because it holds the FK to ``Order`` (``order = FK(SET_NULL,
null=True)`` per ``0001_initial.py:188-197``). Reversing this order
would attempt to drop ``Order`` while ``PaymentEvent`` still references
it — even with ``SET_NULL`` the table-drop on PostgreSQL fails
because the FK constraint object still exists. Don't reorder.

After this migration applies, Django still recognizes the app in
``INSTALLED_APPS`` so the migration history stays consistent. A
**follow-up PR (tracked in the post-deploy cleanup issue referenced
from the PR body)** removes the app entry + the remaining stub files
(``apps.py``, ``__init__.py``, migrations dir) once the deploy window
has confirmed no rollback need.

### Irreversibility — INTENTIONAL

``Migration.atomic = True`` and ``operations`` carry no ``reverse_sql``
on the DeleteModel side. Django's standard behaviour is that
``DeleteModel`` is reversible only if the original ``CreateModel`` is
still in the migration graph — which it is here (``0001_initial.py``
remains). So Django CAN technically reverse this migration by re-
creating the tables. **But the tables would be empty** — there's no
backfill. Production rollback after deploy is operationally
useless; treat this as one-way.

### Pre-merge gates (tech-lead Q3 preconditions) — ORDERED TIMELINE

The four preconditions are NOT an unordered checklist. They form
a strict temporal sequence T0 → T4. Out-of-order execution causes
silent webhook loss or false-negative monitoring. The deploy
operator MUST verify each timestamp is later than the previous.

* **T0 — Alpha endpoint live.** Ayla ``/api/v1/payments/webhook`` is
  production-ready: HMAC verified, idempotency dedup wired, Ayla CI
  green on the Bucket 6 endpoint PR. Sign-off: Alpha tech lead.
* **T1 — Cabinet URL switch.** YooKassa Personal Cabinet webhook URL
  flipped from ``<bot-platform>/api/v1/yookassa/webhook/`` to the
  Ayla endpoint. ``T1 > T0`` (don't flip the cabinet before Ayla
  endpoint exists). Sign-off: ops, with screenshot of cabinet config.
  **Single-shop assumption (#741).** This T1 step describes ONE
  Cabinet URL flip — current deployment is single-shop (one
  ``YOOKASSA_SHOP_ID``). If a future retirement is performed against
  a multi-shop fleet, replace T1 with a per-shop loop and adjust T2
  to soak per-shop traffic (otherwise shop A flipped at T1a + shop
  B flipped at T1b leaves the T2 24h window measuring only shop A).
* **T2 — 24-hour zero-traffic soak.** Measure inbound traffic on
  bot-platform ``/api/v1/yookassa/webhook/`` for 24 contiguous hours
  AFTER T1. ``T2_start > T1`` is mandatory — if measured between T0
  and T1, zero traffic is structurally guaranteed (cabinet still
  points here, but Ayla endpoint is just newly-live elsewhere); the
  check has zero detection value. Sign-off: W2 monitoring.
* **T3 — Production data audit.** Run on **bot-platform's** prod
  replica (these are bot-platform tables being dropped — running on
  Ayla's replica either errors with ``relation "orders_order" does
  not exist`` and gets misread as «zero rows», or hits an unrelated
  Ayla table of coincidentally similar name). Use raw SQL, NOT
  Django shell, to bypass ``TenantScopedManager`` (the default
  manager filters by current tenant scope and would silently return
  0 outside ``tenant_scope(...)``):

  .. code-block:: bash

     psql "$BOT_PLATFORM_REPLICA_DSN" -c \
       "SELECT count(*) FROM orders_order;"
     psql "$BOT_PLATFORM_REPLICA_DSN" -c \
       "SELECT count(*) FROM orders_paymentevent \
        WHERE created_at > NOW() - INTERVAL '30 days';"

  Both zero OR only ``paymentevent`` non-zero → proceed. ``orders_order``
  > 0 → STOP, escalate (data not migrated to Ayla yet). ``T3 > T2``.
* **T4 — Deploy this PR.** ``T4 > T3``. DROP TABLE runs as part of
  ``manage.py migrate``.

If any timestamp inversion is observed (e.g. T2_start ≤ T1), the
operator MUST extend the soak window so that 24 contiguous hours
follow T1. There is no rationalisation that makes inverted ordering
safe.

References:
* docs/adr/ADR-0009-ayla-split-domain-architecture.md §Domain ownership
* docs/plans/2026-05-20-phase-0-sprint-plan.md §Bucket 6
* Sibling deletion: apps/integrations/yookassa/ (full removal in this PR)
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(name="PaymentEvent"),
        migrations.DeleteModel(name="Order"),
    ]
