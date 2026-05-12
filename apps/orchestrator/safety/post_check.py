"""Safety post-check (DRF-538 / Sprint 6 / O4).

Step 12 of the orchestrator pipeline. Inspects the outbound assistant
text AFTER the skill returned but BEFORE the composer ships it. Returns
:class:`SafetyVerdict` ∈ {allow, revise, block} — no `handoff` here
(escalation is a pre-skill decision, not a post-skill one).

### Verdicts

- **allow** — text passes; composer ships as-is.
- **revise** — text triggered a soft pattern (overly assertive medical
  claim, etc.); pipeline asks the skill to rewrite or replaces with a
  templated safe variant. Phase 0 caller maps revise → block (skill
  retry comes in Phase 1 with the retry loop).
- **block** — outright dangerous output. Pipeline replaces with a
  canned safety disclaimer.

### Pattern source — delegated to Sprint 4 / C4

Per the plan: O4 must NOT duplicate Sprint 4's `voice_check.validate_voice`.
Post-check forwards the response text + per-tenant brand voice to
:func:`apps.orchestrator.safety.voice_check.validate_voice`, which:

1. Compiles + matches `voice["forbidden_phrases"]` regex patterns
2. Emits the `safety_triggered` event on any match (consistent
   observability across pre/post)
3. Returns the matched pattern strings

O4 additionally runs an outbound-specific regex set
(`_OUTBOUND_PATTERNS`) — patterns that should never appear in a bot
reply regardless of brand voice (PII leak markers, leaked system
prompt fragments, hallucinated guarantee claims).

### Verdict priority

BLOCK > REVISE > ALLOW. Multiple matches collapse to highest.

### Latency budget

PHASE0_DESIGN §5.2 budget: <50ms p95. Regex search + voice_check
delegate; no LLM. Test: 100 calls in <100ms.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class PostCheckVerdict(str, Enum):
    ALLOW = "allow"
    REVISE = "revise"
    BLOCK = "block"


@dataclass(frozen=True)
class PostCheckResult:
    """Verdict + matched patterns (forensic provenance)."""

    verdict: PostCheckVerdict
    matched_patterns: list[str] = field(default_factory=list)
    reason: str = ""


# Outbound-specific patterns — Bot must NEVER produce these regardless
# of brand voice. Pinned constants; tenant overrides land in BrandVoiceConfig.
_OUTBOUND_PATTERNS: dict[str, list[str]] = {
    PostCheckVerdict.BLOCK.value: [
        # Direct PII leak (phone number in response) — handles "+79991234567",
        # "+7 999 123-45-67", "+7 (999) 123-45-67". * allows multi-char
        # separators (close-paren + space, etc.).
        r"\+7[\s\-\(]*\d{3}[\s\-\)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}",
        # Email leak
        r"[\w\.-]+@[\w\.-]+\.\w+",
        # OpenAI key leak (catastrophic — log + block)
        r"sk-[a-zA-Z0-9]{20,}",
        # System prompt leakage signals
        r"(?i)\b(system prompt|вот мой промпт|here is my system prompt)\b",
        # Hallucinated medical guarantees
        r"(?i)\b(гарантирую полное излечение|guaranteed cure|100% safe)\b",
    ],
    PostCheckVerdict.REVISE.value: [
        # Soft over-assertion — bot should soften, not refuse
        r"(?i)\b(I am certain|i'm 100% sure|без сомнения вылечит)\b",
    ],
}


# Compiled patterns cache. Shared with pre_check would be a leak —
# different verdict sets. Use a separate cache.
_COMPILED: dict[str, re.Pattern[str]] = {}


def _compile(pattern: str) -> re.Pattern[str] | None:
    """Return cached compiled regex or None on bad pattern."""

    cached = _COMPILED.get(pattern)
    if cached is not None:
        return cached
    try:
        compiled = re.compile(pattern)
    except re.error:
        logger.warning("safety.post_check.bad_regex pattern=%r", pattern)
        return None
    _COMPILED[pattern] = compiled
    return compiled


def reset_cache() -> None:
    """Test hook — flush the compiled-regex cache."""

    _COMPILED.clear()


# Verdict priority — higher index = higher priority.
_VERDICT_PRIORITY = [
    PostCheckVerdict.ALLOW.value,
    PostCheckVerdict.REVISE.value,
    PostCheckVerdict.BLOCK.value,
]


def post_check(
    response_text: str,
    *,
    brand_voice: dict | None = None,
) -> PostCheckResult:
    """Inspect outbound assistant text → :class:`PostCheckResult`.

    Args:
      response_text: text the bot is about to send.
      brand_voice: optional dict (typically from BrandVoiceConfig). If
        ``forbidden_phrases`` present, delegates to Sprint 4 / C4
        :func:`validate_voice` and treats any match as BLOCK.

    Returns:
      :class:`PostCheckResult`. Empty text → ALLOW (skill returned
      nothing — caller's problem, not safety's).

    ### Verdict priority
    BLOCK > REVISE > ALLOW. Multiple matches across verdicts: highest wins.
    `matched_patterns` carries ALL matches for forensic / observability.
    """

    if not response_text:
        return PostCheckResult(verdict=PostCheckVerdict.ALLOW)

    matched: list[str] = []
    triggered: set[str] = set()

    # 1. Outbound-specific regex set (PII leak, key leak, hallucinated guarantees).
    for verdict, patterns in _OUTBOUND_PATTERNS.items():
        for pattern in patterns:
            compiled = _compile(pattern)
            if compiled is None:
                continue
            if compiled.search(response_text):
                matched.append(pattern)
                triggered.add(verdict)

    # 2. Delegate brand-voice patterns to Sprint 4 / C4 (no duplication).
    if brand_voice and brand_voice.get("forbidden_phrases"):
        try:
            from apps.orchestrator.safety.voice_check import validate_voice

            voice_matches = validate_voice(response_text, brand_voice)
            if voice_matches:
                matched.extend(voice_matches)
                triggered.add(PostCheckVerdict.BLOCK.value)
        except Exception:  # noqa: BLE001 — post-check is safety boundary
            logger.exception("safety.post_check.voice_check_failed")

    final = _reduce(triggered)
    return PostCheckResult(
        verdict=final,
        matched_patterns=matched,
        reason=_reason(triggered, matched),
    )


def _reduce(verdicts: set[str]) -> PostCheckVerdict:
    """Pick the highest-priority verdict. Defaults to ALLOW."""

    if not verdicts:
        return PostCheckVerdict.ALLOW
    highest = max(
        verdicts,
        key=lambda v: _VERDICT_PRIORITY.index(v) if v in _VERDICT_PRIORITY else -1,
    )
    return PostCheckVerdict(highest)


def _reason(verdicts: set[str], matched: list[str]) -> str:
    if not verdicts:
        return ""
    return f"matched={len(matched)} verdicts={sorted(verdicts)}"
