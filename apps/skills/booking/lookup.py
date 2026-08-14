"""Personal booking-lookup detector (E2E-BOT-02A).

Boundary the production dispatcher needs on the no-intent fallback
path (channel webhook handlers dispatch with ``SkillContext.intent``
unset):

  * **IN** — a possessive / first-person reference to the caller's own
    booking(s) (or a bare "покажи …" request addressed to the bot)
    combined with a lookup/time/list question, where the booking noun
    stands BARE (a standalone word, no domain-shifting complement)::

        "Когда у меня следующая запись?"
        "Покажи мои записи"
        "Мои записи покажи"       (reverse word order — DRF-1055)
        "мои записи"              (no verb at all — DRF-1055)
        "покажи записи"           (no possessive — DRF-1055)
        "мои визиты" / "мои брони"  (synonyms — DRF-1055)
        "что у меня записано"     (DRF-1055)
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

  * **OUT** — the intent to CREATE a booking. A false positive here is
    worse than a miss (DRF-1055 §2): the person who wants an
    appointment gets a list of the ones they already have and a dead
    end::

        "хочу записаться"
        "запиши меня"
        "записаться на маникюр"
        "можно записаться?"
        "покажи свободные записи"   (availability, not own bookings)

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
    round 5), a CLOSED safe-tail word that cannot be a complement at
    all (a show-verb, "у меня", "есть", a politeness marker —
    DRF-1055) or trailing non-word characters — is the lookup form
    (review round 4). The same guard applies to the "визит" / "бронь"
    synonyms: "визиты вебинара" and "брони в отеле" reject exactly as
    "записи с диктофона" does.

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


def _normalize(text: str) -> str:
    """Lower-case, fold «ё»→«е», collapse internal whitespace.

    Same contract as the two other Russian matchers in this codebase
    (``apps.skills.menu.matching.normalize``,
    ``apps.persona.memory_commands._normalise``) minus the punctuation
    stripping — this module's matchers use trailing punctuation
    (``[^\\w]*$``) as an explicit end-of-phrase marker, so punctuation
    must survive normalisation. ё-folding means every pattern below is
    written in its «е» form only; a pattern containing «ё» could never
    match.
    """

    return " ".join((text or "").lower().replace("ё", "е").split())


# Mutation/change verbs. A personal reference plus one of these is a
# request to CHANGE a booking (or create one), not to look at it.
# "перенес" covers "перенести" (FAQ "можно ли перенести" is
# additionally excluded by the missing personal reference); "поменя" /
# "замен" cover change-requests like "можно поменять мою запись?".
_MUTATION_SIGNAL = re.compile(
    r"перенес|отмен|запиши|забронир|поменя|замен|измени|сдвин|удал",
    re.IGNORECASE,
)

# DRF-1055 — the create-intent negation window, kept EXPLICIT and
# separate from the mutation window above because the two protect
# different things and the brief calls this one out by name. Widening
# the detector (§ below: synonyms, reverse word order, verb-less and
# possessive-less forms) increases the surface where a "I want to
# book" turn could be mistaken for "show me my bookings"; that
# mistake is the worst outcome in the product (a person who wants an
# appointment gets their existing list and no way forward), so the
# create verbs are rejected before any positive test runs.
#
#   * "записать" is a prefix of "записаться" — one root covers
#     «хочу записаться» / «можно записаться?» / «записаться на
#     маникюр» / «запишите меня» is covered by _MUTATION_SIGNAL's
#     «запиши»;
#   * "записыва" covers «записываться» / «сколько заранее нужно
#     записываться»;
#   * "свободн" covers the availability question — «покажи свободные
#     записи на завтра» reads as a lookup by shape («покажи» +
#     bare booking noun + temporal complement) but asks for the
#     salon's free slots, not the caller's bookings.
#
# NOTE the roots deliberately do NOT match the noun forms the detector
# needs: «записан» / «записано» / «записи» are not prefixed by
# "записать"/"записыва".
_CREATE_INTENT_SIGNAL = re.compile(
    r"записать|записыва|свободн",
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
#
# DRF-1055 — the show-verbs became ROOTS instead of three exact forms:
# "пока[жз]" covers покажи / покажите / показать / показывай,
# "посмотр" covers посмотреть / посмотри / посмотрю / посмотрим,
# "глян" covers глянуть / глянь. "\bчто\b" carries «что у меня
# записано»; it is a broad word, which is why it only ever qualifies
# in conjunction with a bare booking noun AND a personal reference.
_LOOKUP_SIGNAL = re.compile(
    r"когда|во сколько|на когда|какая|какие|какой|какую|\bчто\b|"
    r"пока[жз]|посмотр|глян|список|\bесть\b",
    re.IGNORECASE,
)

# DRF-1055 — the booking noun family. Three synonyms the pilot's users
# actually typed, each an EXPLICIT declension alternation rather than a
# "запис" / "визит" / "брон" prefix: a prefix also swallows the
# create-intent verb («записаться»), the compound noun («визитка») and
# the process noun («бронирование» is fine, «бронировать» is not).
#
# Morphology covered (§4 of the brief): «запись / записи / записей /
# записью / записям / записями / записях», the short participles
# «записан / записана / записано / записаны», «визит / визита /
# визиты / визитов / визитам / визитами / визитах / визите»,
# «бронь / брони / броней / бронью / броням / бронями / бронях» and
# «бронирование / бронирования / бронированию / бронированиях».
_BOOKING_NOUN = (
    r"запис(?:ь|и|ей|ью|ам|ям|ями|ях|ан|ана|ано|аны)"
    r"|визит(?:ы|а|ов|ам|ами|ах|е|у)?"
    r"|брон(?:ь|и|ей|ью|ям|ями|ях)"
    r"|бронирован(?:ие|ия|ий|ию|иям|иями|иях)"
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

# DRF-1055 — words that may follow the booking noun WITHOUT re-scoping
# it out of the appointment domain. This is the half of the matcher
# that the owner's own phrasing («Мои записи покажи») hit: the domain
# guard used to be the phrase-final anchor itself, so ANY word after
# the noun rejected the turn, and reverse word order reads as a
# re-scoped noun.
#
# The guard is now stated directly instead of via position: a noun is
# re-scoped by a COMPLEMENT — a genitive noun («запись вебинара») or a
# prepositional phrase («записи с диктофона», «запись к врачу»). A
# show-verb, a personal marker or a politeness word is none of those —
# it is a separate element of the clause and cannot shift the domain.
# So a closed list of such words may close the phrase too, and the
# phrase-final anchor keeps doing its job for everything else (an
# unknown word after the noun still rejects — the blacklist trap of
# the two previous patches is not re-entered).
_SAFE_TAIL = (
    r"пока[жз]\w*"  # «мои записи покажи» / «покажите»
    r"|посмотр\w*"  # «мои записи посмотреть»
    r"|глян\w*"
    r"|у\s+меня"  # «какие записи у меня есть?»
    r"|есть"
    r"|пожалуйста"
)

# The booking noun as a STANDALONE word, closed only by an allow-listed
# complement / safe tail word / trailing non-word characters. This is
# the positive semantic test that replaced the substring "запис" root
# check (review round 3) and the strict phrase-final anchor (review
# round 4):
#
#   * word-initial ``\b`` rejects compounds — "аудиозапись" and
#     "звукозапись" have no word boundary before "запис";
#   * the closed allow-listed complement keeps "записи на этой
#     неделе" / "записи на 15 августа" / "запись на завтра в салоне"
#     IN while a re-scoped noun stays OUT — in "запись вебинара" /
#     "записи с диктофона" / "записи на диктофоне" / "запись к врачу"
#     the complement shifts "запись" out of the appointment domain;
#   * the closed safe-tail list (DRF-1055) additionally lets a
#     show-verb / personal marker / politeness word close the phrase,
#     so word order stops mattering;
#   * the optional ``,`` before the separator carries «покажи мои
#     записи, пожалуйста»;
#   * ``[^\w]*$`` strips any trailing non-word characters (",", "!!",
#     emoji) without enumerating punctuation;
#   * the suffix alternation rejects verb forms — "записаться",
#     "записываться" never match a noun declension.
_BARE_BOOKING_WORD = re.compile(
    r"\b(?:" + _BOOKING_NOUN + r")\b"
    r"(?:,?\s+(?:" + _ALLOWED_COMPLEMENT + r"|" + _SAFE_TAIL + r")){0,3}"
    r"[^\w]*$",
    re.IGNORECASE,
)

# Possessive pronoun qualifying a booking noun: "мои записи", "свою
# запись", "моих записей", "мои визиты", "мои брони". DRF-1055 allows
# ONE intervening word so an adjective does not break the marker
# («мои ближайшие записи»); the domain guard and the lookup-signal
# requirement still have to pass independently.
_POSSESSIVE_BOOKING = re.compile(
    r"\b(?:мои|моя|мое|мою|моих|моей|моим|моими"
    r"|свои|своя|свое|свою|своих|своей|своим|своими)"
    r"(?:\s+\w+)?\s+(?:" + _BOOKING_NOUN + r")",
    re.IGNORECASE,
)

# DRF-1055 — a bare possessive request IS the whole message: «мои
# записи», «мои визиты», «мои брони». There is no lookup verb to find
# and none is needed; a message that consists of nothing but "my
# bookings" is a request to see them. Deliberately strict (whole-string
# ``fullmatch``, no intervening word, no extra clause) so that the
# lookup-signal requirement keeps rejecting everything it rejects
# today — «Мне нравится моя запись?», «У меня шесть записей».
_BARE_POSSESSIVE_REQUEST = re.compile(
    r"(?:мои|моя|мое|мою|моих|моей|свои|своя|свое|свою|своих|своей)\s+"
    r"(?:" + _BOOKING_NOUN + r")"
    r"(?:,?\s+пожалуйста)?[^\w]*",
    re.IGNORECASE,
)

# DRF-1055 — an imperative addressed to the bot ("покажи записи",
# "список записей") is itself a personal reference: the bot has no
# bookings of its own to show and no third party's to show either, so
# the only referent is the caller. Without this the possessive-less
# half of the pilot's phrasings can never route. The domain guard, the
# mutation window and the create-intent window all still apply, so
# «покажи запись вебинара» / «покажи свободные записи» stay OUT.
_SHOW_REQUEST = re.compile(
    r"пока[жз]|посмотр|глян|\bсписок\b",
    re.IGNORECASE,
)

# "у меня" — personal scope marker ("Когда у меня запись?",
# "У меня есть запись?").
_AT_ME = re.compile(r"у меня", re.IGNORECASE)

# First-person booked state: "я записан", "я записана".
_I_AM_BOOKED = re.compile(r"\bя\s+записан", re.IGNORECASE)


def is_personal_booking_lookup(text: str) -> bool:
    """True when ``text`` asks about the caller's own existing bookings.

    Four independent gates, in order:

    1. no mutation verb and no create-intent verb (the negation
       windows — a create-intent false positive is the worst outcome);
    2. a BARE standalone booking noun — «запись» / «визит» / «бронь»
       and their declensions — not re-scoped by a complement
       («запись вебинара», «записи с диктофона»);
    3. an explicit lookup/time/list question word, OR the whole
       message being a bare possessive request («мои записи») where
       there is no verb to find;
    4. a personal reference — a possessive, «у меня», «я записан», or
       a show-imperative addressed to the bot («покажи записи»).

    Text is normalized first (lower-case, ё→е, whitespace collapsed),
    so "Когда   у   меня   следующая запись?" (or tabs, or capitals)
    routes identically. Conservative by design — a False only means
    "not provably a personal lookup", the turn keeps its previous
    routing.
    """

    normalized = _normalize(text)
    if not normalized:
        return False
    if _MUTATION_SIGNAL.search(normalized):
        return False
    if _CREATE_INTENT_SIGNAL.search(normalized):
        return False
    if not _BARE_BOOKING_WORD.search(normalized):
        return False
    if not (_LOOKUP_SIGNAL.search(normalized) or _BARE_POSSESSIVE_REQUEST.fullmatch(normalized)):
        return False
    return bool(
        _POSSESSIVE_BOOKING.search(normalized)
        or _AT_ME.search(normalized)
        or _I_AM_BOOKED.search(normalized)
        or _SHOW_REQUEST.search(normalized)
    )


def booking_mutation_flow(text: str) -> str | None:
    """Classify a mutation request as ``"reschedule"`` / ``"cancel"`` / None.

    D-10 — used by the booking skill to tag the continuation state
    (``skill_state["booking_flow"]``) when a mutation-request turn ends
    with a bookings listing (disambiguation) instead of a tool preview.
    Pure text processing, same module as the lookup detector so the two
    never drift apart.
    """
    normalized = _normalize(text)
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
# «днём» is spelled «днем» here because _normalize folds ё→е — a
# pattern carrying «ё» could never match (DRF-1055).
_SELECTION_DAYPART = re.compile(
    r"\b(?:утром|днем|вечером|ночью|попозже|пораньше|в\s+обед)\b",
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
    normalized = _normalize(text)
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
