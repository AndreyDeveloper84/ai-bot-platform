"""Deterministic explicit green-fact extractor (M-B2 / #1099, pilot-narrow).

The pilot does NOT actively ask memory questions (founder decision 2026-07-09).
The only write path is a **spontaneous, explicit** self-statement — e.g. the
user types «я веган». This module recognises a small, conservative set of such
statements with plain patterns (NOT an LLM). General conversational extraction
is post-pilot (M-C2, ADR-0011 §4 «inferred» source).

Everything here is 🟢 green + ``source='explicit'`` — the only zone/source the
pilot writes (green-explicit bypasses minor-protection per ADR-0011 §10.2).

Conservative by design: **high precision over recall.** Each rule is anchored on
a first-person self-statement — the pronoun «я» must sit immediately before the
keyword («я веган», «я — вегетарианка», «я не ем мясо»). This adjacency alone
rejects the trust-eroding false positives: «жена веганка» (not the user), «ресторан
для веганов» (not a self-statement), «я не веган» / «не хочу быть веганом» (the
particle breaks the «я»→keyword adjacency). A missed fact is cheap; a wrong fact
about the user erodes trust — which is the whole point of the feature.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GreenFactCandidate:
    """An explicit green fact extracted from a user turn, ready for the writer."""

    kind: str
    content: dict[str, Any]

    @property
    def dedup_key(self) -> tuple[str, Any, Any]:
        """Identity for dedup: same (kind, key, value) → same fact."""

        return (self.kind, self.content.get("key"), self.content.get("value"))


# Self-anchor: «я», then only whitespace/dash, then the keyword stem. The
# stem covers Russian morphology (веган/веганка/веганство). The adjacency is
# the negation guard: «я не веган» has «не» between «я» and the stem, so it
# never matches. `_SELF` = «я» + optional dash + whitespace.
_SELF = r"\bя\b(?:\s*[—–-]\s*|\s+)"
_DIET_RULES: list[tuple[re.Pattern[str], GreenFactCandidate]] = [
    (
        re.compile(_SELF + r"веган", re.IGNORECASE),
        GreenFactCandidate(kind="lifestyle", content={"key": "diet", "value": "vegan"}),
    ),
    (
        re.compile(_SELF + r"вегетариан", re.IGNORECASE),
        GreenFactCandidate(kind="lifestyle", content={"key": "diet", "value": "vegetarian"}),
    ),
    (
        # «я не ем мясо» — affirmative vegetarian statement; the «не» is part of
        # the phrase, so it is matched explicitly (not via the adjacency rule).
        re.compile(r"\bя\b\s+не\s+ем\s+мяс", re.IGNORECASE),
        GreenFactCandidate(kind="lifestyle", content={"key": "diet", "value": "vegetarian"}),
    ),
]


def extract_green_facts(text: str) -> list[GreenFactCandidate]:
    """Extract explicit green facts from one user turn.

    Returns a de-duplicated list (by ``dedup_key``); empty when nothing matches.
    Never raises — a non-string / empty input yields ``[]``.
    """

    if not text or not isinstance(text, str):
        return []

    found: dict[tuple[str, Any, Any], GreenFactCandidate] = {}
    for pattern, candidate in _DIET_RULES:
        if pattern.search(text) is not None:
            found.setdefault(candidate.dedup_key, candidate)
    return list(found.values())
