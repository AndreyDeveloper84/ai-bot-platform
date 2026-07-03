"""Safety pre-check (DRF-537 / Sprint 6 / O3).

Step 7 of the orchestrator pipeline. Inspects user text + intent decision
BEFORE invoking the skill; returns a :class:`SafetyVerdict` ∈
{allow, clarify, block, handoff}. Regex keyword guard — no LLM, <10ms p95.

### Verdicts

- **allow** — proceed to skill dispatch (step 10).
- **clarify** — message is ambiguous; reply with a generic clarification
  prompt, do NOT engage the skill (low-risk medical question, vague
  pronoun reference, etc.).
- **block** — refuse outright with a disclaimer (acute medical symptom,
  legal advice request, forbidden topic). Pipeline skips skill + tool
  layers, jumps to composer with a canned safety response.
- **handoff** — escalate to a human operator via AdminTask. Used when
  the regex hits a high-risk pattern (suicidal ideation, abuse, etc.)
  OR when intent_decision.risk_level=='high'.

### Pattern source

- Default patterns ship in :const:`_DEFAULT_PATTERNS` — Russian + English
  for medical / drug / acute / legal / suicidal / forbidden.
- Tenant overrides via ``settings.SAFETY_PATTERNS`` (merged on top of
  defaults — partial override doesn't wipe defaults).
- Per-tenant BrandVoiceConfig.forbidden_phrases is consulted as a
  generic "block" gate (operators add tenant-specific disallowed
  vocabulary there; voice_check (Sprint 4 / C4) validates outbound).

### intent_decision integration

The router (O2) returns `risk_level` ∈ {low, medium, high}. Pre-check
elevates verdicts by risk:
- intent_decision.risk_level=='high' → at least 'handoff' regardless of regex
- intent_decision.risk_level=='medium' → blocked patterns become 'handoff'
- intent_decision.risk_level=='low' → normal regex verdicts

This lets the LLM context-aware risk assessment elevate borderline
cases that the regex alone might miss.

### Latency budget

PHASE0_DESIGN §5.2 budget: <10ms. Per-pattern compilation is cached at
module load. Test: 100 calls in <50ms.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


class SafetyVerdict(str, Enum):
    ALLOW = "allow"
    CLARIFY = "clarify"
    BLOCK = "block"
    HANDOFF = "handoff"


@dataclass(frozen=True)
class SafetyResult:
    """Verdict + provenance: which pattern fired (forensic / observability)."""

    verdict: SafetyVerdict
    matched_patterns: list[str] = field(default_factory=list)
    reason: str = ""


# Pinned default safety patterns. Tenant overrides MERGE on top via settings.
# Verdicts encode the action — patterns map to specific verdicts.
_DEFAULT_PATTERNS: dict[str, list[str]] = {
    # ── Suicidal ideation / self-harm (#1081) ─────────────────────────────
    # Coverage-first (founder 2026-07-03): a MISS is a safety failure; a
    # false-positive crisis reply is only a UX cost on a beauty marketplace.
    # This is the LIVE, SOLE net (no LLM fallback), so patterns lean toward
    # recall. Russian morphology via \w* stems; reflexive `себя`/`себе` matched
    # in BOTH word orders (Russian reorders freely — «убью себя» AND «себя
    # убью»). EN verb stems keep a leading \b so «send it» / «change myself» /
    # «haircut myself» don't false-trigger. Known accepted over-triggers
    # («умираю как хочу…», «убьюсь если…», «cut myself shaving») are in the PR
    # #1081 review table.
    SafetyVerdict.HANDOFF.value: [
        # RU — base stems + idioms «наложить на себя руки» / «счёты с жизнью».
        r"(?i)(\bсамоубийств|\bсуицид|налож\w*\s+на\s+себя\s+руки|сч[её]ты\s+с\s+жизнью)",
        # RU — kill-self, either word order.
        r"(?i)(убить\s+себя|себя\s+убить|убь[юё]\s+себя|себя\s+убь\w*|\bубьюсь\b"
        r"|поконч\w*\s+(с\s+собой|со\s+всем))",
        # RU — want-to-die / not-want-to-live / tired-of-life.
        r"(?i)(хоч\w*\s+(умереть|умирать|сдохнуть|исчезнуть)|лучше\s+(мне\s+)?умереть"
        r"|не\s+хоч\w*\s+(больше\s+)?(жить|существовать)|жить\s+(больше\s+)?не\s+хоч"
        r"|надоело\s+жить|устал\w*\s+(жить|от\s+жизни))",
        # RU — no-meaning-in-life (both «нет смысла жить» and «смысла в жизни нет»).
        r"(?i)((нет|не\s+вижу|какой)\s+смысла?\s+(в\s+)?жи(ть|зни)|зачем\s+(мне\s+)?жить"
        r"|жизнь\s+не\s+имеет\s+смысла|смысла?\s+в\s+жизни\s+нет|в\s+жизни\s+нет\s+смысла)",
        # RU — burden / better-off-gone ideation.
        r"(?i)(лучше\s+бы\s+(я\s+(не\s+жил|не\s+родил|умер)|меня\s+не\s+было)"
        r"|(всем\s+)?(будет\s+)?лучше\s+без\s+меня|без\s+меня\s+(всем\s+)?лучше)",
        # RU — self-harm acts, either word order.
        r"(?i)((реж|рез|порез)\w*\s+себя|себя\s+(реж|рез|порез)\w*"
        r"|причин\w*\s+себе\s+(вред|боль)|себе\s+(вред|боль|больно|причин\w*|навред\w*)"
        r"|навред\w*\s+себе)",
        # RU — method statements. Reflexive endings vary (вскроюСЬ / вскрытьСЯ).
        r"(?i)(вскр\w*\s+вены|(реж|рез)\w*\s+вены|вскр(о|ы)\w*с[ья]|повеш\w*сь|повеси\w*ся"
        r"|наглота\w*\s+таблеток|горсть\s+таблеток|таблеток\s+и\s+усну"
        r"|(прыгн|спрыгн|шагн)\w*\s+с\s+(крыши|моста|балкона|окна)|исчезнуть\s+навсегда)",
        # EN — want-to-die / not-want-to-live / no-point.
        r"(?i)(wan(t\w*|na)\s+(to\s+)?die|(don'?t|do\s+not)\s+want\s+to\s+(live|be\s+alive)"
        r"|no\s+reason\s+to\s+live|no\s+point\s+(in\s+)?living|nothing\s+to\s+live\s+for"
        r"|better\s+off\s+dead)",
        # EN — method / self-harm verbs (leading \b guards against send/change/haircut).
        r"(?i)(\bkill\w*\s+my\s?self|\bhang\w*\s+my\s?self|\b(hurt|harm|cut)\w*\s+my\s?self"
        r"|\bslit\w*\s+my\s+wrist|take\s+my\s+own\s+life|\bend\s+(my\s+life|it(\s+all)?)\b"
        r"|\bsuicid|self[\s-]?harm|\boverdos)",
        # Acute medical emergency
        r"(?i)(\bскорая\b|\bemergency\b|\bумираю\b|\bdying\b|сердечный приступ|heart attack)",
        # Abuse / domestic violence — stems
        r"(?i)(\bизбива|\bнасили|\babuse\b|\bbattered\b)",
    ],
    SafetyVerdict.BLOCK.value: [
        # Specific drug names (recommend → block)
        r"(?i)\b(ибупрофен|анальгин|парацетамол|кеторол|tramadol|opioid)\b",
        # Diagnosis requests with definitive words
        r"(?i)\b(поставьте диагноз|diagnose me|у меня (рак|онколог))",
        # Legal advice
        r"(?i)\b(подать в суд|sue them|lawsuit|юридическ.{1,3} совет)\b",
    ],
    SafetyVerdict.CLARIFY.value: [
        # Vague medical questions (need handoff suggestion, not block)
        r"(?i)\b(что у меня|what's wrong with me|почему болит)\b",
    ],
}


# Compiled patterns cache. Key: pattern source string. Value: compiled regex.
_COMPILED: dict[str, re.Pattern[str]] = {}


def _compile(pattern: str) -> re.Pattern[str] | None:
    """Return cached compiled regex or None on bad pattern."""

    cached = _COMPILED.get(pattern)
    if cached is not None:
        return cached
    try:
        compiled = re.compile(pattern)
    except re.error:
        logger.warning("safety.pre_check.bad_regex pattern=%r", pattern)
        return None
    _COMPILED[pattern] = compiled
    return compiled


def reset_cache() -> None:
    """Test hook — flush the compiled-regex cache."""

    _COMPILED.clear()


def _verdict_patterns() -> dict[str, list[str]]:
    """Merge settings overrides on top of defaults.

    Tenant adds custom patterns via ``settings.SAFETY_PATTERNS = {verdict: [patterns]}``.
    For each verdict, the override list is APPENDED to defaults — operators
    extend safety, never weaken it.
    """

    cfg = getattr(settings, "SAFETY_PATTERNS", None) or {}
    merged = {k: list(v) for k, v in _DEFAULT_PATTERNS.items()}
    for verdict, patterns in cfg.items():
        if not isinstance(patterns, list):
            continue
        merged.setdefault(verdict, []).extend(patterns)
    return merged


def pre_check(
    text: str,
    intent_decision: Any | None = None,
    *,
    brand_voice: dict | None = None,
) -> SafetyResult:
    """Inspect inbound text + intent decision → :class:`SafetyResult`.

    Args:
      text: user message.
      intent_decision: optional :class:`IntentDecision` (O2). If risk_level=='high',
        verdict is elevated to at least 'handoff'.
      brand_voice: optional dict (from BrandVoiceConfig). If
        ``forbidden_phrases`` contains a pattern that matches, verdict
        becomes 'block' regardless of other matches.

    Returns:
      :class:`SafetyResult`. Default verdict is ALLOW. Empty text → ALLOW.

    ### Verdict priority (highest wins)
    HANDOFF > BLOCK > CLARIFY > ALLOW

    Multiple matches across verdicts: highest-priority verdict wins, but
    `matched_patterns` carries ALL matches (forensic). Cross-tenant
    operators see the full picture in the safety_triggered audit row
    Sprint 4 emits.
    """

    if not text:
        return SafetyResult(verdict=SafetyVerdict.ALLOW)

    matched: list[str] = []
    triggered_verdicts: set[str] = set()

    for verdict, patterns in _verdict_patterns().items():
        for pattern in patterns:
            compiled = _compile(pattern)
            if compiled is None:
                continue
            if compiled.search(text):
                matched.append(pattern)
                triggered_verdicts.add(verdict)

    # BrandVoice forbidden_phrases — operator-defined per-tenant block list.
    if brand_voice:
        for pattern in brand_voice.get("forbidden_phrases", []) or []:
            compiled = _compile(pattern)
            if compiled is None:
                continue
            if compiled.search(text):
                matched.append(pattern)
                triggered_verdicts.add(SafetyVerdict.BLOCK.value)

    # Map intent_decision.risk_level into the verdict elevation.
    elevation = _risk_elevation(intent_decision)
    if elevation:
        triggered_verdicts.add(elevation)

    # Reduce to highest-priority verdict.
    final = _reduce_verdict(triggered_verdicts)
    return SafetyResult(
        verdict=final,
        matched_patterns=matched,
        reason=_reason(triggered_verdicts, matched),
    )


# Verdict priority — higher index = higher priority.
_VERDICT_PRIORITY = [
    SafetyVerdict.ALLOW.value,
    SafetyVerdict.CLARIFY.value,
    SafetyVerdict.BLOCK.value,
    SafetyVerdict.HANDOFF.value,
]


def _reduce_verdict(verdicts: set[str]) -> SafetyVerdict:
    """Pick the highest-priority verdict that fired. Defaults to ALLOW."""

    if not verdicts:
        return SafetyVerdict.ALLOW
    highest = max(
        verdicts, key=lambda v: _VERDICT_PRIORITY.index(v) if v in _VERDICT_PRIORITY else -1
    )
    return SafetyVerdict(highest)


def _risk_elevation(intent_decision: Any | None) -> str | None:
    """Translate intent_decision.risk_level into a verdict elevation.

    high → HANDOFF (operator must see anything LLM flagged as high-risk).
    medium → BLOCK (do not let the bot proceed; canned disclaimer).
    low / None → no elevation.
    """

    if intent_decision is None:
        return None
    risk = getattr(intent_decision, "risk_level", None)
    if risk == "high":
        return SafetyVerdict.HANDOFF.value
    if risk == "medium":
        return SafetyVerdict.BLOCK.value
    return None


def _reason(verdicts: set[str], matched: list[str]) -> str:
    """Human-readable reason for the verdict, used in events / audit."""

    if not verdicts:
        return ""
    if matched:
        return f"matched={len(matched)} verdicts={sorted(verdicts)}"
    return f"risk_elevation verdicts={sorted(verdicts)}"
