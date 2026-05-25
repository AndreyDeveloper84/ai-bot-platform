"""payment_failed skill — реагирует на payment.failed события из Ayla.

Точки входа:

* ``on_payment_failed_event(envelope)`` — direct-call entry point из
  Gamma's #443 payment.* consumer (когда приходит ``payment.failed``
  domain event через ``/api/v1/internal/events/ingest``).
* ``PaymentRetryCallbackSkill`` — обработчик inline-кнопки [Оплатить]
  в client DM (callback prefix ``cb:payment:retry:<payment_id>``).

Архитектура и UX-решение зафиксированы в директиве tech-lead 2026-05-24
(Variant C: DM клиенту + info-only master DM, без service-token
impersonation; retry-кнопка adресована клиенту, не мастеру).
"""

from apps.skills.payment_failed.skill import (
    CALLBACK_PAYMENT_RETRY_PREFIX,
    PaymentRetryCallbackSkill,
    on_payment_failed_event,
)

__all__ = [
    "CALLBACK_PAYMENT_RETRY_PREFIX",
    "PaymentRetryCallbackSkill",
    "on_payment_failed_event",
]
