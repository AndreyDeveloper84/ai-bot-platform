"""YClients booking API client.

Phase 1 / B1 (DRF-837). Ports ``mysite/services_app/yclients_api.py`` —
prod-validated client used by Формула-Тела for service catalog, staff
discovery, booking creation, and cancellation.

Single-tenant scope for this port: credentials read from ``settings.YCLIENTS_*``.
Per-tenant credential storage (encrypted on ``Tenant``) is a follow-up;
empty defaults ship the integration dormant until env is configured.
"""

from apps.integrations.yclients.client import (
    AvailableTime,
    BookingRecord,
    DateSlot,
    Service,
    Staff,
    UserRecord,
    YClientsAPI,
    YClientsAPIError,
    YClientsUnavailableError,
    get_yclients_client,
    reset_yclients_client,
)

__all__ = [
    "AvailableTime",
    "BookingRecord",
    "DateSlot",
    "Service",
    "Staff",
    "UserRecord",
    "YClientsAPI",
    "YClientsAPIError",
    "YClientsUnavailableError",
    "get_yclients_client",
    "reset_yclients_client",
]
