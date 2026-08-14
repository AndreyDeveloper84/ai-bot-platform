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
        "куда я записан"          (OD-IR1 corpus)
        "к кому я записан"        (OD-IR1 corpus)
        "к кому я записался"      (OD-IR1, nearest relative form)

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

The module also hosts three sibling pure-text detectors:
:func:`booking_mutation_flow` (tags the D-10 continuation state with the
requested verb), :func:`looks_like_flow_selection` (bounds which
follow-up turns the continuation may claim) and — DRF-1060 —
:func:`is_cancel_request`, which recognises the natural refusals people
type instead of «отмени»: «не приду», «не смогу прийти», «снимите
меня», «передумала». That one is a MUTATION predicate, so its risk
profile is the mirror image of the lookup detector's and every gate is
written to reject first; see its own section below.

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

# DRF-1060 — the EXPLICIT cancel verbs. This root pair is all the
# detector knew until 2026-08-14; the natural refusal phrasings below
# («не приду», «не смогу прийти», «передумала») carry no cancel verb at
# all and were invisible. Kept as a separate name because the two halves
# have different guards: an explicit «отмени» states its own object, a
# refusal has to be read from the shape of the clause.
_CANCEL_SIGNAL = re.compile(
    r"отмен|удал",
    re.IGNORECASE,
)


# ─── DRF-1060 — natural cancellation phrasings (OD-IR1) ──────────────
#
# Owner decision OD-IR1 requires a regression corpus of natural
# phrasings for every pilot-critical intent. Cancellation is a MUTATION,
# so the risk profile is the mirror image of the lookup detector's:
# there, a miss costs a list the person does not get; here, a FALSE
# POSITIVE puts a "shall I cancel your visit?" card in front of someone
# who asked about something else. Every gate below is therefore written
# to reject first.
#
# Construction is the one DRF-1055 arrived at after seven review rounds,
# and for the same reasons:
#
#   * CLOSED classes of word forms, never an open ``\w+`` slot — an open
#     slot is exactly what produced the left-re-scoping regression there;
#   * the domain guard stated DIRECTLY (which complements may close the
#     refusal) rather than via phrase position;
#   * negation windows EXPLICIT and running BEFORE any positive test, so
#     a rejection is visible in the code and testable, not a side effect
#     of phrase shape.
#
# One asymmetry vs. DRF-1055 is deliberate. There, the guarded token was
# a domain-AMBIGUOUS NOUN («запись» is equally an appointment and an
# audio file), so the guard had to be symmetric — a modifier on either
# side re-scopes it. Here the guarded token is a FIRST-PERSON VERB
# PHRASE. Its subject is fixed by morphology («не приду» / «не смогу» can
# only be the speaker — «не придёт» / «не сможет» are different forms and
# are not in the class), so the left side cannot re-scope it the way an
# adjective re-scopes a noun. What CAN re-scope it from the left is a
# hypothetical framing («а что если я не приду?»), and that is handled by
# an explicit window rather than by a lead-word class. The two branches
# that are NOT self-identifying — a bare modal («не смогу», which also
# fits «оплатить не смогу») and the mind-change verbs («передумал», which
# also fits «насчёт цвета передумал») — DO carry a closed lead class and
# are anchored to the start of the message.

# Objects that are not a booking. The «отмен» / «удал» roots are
# domain-blind: VERIFIED on 2026-08-14, «удали мои данные» (verbatim a
# privacy-skill phrase — ``apps.skills.privacy_consent.skill``) and
# «отмени напоминание» both classified as a booking cancellation. The
# salon has exactly one cancellable object, so the cheap and stable
# statement is which objects are foreign to it.
_CANCEL_FOREIGN_OBJECT = re.compile(
    r"данн(?:ые|ых|ыми)|аккаунт|профил|подписк|рассылк"
    r"|уведомлен|напоминан|сообщени|переписк|истори|отзыв|оплат|платеж"
    r"|заказ|билет|\bотел(?:ь|я|е)\b|гостиниц"
    r"|удал\w*\s+меня",
    re.IGNORECASE,
)

# Negation window — the turn asks ABOUT cancellation (hypothetically, or
# about the policy or its price) instead of requesting one. «а что если я
# не приду?», «какие правила отмены?», «сколько стоит отмена?» must never
# be answered with a cancel card: the person asked how it works, and
# offering to drop their visit is the false positive this whole detector
# is shaped to avoid.
_CANCEL_QUESTION_FRAME = re.compile(
    r"\bесли\b|что\s+будет|\bвдруг\b|правил|услови|штраф|\bсгор(?:ит|ают)\b"
    r"|деньги\s+вернут|вернут\s+ли|сколько\s+(?:стоит|будет)|когда\s+можно",
    re.IGNORECASE,
)

# Negation window — an alternative time proposed in the SAME turn makes
# it a RESCHEDULE. «Не смогу прийти в среду, можно в четверг?» is the
# canonical example from the brief: the visit is not being dropped, it is
# being moved, and answering it with a cancellation loses the booking.
# _RESCHEDULE_SIGNAL above catches the explicit verbs; this catches the
# proposal without one.
_RESCHEDULE_PROPOSAL = re.compile(
    r"\bа\s+можно\b|\bможно\s+(?:ли\s+)?(?:в|во|на)\b|\bдава(?:й|йте)\b"
    r"|друг(?:ой|ое|ую|ие)\s+(?:день|дня|дату|дате|время|раз)"
    r"|\bпопозже\b|\bпораньше\b|\bвместо\b|\bлучше\s+(?:в|во|на)\b"
    r"|перезапиш",
    re.IGNORECASE,
)

# Negation window — double negation is a CONFIRMATION, the exact
# opposite: «я не передумал» / «не отказываюсь» keeps the booking.
_CANCEL_DOUBLE_NEGATION = re.compile(
    r"\bне\s+(?:переду\w*|раздума\w*|отказ\w*|снима\w*)",
    re.IGNORECASE,
)

# Words that may open the message in front of an anchored refusal —
# greetings, apologies, hedges, the first-person subject, a day. A CLOSED
# class: this is the lead guard for the two branches whose core is not
# self-identifying.
_REFUSAL_LEAD = (
    r"я|мне|у\s+меня|но|а|и|ой|эх|увы|жаль|к\s+сожалению"
    r"|извините|извини|простите|прости|прошу\s+прощения|пожалуйста"
    r"|здравствуйте|привет|добрый\s+день|добрый\s+вечер|доброе\s+утро"
    r"|наверное|наверно|похоже|видимо|боюсь|кажется|скорее\s+всего"
    r"|завтра|сегодня|послезавтра"
    r"|в(?:о)?\s+(?:понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)"
)

# Complements that may CLOSE a refusal without moving it out of the
# appointment domain — a CLOSED list, the same shape as the lookup
# detector's ``_ALLOWED_COMPLEMENT`` and for the same reason: an open
# «на <слово>» branch would re-open the foreign domains («не смогу
# прийти на вебинар»), and a service name is not lexically
# distinguishable from an event name, so services are not recognised at
# all (the DRF-1055 product rule, unchanged here).
#
# Note what is NOT here: «в другой салон». «не приду в другой салон» is
# the brief's negative — «в салон» closes the refusal, «в другой салон»
# does not, because the allow-list matches the whole prepositional group
# and not just its head.
_REFUSAL_TAIL = (
    r"завтра|сегодня|послезавтра|сейчас|уже|больше|вообще|совсем|точно|пока"
    r"|наверное|наверно|похоже|видимо|скорее\s+всего|к\s+сожалению|увы|жаль"
    r"|извините|извини|простите|прости|пожалуйста|спасибо"
    r"|в\s+салон|в\s+салоне|к\s+вам|к\s+мастеру"
    r"|на\s+(?:запись|записи|визит|прием|сеанс|процедуру)"
    r"|от\s+(?:запис\w+|визита|брони|бронирован\w+|приема|сеанса|процедуры)"
    r"|с\s+(?:запис\w+|визита|брони|приема|сеанса)"
    r"|из\s+(?:запис\w+|брони)"
    r"|на\s+(?:завтра|сегодня|послезавтра)"
    r"|в(?:о)?\s+(?:понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)"
    r"|на\s+(?:этой|следующей)\s+неделе"
    r"|в\s+это\s+время|в\s+этот\s+раз|вовремя"
    r"|в\s+\d{1,2}(?:[:.]\d{2})?"
    r"|\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля"
    r"|августа|сентября|октября|ноября|декабря)"
)

# Verbs of ARRIVING. Under negation each of these says «I will not be at
# my appointment». Closed and first-person throughout — «придёт» /
# «сможет» (third person) are not in the class, which is what keeps
# «мастер не сможет прийти» out without a separate rule.
_ARRIVE_INFINITIVE = r"прийти|придти|подойти|приехать|доехать|дойти|попасть|добраться|быть"
_ARRIVE_FIRST_PERSON = r"приду|прийду|приеду|подойду|попаду|доеду|доберусь"
# Modals of failure — «не смогу», «не получится», «не выйдет».
_FAIL_MODAL = r"смогу|получится|выйдет|успею|удастся"

# Branch 1 — the refusal names the act of arriving, so it identifies
# itself and needs no lead guard: «не смогу прийти», «прийти не
# получится», «я завтра не приду», «к сожалению, не приеду». Only the
# RIGHT side is guarded (closed complement list + phrase-final anchor),
# because only a complement can move the clause into another domain.
_NOT_COMING = re.compile(
    r"(?:^|\W)"
    r"(?:"
    r"не\s+(?:" + _FAIL_MODAL + r")\s+(?:" + _ARRIVE_INFINITIVE + r")"
    r"|(?:" + _ARRIVE_INFINITIVE + r")\s+не\s+(?:" + _FAIL_MODAL + r")"
    r"|не\s+(?:" + _ARRIVE_FIRST_PERSON + r")"
    r")\b"
    r"(?:[\s,]+(?:" + _REFUSAL_TAIL + r")){0,3}"
    r"[^\w]*$",
    re.IGNORECASE,
)

# Branch 2 — the bare modal («не смогу», «не получится») and the bare
# «не буду». These do NOT name what is being refused, so they are
# ANCHORED to the start of the message modulo the closed lead class:
# «не смогу» IN, «оплатить не смогу» OUT (that is a payment problem, not
# a cancellation), «маникюр делать не буду» OUT.
_NOT_COMING_BARE = re.compile(
    r"^(?:(?:" + _REFUSAL_LEAD + r")[\s,]+)*"
    r"не\s+(?:" + _FAIL_MODAL + r"|буду)\b"
    r"(?:[\s,]+(?:" + _REFUSAL_TAIL + r")){0,3}"
    r"[^\w]*$",
    re.IGNORECASE,
)

# Branch 3 — the mind changed. Both genders spelled out; anchored for
# the same reason as branch 2 — «передумал» does not name its object, so
# «передумал насчёт цвета» (the brief's negative) must be rejected by the
# tail guard and «насчёт цвета передумал» by the lead anchor.
_CHANGED_MIND = re.compile(
    r"^(?:(?:" + _REFUSAL_LEAD + r")[\s,]+)*"
    r"(?:передума(?:л|ла|ли)|раздума(?:л|ла|ли)"
    r"|отказыва(?:юсь|емся)|откажусь|отказаться)\b"
    r"(?:[\s,]+(?:" + _REFUSAL_TAIL + r")){0,3}"
    r"[^\w]*$",
    re.IGNORECASE,
)

# Branch 4 — "take me off the list". Note «удали меня» is deliberately
# NOT in this class: it is a verbatim privacy-skill delete phrase
# (``apps.skills.privacy_consent.skill._DELETE_KEYWORDS``) and belongs to
# that skill, which is registered first. «отпишите меня» is accepted
# BARE — in a 1:1 salon dialogue the only list the caller is on is the
# appointment — but «отпишите меня от рассылки» is rejected twice over
# (foreign object window, and «от рассылки» is not an allowed tail).
_REMOVE_ME = re.compile(
    r"^(?:(?:" + _REFUSAL_LEAD + r")[\s,]+)*"
    r"(?:снимите|сними|снимете|уберите|убери|отпишите|отпиши"
    r"|вычеркните|вычеркни|исключите|исключи)\s+меня\b"
    r"(?:[\s,]+(?:" + _REFUSAL_TAIL + r")){0,3}"
    r"[^\w]*$",
    re.IGNORECASE,
)


def _looks_like_cancel(normalized: str) -> bool:
    """Cancellation test over already-normalized text.

    Gate order is load-bearing — every rejection runs before every
    positive test, so a false positive needs a bug in a window rather
    than a gap in one.
    """

    # Foreign object and hypothetical framing reject BOTH halves: an
    # explicit «отмени напоминание» is as wrong a cancellation as an
    # implicit one, and «какие правила отмены?» is a question.
    if _CANCEL_FOREIGN_OBJECT.search(normalized):
        return False
    if _CANCEL_QUESTION_FRAME.search(normalized):
        return False
    # An explicit cancel verb states its own object; nothing further to
    # prove. Behaviour of this branch is unchanged from before DRF-1060
    # except for the two windows above.
    if _CANCEL_SIGNAL.search(normalized):
        return True
    # The natural-refusal half carries three more windows.
    if _CANCEL_DOUBLE_NEGATION.search(normalized):
        return False
    # «записаться не смогу» is a refusal to BOOK, not to attend — the
    # same create-intent window the lookup detector runs, reused verbatim
    # so the two cannot drift apart.
    if _CREATE_INTENT_SIGNAL.search(normalized):
        return False
    if _RESCHEDULE_SIGNAL.search(normalized) or _RESCHEDULE_PROPOSAL.search(normalized):
        return False
    return bool(
        _NOT_COMING.search(normalized)
        or _NOT_COMING_BARE.match(normalized)
        or _CHANGED_MIND.match(normalized)
        or _REMOVE_ME.match(normalized)
    )


def is_cancel_request(text: str) -> bool:
    """True when ``text`` asks to cancel the caller's own appointment.

    DRF-1060 / OD-IR1. Covers the explicit verbs («отмени мою запись»)
    and the natural refusals a person actually types instead of them:
    «не приду», «не смогу прийти», «не получится прийти», «снимите
    меня», «отпишите меня», «не буду», «передумал» / «передумала».

    Conservative by design and deliberately more conservative than
    :func:`is_personal_booking_lookup`: this predicate feeds a MUTATION
    path, so a False only costs a miss (the turn keeps its previous
    routing) while a True offers to cancel a visit. A turn that proposes
    another time is a RESCHEDULE and returns False here — see
    :func:`booking_mutation_flow`.
    """

    normalized = _normalize(text)
    if not normalized:
        return False
    return _looks_like_cancel(normalized)


# Lookup / time / list question signals. NOTE: a bare "?" is NOT a
# signal — review P1 showed it makes any possessive "запись" question
# ("мне нравится моя запись?", "можно поменять мою запись?") qualify.
# The signal must be an explicit lookup/time/list word.
#
# DRF-1055 — the show-verbs became ROOTS instead of three exact forms:
# "пока[жз]" covers покажи / покажите / показать / показывай,
# "посмотр" covers посмотреть / посмотри / посмотрю / посмотрим,
# "глян" covers глянуть / глянь. "\bчто\b" carries «что у меня
# записано» and "\bгде\b" carries «где мои записи»; both are broad
# words, which is why they only ever qualify in conjunction with a bare
# booking noun AND a personal reference.
#
# OD-IR1 (owner decision «Pilot routing», §1 lexical gaps) — the list
# knew the TIME question about one's own appointment («когда я
# записан») but neither of the other two a person actually asks: WHERE
# («куда я записан») and TO WHOM («к кому я записан»). Both are in the
# owner's minimal corpus and this gate was the ONLY one they failed —
# the bare booking noun («записан»), the safe lead («я») and the
# personal reference (_I_AM_BOOKED) all matched already. Word-bounded
# so «откуда» / «никуда» do not qualify; as broad as «где», and
# harmless for the same reason — a bare booking noun AND a personal
# reference are still required independently.
_LOOKUP_SIGNAL = re.compile(
    r"когда|во сколько|на когда|какая|какие|какой|какую|\bчто\b|\bгде\b|"
    r"\bкуда\b|\bк\s+кому\b|"
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
#
# OD-IR1 — «ался» / «алась» join the short participles «записан /
# записана» as the SAME booked-state word: for a male speaker the past
# reflexive («к кому я записался») is the more natural way to say what
# «я записан» says, and it is the nearest relative of the two corpus
# phrasings this patch closes. Added as two EXACT forms, keeping the
# class closed — the create-intent roots («записать» / «записыва»)
# still do not reach it, so «хочу записаться» / «куда записаться» stay
# rejected by the negation window one gate earlier.
_BOOKING_NOUN = (
    r"запис(?:ь|и|ей|ью|ам|ям|ями|ях|ан|ана|ано|аны|ался|алась)"
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

# DRF-1055 (review) — adjectives that may sit in front of the booking
# noun. A CLOSED temporal / ordinal / quantity class, NOT an open
# ``\w+`` slot: an open slot re-opens from the LEFT exactly the domain
# leak review rounds 3-7 closed from the right — «мои аудио записи»,
# «мои дневниковые записи», «мои экранные записи», «мои рабочие
# записи» would all qualify while «записи с диктофона» stays OUT.
# These adjectives narrow WHICH of the caller's bookings are asked
# about; they cannot move the noun into another domain.
_SAFE_ADJECTIVE = (
    r"(?:ближайш|следующ|предстоящ|будущ|прошл|последн|актуальн|текущ|оставш)\w*"
    r"|все|всех"
)

# DRF-1055 (review) — words that may IMMEDIATELY PRECEDE the booking
# noun. The domain guard has to be symmetric: Russian re-scopes a noun
# from the left as readily as from the right («аудио записи»,
# «дневниковые записи», «телефонные записи», «чужие записи»), and the
# right-hand guard cannot see any of it. Without this, widening the
# detector with the show-imperative rule («покажи <noun>») would have
# traded the round-3..7 domain guard away: «покажи мои аудио записи»
# passed, because the noun still stood phrase-FINAL.
#
# The closed class: possessives, the safe adjectives above, the
# show-verbs and question words the lookup signal already knows, the
# personal markers, and «по» (the owner's «Покажи по записи» typo).
# Anything else in front of the noun — an unknown adjective or a
# qualifying noun — rejects, exactly as an unknown word after it does.
_SAFE_LEAD = (
    r"мои|моя|мое|мою|моих|моей|моим|моими"
    r"|свои|своя|свое|свою|своих|своей|своим|своими"
    r"|" + _SAFE_ADJECTIVE + r"|пока[жз]\w*|посмотр\w*|глян\w*|список"
    r"|когда|какая|какие|какой|какую|что|где|есть"
    r"|меня|я|мне|по|и|а"
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
#   * the closed safe-LEAD list (DRF-1055 review) rejects a re-scoping
#     modifier in front of the noun ("аудио записи", "дневниковые
#     записи", "чужие записи") and, as a side effect, the compounds
#     "аудиозапись" / "звукозапись" — they are neither at the start of
#     the message nor preceded by a lead word plus a separator;
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
    r"(?:^|\b(?:" + _SAFE_LEAD + r")\W+)"
    r"(?:" + _BOOKING_NOUN + r")\b"
    r"(?:,?\s+(?:" + _ALLOWED_COMPLEMENT + r"|" + _SAFE_TAIL + r")){0,3}"
    r"[^\w]*$",
    re.IGNORECASE,
)

# Possessive pronoun qualifying a booking noun: "мои записи", "свою
# запись", "моих записей", "мои визиты", "мои брони". DRF-1055 allows
# ONE intervening adjective from the closed class above so «мои
# ближайшие записи» does not break the marker; the domain guard and the
# lookup-signal requirement still have to pass independently.
_POSSESSIVE_BOOKING = re.compile(
    r"\b(?:мои|моя|мое|мою|моих|моей|моим|моими"
    r"|свои|своя|свое|свою|своих|своей|своим|своими)"
    r"(?:\s+(?:" + _SAFE_ADJECTIVE + r"))?\s+(?:" + _BOOKING_NOUN + r")",
    re.IGNORECASE,
)

# DRF-1055 — a bare possessive request IS the whole message: «мои
# записи», «мои визиты», «мои брони». There is no lookup verb to find
# and none is needed; a message that consists of nothing but "my
# bookings" is a request to see them. Deliberately strict (whole-string
# ``fullmatch``, every slot a CLOSED class — the same safe adjectives
# and the same allow-listed complements the domain guard uses) so that
# the lookup-signal requirement keeps rejecting everything it rejects
# today: «Мне нравится моя запись?» and «У меня шесть записей» carry
# words outside those classes, «мои записи в тетради» / «мои брони в
# отеле» carry a complement outside the allow-list.
_BARE_POSSESSIVE_REQUEST = re.compile(
    r"(?:мои|моя|мое|мою|моих|моей|свои|своя|свое|свою|своих|своей)\s+"
    r"(?:(?:" + _SAFE_ADJECTIVE + r")\s+)?"
    r"(?:" + _BOOKING_NOUN + r")"
    r"(?:,?\s+(?:" + _ALLOWED_COMPLEMENT + r")){0,2}"
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

# First-person booked state: "я записан", "я записана". OD-IR1 adds the
# past reflexive "я записался" / "я записалась" — same state, the form
# a male speaker reaches for first ("к кому я записался"). The two
# forms are spelled out rather than left to a "записа\w*" tail: that
# tail would also swallow "я записал" (recorded something) and
# "я записался бы" is not a lookup either way.
_I_AM_BOOKED = re.compile(r"\bя\s+запис(?:ан|ался|алась)", re.IGNORECASE)


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

    Reschedule is tested FIRST and that order is load-bearing: «перенеси»
    and «не смогу прийти в среду, можно в четверг?» both express an
    inability to attend, but only one of them drops the visit. DRF-1060
    additionally makes the cancel half reject a turn that proposes
    another time WITHOUT a reschedule verb — such a turn returns ``None``
    (no flow tagged) rather than being mis-tagged as a cancellation.
    """
    normalized = _normalize(text)
    if not normalized:
        return None
    if _RESCHEDULE_SIGNAL.search(normalized):
        return "reschedule"
    if _looks_like_cancel(normalized):
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
