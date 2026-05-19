"""Async YClients sync — best-effort push of platform bookings.

Per `docs/design/handoffs/2026-05-18-schedule-management-handoff.md` §10
sync direction:

> Bookings: bidirectional, our manual bookings push to YC, YC bookings
> pull in.

This module owns the platform→YC half. Triggered by
:func:`apps.booking.services.create.create_customer_booking` after a
successful DB commit (eventual consistency: platform = source of
truth, YC = mirror).

### Why a task, not inline

Pushing inside the request would couple Mini App latency to YClients
API (50-500ms typical, seconds under WAF heat). Per skill-ref MAX
P4 latency budget, the Mini App POST /bookings must respond in
< 500ms. Async + retry is the only way to honour both.

### Idempotency

Stores the returned ``yclients_record_id`` in
``BookingRequest.attribution_metadata['yclients_record_id']``. When
the YClients ``record.create`` webhook later fires for the same id
(YC echoes every write back), :func:`apps.integrations.yclients.webhooks`
sees the field already populated and short-circuits — no duplicate
BookingRequest row.

### Failure modes

* YClients API down → retry 3× with exponential backoff (Celery default)
  → after exhaustion: log + emit ``yclients.push.failed`` event +
  attribution_metadata[``yc_push_status``] = ``failed``. Admin manually
  resolves via Schedule Management UI conflict view.
* Master has no ``yclients_staff_id`` → tenant doesn't have YC for this
  master (catalog-only mode) → silent skip + status=``skipped_no_staff``.
* Tenant feature flag ``yclients_integration`` disabled → silent skip
  + status=``skipped_no_integration``.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


def _yclients_service_id(service) -> int | None:
    """Pull YClients service id from CatalogService.raw blob.

    The catalog sync (apps.catalog.services.upserter) stores the raw
    mysite row in ``raw`` — for YC-connected mysite tenants this
    includes the YClients service id under one of several keys
    (mysite uses ``yclients_service_id`` historically, some installs
    use ``yc_id``). Falls back to None.
    """

    raw = service.raw or {}
    for key in ("yclients_service_id", "yc_service_id", "yc_id"):
        val = raw.get(key)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                continue
    return None


def _format_yc_datetime(visit_at) -> str:
    """YClients expects ``YYYY-MM-DDTHH:MM:SS`` in salon local TZ.

    ``visit_at`` is timezone-aware (stored UTC). We strip the offset
    and format in local time — YClients interprets in salon TZ on
    their side per their company_id config.
    """

    return visit_at.strftime("%Y-%m-%dT%H:%M:%S")


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,  # 30s, then exponential via celery
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def push_booking_to_yclients(self, *, booking_id: str) -> dict[str, Any]:
    """Push a platform-created BookingRequest to YClients API.

    Idempotent. Re-running on a booking that already pushed (has
    ``yclients_record_id`` in metadata) is a no-op.

    Parameters
    ----------
    booking_id : str (UUID)
        ``BookingRequest.id`` to sync.

    Returns
    -------
    dict with ``status`` field — one of ``pushed``, ``already_pushed``,
    ``skipped_no_integration``, ``skipped_no_staff``,
    ``skipped_no_service_mapping``, ``failed`` (terminal).
    """

    # Late imports — Celery worker context, avoid Django app-load issues.
    from apps.booking.models import BookingRequest
    from apps.events.services import emit
    from apps.integrations.yclients.client import (
        YClientsAPIError,
        get_yclients_client,
    )

    try:
        booking = BookingRequest.all_tenants.select_related(
            "tenant", "service", "master", "bot_user"
        ).get(id=booking_id)
    except BookingRequest.DoesNotExist:
        logger.warning("yclients.push.booking_not_found id=%s", booking_id)
        return {"status": "booking_not_found"}

    meta = booking.attribution_metadata or {}
    if meta.get("yclients_record_id"):
        return {"status": "already_pushed", "record_id": meta["yclients_record_id"]}

    tenant = booking.tenant
    if not (tenant.features or {}).get("yclients_integration", False):
        return _record_skip(booking, "skipped_no_integration")

    master = booking.master
    if master is None or not master.yclients_staff_id:
        return _record_skip(booking, "skipped_no_staff")

    service = booking.service
    yc_service_id = _yclients_service_id(service) if service else None
    if yc_service_id is None:
        return _record_skip(booking, "skipped_no_service_mapping")

    try:
        client = get_yclients_client()
        record = client.create_record(
            staff_id=master.yclients_staff_id,
            services=[yc_service_id],
            datetime=_format_yc_datetime(booking.visit_at),
            client_phone=booking.client_phone or "",
            client_name=booking.client_name or "",
            comment=f"Создано через помощника (booking_id={booking.id})",
        )
    except YClientsAPIError as exc:
        logger.exception(
            "yclients.push.api_error booking=%s retry=%s",
            booking.id,
            self.request.retries,
        )
        if self.request.retries >= self.max_retries:
            _record_terminal_failure(booking, str(exc))
            emit(
                "yclients.push.failed",
                properties={"booking_id": str(booking.id), "reason": str(exc)},
            )
            return {"status": "failed", "reason": str(exc)}
        raise  # re-raise → Celery retries

    # Persist YC record id for idempotency on the inbound webhook side.
    meta["yclients_record_id"] = str(record.record_id)
    meta["yc_push_status"] = "pushed"
    BookingRequest.all_tenants.filter(pk=booking.pk).update(attribution_metadata=meta)

    emit(
        "yclients.push.succeeded",
        properties={
            "booking_id": str(booking.id),
            "yclients_record_id": str(record.record_id),
        },
    )
    logger.info(
        "yclients.push.ok booking=%s yc_record=%s",
        booking.id,
        record.record_id,
    )
    return {"status": "pushed", "record_id": str(record.record_id)}


def _record_skip(booking, reason: str) -> dict[str, Any]:
    meta = booking.attribution_metadata or {}
    meta["yc_push_status"] = reason
    booking.__class__.all_tenants.filter(pk=booking.pk).update(attribution_metadata=meta)
    logger.info("yclients.push.skipped booking=%s reason=%s", booking.id, reason)
    return {"status": reason}


def _record_terminal_failure(booking, reason: str) -> None:
    meta = booking.attribution_metadata or {}
    meta["yc_push_status"] = "failed"
    meta["yc_push_error"] = reason[:200]
    booking.__class__.all_tenants.filter(pk=booking.pk).update(attribution_metadata=meta)


__all__ = ["push_booking_to_yclients"]
