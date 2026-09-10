"""Ayla's health-check refusal on booking — one contract for three surfaces.

Ayla answers **HTTP 422** with one of three machine codes when a service
may not be booked without a screening question first. The refusal is a
medical decision taken deliberately upstream; it is not a validation
error, not a race, and above all not a broken server.

Three surfaces in this repo create bookings and each used to render this
refusal its own way (DRF-1614):

* the Mini App client surface — ``apps/miniapp_api/views.py``
* the conversational channel — ``apps/skills/booking/``
* the salon administrator surface — ``apps/admin_api/views_booking_create.py``

The codes live here, in one module, rather than next to each surface,
because the defect this ticket fixes IS divergence: three readings of one
refusal. A constant copied three times is three constants.

# The codes

``HEALTH_CHECK_REQUIRED``
    The service is marked as needing a screening question. Known answer.

``HEALTH_CHECK_UNKNOWN``
    Nobody has annotated the service yet, so we do not know. Fail closed:
    an unannotated service is handed to a human, not booked.

``HEALTH_CHECK_NOT_APPLICABLE``
    The legacy ``Service`` layer, where the question cannot be answered at
    all: the column an answer would live in does not exist in that model
    (§100.A). Not «unknown» — **inapplicable**, and the owner gave that
    state its own name precisely so it would stop being filed under the
    other one. Answering ``False`` there is forbidden outright: a layer
    holding no health information would be asserting «no screening
    needed», which is the fiction this whole ticket removes.

# Why 422 and not a 2xx with a polite body

Tempting, and it breaks on the consumer we have not updated yet: a client
that reads 2xx as success shows «вы записаны» over a response that has no
booking in it. On a 4xx an un-updated surface degrades into «an error» —
wrong tone, but **no false promise**. Of the two degradations the owner
took the safe one (§100.B). Creation answers 201; this outcome never
answers 2xx.

# One name outwards, two counters inwards

``REQUIRED`` and ``UNKNOWN`` produce the **same sentence** for the person:
the difference between «we know you need to be asked» and «we have not
annotated this service» is ours, not theirs, and showing it would explain
our bookkeeping to somebody who came to book a haircut.

The log MUST keep them apart. The service-annotation queue is prioritised
by how many ``UNKNOWN`` refusals a service produced; merged into
``REQUIRED`` that counter loses its criterion, and the loss shows up weeks
later as a queue in the wrong order with no visible cause.

So: one outward name, two inward counters. Never the reverse.

# NOT_APPLICABLE promises nothing

The other two say «we will pass this to a specialist», because somebody
will. ``NOT_APPLICABLE`` must not say it, because on that path nobody is
assigned. A promise of a consultation that no one will hold is the same
family of defect as a link to a screen that does not exist: a refusal is
understood and acted on, a non-existent door is searched for.

# Never «you are booked»

No branch here yields a booking id or a confirming sentence. That is not
a matter of tone but a checkable property, and it is pinned by contract
tests rather than left to the next person editing the copy.
"""

from __future__ import annotations

from typing import Final

HEALTH_CHECK_REQUIRED: Final = "HEALTH_CHECK_REQUIRED"
HEALTH_CHECK_UNKNOWN: Final = "HEALTH_CHECK_UNKNOWN"
HEALTH_CHECK_NOT_APPLICABLE: Final = "HEALTH_CHECK_NOT_APPLICABLE"

#: Every code that makes a 422 a health-check refusal. A 422 whose code is
#: NOT in here keeps its previous meaning ("the booking's own state forbids
#: this") — we do not guess a medical refusal from an unknown code.
HEALTH_CHECK_CODES: Final = frozenset(
    {
        HEALTH_CHECK_REQUIRED,
        HEALTH_CHECK_UNKNOWN,
        HEALTH_CHECK_NOT_APPLICABLE,
    }
)

#: Codes that hand the request to a human who will actually ask the
#: questions. Kept as its own set, not as "everything except
#: NOT_APPLICABLE": a fourth code added upstream must arrive here as a
#: decision, not inherit a promise by falling on the wrong side of a `!=`.
PROMISES_A_SPECIALIST: Final = frozenset({HEALTH_CHECK_REQUIRED, HEALTH_CHECK_UNKNOWN})

#: Owner's wording, verbatim (§98). Do not edit for style: it is the
#: sentence the owner approved, and §98 requires the SAME sentence on all
#: three surfaces — Mini App, admin console and the internal REST path.
HANDOFF_TEXT: Final = (
    "Перед записью нужно уточнить несколько вопросов. Передадим запрос специалисту"
)

#: The refusal that promises nothing (§100.A). Capitalised from the
#: owner's mid-sentence quote «запись этим способом сейчас недоступна»;
#: the words are theirs, the capital is ours.
NOT_APPLICABLE_TEXT: Final = "Запись этим способом сейчас недоступна"

#: What the surface reports outwards. `REQUIRED` and `UNKNOWN` share one
#: name deliberately (see module docstring); `NOT_APPLICABLE` gets its own
#: because its sentence differs and a screen must be able to tell them
#: apart without reading prose.
OUTWARD_HANDOFF: Final = "health_check_handoff"
OUTWARD_UNAVAILABLE: Final = "health_check_unavailable"


def is_health_check_code(code: str | None) -> bool:
    """True when this Ayla error code means «screening first»."""

    return (code or "") in HEALTH_CHECK_CODES


def promises_a_specialist(code: str | None) -> bool:
    """True when the person may be told somebody will get back to them."""

    return (code or "") in PROMISES_A_SPECIALIST


def text_for(code: str | None) -> str:
    """The sentence shown to the person for this refusal code.

    Anything that is not a known promising code gets the sentence that
    promises nothing. Failing that way round is deliberate: a refusal
    heard as final costs a person one attempt, a promise nobody keeps
    costs them a wait.
    """

    return HANDOFF_TEXT if promises_a_specialist(code) else NOT_APPLICABLE_TEXT


def outward_code(code: str | None) -> str:
    """The name this refusal travels under on an API surface."""

    return OUTWARD_HANDOFF if promises_a_specialist(code) else OUTWARD_UNAVAILABLE


__all__ = [
    "HANDOFF_TEXT",
    "HEALTH_CHECK_CODES",
    "HEALTH_CHECK_NOT_APPLICABLE",
    "HEALTH_CHECK_REQUIRED",
    "HEALTH_CHECK_UNKNOWN",
    "NOT_APPLICABLE_TEXT",
    "OUTWARD_HANDOFF",
    "OUTWARD_UNAVAILABLE",
    "PROMISES_A_SPECIALIST",
    "is_health_check_code",
    "outward_code",
    "promises_a_specialist",
    "text_for",
]
