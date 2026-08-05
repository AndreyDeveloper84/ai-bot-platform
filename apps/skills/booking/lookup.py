"""Personal booking-lookup detector (E2E-BOT-02A).

Boundary the production dispatcher needs on the no-intent fallback
path (channel webhook handlers dispatch with ``SkillContext.intent``
unset):

  * **IN** — a possessive / first-person reference to the caller's own
    booking(s) combined with a lookup/time/list question, where the
    booking noun stands BARE (a standalone word, no domain-shifting
    complement)::

        "Когда у меня следующая запись?"
        "Покажи мои записи"
        "На когда я записан?"

  * **OUT** — FAQ about the booking *process*, which carries no
    personal reference::

        "Как записаться?"
        "Как работает запись?"
        "Можно ли перенести запись?"   (informational, not a request
                                        to change a specific booking)

  * **OUT** — mutation/change requests, even with a personal
    reference — these belong to the LLM tool-choice path
    (cancel/reschedule/confirm), never to the read-only lookup::

        "Перенеси мою запись"
        "Отмени мою запись"
        "Можно поменять мою запись?"

  * **OUT** — "запись" outside the salon-booking domain. Rejected
    SEMANTICALLY, not by vocabulary blacklist (review round 3):
    compounds fail the standalone-word test ("аудиозапись",
    "звукозапись"), and a non-allow-listed complement after the noun
    re-scopes it away from an appointment ("запись вебинара", "записи
    с диктофона", "запись в реестре", "запись к врачу", "записи камер
    наблюдения", "запись в трудовой книжке", "записи в дневнике").
    A bare "запись" — optionally closed by a CLOSED allow-listed
    temporal/salon complement ("на завтра", "на 15 августа",
    "в салоне", "к мастеру" — no open "на <word>" branch, review
    round 5), a politeness marker or trailing non-word characters —
    is the lookup form (review round 4).

Consumers:

  * :meth:`apps.skills.booking.skill.BookingSkill.matches` — claims
    these turns on the keyword-fallback path (the literal "запись"
    keyword misses "мои записи" / "я записан").
  * :meth:`apps.skills.faq.skill.FAQSkill.matches` — yields them (the
    generic question signals "когда" / "?" would otherwise intercept).
  * :meth:`apps.skills.booking.skill.BookingSkill.handle` — selects
    the read-only ``show_my_bookings`` tool deterministically.

The module also hosts two sibling pure-text detectors used by the
booking skill's D-10 flow continuation: :func:`booking_mutation_flow`
(tags the continuation state with the requested verb) and
:func:`looks_like_flow_selection` (bounds which follow-up turns the
continuation may claim).

Pure text processing, no Django imports — safe to import from any
skill module at registration time.
"""

from __future__ import annotations

import re

# Mutation/change verbs. A personal reference plus one of these is a
# request to CHANGE a booking (or create one), not to look at it.
# "перенес" covers "перенести" (FAQ "можно ли перенести" is
# additionally excluded by the missing personal reference); "поменя" /
# "замен" cover change-requests like "можно поменять мою запись?".
_MUTATION_SIGNAL = re.compile(
    r"перенес|отмен|запиши|забронир|поменя|замен|измени|сдвин|удал",
    re.IGNORECASE,
)

# D-10 — split mutation signals by target flow so the booking skill can
# tag the continuation state (``skill_state["booking_flow"]["flow"]``)
# with the verb the user actually asked for. Union equals the relevant
# subset of _MUTATION_SIGNAL (create-verbs «запиши/забронир» are not
# continuation flows — they start a fresh booking instead).
_RESCHEDULE_SIGNAL = re.compile(
    r"перенес|поменя|замен|измени|сдвин",
    re.IGNORECASE,
)
_CANCEL_SIGNAL = re.compile(
    r"отмен|удал",
    re.IGNORECASE,
)

# Lookup / time / list question signals. NOTE: a bare "?" is NOT a
# signal — review P1 showed it makes any possessive "запись" question
# ("мне нравится моя запись?", "можно поменять мою запись?") qualify.
# The signal must be an explicit lookup/time/list word.
_LOOKUP_SIGNAL = re.compile(
    r"когда|во сколько|на когда|какая|какие|какой|какую|"
    r"покажи|показать|посмотреть|список|\bесть\b",
    re.IGNORECASE,
)

# Complements AFTER the booking noun that do NOT re-scope the domain —
# a CLOSED list (review round 5 P1: the open "на <word>" branch
# re-opened the recording-device domain — "записи на диктофоне"
# qualified while "записи с диктофона" was OUT; "на маникюр" is not
# lexically distinguishable from "на вебинар", so services are not
# recognized at all): temporal words, days of week, digit dates with a
# CLOSED month list ("на 15 августа" IN, "на 4 диктофона" OUT — review
# round 6) of EXACT genitive forms with no wildcard tail — a month-root
# prefix is not a date ("на 3 майки", "на 2 мартышки" OUT — review
# round 7), week scope (incl. "прошлой" — a past-bookings
# question is still a personal lookup), salon scope ("в салоне",
# "к мастеру"). A genitive noun ("запись вебинара") and any other
# complement ("к врачу", "с диктофона", "в реестре", "на диктофоне")
# are NOT in the list and still reject.
_ALLOWED_COMPLEMENT = (
    r"на\s+(?:завтра|сегодня|послезавтра"
    r"|(?:этой|следующей|прошлой)\s+неделе"
    r"|понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье"
    r"|\d{1,2}(?:\s+(?:января|февраля|марта|апреля|мая|июня|июля"
    r"|августа|сентября|октября|ноября|декабря))?)"
    r"|в\s+салоне"
    r"|к\s+мастеру"
)

# The booking noun as a STANDALONE word, closed only by an allow-listed
# complement / politeness marker / trailing non-word characters. This
# is the positive semantic test that replaced the substring "запис"
# root check (review round 3) and the strict phrase-final anchor
# (review round 4):
#
#   * word-initial ``\b`` rejects compounds — "аудиозапись" and
#     "звукозапись" have no word boundary before "запис";
#   * the closed allow-listed complement keeps "записи на этой
#     неделе" / "записи на 15 августа" / "запись на завтра в салоне"
#     IN while a re-scoped noun stays OUT — in "запись вебинара" /
#     "записи с диктофона" / "записи на диктофоне" / "запись к врачу"
#     the complement shifts "запись" out of the appointment domain;
#   * ``[^\w]*$`` strips any trailing non-word characters (",", "!!",
#     emoji) without enumerating punctuation;
#   * the suffix alternation rejects verb forms — "записаться",
#     "записываться" never match a noun declension.
_BARE_BOOKING_WORD = re.compile(
    r"\bзапис(?:ь|и|ей|ью|ам|ям|ями|ях|ан|ана|аны)\b"
    r"(?:\s+(?:" + _ALLOWED_COMPLEMENT + r")){0,2}"
    r"(?:\s*,?\s*пожалуйста)?"
    r"[^\w]*$",
    re.IGNORECASE,
)

# Possessive pronoun directly qualifying a "запис…" word:
# "мои записи", "свою запись", "моих записей".
_POSSESSIVE_BOOKING = re.compile(
    r"\b(мои|моя|моё|мою|моих|моей|свои|своя|своё|свою|своих|своей)\s+запис",
    re.IGNORECASE,
)

# "у меня" — personal scope marker ("Когда у меня запись?",
# "У меня есть запись?").
_AT_ME = re.compile(r"у меня", re.IGNORECASE)

# First-person booked state: "я записан", "я записана".
_I_AM_BOOKED = re.compile(r"\bя\s+записан", re.IGNORECASE)


def is_personal_booking_lookup(text: str) -> bool:
    """True when ``text`` asks about the caller's own existing bookings.

    Requires a BARE standalone booking noun (closed only by an
    allow-listed temporal/salon complement, a politeness marker or
    trailing non-word characters), a personal reference, and an
    explicit lookup/time/list question word (a bare "?" does NOT
    qualify); excluded when a mutation/change verb is present. Internal
    whitespace is normalized first, so "Когда   у   меня   следующая
    запись?" (or tabs) routes identically. Conservative by design — a
    False only means "not provably a personal lookup", the turn keeps
    its previous routing.
    """

    normalized = " ".join((text or "").lower().split())
    if not normalized:
        return False
    if _MUTATION_SIGNAL.search(normalized):
        return False
    if not _BARE_BOOKING_WORD.search(normalized):
        return False
    if not _LOOKUP_SIGNAL.search(normalized):
        return False
    return bool(
        _POSSESSIVE_BOOKING.search(normalized)
        or _AT_ME.search(normalized)
        or _I_AM_BOOKED.search(normalized)
    )


def booking_mutation_flow(text: str) -> str | None:
    """Classify a mutation request as ``"reschedule"`` / ``"cancel"`` / None.

    D-10 — used by the booking skill to tag the continuation state
    (``skill_state["booking_flow"]``) when a mutation-request turn ends
    with a bookings listing (disambiguation) instead of a tool preview.
    Pure text processing, same module as the lookup detector so the two
    never drift apart.
    """
    normalized = " ".join((text or "").lower().split())
    if not normalized:
        return None
    if _RESCHEDULE_SIGNAL.search(normalized):
        return "reschedule"
    if _CANCEL_SIGNAL.search(normalized):
        return "cancel"
    return None


# D-10 review (Wave-1 follow-up, finding #2) — selection-shaped turn
# detector bounding the booking-flow continuation claim. While
# ``skill_state["booking_flow"]`` is fresh the booking skill claims
# follow-up turns; the original UNRESTRICTED claim swallowed any text
# for the full 10-minute TTL («спасибо», a fresh «Хочу маникюр»
# request) into the flow — mis-routing new requests and costing two
# LLM calls per off-topic turn. The claim is now bounded to turns that
# look like a disambiguation answer:
#
#   * ordinal pick — «первую», «вторая», «последнюю»;
#   * bare position number — «2» (whole message);
#   * time — «20:00», «в 8 вечера», spaced «20 00» (review round 3 —
#     channel users type «20 00» without the colon and the turn fell
#     through to echo while the flow was alive);
#   * daypart — «утром», «вечером», «попозже», «в обед» (review round 2 —
#     the most frequent answers to «во сколько?» fell through to echo);
#   * date — «9 августа», «на завтра», «в пятницу».
#
# Anything else keeps its previous routing; the flow state stays fresh
# until its TTL or the next selection-shaped turn.
_SELECTION_ORDINAL = re.compile(
    r"\b(перв(?:ая|ой|ого|ое|ый|ую|ым|ом)"
    r"|втор(?:ая|ой|ого|ое|ую|ым|ом)"
    r"|треть(?:я|ей|его|е|ий|ью|им|ем)"
    r"|последн(?:яя|ей|его|ее|ий|юю|им|ем))\b",
    re.IGNORECASE,
)
_SELECTION_NUMBER_ONLY = re.compile(r"\d{1,2}")
_SELECTION_TIME = re.compile(r"\b\d{1,2}[:.]\d{2}\b")
# Spaced time «20 00» — hour bounded to 0-23 and minute to [0-5]\d so
# phone fragments («8 999 123 45 67») and ages («мне 30 лет») do not
# match; deliberately NOT the wider «\d{1,2}\s\d{2}» form.
_SELECTION_SPACED_TIME = re.compile(r"\b([01]?\d|2[0-3])\s[0-5]\d\b")
_SELECTION_WORD_TIME = re.compile(
    r"\b\d{1,2}\s*(?:утра|вечера|дня|ночи)\b",
    re.IGNORECASE,
)
# Daypart / relative-time answers to «во сколько?» — no digits at all.
_SELECTION_DAYPART = re.compile(
    r"\b(?:утром|днём|днем|вечером|ночью|попозже|пораньше|в\s+обед)\b",
    re.IGNORECASE,
)
_SELECTION_DATE = re.compile(
    r"\b\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|"
    r"сентября|октября|ноября|декабря)\b"
    r"|\b(?:сегодня|завтра|послезавтра|понедельник\w*|вторник\w*|сред[ауы]|"
    r"четверг\w*|пятниц[ауы]|суббот[ауы]|воскресен\w*)\b",
    re.IGNORECASE,
)


def looks_like_flow_selection(text: str) -> bool:
    """True when ``text`` looks like a flow-continuation selection answer.

    Conservative by design — a False only means "not provably a
    selection", the turn keeps its previous routing and the flow state
    survives for the next selection-shaped turn.
    """
    normalized = " ".join((text or "").lower().split())
    if not normalized:
        return False
    if _SELECTION_NUMBER_ONLY.fullmatch(normalized):
        return True
    return bool(
        _SELECTION_ORDINAL.search(normalized)
        or _SELECTION_TIME.search(normalized)
        or _SELECTION_SPACED_TIME.search(normalized)
        or _SELECTION_WORD_TIME.search(normalized)
        or _SELECTION_DAYPART.search(normalized)
        or _SELECTION_DATE.search(normalized)
    )
