"""Trace differ — text similarity + tool-calls + metrics delta (DRF-517/518/519 / Sprint 5 / D4+D5+D6).

Three pure functions used by the CLI ``diff`` subcommand to compare
a baseline trace against a candidate (post-prompt-change) trace.

### Phase 0 vs Phase 1

- text_similarity: Phase 0 ships ``difflib.SequenceMatcher.ratio()``.
  Phase 1 swaps to cosine-via-embeddings; signature stable.
- tool_calls_diff: structural diff, no LLM dependency, stays Phase-0.
- metrics_delta: numeric delta with thresholds, stays Phase-0.

### Why pure functions

No tenant scope, no DB, no settings. The CLI orchestrates; differ
just computes. Easy to unit-test, easy to replace.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from typing import Any

# Default delta thresholds — design §7.5.
LATENCY_ALARM_THRESHOLD = 0.30  # +30%
TOKENS_ALARM_THRESHOLD = 0.50  # +50%


# --- D4: text similarity ---------------------------------------------------


def text_similarity(a: str, b: str) -> float:
    """Return similarity ratio in [0.0, 1.0].

    Phase 0: difflib.SequenceMatcher. Phase 1 will swap to cosine
    over OpenAI text-embedding-3-small without changing this signature.

    Identical strings → 1.0. Completely different (no chars in common)
    → 0.0. Partial overlap → fractional.
    """

    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# --- D5: tool calls structural diff ----------------------------------------


def _tool_call_key(call: dict[str, Any]) -> tuple[str, str]:
    """Stable (tool_name, args_hash) for set-style diffing."""

    name = str(call.get("name", ""))
    args = call.get("args", {})
    args_json = json.dumps(args, sort_keys=True, default=str)
    args_hash = hashlib.sha256(args_json.encode("utf-8")).hexdigest()[:16]
    return name, args_hash


def tool_calls_diff(
    a_calls: list[dict[str, Any]], b_calls: list[dict[str, Any]]
) -> dict[str, list[Any]]:
    """Structural diff of two tool-call lists.

    Returns:
      ``{"added": [...], "removed": [...], "changed": [...]}``

      - added: (name, args_hash) tuples present in b not in a
      - removed: in a not in b
      - changed: name present in both but args_hash differs
    """

    a_keys = {_tool_call_key(c) for c in a_calls}
    b_keys = {_tool_call_key(c) for c in b_calls}

    a_by_name = {name: hsh for name, hsh in a_keys}
    b_by_name = {name: hsh for name, hsh in b_keys}

    added: list[tuple[str, str]] = sorted(b_keys - a_keys)
    removed: list[tuple[str, str]] = sorted(a_keys - b_keys)

    # Filter "changed" out of added/removed: same name in both with
    # different hash.
    changed_names = {
        name for name in a_by_name.keys() & b_by_name.keys() if a_by_name[name] != b_by_name[name]
    }
    if changed_names:
        added = [(n, h) for n, h in added if n not in changed_names]
        removed = [(n, h) for n, h in removed if n not in changed_names]

    changed: list[dict[str, str]] = [
        {"name": name, "from": a_by_name[name], "to": b_by_name[name]}
        for name in sorted(changed_names)
    ]

    return {"added": added, "removed": removed, "changed": changed}


# --- D6: metrics delta -----------------------------------------------------


def _pct_delta(baseline: float, candidate: float) -> float:
    """Return (candidate - baseline) / baseline, safe for zero baseline.

    Zero baseline + positive candidate → 1.0 (100% increase from nothing).
    Both zero → 0.0.
    """

    if baseline == 0:
        return 0.0 if candidate == 0 else 1.0
    return (candidate - baseline) / baseline


def metrics_delta(
    a: dict[str, Any],
    b: dict[str, Any],
    *,
    latency_threshold: float = LATENCY_ALARM_THRESHOLD,
    tokens_threshold: float = TOKENS_ALARM_THRESHOLD,
) -> dict[str, Any]:
    """Compute voice/latency/tokens delta with alarm flags.

    Args:
      a: baseline metrics dict (latency_ms, tokens, voice_check_passed)
      b: candidate metrics dict (same shape)
      latency_threshold: fractional increase that triggers latency alarm
      tokens_threshold: fractional increase that triggers tokens alarm

    Returns:
      Dict with deltas + alarm booleans.
    """

    latency_a = float(a.get("latency_ms", 0))
    latency_b = float(b.get("latency_ms", 0))
    latency_delta = _pct_delta(latency_a, latency_b)

    tokens_a = float(a.get("tokens", 0))
    tokens_b = float(b.get("tokens", 0))
    tokens_delta = _pct_delta(tokens_a, tokens_b)

    voice_a = bool(a.get("voice_check_passed", True))
    voice_b = bool(b.get("voice_check_passed", True))

    return {
        "latency_delta_pct": latency_delta,
        "latency_alarm": latency_delta > latency_threshold,
        "tokens_delta_pct": tokens_delta,
        "tokens_alarm": tokens_delta > tokens_threshold,
        "voice_check_changed": voice_a != voice_b,
    }
