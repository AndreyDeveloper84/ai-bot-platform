"""Live vs terminal statuses on the Ayla booking mirror — one home (DRF-1034).

``RemoteBookingProxy.status`` is a mirror of Ayla's wire value, and Django does
not enforce ``choices`` at the DB level, so the column legitimately contains
values that are not in :class:`~apps.booking.models.RemoteBookingProxy.Status`
— ``awaiting_payment`` is on the pilot right now. Every read path that asks
«is this booking still going to happen?» therefore has to work from raw
strings, and three of them independently grew their own copy of the list
(``apps.master_api.services.visit_source.UPCOMING_STATUSES``,
``apps.miniapp_api.views._AYLA_UPCOMING_STATUSES``, and the reminder
send-time re-check).

New readers use these. The two pre-existing copies are left alone deliberately
— swapping them out is a mechanical change to surfaces owned by other work in
flight, and getting it wrong is worse than the duplication.
"""

from __future__ import annotations

from typing import Final

#: The visit is still expected to happen. Reminding about it, showing it in
#: «мои записи», and treating it as payable are all correct.
LIVE_STATUSES: Final[frozenset[str]] = frozenset(
    {"confirmed", "awaiting_payment", "pending_payment", "tentative"}
)

#: The visit will not happen, or already did. Terminal.
TERMINAL_STATUSES: Final[frozenset[str]] = frozenset({"cancelled", "completed", "no_show"})
