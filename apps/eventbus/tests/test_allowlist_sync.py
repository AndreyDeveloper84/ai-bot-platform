"""Guard: the two event-name allowlists must not drift apart.

There are TWO independent allowlists on the ingest path, at different
layers:

* ``ingest_envelope.ALLOWED_EVENT_NAMES`` — the *parse* layer. A name
  absent here is rejected at envelope parsing with HTTP 400
  (``invalid_event_name``) before dispatch is ever reached.
* ``ingest_dispatcher._KNOWN_NAMES`` — the *dispatch* layer
  (``event-contract.md`` §3 closed taxonomy). A name absent here is
  rejected at dispatch with the ``unknown_event_name`` outcome (DLQ +
  422).

Because the parse layer runs first, the two sets being out of sync is a
silent footgun (this bit us once during the C6 smoke for
``booking.confirmed``):

* name in ``_KNOWN_NAMES`` but NOT ``ALLOWED_EVENT_NAMES`` → dead entry,
  unreachable at dispatch (parse rejects it first with 400);
* name in ``ALLOWED_EVENT_NAMES`` but NOT ``_KNOWN_NAMES`` → parses fine,
  then dispatch surprises the operator with ``unknown_event_name`` (422).

For the MVP event set the two allowlists MUST be identical. This test
pins that invariant so adding an event name to one file but not the
other fails CI loudly instead of leaking to runtime as a 400/422 mix-up.
"""

from __future__ import annotations

from apps.eventbus.ingest_dispatcher import _KNOWN_NAMES, registered_handlers
from apps.eventbus.ingest_envelope import ALLOWED_EVENT_NAMES


def test_parse_and_dispatch_allowlists_are_identical() -> None:
    only_in_parse = ALLOWED_EVENT_NAMES - _KNOWN_NAMES
    only_in_dispatch = _KNOWN_NAMES - ALLOWED_EVENT_NAMES
    assert ALLOWED_EVENT_NAMES == _KNOWN_NAMES, (
        "Event-name allowlists drifted apart — keep "
        "ingest_envelope.ALLOWED_EVENT_NAMES and "
        "ingest_dispatcher._KNOWN_NAMES in sync.\n"
        f"  In ALLOWED_EVENT_NAMES only (parses but dispatch → 422 "
        f"unknown_event_name): {sorted(only_in_parse)}\n"
        f"  In _KNOWN_NAMES only (dead — parse rejects with 400 first): "
        f"{sorted(only_in_dispatch)}"
    )


def test_every_registered_handler_name_is_allowlisted() -> None:
    # The registry is populated by EventbusConfig.ready() at app load
    # (register_*_handlers for booking/payment/catalog/identity/
    # schedule/reviews). A handler bound to a name missing from the
    # allowlists can never fire — the request is rejected upstream.
    handler_names = {name for (name, _version) in registered_handlers()}
    assert handler_names, (
        "No handlers registered — EventbusConfig.ready() did not run; "
        "this guard would be vacuously true. Investigate app loading."
    )
    missing = handler_names - (ALLOWED_EVENT_NAMES & _KNOWN_NAMES)
    assert not missing, (
        "Registered event handler(s) for name(s) absent from one or both "
        f"allowlists — they can never fire: {sorted(missing)}. Add the "
        "name to ingest_envelope.ALLOWED_EVENT_NAMES and "
        "ingest_dispatcher._KNOWN_NAMES."
    )
