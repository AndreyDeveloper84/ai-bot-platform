"""Booking skill (DRF-839 / Phase 1 / B3).

LLM-tool-use skill that turns chat messages like "запиши меня на массаж"
into real YClients bookings.

Four tools (LLM-callable) wire the user query to YClients HTTP via the
B1 client and to the B2 persistence models:

* :data:`apps.skills.booking.tools.SHOW_MASTERS_TOOL_SPEC` —
  ``show_masters(service_id?, service_name?)`` lists staff who offer
  the requested service.
* :data:`apps.skills.booking.tools.SHOW_SLOTS_TOOL_SPEC` —
  ``show_slots(master_id, date_from?)`` lists available time windows.
* :data:`apps.skills.booking.tools.CONFIRM_BOOKING_TOOL_SPEC` —
  ``confirm_booking(master_id, service_id, slot_datetime, client_phone?)``
  creates a YClients record + a :class:`BookingRequest` row.
* :data:`apps.skills.booking.tools.SHOW_MY_BOOKINGS_TOOL_SPEC` —
  ``show_my_bookings()`` lists the bot_user's upcoming bookings.

Shape mirrors :mod:`apps.skills.faq` — two LLM calls per turn, ≤1 tool
between, sync ``handle()`` bridging via ``asyncio.run`` to the async
provider methods, ``should_handoff`` set on every error path so the
O2 pipeline step 10.5 catches it.
"""
