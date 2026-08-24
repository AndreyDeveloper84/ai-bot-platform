"""Pain mention + red-flag classifier.

Sprint 9 / P7 (DRF-824). Pure regex — no LLM, no Ayla. Two-tier output:

* :data:`PainSignal.NONE` — no pain context detected; skill doesn't match.
* :data:`PainSignal.SOFT` — mention of pain or discomfort that warrants
  the diagnostic-first response (1-2 clarifying questions before any
  service recommendation, per DRF-358 T04).
* :data:`PainSignal.RED_FLAG` — symptom set indicating "see a doctor,
  massage is not appropriate". Skill responds with the redirect text
  and skips the booking flow.

## Design

The mysite DRF-358 fix kept the diagnostic-first rule as a system-prompt
nudge plus voice examples — it did NOT detect pain in code. We add a
classifier here because Sprint 9 doesn't yet have the booking skill
that would consume the system prompt. The skill catches the same
incidents (dev-bot 2026-05-08 09:10, cold "не могу с заказом") before
the LLM-driven path picks them up.

False-positive cost: one extra empathic question. False-negative cost:
the user gets a tone-deaf "вот наши услуги" reply for "болит спина".
We tune for low false-negative — broad stem list.

## DRF-973 — the stem is a WORD, not a substring

The seed list above was matched with ``stem in lower``. That is a rule
about the SHAPE of a word rather than its meaning, and in Russian it
misfires constantly: «спасибо **бол**ьшое» — a person saying goodbye —
was classified as a pain report and answered with «где именно болит?».
So were «я **бол**ьше не приду» (a cancellation), «**стрел**ки»
(eyeliner — a service we sell), «**хрустал**ьный маникюр», «зажим для
волос» and «напряжённая неделя».

The fix has two halves, and BOTH are written so they can only ever
remove a false positive:

1. :data:`_NOT_PAIN` — phrases that contain a pain stem and are not
   pain. They are BLANKED from the text (replaced by a space, never
   deleted, so masking cannot join two halves into a new match) before
   any pain test runs. Whatever else the message says is still read:
   «болтали про то, что болит спина» keeps its «болит».
2. Stems match at a WORD START only, modulo a closed list of Russian
   verbal prefixes (:data:`_STEM_PREFIXES`) so «**прострел** в
   пояснице» and «за**бол**ела спина» keep matching. This is what
   drops «фут**бол**» without needing a line for every ball game.

Neither half can create a false NEGATIVE that the substring rule did
not already have: half 1 only ever removes text that is listed here by
name, half 2 only ever rejects a stem occurrence that starts in the
middle of a word with an unlisted prefix. The corpus in
``tests/test_classifier.py`` pins both directions — a genuine complaint
must keep reaching the screening, which is the whole reason this gate
exists.

## DRF-973 (found while measuring) — two red flags that never fired

«онемела рука» and «немеет рука» — the two ways a person actually
reports numbness — matched NOTHING: the red-flag pattern was
``\bонемен``, which only covers the noun «онемение» and the masculine
«онемел», and neither phrase carries a soft-pain stem either. A
red-flag miss is the expensive direction for this module, so the
pattern was widened to the verb forms of both «онеметь» and «неметь».
"""

from __future__ import annotations

import re
from enum import Enum


class PainSignal(str, Enum):
    NONE = "none"
    SOFT = "soft"
    RED_FLAG = "red_flag"


# ─── pain stems (broad — a miss is the expensive direction) ───────────────
#
# Matched at a word start (see :func:`_mentions_stem`), NOT as a bare
# substring — DRF-973. Multi-word entries («тяжесть в») anchor on their
# first token and are otherwise unchanged.

_PAIN_STEMS: frozenset[str] = frozenset(
    {
        "бол",  # болит / больно / болью
        "ноет",
        "ноют",
        "ноющ",
        "тянет",
        "тянущ",
        "хрустит",
        "хруст",
        "ломит",
        "ломота",
        "колет",
        "стрел",  # стреляет / стреляющ
        "дёргает",
        "дергает",
        "пульсир",
        "защемил",
        "защемля",
        "зажим",  # зажимает / зажим в шее
        "напряж",  # напряжение в спине
        "спазм",
        "судорог",
        # Body part / state combos that mean pain without «бол»
        "тяжесть в",
        "усталость в",
        "не могу повернуть",
        "не могу нагнуться",
        "трудно дышать",
    }
)


# ─── DRF-973 — phrases that carry a pain stem and are not pain ────────────
#
# Every entry below was OBSERVED to misfire on the pre-patch classifier
# (2026-08-24 run, `docs/REPORT_DRF973.md`). They are BLANKED before any
# pain test, so the rest of the message is still read in full.
#
# The list is a CLOSED set of phrases, deliberately not an open «word
# that happens to start with a stem» rule: the guarded direction here is
# the false NEGATIVE (a real complaint answered with «вот наши услуги»),
# so a word may only stop counting as pain if it is named here.
#
# ADD to this list when a false positive is observed. Do NOT add a
# phrase that could also be a complaint — «болит» in any form belongs to
# the screening, whatever else the sentence does.
_NOT_PAIN: tuple[re.Pattern[str], ...] = (
    # «больш*» — the whole «большой» family starts with the «бол» stem.
    # «спасибо большое» (the ticket's headline case), «я больше не
    # приду» (a cancellation, DRF-1060), «большая чистка лица».
    re.compile(r"больш\w*", re.IGNORECASE),
    # small talk: «болтать» / «болтливый» / «болтун».
    re.compile(r"болт(?:а|л|у)\w*", re.IGNORECASE),
    # «болото» — appears in place names and idioms.
    re.compile(r"болот\w*", re.IGNORECASE),
    # «болею за» — supporting a team, not an illness. Anchored on the
    # preposition so «болею уже неделю» stays a complaint.
    re.compile(r"бол(?:ею|еешь|еет|еем|еете|еют|ел|ела|ело|ели)\s+за\b", re.IGNORECASE),
    # «стрелки» — eyeliner. A SERVICE WE SELL, and the «стрел» stem
    # («стреляет в шею») swallowed every request for one.
    re.compile(r"стрелк\w*", re.IGNORECASE),
    # «хрустальный» — a nail-design finish, not «хруст в шее».
    re.compile(r"хрустал\w*", re.IGNORECASE),
    # «зажим для волос» — a hair clip, not «зажим в шее». Only the
    # purchase phrasing is masked: bare «зажим» stays a complaint.
    re.compile(r"зажим\w*\s+для\b", re.IGNORECASE),
    # «напряжённая неделя / график / работа» — the reason a person books
    # a relaxing massage, not a symptom. «напряжение в спине» is
    # untouched: only these complements are masked.
    re.compile(
        r"напряж[её]нн\w*\s+(?:график\w*|недел\w*|день|дня|дн[ий]\w*"
        r"|месяц\w*|период\w*|работ\w*|разговор\w*)",
        re.IGNORECASE,
    ),
    # «больно ли?» / «а это больно?» / «не колет ли лазер?» — a question
    # about a procedure that has NOT happened, not a report of pain that
    # has. Answering it with «где именно болит?» is the same defect this
    # ticket is about, one axis over: the FORM carries a pain word, the
    # MEANING is a price-list question.
    #
    # ONLY the hypothetical frames are masked, and each needs an
    # interrogative particle or the future tense to qualify — a bare
    # «больно» is untouched. «больно ли делать массаж, если болит
    # спина» therefore still reaches the screening on its «болит».
    re.compile(r"больно\s+ли\b", re.IGNORECASE),
    re.compile(r"\b(?:будет\s+больно|больно\s+будет)\b", re.IGNORECASE),
    re.compile(r"\b(?:это|а\s+это)\s+больно\s*\?", re.IGNORECASE),
    re.compile(r"\b(?:не\s+)?колет\s+ли\b", re.IGNORECASE),
)


def _mask_not_pain(text: str) -> str:
    """Blank every :data:`_NOT_PAIN` phrase, preserving offsets' meaning.

    Replaced by a SPACE rather than removed: deletion could weld two
    halves of the message into a stem that neither half contained.
    """

    for pattern in _NOT_PAIN:
        text = pattern.sub(" ", text)
    return text


# Verbal prefixes a pain stem may carry. CLOSED — this is what lets
# «прострел» and «заболела» match while «футбол» / «баскетбол» do not.
#
# Multi-letter only, on purpose. A one-letter prefix («о», «у», «с»)
# buys nothing — no phrasing in the corpus needs one — and each of them
# re-opens the middle of a word to the stem: «о» + «бол» would make
# «оболочка» a pain report, which is the very rule this fix replaces.
_STEM_PREFIXES: tuple[str, ...] = (
    "про",
    "за",
    "раз",
    "рас",
    "при",
    "на",
    "под",
    "по",
    "пере",
    "об",
    "вы",
    "из",
)

_STEM_PREFIX_ALT = "|".join(sorted(_STEM_PREFIXES, key=len, reverse=True))


def _stem_pattern(stem: str) -> re.Pattern[str]:
    """Word-start matcher for one stem, modulo :data:`_STEM_PREFIXES`."""

    return re.compile(
        r"(?<![^\W\d_])(?:" + _STEM_PREFIX_ALT + r")?" + re.escape(stem),
        re.IGNORECASE,
    )


_PAIN_STEM_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    _stem_pattern(stem) for stem in sorted(_PAIN_STEMS)
)


# ─── red-flag patterns — "see a doctor" cases ─────────────────────────────


# These patterns indicate something massage / nutrition cannot help and
# may worsen. The rule of thumb: anything that looks like a neurological,
# vascular, or acute systemic symptom.
_RED_FLAG_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Numbness / nerve-root signs.
    #
    # DRF-973 — the list held ``\bонемен`` and nothing else, which covers
    # the noun «онемение» and the masculine «онемел». «онемела рука» and
    # «немеет рука» — the two phrasings a person actually types — matched
    # NEITHER this list NOR any soft-pain stem and classified as NONE: a
    # red flag that never fired. Both verbs are spelled out now, in
    # EXACT forms (never «неме\w*») so «немецкий» cannot qualify.
    re.compile(r"\bонемен", re.IGNORECASE),
    re.compile(r"\b(?:о)?неме(?:ет|ют|л|ла|ло|ли|ть|вш\w*)\b", re.IGNORECASE),
    re.compile(r"потерял[аио]? чувствит", re.IGNORECASE),
    re.compile(r"отнима(?:ет|ется|ются)", re.IGNORECASE),
    re.compile(r"отдаёт в (?:руку|ногу|пальц)", re.IGNORECASE),
    re.compile(r"отдает в (?:руку|ногу|пальц)", re.IGNORECASE),
    # Acute systemic
    re.compile(r"температур[аы]\s*\d", re.IGNORECASE),
    re.compile(r"температур[аы]\s*(?:высок|поднял)", re.IGNORECASE),
    re.compile(r"тошн(?:ит|ота)", re.IGNORECASE),
    re.compile(r"рвот", re.IGNORECASE),
    # Vascular / cardiac warning
    re.compile(r"давит в груди", re.IGNORECASE),
    re.compile(r"одышк", re.IGNORECASE),
    re.compile(r"учащ[её]нный пульс", re.IGNORECASE),
    # Functional collapse
    re.compile(r"не могу встать", re.IGNORECASE),
    re.compile(r"не могу ходить", re.IGNORECASE),
    re.compile(r"теря(?:ю|ет) сознание", re.IGNORECASE),
    # Pregnancy + back pain is a soft red-flag — surface but don't block;
    # caller emits the warning. We keep this OUT of the regex list for
    # now; future versions can add tiered red-flags.
)


# Cap message length — long free-text is a question, not a pain report.
_MAX_LEN = 200


def classify(text: str) -> PainSignal:
    """Return the strongest pain signal in ``text``.

    Type-tolerant: non-``str`` returns :data:`PainSignal.NONE`. We never
    raise — bad upstream data is a router bug, not a classifier bug.
    """
    if not isinstance(text, str):
        return PainSignal.NONE
    stripped = text.strip()
    if not stripped or len(stripped) > _MAX_LEN:
        return PainSignal.NONE

    # DRF-973 — the false-friend phrases are blanked ONCE and both tiers
    # read the masked text. Nothing in :data:`_NOT_PAIN` is a red flag,
    # so masking cannot hide one; scanning the same string with both
    # tiers is what keeps them from disagreeing about what was said.
    lower = _mask_not_pain(stripped.lower())

    # Red flags take priority — if the message contains a red-flag
    # pattern, the soft-pain path is shadowed.
    for pattern in _RED_FLAG_PATTERNS:
        if pattern.search(lower):
            return PainSignal.RED_FLAG

    for pattern in _PAIN_STEM_PATTERNS:
        if pattern.search(lower):
            return PainSignal.SOFT

    return PainSignal.NONE
