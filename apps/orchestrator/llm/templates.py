"""Static fallback templates for degraded concierge turns (DRF-428 / D1).

Sprint 1 shipped ONE line here — the «отвечу через минуту» promise — and
every degraded return in the orchestrator reached for it. DRF-1489 split
that: a line that promises the bot will come back may only be sent on a
turn where something actually comes back.

**The contract this module now carries.**

``OUTAGE_RU`` / ``OUTAGE_EN`` promise a return. A reply carrying that text
MUST also carry ``DiscoveryReply.outage=True``, because that flag is what
makes the channel draw the «AI недоступна» screen with «Повторить» — the
button that keeps the promise by re-sending the person's own words
(``apps/channels/max/handler.py``, макет C01). Without the flag the text
is a lie: the turn is over, nobody returns to it, and the person waits out
a minute that was never going to end in an answer.

Every OTHER degraded return — the model answered, it just answered
unusably — gets one of the promise-free lines below. They say what
happened and, where the missing datum is known, ask for it. None of them
says the bot will do anything on its own.

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


NO_ANSWER_RU = "Не смогла собрать ответ на это. Спроси, пожалуйста, другими словами."
"""The model answered, but the answer was unusable (DRF-1489).

A blank clarification, a tool name we do not serve, malformed arguments,
a skill that declined. The turn WAS taken — offering «Повторить» here
would be lying a second way — so the line neither promises nor apologises
for an outage that is not happening.
"""

NO_ANSWER_EN = "I couldn't put an answer together for that. Could you ask it another way?"


BOOKING_NEEDS_NAME_RU = "К кому записать? Назови имя мастера или услугу — найду и запишу."
"""``start_booking`` arrived naming nobody (DRF-1489).

The missing datum is known exactly, so this asks for THAT instead of
apologising in general. Not an outage: the model was reached and replied.
"""

BOOKING_NEEDS_NAME_EN = "Who should I book you with? Name the master or the service."


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
    """Promise-free line for «the model answered, but unusably»."""

    return NO_ANSWER_EN if lang == "en" else NO_ANSWER_RU


def get_booking_needs_name(lang: str = "ru") -> str:
    """Promise-free line for «start_booking named nobody»."""

    return BOOKING_NEEDS_NAME_EN if lang == "en" else BOOKING_NEEDS_NAME_RU
