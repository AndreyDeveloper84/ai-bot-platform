"""Booking domain — BookingRequest + BookingReminder models.

Phase 1 / B2 (DRF-838). Bundled with the YClients admin webhook port
because the webhook must WRITE rows into these tables; landing the
webhook before the models exist would leave the port half-wired.

This package owns ONLY the data model + admin in this PR. The booking
**skill** (LLM-callable tool layer that creates rows from a client-
side chat flow) lands in B3 / DRF-839 and consumes — but does not
modify — these models.
"""
