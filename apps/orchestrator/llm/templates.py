"""Static fallback templates for breaker-open responses (DRF-428 / D1).

Sprint 1 ships static template strings. When ``ayla-ai-core`` is
importable (per review revision 5C), this module gets a thin import
wrapper:

    from ayla_ai_core.voice import get_fallback_template
    OUTAGE_RU = get_fallback_template("outage_ru")

For now, hardcoded strings keep CI green without the extra dep. (The
original note here said this waited on ``GH_DEPLOY_TOKEN``; ayla-ai-core
is a public repo and needs no token — DRF-1466.)
"""

from __future__ import annotations

OUTAGE_RU = "Извини, у меня сейчас короткий технический сбой — отвечу через минуту."
"""Default Russian fallback returned when the LLM breaker is open."""


OUTAGE_EN = "Sorry, I'm having a brief technical issue — I'll be back in a moment."
"""Default English fallback."""


def get_fallback(lang: str = "ru") -> str:
    """Return the fallback template for the given language code.

    Sprint 1 supports ru/en only. Sprint 4+ pulls per-tenant brand voice
    via ``apps.voice`` and ``ayla-ai-core``.
    """

    return OUTAGE_EN if lang == "en" else OUTAGE_RU
