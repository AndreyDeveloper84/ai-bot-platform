"""Voice forbidden-phrases guard (DRF-491 / Sprint 4 / C4).

Post-LLM safety check: scans outbound assistant text for regex
patterns in ``voice["forbidden_phrases"]``. Returns a list of matched
pattern strings (empty = clean). Emits ``safety_triggered`` event
on any match.

### Design choices

- **Regex, not NLP** — Phase 0 ships keyword/phrase patterns. Phase 1
  adds an NLP layer.
- **Case-insensitive by default** — operators don't have to remember
  to add ``(?i)`` to every pattern.
- **Lazy compile + cache** — patterns compile once per unique regex
  string, kept in a module-level dict. Reset via :func:`reset_cache`
  in tests.
- **Bad regex skipped, not raised** — operator typos shouldn't crash
  the request path. Log a warning and continue with the remaining
  patterns.
- **Returns all matches, not just first** — operator sees the full
  list of triggered patterns.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from apps.events.services import emit
from apps.events.vocabulary import SAFETY_TRIGGERED

logger = logging.getLogger(__name__)

# Cache compiled patterns by (raw pattern string, case-flag). Module-level
# dict — survives the process; reset via reset_cache() in tests.
_COMPILED: dict[str, re.Pattern[str]] = {}


def _compile(pattern: str) -> re.Pattern[str] | None:
    """Return cached compiled regex or None on bad pattern."""

    cached = _COMPILED.get(pattern)
    if cached is not None:
        return cached
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error:
        logger.warning("voice_check.bad_regex pattern=%r", pattern)
        return None
    _COMPILED[pattern] = compiled
    return compiled


def reset_cache() -> None:
    """Test hook — flush the compiled-regex cache."""

    _COMPILED.clear()


def validate_voice(text: str, voice: dict[str, Any]) -> list[str]:
    """Scan `text` for forbidden phrases.

    Args:
      text: outbound assistant text to scan.
      voice: dict from :func:`apps.voice.services.load_brand_voice`.

    Returns:
      List of matched pattern strings. Empty list = clean.

    Side effects:
      Emits one ``safety_triggered`` event per call with the full
      matches list when any pattern fires. No event emitted when
      clean (telemetry hygiene).
    """

    patterns = voice.get("forbidden_phrases", []) or []
    if not patterns:
        return []

    matches: list[str] = []
    for pattern in patterns:
        if not isinstance(pattern, str):
            logger.warning("voice_check.non_string_pattern pattern=%r", pattern)
            continue
        compiled = _compile(pattern)
        if compiled is None:
            continue  # bad regex, logged in _compile
        if compiled.search(text):
            matches.append(pattern)

    if matches:
        emit(
            SAFETY_TRIGGERED,
            properties={
                "reason": "forbidden_phrase",
                "patterns": matches,
                "voice_version": voice.get("version", 0),
            },
        )
        logger.info(
            "voice_check.forbidden_phrase_matched count=%d voice_version=%s",
            len(matches),
            voice.get("version", 0),
        )
    return matches
