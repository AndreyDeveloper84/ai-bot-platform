"""Orders app — payment artefacts (DRF-843 / Phase 1 / B7).

Houses the :class:`Order` model (today: gift certificate sales; tomorrow:
deposits, package deals) and the :class:`PaymentEvent` idempotency
ledger for inbound payment webhooks.

The orders app is intentionally separate from :mod:`apps.booking` —
bookings are appointment artefacts (a reserved slot on a calendar);
orders are payment artefacts (money intent / receipt). The two
intersect when a booking is paid upfront, but the lifecycle, audit
needs, and analytics use cases are distinct.

Integration point with YooKassa lives in
:mod:`apps.integrations.yookassa`; the bot-facing ``buy_certificate``
LLM tool lives in :mod:`apps.skills.booking.tools`.
"""
