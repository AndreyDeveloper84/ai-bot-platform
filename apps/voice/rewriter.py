"""Voice rewriter — Phase-0 passthrough stub (DRF-490 / Sprint 4 / C3).

Phase 0 ships the call-site stable. Phase 1 wires the actual rewrite
through ``ayla-ai-core.voice.rewrite()`` — the function signature here
matches what that integration expects, so call sites land once.

### Contract

``rewrite_to_voice(text, voice) → str`` — returns the input text
unchanged in Phase 0. The caller can safely route every assistant
reply through this without behavioural change today.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def rewrite_to_voice(text: str, voice: dict[str, Any]) -> str:
    """Rewrite `text` in the configured voice. Phase-0: passthrough.

    Args:
      text: assistant reply text.
      voice: dict from :func:`apps.voice.services.load_brand_voice`.

    Returns:
      The rewritten text. Phase 0: input unchanged.
    """

    logger.debug(
        "voice.rewrite.skipped phase=0 voice_version=%s text_len=%d",
        voice.get("version", 0),
        len(text),
    )
    return text
