"""Human time: one vocabulary, one set of boundaries (DRF-1325).

Two halves of the same gap, and this module owns both ends of it.

**Input.** «хочу на массаж завтра вечером» reached the pilot bot on
2026-08-23 at 17:48 and the time half of it was dropped without a word —
neither used nor asked back. The booking that came out of that dialogue
stood on 28.08 11:30: five days out and not an evening.

**Output.** The other half of the same gap: the bot answers with a bare
calendar («Выберите дату», «Выберите время»). People do not think in
dates — they think «завтра», «в субботу», «после работы».

**Why one module.** Утро / день / вечер must mean the same thing in the
parse, in the chips and in the confirmation. Boundaries scattered over
three call sites are boundaries that drift, and a drifted «вечер» is how
you offer 14:30 to somebody who asked for an evening. So the split lives
here, once:

    Утро   — до 12:00
    День   — 12:00 … 17:00
    Вечер  — с 17:00

The three buckets tile the whole 24 hours with no gap and no overlap:
:func:`part_for_hour` is total, so no slot can fall outside the vocabulary
and silently acquire the wrong label.

**What this module deliberately does NOT do.** It never asserts that a
time is free. There is no authoritative availability contract
(``docs/OD_SALON_P0_CONTRACT.md``: «Ayla/UI не должны утверждать "это
время гарантированно свободно"»), and the final word belongs to
``create`` with its 409. Everything here narrows and proposes; the slot
lists the chips lead to are built from the schedule read the booking flow
already performs, so a chip only ever appears when the day or the bucket
behind it actually holds something.

**Timezone.** Parsing is timezone-free on purpose: «завтра» is an offset
and «вечер» is a label — neither needs a zone. The offset becomes a
calendar date only at render time, inside ``tenant_scope(T)``, against
``Tenant.timezone`` (``apps/tenancy/models.py``, default
``Europe/Moscow``). That is the honest local source in THIS service:
``SpecialistProfile.timezone`` lives in the Ayla backend and is not
mirrored into the bot's catalog (``apps/catalog/models.py`` carries no
timezone column at all). Resolving per tenant also sidesteps the trap the
salon audit recorded (``docs/REPORT_SALON_P0.md``, «Окно считается по
мастеру, а не по салону»): one local date must not be re-derived per
master, or the same «завтра» becomes a different window for each of them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Parts of the day — THE definition. Nothing else may hard-code these hours.
# ---------------------------------------------------------------------------

PART_MORNING = "morning"
PART_DAY = "day"
PART_EVENING = "evening"

# Exclusive upper bound (local hour) of each part. The last part has no upper
# bound — it absorbs the rest of the 24h, which is what makes part_for_hour
# total and therefore incapable of leaving a slot unlabelled.
_MORNING_UNTIL_HOUR = 12
_DAY_UNTIL_HOUR = 17

PART_ORDER: tuple[str, ...] = (PART_MORNING, PART_DAY, PART_EVENING)

# Chip captions. Also the words used when reading a choice back, so the
# confirmation cannot disagree with the button that produced it.
PART_CHIP_LABELS: dict[str, str] = {
    PART_MORNING: "Утро",
    PART_DAY: "День",
    PART_EVENING: "Вечер",
}

# The same three parts in the case a sentence needs («…завтра вечером»).
PART_PHRASES: dict[str, str] = {
    PART_MORNING: "утром",
    PART_DAY: "днём",
    PART_EVENING: "вечером",
}

# Human-readable boundary, shown next to the chips so the user can see what
# the bot means by the word BEFORE tapping it.
PART_RANGE_HINTS: dict[str, str] = {
    PART_MORNING: f"до {_MORNING_UNTIL_HOUR}:00",
    PART_DAY: f"{_MORNING_UNTIL_HOUR}:00–{_DAY_UNTIL_HOUR}:00",
    PART_EVENING: f"с {_DAY_UNTIL_HOUR}:00",
}

# The five-value vocabulary the personal-context contract already uses
# (apps/orchestrator/memory_ask.py, apps/persona/memory_extract.py) folded
# onto the three chips. Declared here so the two vocabularies cannot drift
# apart unnoticed: a new contract value that is not in this map is a
# KeyError at the seam, not a silently mislabelled evening.
CONTRACT_SLOT_TO_PART: dict[str, str] = {
    "early_morning": PART_MORNING,
    "morning": PART_MORNING,
    "afternoon": PART_DAY,
    "evening": PART_EVENING,
    "late_evening": PART_EVENING,
}


def part_for_hour(hour: int) -> str:
    """Which part of the day a local hour belongs to. Total over ``0..23``."""
    if hour < _MORNING_UNTIL_HOUR:
        return PART_MORNING
    if hour < _DAY_UNTIL_HOUR:
        return PART_DAY
    return PART_EVENING


_HHMM_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def part_of_iso_datetime(value: str) -> str | None:
    """Part of the day for a slot string, or ``None`` if no hour is readable.

    The hour is read AS WRITTEN. Every slot on this path comes from the
    schedule read for one local calendar day and is already local to the
    salon, so re-projecting it into another zone would move it, not fix it.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return part_for_hour(datetime.fromisoformat(raw.replace("Z", "+00:00")).hour)
    except (TypeError, ValueError):
        pass
    match = _HHMM_RE.search(raw)
    if match is None:
        return None
    hour = int(match.group(1))
    return part_for_hour(hour) if 0 <= hour <= 23 else None


# ---------------------------------------------------------------------------
# Parsing what a person said
# ---------------------------------------------------------------------------

# Day words. Order matters: «послезавтра» contains «завтра».
_DAY_OFFSET_RULES: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"послезавтра", re.IGNORECASE), 2),
    (re.compile(r"\bзавтра\b", re.IGNORECASE), 1),
    (re.compile(r"\bсегодня\b", re.IGNORECASE), 0),
)

# Weekday names → Monday-based index. «в субботу» from the ticket lives here.
_WEEKDAY_RULES: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\bпонедельник\w*", re.IGNORECASE), 0),
    (re.compile(r"\bвторник\w*", re.IGNORECASE), 1),
    (re.compile(r"\bсред[ауыое]\w*", re.IGNORECASE), 2),
    (re.compile(r"\bчетверг\w*", re.IGNORECASE), 3),
    (re.compile(r"\bпятниц\w*", re.IGNORECASE), 4),
    (re.compile(r"\bсуббот\w*", re.IGNORECASE), 5),
    (re.compile(r"\bвоскресень\w*|\bвоскресен\w*", re.IGNORECASE), 6),
)

# «в выходные» — the nearest weekend day, Saturday first.
_WEEKEND_RE = re.compile(r"\bвыходн\w*", re.IGNORECASE)

# Part-of-day words. The same shapes the memory extractor already recognises
# (apps/persona/memory_extract.py), folded onto the three chips — the
# five-value contract vocabulary stays the memory layer's business.
_PART_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"поздн\w*\s+вечер|\bночью\b|\bноч[ьи]\b", re.IGNORECASE), PART_EVENING),
    (re.compile(r"ранн\w*\s+утр", re.IGNORECASE), PART_MORNING),
    (re.compile(r"\bутр(?:о|ом|а|у)\b", re.IGNORECASE), PART_MORNING),
    (re.compile(r"\bдн[её]м\b|\bв\s+обед\b|\bобеденн\w*", re.IGNORECASE), PART_DAY),
    (re.compile(r"\bвечер(?:ом|а|е)?\b", re.IGNORECASE), PART_EVENING),
    # «после работы» is an evening in every pilot dialogue on record.
    (re.compile(r"после\s+работ\w*", re.IGNORECASE), PART_EVENING),
)

# «после шести», «после 18» — resolve the HOUR, then bucket it through the
# single definition above instead of guessing the label directly. Word
# numerals mirror _AFTER_WORD_HOURS in apps/persona/memory_extract.py.
_AFTER_WORD_HOURS = {"пяти": 17, "шести": 18, "семи": 19, "восьми": 20, "девяти": 21}
_AFTER_WORD_RE = re.compile(r"после\s+(пяти|шести|семи|восьми|девяти)\b", re.IGNORECASE)
_AFTER_NUM_RE = re.compile(r"после\s+(\d{1,2})(?::\d{2})?\s*(?:часов|часа|ч)?\b", re.IGNORECASE)

# A named weekday is always the NEXT occurrence within one week: somebody who
# is already inside Saturday says «сегодня», not «в субботу».
_WEEK = 7


@dataclass(frozen=True)
class TimePreference:
    """What the user said about time, BEFORE it becomes a calendar date.

    ``day_offset`` — days from "today in the salon's zone" (0/1/2/…), or
    ``None`` when only a part of the day was named («вечером»).
    ``part`` — one of :data:`PART_ORDER`, or ``None`` when only a day was
    named («завтра»).
    ``said`` — the verbatim fragments, kept so the bot can read the request
    back in the user's own words instead of paraphrasing it.
    """

    day_offset: int | None = None
    part: str | None = None
    said: str = ""

    def __bool__(self) -> bool:
        return self.day_offset is not None or self.part is not None

    def as_state(self) -> dict[str, Any]:
        return {"day_offset": self.day_offset, "part": self.part, "said": self.said}

    @classmethod
    def from_state(cls, raw: Any) -> TimePreference | None:
        if not isinstance(raw, dict):
            return None
        offset = raw.get("day_offset")
        part = raw.get("part")
        if offset is not None and not isinstance(offset, int):
            return None
        if part is not None and part not in PART_ORDER:
            return None
        pref = cls(day_offset=offset, part=part, said=str(raw.get("said") or ""))
        return pref or None


def parse_time_preference(text: str, *, weekday_today: int | None = None) -> TimePreference | None:
    """Extract the day and/or part of day a person named, or ``None``.

    ``weekday_today`` (Monday=0) is needed ONLY to turn a weekday word into
    an offset; a caller that does not yet know the salon's local weekday can
    omit it and the weekday branch is skipped rather than guessed against
    the server's zone.

    Returns ``None`` — not an empty preference — when nothing was said about
    time, so a caller can tell "no preference" from "an unparsable one"
    without inspecting fields.
    """
    raw = (text or "").strip()
    if not raw:
        return None

    fragments: list[str] = []

    day_offset: int | None = None
    for pattern, offset in _DAY_OFFSET_RULES:
        match = pattern.search(raw)
        if match is not None:
            day_offset = offset
            fragments.append(match.group(0).lower())
            break

    if day_offset is None and weekday_today is not None:
        for pattern, target in _WEEKDAY_RULES:
            match = pattern.search(raw)
            if match is not None:
                day_offset = ((target - weekday_today) % _WEEK) or _WEEK
                fragments.append(match.group(0).lower())
                break

    if day_offset is None and weekday_today is not None and _WEEKEND_RE.search(raw):
        # Saturday if it is still ahead, otherwise Sunday, otherwise the
        # Saturday of the coming week.
        to_saturday = (5 - weekday_today) % _WEEK
        to_sunday = (6 - weekday_today) % _WEEK
        day_offset = to_saturday or to_sunday or _WEEK
        fragments.append("выходные")

    part: str | None = None
    for pattern, value in _PART_RULES:
        match = pattern.search(raw)
        if match is not None:
            part = value
            fragments.append(match.group(0).lower())
            break

    if part is None:
        match = _AFTER_WORD_RE.search(raw)
        if match is not None:
            part = part_for_hour(_AFTER_WORD_HOURS[match.group(1).lower()])
            fragments.append(match.group(0).lower())
        else:
            match = _AFTER_NUM_RE.search(raw)
            if match is not None:
                hour = int(match.group(1))
                if 0 <= hour <= 23:
                    part = part_for_hour(hour)
                    fragments.append(match.group(0).lower())

    if day_offset is None and part is None:
        return None
    return TimePreference(day_offset=day_offset, part=part, said=" ".join(fragments))


# ---------------------------------------------------------------------------
# Dates a person spells out — «16 августа 2026», «16.08.2026» (DRF-1101)
# ---------------------------------------------------------------------------
#
# ``parse_time_preference`` above reads RELATIVE time: «завтра», «в субботу»,
# «вечером». That is what people say before the bot has asked. Once the day
# chips are on screen a second dialect appears, and the pilot dialogue
# DRF-1101 is filed about is written entirely in it:
#
#     09:03:30  16.08.2016          ← a typo in the year
#     09:03:49  16 августа 2026     ← the same day, spelled out
#
# Kept separate from ``parse_time_preference`` on purpose. That function has
# three callers and returns an OFFSET from a day it never has to know; this
# one returns a calendar date and therefore needs ``today`` to disambiguate a
# missing year. Folding them together would have forced ``today`` on every
# caller of the older one — including the render-time ones that deliberately
# do not have a tenant yet.

_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")

# Day-first, dot-separated: «16.08», «16.08.26», «16.08.2026». Only the dot is
# accepted as a separator. «/» and «-» would turn «1-2» and «1/2» into the
# first of February, and a booking chat is full of small numbers.
_DOTTED_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b")

# «16 августа», «16 авг 2026». The month stems are the ones the chip captions
# already use (``_RU_MONTHS_SHORT``), so the two vocabularies cannot drift.
_NAMED_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+(янв|фев|мар|апр|ма[йя]|июн|июл|авг|сен|окт|ноя|дек)\w*(?:\s+(\d{4}))?",
    re.IGNORECASE,
)
_MONTH_STEM_TO_NUMBER: dict[str, int] = {
    "янв": 1,
    "фев": 2,
    "мар": 3,
    "апр": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сен": 9,
    "окт": 10,
    "ноя": 11,
    "дек": 12,
}

_TWO_DIGIT_YEAR_BASE = 2000
_MAX_MONTH = 12
_TWO_DIGIT_YEAR_LIMIT = 100


def _build_date(year: int, month: int, day: int) -> date_cls | None:
    """A real calendar date, or ``None`` for «31 февраля» and friends."""
    try:
        return date_cls(year, month, day)
    except ValueError:
        return None


def _next_occurrence(month: int, day: int, today: date_cls) -> date_cls | None:
    """This year's ``day.month`` when it is still ahead, else next year's."""
    candidate = _build_date(today.year, month, day)
    if candidate is not None and candidate >= today:
        return candidate
    return _build_date(today.year + 1, month, day)


def parse_explicit_date(text: str, *, today: date_cls) -> date_cls | None:
    """The calendar date a person spelled out, or ``None``.

    ``today`` is the SALON's local today (:func:`local_today`) and is used for
    one thing: a date written without a year («16 августа») means the next such
    day — this year if it is still ahead, next year otherwise. Nobody booking a
    massage means the one that already happened.

    A date WITH a year is returned exactly as written, past or not. «16.08.2016»
    is the typo the ticket's dialogue opens with, and the caller has to be able
    to tell a typo from a day the master simply is not working. Deciding that
    here would hide it.
    """
    raw = (text or "").strip()
    if not raw:
        return None

    match = _ISO_DATE_RE.search(raw)
    if match is not None:
        year, month, day = (int(part) for part in match.groups())
        return _build_date(year, month, day)

    match = _NAMED_DATE_RE.search(raw)
    if match is not None:
        day = int(match.group(1))
        month = _MONTH_STEM_TO_NUMBER[match.group(2).lower()]
        raw_year = match.group(3)
        if raw_year is not None:
            return _build_date(int(raw_year), month, day)
        return _next_occurrence(month, day, today)

    match = _DOTTED_DATE_RE.search(raw)
    if match is not None:
        day, month = int(match.group(1)), int(match.group(2))
        if month > _MAX_MONTH:
            # «17.30» is a time somebody wrote with a dot, not the 17th of
            # month 30. Refusing beats guessing: the caller's fallback is the
            # day chips, which cannot be wrong.
            return None
        raw_year = match.group(3)
        if raw_year is not None:
            year = int(raw_year)
            if year < _TWO_DIGIT_YEAR_LIMIT:  # «16.08.26»
                year += _TWO_DIGIT_YEAR_BASE
            return _build_date(year, month, day)
        return _next_occurrence(month, day, today)

    return None


# ---------------------------------------------------------------------------
# Turning a preference into a calendar date — tenant-local, once
# ---------------------------------------------------------------------------

FALLBACK_TZ = "Europe/Moscow"


def tenant_zone(tenant: Any) -> ZoneInfo:
    """The salon's zone, or ``Europe/Moscow``.

    Deliberately per TENANT and never per master: the bot's catalog mirror
    carries no per-specialist timezone column, and re-deriving one local
    date per master is exactly the divergence the salon audit warns about.
    """
    name = (getattr(tenant, "timezone", "") or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            logger.warning("time_preference.bad_tenant_timezone value=%s", name[:64])
    return ZoneInfo(FALLBACK_TZ)


def local_today(tenant: Any, *, now: datetime | None = None) -> date_cls:
    """Today's calendar date in the salon's zone.

    The server runs on UTC; between 21:00 and midnight Moscow time «сегодня»
    computed server-side is YESTERDAY for the salon. Every "today" on the
    booking path goes through here.
    """
    from django.utils import timezone as dj_timezone

    moment = now or dj_timezone.now()
    return moment.astimezone(tenant_zone(tenant)).date()


def resolve_date(pref: TimePreference | None, today: date_cls) -> str | None:
    """ISO date the preference points at, or ``None`` if it names no day."""
    if pref is None or pref.day_offset is None:
        return None
    return (today + timedelta(days=pref.day_offset)).isoformat()


_RELATIVE_DAY_WORDS = ("сегодня", "завтра", "послезавтра")
_RU_WEEKDAYS_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
_RU_MONTHS_SHORT = (
    "янв",
    "фев",
    "мар",
    "апр",
    "мая",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
)


def day_label(iso_date: str, today: date_cls) -> str:
    """Chip caption for a date: «Сегодня» / «Завтра» / «Послезавтра» / «25 авг (Пн)».

    The relative words are the point of the ticket — a person reads «Завтра»
    instantly and «2026-08-24» not at all — but they only hold for three
    days, after which a date is genuinely clearer than a count.
    """
    try:
        day = date_cls.fromisoformat(iso_date)
    except (TypeError, ValueError):
        return iso_date
    delta = (day - today).days
    if 0 <= delta < len(_RELATIVE_DAY_WORDS):
        return _RELATIVE_DAY_WORDS[delta].capitalize()
    return f"{day.day} {_RU_MONTHS_SHORT[day.month - 1]} ({_RU_WEEKDAYS_SHORT[day.weekday()]})"


def day_phrase(iso_date: str, today: date_cls) -> str:
    """The same date inside a sentence: «завтра», «25 авг (Пн)»."""
    label = day_label(iso_date, today)
    return label.lower() if label.lower() in _RELATIVE_DAY_WORDS else label


def describe(pref: TimePreference | None, iso_date: str | None, today: date_cls) -> str:
    """Read a request back: «завтра вечером», «вечером», «25 авг (Пн)»."""
    if pref is None:
        return ""
    said: list[str] = []
    if iso_date:
        said.append(day_phrase(iso_date, today))
    if pref.part:
        said.append(PART_PHRASES[pref.part])
    return " ".join(said)


# ---------------------------------------------------------------------------
# Carrying the preference from the turn that said it to the turn that uses it
# ---------------------------------------------------------------------------

STATE_KEY = "time_pref"

# The preference belongs to ONE booking attempt. Ten minutes is the window
# the booking skill already gives its own pending state (_FLOW_STATE_TTL);
# past it a stale «завтра» would silently mean a different day than the one
# the person meant.
STATE_TTL_SECONDS = 600


def save_time_preference(conversation: Any, pref: TimePreference | None) -> None:
    """Persist the preference on a conversation. Best-effort by contract.

    A failure here must never cost the user their turn: the worst outcome of
    losing the preference is the day chips, which is this ticket's own
    no-preference path.
    """
    if conversation is None:
        return
    try:
        from django.utils import timezone as dj_timezone

        state = dict(getattr(conversation, "skill_state", None) or {})
        if pref is None:
            if STATE_KEY not in state:
                return
            state.pop(STATE_KEY, None)
        else:
            payload = pref.as_state()
            payload["at"] = dj_timezone.now().isoformat()
            state[STATE_KEY] = payload
        conversation.skill_state = state
        conversation.save(update_fields=["skill_state"])
    except Exception:  # noqa: BLE001 — never break a turn over a hint
        logger.exception("time_preference.save_failed")


def load_time_preference(conversation: Any) -> TimePreference | None:
    """Read back a fresh preference, or ``None`` (missing, stale, corrupt)."""
    if conversation is None:
        return None
    try:
        from django.utils import timezone as dj_timezone

        raw = (getattr(conversation, "skill_state", None) or {}).get(STATE_KEY)
        if not isinstance(raw, dict):
            return None
        stamped = raw.get("at")
        if stamped:
            try:
                age = (dj_timezone.now() - datetime.fromisoformat(str(stamped))).total_seconds()
            except (TypeError, ValueError):
                return None
            if age > STATE_TTL_SECONDS or age < -STATE_TTL_SECONDS:
                return None
        return TimePreference.from_state(raw)
    except Exception:  # noqa: BLE001
        logger.exception("time_preference.load_failed")
        return None
