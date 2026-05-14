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
"""

from __future__ import annotations

import re
from enum import Enum


class PainSignal(str, Enum):
    NONE = "none"
    SOFT = "soft"
    RED_FLAG = "red_flag"


# ─── pain stems (broad — false-positives are cheap) ───────────────────────

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


# ─── red-flag patterns — "see a doctor" cases ─────────────────────────────


# These patterns indicate something massage / nutrition cannot help and
# may worsen. The rule of thumb: anything that looks like a neurological,
# vascular, or acute systemic symptom.
_RED_FLAG_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Numbness / nerve-root signs
    re.compile(r"\bонемен", re.IGNORECASE),
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

    lower = stripped.lower()

    # Red flags take priority — if the message contains a red-flag
    # pattern, the soft-pain path is shadowed.
    for pattern in _RED_FLAG_PATTERNS:
        if pattern.search(lower):
            return PainSignal.RED_FLAG

    for stem in _PAIN_STEMS:
        if stem in lower:
            return PainSignal.SOFT

    return PainSignal.NONE
