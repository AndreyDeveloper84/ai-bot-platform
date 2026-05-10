"""Phase 3.2A T02: free-text health-signal detector for TIER-B trigger.

Pure regex (no LLM). High-signal phrases only — false negatives acceptable
(LLM-classifier — backlog Phase 3.2C).

Returns a slug ∈ {"pregnancy", "breastfeeding", "diabetes", "hypertension",
"eating_disorder"} or None. Caller maps slug → first-screen routing.
"""
from __future__ import annotations

import re

# Patterns ranked by specificity — first match wins.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("pregnancy", re.compile(
        r"\b(берем(е|и)н|выкидыш|вынашиваю|вынашиваеш)", re.IGNORECASE,
    )),
    ("breastfeeding", re.compile(
        r"\b(кормлю\s+груд|на\s+гв|грудное\s+вскармливан)", re.IGNORECASE,
    )),
    ("diabetes", re.compile(
        r"\b(диабет|преддиабет|инсулин(?!овая))", re.IGNORECASE,
    )),
    ("hypertension", re.compile(
        r"\b(гипертон|давлени(е|я)\s+\d|повышенное\s+давлен)", re.IGNORECASE,
    )),
    ("eating_disorder", re.compile(
        r"(срыв\b|переед(а|е)|ненавижу\s+свой\s+вес|противно\s+на\s+себя)",
        re.IGNORECASE,
    )),
]


def detect_health_signal(text) -> str | None:
    """Return slug if text matches a known health pattern, else None.

    Type-tolerant: non-string inputs return None (defensive).
    """
    if not isinstance(text, str) or not text.strip():
        return None
    for slug, pattern in _PATTERNS:
        if pattern.search(text):
            return slug
    return None
