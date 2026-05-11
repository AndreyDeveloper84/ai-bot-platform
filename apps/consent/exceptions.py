"""Consent exceptions (DRF-458 / Sprint 3 / A3)."""

from __future__ import annotations


class ConsentDenied(Exception):
    """Raised when a guarded operation is invoked without the required consent.

    Sprint 3 ships this as a primitive for the upcoming tool registry
    (Sprint 4+). The PrivacyConsentSkill (D2) catches it and converts
    to a user-facing re-prompt; the orchestrator treats uncaught
    instances as a safety stop.
    """

    def __init__(self, consent_type: str, *, bot_user_id: str | None = None) -> None:
        self.consent_type = consent_type
        self.bot_user_id = bot_user_id
        super().__init__(
            f"Consent '{consent_type}' is required but not granted"
            + (f" (bot_user={bot_user_id})" if bot_user_id else "")
        )
