"""Static fallback templates for degraded concierge turns (DRF-428 / D1).

Sprint 1 shipped ONE line here — the «отвечу через минуту» promise — and
every degraded return in the orchestrator reached for it. DRF-1489 split
that: a line that promises the bot will come back may only be sent on a
turn where something actually comes back.

**The contract this module now carries.**

The lines here split in two by ONE question: **does a «Повторить» button
appear under this text?**

``OUTAGE_RU`` / ``OUTAGE_EN`` and ``NO_ANSWER_RETRY_*`` are the
button-bearing half. The first promises a return, the second asks
«Попробовать ещё раз?»; a text may only offer either where the offer can
be taken. A reply carrying one of them MUST therefore also carry
``DiscoveryReply.outage=True`` — that flag is what makes the channel draw
the «AI недоступна» screen with «Повторить», the button that re-sends the
person's own words (``apps/channels/max/handler.py``, макет C01). Without
the flag the text is a lie: the turn is over, nobody returns to it, and
the person waits out a minute that was never going to end in an answer.
The converse holds too, and a test pins both directions.

Every OTHER degraded return — the model answered, it just answered
unusably — gets one of the button-free lines below. They neither promise
nor ask, and none of them blames the person for how they phrased it: on
those branches the cause is inside the platform, and a line that sends
someone away to «спросить другими словами» would move our failure onto
them (owner's ruling on DRF-1489).

When ``ayla-ai-core`` is importable (per review revision 5C), this module
gets a thin import wrapper:

    from ayla_ai_core.voice import get_fallback_template
    OUTAGE_RU = get_fallback_template("outage_ru")

For now, hardcoded strings keep CI green without the extra dep. (The
original note here said this waited on ``GH_DEPLOY_TOKEN``; ayla-ai-core
is a public repo and needs no token — DRF-1466.)
"""

from __future__ import annotations

OUTAGE_RU = "Извини, у меня сейчас короткий технический сбой — отвечу через минуту."
"""Russian outage line. PROMISES A RETURN — only for ``outage=True`` replies."""


OUTAGE_EN = "Sorry, I'm having a brief technical issue — I'll be back in a moment."
"""English outage line. Same rule: only where «Повторить» is drawn."""


NOT_PARSED_RU = "Не разобрала эту фразу. Напиши её иначе — что и сколько, обычными словами."
"""A skill's parser refused the phrase the model handed it (DRF-1489).

Nothing is broken and nothing is coming: the same words re-sent produce
the same tool call and the same refusal. So the line asks for different
words instead of promising a retry that would loop.
"""

NOT_PARSED_EN = "I couldn't read that phrase. Say it differently — what and how much, plainly."


NO_ANSWER_RU = (
    "Не получилось подготовить ответ на этот запрос. "
    "Можешь задать другой вопрос или попробовать позже."
)
"""The model answered, but the answer was unusable (DRF-1489).

A blank clarification, a tool name we do not serve, malformed arguments,
a skill that declined. Four cases, three call sites — the blank-question
branch is reached both by a genuine empty ``ask_clarification`` and by
``_dispatch_tool``'s internal degrade.

The turn WAS taken, so there is no «Повторить» here — offering it would be
lying a second way. Two things this line deliberately does NOT do:

* **It does not blame the person.** The first draft said «спроси, пожалуйста,
  другими словами», and the owner rejected it: the cause is inside the
  platform, and asking someone to rephrase a perfectly good question moves
  our failure onto them.
* **It does not promise.** «Можешь … попробовать позже» is an option left
  open to the person, not a commitment that the bot will come back — the
  distinction this whole ticket exists to keep.

It has to stay honest on all four cases, including the three that are
unreachable today (see the call sites in
:mod:`apps.orchestrator.concierge`): each of them really is «we could not
prepare an answer», on none of them is the person at fault, and on none of
them does the sentence claim the bot will act by itself.
"""

NO_ANSWER_EN = (
    "I couldn't prepare an answer to this request. You can ask something else or try later."
)


NO_ANSWER_RETRY_RU = "Не получилось подготовить ответ. Попробовать ещё раз?"
"""The model was called and no answer came out of the turn (DRF-1489).

Not a refusal by the rules, not the person's mistake — the attempt simply
did not happen. So this one, alone among the degraded lines, ASKS: and it
may ask only because the reply carrying it sets ``outage=True``, which is
what puts «Повторить» under it. Text and button make the same offer — the
principle the whole ticket is about.

Never use it on a reply without that flag.

**Today the person does not read these words in MAX.** The channel
substitutes its own C01 screen text for every ``outage=True`` reply
(``handler.py`` → ``AI_UNAVAILABLE_TEXT``), so this line is what the
concierge SAYS while the screen still shows «временные трудности с
подключением». Closing that gap is a one-line change in the handler, which
DRF-1489 was not allowed to touch.
"""

NO_ANSWER_RETRY_EN = "I couldn't prepare an answer. Try again?"


BOOKING_NEEDS_NAME_RU = "К кому записать? Назови имя мастера или услугу — найду и запишу."
"""``start_booking`` arrived naming nobody (DRF-1489).

The missing datum is known exactly, so this asks for THAT instead of
apologising in general. Not an outage: the model was reached and replied.
"""

BOOKING_NEEDS_NAME_EN = "Who should I book you with? Name the master or the service."


#: The getters whose text offers something — a return, or another try.
#: A reply carrying one of these MUST set ``DiscoveryReply.outage=True``,
#: and a reply with that flag MUST carry one. Named here so the test that
#: pins both directions reads the list from the module that owns it.
BUTTON_BEARING_GETTERS = frozenset({"get_fallback", "get_no_answer_retry"})


def get_fallback(lang: str = "ru") -> str:
    """The outage line — ONLY for replies that carry ``outage=True``.

    Sprint 1 supports ru/en only. Sprint 4+ pulls per-tenant brand voice
    via ``apps.voice`` and ``ayla-ai-core``.
    """

    return OUTAGE_EN if lang == "en" else OUTAGE_RU


def get_not_parsed(lang: str = "ru") -> str:
    """Promise-free line for «the skill's parser refused this phrase»."""

    return NOT_PARSED_EN if lang == "en" else NOT_PARSED_RU


def get_no_answer(lang: str = "ru") -> str:
    """Button-free line for «the model answered, but unusably»."""

    return NO_ANSWER_EN if lang == "en" else NO_ANSWER_RU


def get_no_answer_retry(lang: str = "ru") -> str:
    """The asking line — ONLY for replies that carry ``outage=True``."""

    return NO_ANSWER_RETRY_EN if lang == "en" else NO_ANSWER_RETRY_RU


def get_booking_needs_name(lang: str = "ru") -> str:
    """Promise-free line for «start_booking named nobody»."""

    return BOOKING_NEEDS_NAME_EN if lang == "en" else BOOKING_NEEDS_NAME_RU
