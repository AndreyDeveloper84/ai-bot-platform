"""Cross-domain insights skill — Ayla insight card callbacks.

Sprint 9 / P6 (DRF-823). Owns ``cb:cross:{seen,dismiss,convert}:{shown_id}``
emitted by an Ayla cross-domain insight card (e.g. "vit-D deficit 5d →
argan-oil massage"). Ported from ``legacy_maxbot/handlers/cross_domain.py``
(DRF-274 / B-4 mysite path).

## Scope note — ticket vs reality

The DRF-823 description mentioned "mixed-intent routing" — splitting a
message that touches multiple domains into sequential per-domain
turns. The mysite source named ``cross_domain.py`` is actually about
**Ayla cross-domain insights** (nutrition → beauty-service suggestion
after a food log). The port follows the source.

The mixed-intent splitter remains a Phase 1 design — it needs an
LLM-driven intent decomposer and only becomes useful once both
booking and nutrition skills produce real value (booking skill is in
the Phase 1 backlog DRF-839).

## Three callbacks

* ``cb:cross:seen:{shown_id}`` — telemetry-only fire-and-forget.
  Reply is ``should_send=False`` (silent).
* ``cb:cross:dismiss:{shown_id}`` — user said "не интересно". POST
  to Ayla so the rule doesn't re-fire this week; soft-acknowledge.
* ``cb:cross:convert:{shown_id}`` — user wants the suggested service.
  Sprint 9 cut: we tell them "сейчас позову менеджера" and stop.
  Phase 1 wires this to the booking skill (DRF-839) which then
  posts to Ayla with the appointment_id.

## PII safety

``insight_text`` / ``rationale_text`` are personalised and MUST NOT
be logged at INFO. Use ``shown_id`` / ``rule_slug`` for diagnostics.
Source rule — kept verbatim.
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from apps.integrations.ayla import (
    NutritionAPIError,
    NutritionUnavailableError,
    external_user_id_for,
    get_nutrition_client,
)
from apps.orchestrator.ui.keyboards import parse_callback
from apps.skills.base import SkillContext, SkillResult
from apps.skills.registry import register

logger = logging.getLogger(__name__)


# ─── reply templates ──────────────────────────────────────────────────────


DISMISS_ACK = "Хорошо, не буду больше предлагать на этой неделе."

CONVERT_ACK = "Поняла, передаю менеджеру — он подберёт удобное время и свяжется с тобой."

# Internal-error fallback. The cross-domain telemetry is non-critical;
# if Ayla is down we still want the user to see SOMETHING reassuring.
QUIET_ACK = "Ок, поняла."


# ─── skill ────────────────────────────────────────────────────────────────


@register
class CrossDomainSkill:
    """Three callback handlers for the Ayla insight card."""

    name: ClassVar[str] = "cross_domain"

    def matches(self, context: SkillContext) -> bool:
        text = context.message_text.strip()
        if not text.startswith("cb:cross:"):
            return False
        parsed = parse_callback(text)
        if parsed is None:
            return False
        return parsed["action"] in {"seen", "dismiss", "convert"} and bool(parsed.get("ref"))

    def handle(self, context: SkillContext) -> SkillResult:
        text = context.message_text.strip()
        parsed = parse_callback(text)
        if parsed is None or not parsed.get("ref"):
            return SkillResult(reply_text="", should_send=False)

        action = parsed["action"]
        shown_id = parsed["ref"]
        external_id = external_user_id_for(context.bot_user)

        if action == "seen":
            return self._on_seen(external_id, shown_id)
        if action == "dismiss":
            return self._on_dismiss(external_id, shown_id)
        if action == "convert":
            return self._on_convert(external_id, shown_id)

        # Shouldn't reach — matches() screened.
        return SkillResult(reply_text="", should_send=False)

    # ─── handlers ────────────────────────────────────────────────────────

    def _on_seen(self, external_id: str, shown_id: str) -> SkillResult:
        """Fire-and-forget telemetry; reply silently."""
        try:
            asyncio.run(
                get_nutrition_client().post_cross_domain_seen(
                    external_user_id=external_id,
                    shown_id=shown_id,
                )
            )
        except (NutritionUnavailableError, NutritionAPIError):
            # Telemetry — never raise to user-visible level.
            logger.info("cross_domain.seen.swallow shown_id=%s", shown_id)
        return SkillResult(
            reply_text="",
            should_send=False,
            meta={"reply_kind": "cross_domain_seen"},
        )

    def _on_dismiss(self, external_id: str, shown_id: str) -> SkillResult:
        try:
            asyncio.run(
                get_nutrition_client().post_cross_domain_dismiss(
                    external_user_id=external_id,
                    shown_id=shown_id,
                )
            )
        except (NutritionUnavailableError, NutritionAPIError):
            logger.warning("cross_domain.dismiss.failed shown_id=%s", shown_id)
            return SkillResult(
                reply_text=QUIET_ACK,
                meta={"reply_kind": "cross_domain_dismiss_swallow"},
            )
        return SkillResult(
            reply_text=DISMISS_ACK,
            meta={"reply_kind": "cross_domain_dismiss"},
        )

    def _on_convert(self, external_id: str, shown_id: str) -> SkillResult:
        """Sprint 9 cut — reply acknowledges intent; the booking-side
        post (with appointment_id) lands in Phase 1.

        We deliberately do NOT call ``post_cross_domain_convert`` here
        because we don't have an appointment_id without the booking
        skill. Telemetry stays accurate (no false-positive conversions
        recorded) and the user gets a clear "manager will reach out"
        path.
        """
        return SkillResult(
            reply_text=CONVERT_ACK,
            action_type="cross_domain_convert_pending",
            action_data={"shown_id": shown_id},
            meta={"reply_kind": "cross_domain_convert_pending"},
        )
