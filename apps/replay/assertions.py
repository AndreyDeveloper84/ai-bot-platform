"""Fixture assertion engine (DRF-515 + DRF-516 / Sprint 5 / D2 + D3).

Pure functions that evaluate a captured trace against the fixture's
``must_pass`` / ``forbidden`` / ``voice_check`` constraints. Returns
a list of failure strings (empty = pass).

### Why list[str], not bool

Operators need to debug a fixture failure from the CI run log. A list
of "intent expected 'faq', got 'small_talk'" beats a single "failed"
boolean. The runner formats them into the report JSON.

### Trace shape (what evaluate reads)

The runner shapes the trace dict per turn:

    {
        "intent": str,
        "skill_used": str,
        "safety_decision": "allow" | "block",
        "tool_calls": [{"name": str, "args": dict}, ...],
        "response_text": str,
    }

Missing keys are treated as failures by the respective assertion.
"""

from __future__ import annotations

from typing import Any


def evaluate(
    trace: dict[str, Any],
    must_pass: list[dict[str, Any]],
    forbidden: list[dict[str, Any]],
) -> list[str]:
    """Run must_pass + forbidden against `trace`. Return failure list.

    Supported must_pass keys (each dict in must_pass has ONE key):
      - intent: exact match against trace["intent"]
      - skill_used: exact match against trace["skill_used"]
      - safety_decision: exact match (allow/block)
      - tool_called: str — must appear in trace["tool_calls"][*].name
      - response_contains_any: list[str] — at least one substring in
        trace["response_text"]
      - response_contains_all: list[str] — all substrings present

    Forbidden uses the same DSL but inverts:
      - response_contains_any → fixture fails if ANY substring matches

    Returns:
      List of human-readable failure strings; empty = pass.
    """

    failures: list[str] = []
    for constraint in must_pass:
        failures.extend(_check_must_pass(trace, constraint))
    for constraint in forbidden:
        failures.extend(_check_forbidden(trace, constraint))
    return failures


def evaluate_voice(response_text: str, voice_check: dict[str, Any]) -> list[str]:
    """Run voice_check constraints against the response text.

    Supported keys:
      - max_length: int — len(response_text) over limit fails
      - caps_lock: float — fraction of ALL-CAPS chars over limit fails
      - forbidden_phrases: list[str] — regex patterns; delegates to
        ``apps.orchestrator.safety.voice_check.validate_voice``
    """

    failures: list[str] = []

    max_length = voice_check.get("max_length")
    if max_length is not None and len(response_text) > int(max_length):
        failures.append(f"voice_check.max_length: {len(response_text)} > {max_length}")

    caps_threshold = voice_check.get("caps_lock")
    if caps_threshold is not None and response_text:
        cap_chars = sum(1 for c in response_text if c.isupper())
        # Denominator: only alphabetic chars (otherwise digits + punctuation
        # bias toward "not caps-locky" when actually screaming).
        alpha = sum(1 for c in response_text if c.isalpha())
        if alpha > 0:
            ratio = cap_chars / alpha
            if ratio > float(caps_threshold):
                failures.append(f"voice_check.caps_lock: {ratio:.2f} > {caps_threshold}")

    forbidden_phrases = voice_check.get("forbidden_phrases") or []
    if forbidden_phrases:
        # Phase 0: pattern match locally to avoid touching the Sprint 4
        # validate_voice() event-emission path (which needs tenant scope).
        # Phase 1 may swap to validate_voice() if we add a no-emit mode.
        for pattern in forbidden_phrases:
            if _matches_pattern(response_text, pattern):
                failures.append(f"voice_check.forbidden_phrase: {pattern!r} matched")

    return failures


def _matches_pattern(text: str, pattern: str) -> bool:
    """Cheap re.search wrapper — case-insensitive."""

    import re

    try:
        return bool(re.search(pattern, text, re.IGNORECASE))
    except re.error:
        # Bad regex: treat as no match (operator typo in fixture YAML).
        return False


# --- must_pass evaluators ---------------------------------------------------


def _check_must_pass(trace: dict[str, Any], constraint: dict[str, Any]) -> list[str]:
    """Each constraint is a one-key dict — dispatch to the right check."""

    failures: list[str] = []
    for key, expected in constraint.items():
        failures.extend(_dispatch_check(trace, key, expected, polarity="must"))
    return failures


def _check_forbidden(trace: dict[str, Any], constraint: dict[str, Any]) -> list[str]:
    """Inverted dispatch: failure if the constraint matches."""

    failures: list[str] = []
    for key, expected in constraint.items():
        failures.extend(_dispatch_check(trace, key, expected, polarity="forbidden"))
    return failures


def _dispatch_check(trace: dict[str, Any], key: str, expected: Any, *, polarity: str) -> list[str]:
    """Single check dispatcher.

    polarity = "must" → must match; failure when miss
    polarity = "forbidden" → must NOT match; failure when hit
    """

    response_text = str(trace.get("response_text", ""))

    if key == "intent":
        actual = trace.get("intent", "")
        return _polarity_check("intent", actual == expected, expected, actual, polarity)

    if key == "skill_used":
        actual = trace.get("skill_used", "")
        return _polarity_check("skill_used", actual == expected, expected, actual, polarity)

    if key == "safety_decision":
        actual = trace.get("safety_decision", "")
        return _polarity_check("safety_decision", actual == expected, expected, actual, polarity)

    if key == "tool_called":
        calls = trace.get("tool_calls", []) or []
        names = {c.get("name") for c in calls if isinstance(c, dict)}
        hit = expected in names
        return _polarity_check("tool_called", hit, expected, sorted(names), polarity)

    if key == "response_contains_any":
        substrings = expected if isinstance(expected, list) else [expected]
        hit = any(s in response_text for s in substrings)
        return _polarity_check("response_contains_any", hit, substrings, "<text>", polarity)

    if key == "response_contains_all":
        substrings = expected if isinstance(expected, list) else [expected]
        hit = all(s in response_text for s in substrings)
        # response_contains_all on forbidden is unusual but supported.
        return _polarity_check("response_contains_all", hit, substrings, "<text>", polarity)

    return [f"unknown_assertion_key: {key!r}"]


def _polarity_check(
    label: str,
    hit: bool,
    expected: Any,
    actual: Any,
    polarity: str,
) -> list[str]:
    """Translate boolean hit into polarity-aware failure msg."""

    if polarity == "must":
        if not hit:
            return [f"must_pass.{label}: expected {expected!r}, got {actual!r}"]
        return []
    # forbidden
    if hit:
        return [f"forbidden.{label}: {expected!r} should NOT match"]
    return []
