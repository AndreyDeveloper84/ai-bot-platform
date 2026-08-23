"""Deterministic explicit green-fact extractor (M-B2 / #1099, DRF-1261).

The pilot does NOT actively ask memory questions (founder decision 2026-07-09).
The write path is a **spontaneous, explicit** self-statement — e.g. the user
types «я веган». This module recognises a small, conservative set of such
statements with plain patterns (NOT an LLM). General conversational extraction
is post-pilot (M-C2, ADR-0011 §4 «inferred» source).

Everything here is 🟢 green + ``source='explicit'`` — the only zone/source the
pilot writes (green-explicit bypasses minor-protection per ADR-0011 §10.2).

# Owner ruling 2026-08-23 (DRF-1260) — НЕ ФАБРИКОВАТЬ ФАКТЫ

Normalisation to a known category is FORBIDDEN unless the user named it:

  «я веган»                    -> diet_type=vegan (no fabricated excluded_foods)
  «ем всё, только мясо не ем»  -> excluded_foods=["meat"], НЕ vegetarian
  «не ем свинину/глютен»       -> явное ограничение, НЕ вывод о религии/диагнозе

**Receiver limit (owner ruling, Ответ 3, 2026-08-23):** the Ayla receiver
(``UserPersonalContext``) accepts only ``diet_type`` from a fixed choice list —
there is NO ``excluded_foods`` / ``user_note`` field, and writing a food
exclusion as ``diet_type=vegetarian`` would be the exact fabrication the
ruling forbids. Rule: **store only what the receiver accepts without
distortion; what doesn't fit is DROPPED with an explicit log**, not stored
wrong. So «я не ем мясо» is detected, logged (``diet_exclusion_dropped``)
and NOT stored — the receiver extension (excluded_foods/user_note) is a
separate Ayla-side task, flagged in the report.

# Аллергия — отдельный периметр (DRF-1290)

«У меня аллергия на …» is sensitive/medical and must NOT land in green
memory — not in ``diet``, not as a preference. Until the dedicated perimeter
ships, allergy clauses are DROPPED with an explicit log
(``allergy_clause_dropped``) — never silently, never stored. The whole clause
carrying the allergy marker is excluded from extraction; clean clauses of the
same message («я веган, и у меня аллергия на орехи») still extract.

# Session context ≠ memory

«Сегодня хочу массаж в центре после шести до 3000» is the current search, not
a durable preference. Two guards: (1) session markers («сегодня», «сейчас»,
«завтра», …) suppress extraction entirely; (2) time/district/price/master
candidates require a durable-preference anchor («мне удобно», «обычно могу»,
«мой мастер», «комфортный бюджет»), which a one-off request does not carry.

Conservative by design: **high precision over recall.** A missed fact is
cheap; a wrong fact about the user erodes trust — the whole point of the
feature.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GreenFactCandidate:
    """An explicit green fact extracted from a user turn, ready for the writer.

    ``content`` always carries ``key`` + a scalar ``value`` (dedup identity);
    richer structure rides in extra keys (``diet_type``, ``min``/``max``, …).
    """

    kind: str
    content: dict[str, Any]

    @property
    def dedup_key(self) -> tuple[str, Any, Any]:
        """Identity for dedup: same (kind, key, value) → same fact."""

        return (self.kind, self.content.get("key"), self.content.get("value"))


@dataclass(frozen=True)
class ExtractionDrop:
    """A recognised statement that was deliberately NOT stored.

    ``reason`` is a stable machine tag (``allergy``, ``diet_exclusion``,
    ``favorite_master_unbridgeable``); ``detail`` is a non-personal marker
    (never the user's words — drops must not leak the dropped fact into logs).
    """

    reason: str
    detail: str = ""


@dataclass(frozen=True)
class ExtractionResult:
    """Candidates to persist + explicit drops (observability trail)."""

    candidates: list[GreenFactCandidate] = field(default_factory=list)
    drops: list[ExtractionDrop] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

# Self-anchor: «я», then only whitespace/dash, then the keyword stem. The
# adjacency is the negation guard: «я не веган» has «не» between «я» and the
# stem, so it never matches («я — веган» still does).
_SELF = r"\bя\b(?:\s*[—–-]\s*|\s+)"

# Session markers — «сегодня хочу массаж в центре после шести до 3000» is the
# current search, NOT a durable preference (owner ruling §4). Any session
# marker suppresses extraction for the whole turn.
_SESSION_RE = re.compile(
    r"\b(?:сегодня|сейчас|завтра|послезавтра|на\s+(?:этой|следующей)\s+неделе)\b",
    re.IGNORECASE,
)

# Allergy / medical-sensitivity markers (DRF-1290). A clause carrying one is
# dropped wholesale — never stored, always logged.
_ALLERGY_RE = re.compile(r"аллерг|непереносимост", re.IGNORECASE)

# Clause splitter — allergy scoping is per-clause so «я веган, и у меня
# аллергия на орехи» still extracts the clean «я веган» part.
_CLAUSE_SPLIT_RE = re.compile(r"[,.!?;\n]+|\s+но\s+")


# ---------------------------------------------------------------------------
# diet — полноценный домен (owner ruling §2)
# ---------------------------------------------------------------------------

# Named diet types — stored ONLY when the user names the category herself.
# «не ем свинину» must NEVER become halal: exclusions are not diet types.
# «теперь» between the anchor and the keyword is allowed («я теперь на кето»).
_SELF_NOW = _SELF + r"(?:теперь\s+)?"
_NAMED_DIET_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(_SELF_NOW + r"веган", re.IGNORECASE), "vegan"),
    (re.compile(_SELF_NOW + r"вегетариан", re.IGNORECASE), "vegetarian"),
    (re.compile(_SELF_NOW + r"на\s+кето", re.IGNORECASE), "keto"),
    (
        re.compile(_SELF_NOW + r"(?:ем\s+(?:только\s+)?|соблюдаю\s+)халял", re.IGNORECASE),
        "halal",
    ),
    (
        re.compile(_SELF_NOW + r"(?:ем\s+(?:только\s+)?|соблюдаю\s+)кошер", re.IGNORECASE),
        "kosher",
    ),
)

# Retractions — «я теперь снова ем мясо», «я больше не веган». The correction
# lifecycle (supersession reason=changed) is driven by this candidate.
# Deliberately narrow: «уже не вегетарианка» / «перестал быть веганом» stay
# unextracted (pinned by the existing false-positive tests — ambiguous
# phrasings are not a write basis).
_DIET_RETRACTION_RULES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bя\b\s+(?:теперь\s+)?снова\s+(?:ем|пью)\b", re.IGNORECASE),
    re.compile(r"\bя\b\s+больше\s+не\s+(?:веган|вегетариан\w*)", re.IGNORECASE),
)

# Explicit food exclusions — DETECTED (so the drop is explicit, not silent)
# but NOT stored: the receiver has no excluded_foods field (owner ruling,
# Ответ 3) and mapping «не ем мясо» → vegetarian is the forbidden fabrication.
# «не люблю» is deliberately absent: taste dislike is not a consumption
# exclusion and has no pilot key.
_DIET_EXCLUSION_RE = re.compile(
    r"\bя\b\s+(?:больше\s+)?не\s+(?:ем|пью|употребляю)\b", re.IGNORECASE
)


def _diet_candidate(diet_type: str | None, value: str) -> GreenFactCandidate:
    return GreenFactCandidate(
        kind="lifestyle",
        content={"key": "diet", "value": value, "diet_type": diet_type},
    )


# ---------------------------------------------------------------------------
# preferred_time_slots — удобное время (durable anchors only)
# ---------------------------------------------------------------------------

_TIME_ANCHOR_RE = re.compile(
    r"мне\s+(?:удобно|удобнее|лучше)\b"
    r"|\bя\s+(?:обычно\s+)?(?:могу|предпочитаю|свободен|свободна)\b"
    r"|\bобычно\s+(?:могу|мне\s+удобно)",
    re.IGNORECASE,
)

_SLOT_WORD_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"ранн\w*\s+утр", re.IGNORECASE), "early_morning"),
    (re.compile(r"поздн\w*\s+вечер|\bночью\b", re.IGNORECASE), "late_evening"),
    (re.compile(r"\bутр", re.IGNORECASE), "morning"),
    (re.compile(r"\bдн[её]м\b|\bдень\b|\bдня\b|по\s+дням", re.IGNORECASE), "afternoon"),
    (re.compile(r"вечер", re.IGNORECASE), "evening"),
)

# «после шести» — word numerals carry the cultural evening reading.
_AFTER_WORD_HOURS = {"пяти": 17, "шести": 18, "семи": 19, "восьми": 20, "девяти": 21}
_AFTER_NUM_RE = re.compile(r"после\s+(\d{1,2})(?::\d{2})?\b", re.IGNORECASE)
_AFTER_WORD_RE = re.compile(r"после\s+(пяти|шести|семи|восьми|девяти)\b", re.IGNORECASE)


def _hour_to_slot(hour: int) -> str:
    """Map a stated hour boundary to the contract TimeSlot vocabulary.

    This is an encoding of a STATED time into the receiver's fixed
    vocabulary (contract catalog), not an inference: «мне удобно после
    18:00» is the ruling's own example for preferred_time_slots.
    """

    if hour >= 21:
        return "late_evening"
    if hour >= 17:
        return "evening"
    if hour >= 12:
        return "afternoon"
    if hour >= 9:
        return "morning"
    return "early_morning"


def _time_slot_candidates(clause: str) -> list[GreenFactCandidate]:
    if _TIME_ANCHOR_RE.search(clause) is None:
        return []
    slots: list[str] = []
    for pattern, slot in _SLOT_WORD_RULES:
        if pattern.search(clause) is not None:
            slots.append(slot)
    match = _AFTER_NUM_RE.search(clause)
    if match is not None:
        slots.append(_hour_to_slot(int(match.group(1))))
    word = _AFTER_WORD_RE.search(clause)
    if word is not None:
        slots.append(_hour_to_slot(_AFTER_WORD_HOURS[word.group(1).lower()]))
    # «поздний вечер» ⊃ «вечер», «раннее утро» ⊃ «утро» — keep the specific.
    if "late_evening" in slots and "evening" in slots:
        slots.remove("evening")
    if "early_morning" in slots and "morning" in slots:
        slots.remove("morning")
    ordered: list[str] = []
    for slot in slots:
        if slot not in ordered:
            ordered.append(slot)
    return [
        GreenFactCandidate(
            kind="preference",
            content={"key": "preferred_time_slots", "value": slot},
        )
        for slot in ordered
    ]


# ---------------------------------------------------------------------------
# preferred_districts — предпочтительные районы
# ---------------------------------------------------------------------------

_DISTRICT_ANCHOR_RE = re.compile(
    r"мне\s+(?:удобно|удобнее)\s+(?:в|во)\b"
    r"|\bя\s+предпочитаю\s+(?:район\s+)?",
    re.IGNORECASE,
)
_DISTRICT_WORD_RE = re.compile(r"(?:\bв|\bво)\s+([А-Яа-яЁё][а-яёА-ЯЁ-]{2,})")
# Time/generic words that follow «в/во» but are not places.
_DISTRICT_STOPWORDS = frozenset(
    {"выходные", "выходной", "будни", "будний", "праздники", "принципе", "общем"}
)


def _district_candidates(clause: str) -> list[GreenFactCandidate]:
    if _DISTRICT_ANCHOR_RE.search(clause) is None:
        return []
    out: list[GreenFactCandidate] = []
    seen: set[str] = set()
    for match in _DISTRICT_WORD_RE.finditer(clause):
        word = match.group(1)
        low = word.lower()
        if low in _DISTRICT_STOPWORDS or low in seen:
            continue
        seen.add(low)
        # Verbatim as stated (may be inflected: «в Центре»). Morphological
        # normalisation to the nominative needs a morphology library —
        # post-pilot; storing the spoken form is the honest minimum.
        out.append(
            GreenFactCandidate(
                kind="preference",
                content={"key": "preferred_districts", "value": word},
            )
        )
    return out


# ---------------------------------------------------------------------------
# price_range — комфортный бюджет (один семантический ключ, min/max в контракте)
# ---------------------------------------------------------------------------

_PRICE_FOREIGN_RE = re.compile(r"\$|€|доллар|евро", re.IGNORECASE)
_PRICE_RANGE_RE = re.compile(
    r"(?:комфортн\w*|ориентиру\w*)[^.!?]{0,40}?"
    r"от\s+(\d[\d\s]*)\s*(?:до|–|-)\s*(\d[\d\s]*)",
    re.IGNORECASE,
)
_PRICE_MAX_RE = re.compile(
    r"(?:не\s+готов\w*\s+платить\s+(?:больше|дороже)"
    r"|(?:комфортн\w*|ориентиру\w*)[^.!?]{0,40}?до)\s+(\d[\d\s]*)",
    re.IGNORECASE,
)
_PRICE_MIN_RE = re.compile(
    r"(?:комфортн\w*|ориентиру\w*)[^.!?]{0,40}?от\s+(\d[\d\s]*)",
    re.IGNORECASE,
)
_TYS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*тыс", re.IGNORECASE)


def _price_candidate(clause: str) -> GreenFactCandidate | None:
    if _PRICE_FOREIGN_RE.search(clause) is not None:
        return None  # контракт — рубли; валютную фразу не угадываем

    def _num(raw: str) -> int | None:
        digits = raw.replace(" ", "").replace(" ", "")
        if not digits.isdigit():
            return None
        value = int(digits)
        return value if 0 < value <= 1_000_000 else None

    tys = _TYS_RE.search(clause)
    tys_value: int | None = None
    if tys is not None:
        try:
            tys_value = int(float(tys.group(1).replace(",", ".")) * 1000)
        except ValueError:
            tys_value = None

    lo: int | None = None
    hi: int | None = None
    rng = _PRICE_RANGE_RE.search(clause)
    if rng is not None:
        lo, hi = _num(rng.group(1)), _num(rng.group(2))
    else:
        mx = _PRICE_MAX_RE.search(clause)
        if mx is not None:
            hi = _num(mx.group(1))
        mn = _PRICE_MIN_RE.search(clause)
        if mn is not None and rng is None and mx is None:
            lo = _num(mn.group(1))
        if (
            hi is None
            and lo is None
            and tys_value is not None
            and ("комфортн" in clause.lower() or "ориентиру" in clause.lower())
        ):
            hi = tys_value
    if lo is None and hi is None:
        return None

    parts = []
    content: dict[str, Any] = {"key": "price_range", "currency": "RUB"}
    if lo is not None:
        content["min"] = f"{lo}.00"
        parts.append(f"min:{lo}")
    if hi is not None:
        content["max"] = f"{hi}.00"
        parts.append(f"max:{hi}")
    content["value"] = ",".join(parts)
    return GreenFactCandidate(kind="preference", content=content)


# ---------------------------------------------------------------------------
# favorite_masters — ТОЛЬКО явно названные предпочтения
# ---------------------------------------------------------------------------

_MASTER_RULES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:мой|моя)\s+(?:любим\w+\s+)?мастер\s*[—–-]?\s*(?:это\s+)?([А-ЯЁ][а-яё]+)",
        re.IGNORECASE,
    ),
    re.compile(r"любим\w+\s+мастер\s*[—–-]?\s*(?:это\s+)?([А-ЯЁ][а-яё]+)", re.IGNORECASE),
    re.compile(r"предпочитаю\s+мастера\s+([А-ЯЁ][а-яё]+)", re.IGNORECASE),
    re.compile(r"(?:хожу|записываюсь)\s+(?:только|всегда)\s+к\s+([А-ЯЁ][а-яё]+)", re.IGNORECASE),
    re.compile(r"всегда\s+(?:хожу|записываюсь)\s+к\s+([А-ЯЁ][а-яё]+)", re.IGNORECASE),
)


def _master_candidates(clause: str) -> list[GreenFactCandidate]:
    out: list[GreenFactCandidate] = []
    seen: set[str] = set()
    for pattern in _MASTER_RULES:
        match = pattern.search(clause)
        if match is None:
            continue
        name = match.group(1)
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        # Verbatim as stated (may be inflected: «к Анне»). The Ayla receiver
        # wants SpecialistProfile UUIDs — a name is NOT bridgeable without a
        # cross-tenant lookup; the bridge logs and skips (contract gap).
        out.append(
            GreenFactCandidate(
                kind="preference",
                content={"key": "favorite_masters", "value": name},
            )
        )
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# Single-cardinality keys keep at most ONE candidate per turn — the last one
# stated («я больше не веган, я теперь на кето» → keto wins).
_SINGLE_TURN_KEYS = frozenset({"diet", "price_range"})


def extract_user_facts(text: str) -> ExtractionResult:
    """Extract explicit user-stated green facts from one user turn.

    Returns candidates + explicit drops. Never raises — a non-string / empty
    input yields an empty result.
    """

    if not text or not isinstance(text, str):
        return ExtractionResult()

    drops: list[ExtractionDrop] = []

    # DRF-1290 — allergy clauses are dropped BEFORE anything else, loudly.
    clean_clauses: list[str] = []
    for clause in _CLAUSE_SPLIT_RE.split(text):
        clause = clause.strip()
        if not clause:
            continue
        if _ALLERGY_RE.search(clause) is not None:
            drops.append(ExtractionDrop(reason="allergy"))
            logger.warning(
                "persona.memory_extract.allergy_clause_dropped — sensitive "
                "perimeter (DRF-1290): allergy statement NOT stored"
            )
            continue
        clean_clauses.append(clause)

    # Session context ≠ memory: a session marker suppresses the turn.
    if _SESSION_RE.search(text) is not None:
        return ExtractionResult(candidates=[], drops=drops)

    candidates: list[GreenFactCandidate] = []
    for clause in clean_clauses:
        retraction = any(p.search(clause) is not None for p in _DIET_RETRACTION_RULES)
        named = next(
            (value for pattern, value in _NAMED_DIET_RULES if pattern.search(clause)),
            None,
        )
        if retraction:
            candidates.append(_diet_candidate(None, "none"))
        elif named is not None:
            candidates.append(_diet_candidate(named, named))
        elif _DIET_EXCLUSION_RE.search(clause) is not None:
            # Owner ruling (Ответ 3): excluded_foods has no receiver field;
            # mapping to a diet_type is the forbidden fabrication. Drop loudly.
            drops.append(ExtractionDrop(reason="diet_exclusion"))
            logger.warning(
                "persona.memory_extract.diet_exclusion_dropped — receiver has "
                "no excluded_foods field (contract gap, DRF-1261): NOT stored"
            )

        candidates.extend(_time_slot_candidates(clause))
        candidates.extend(_district_candidates(clause))
        price = _price_candidate(clause)
        if price is not None:
            candidates.append(price)
        candidates.extend(_master_candidates(clause))

    # Dedup by identity; single-turn keys keep only the last stated value.
    seen: dict[tuple[str, Any, Any], GreenFactCandidate] = {}
    order: list[tuple[str, Any, Any]] = []
    for candidate in candidates:
        key = candidate.dedup_key
        if candidate.content.get("key") in _SINGLE_TURN_KEYS:
            # replace any earlier candidate of the same memory key
            stale = [k for k in order if k[1] == key[1]]
            for k in stale:
                order.remove(k)
                seen.pop(k, None)
        if key not in seen:
            order.append(key)
        seen[key] = candidate
    return ExtractionResult(candidates=[seen[k] for k in order], drops=drops)


def extract_green_facts(text: str) -> list[GreenFactCandidate]:
    """Back-compat wrapper: candidates only (drops logged inside)."""

    return extract_user_facts(text).candidates
