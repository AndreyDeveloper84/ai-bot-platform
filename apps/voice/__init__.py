"""apps/voice — service-layer glue around BrandVoiceConfig (DRF-489 / Sprint 4 / C2).

The model lives in ``apps.persona.models.BrandVoiceConfig`` (Sprint 2
shell, Sprint 4 / C1 expansion). This package is the read-side glue:

  * :func:`load_brand_voice` — cached loader returning a dict-shape
    voice config (Phase-0 stable shape; Phase 1 will wire to
    ayla-ai-core's BrandVoiceCfg dataclass).
  * :func:`rewrite_to_voice` — Phase-0 stub (passthrough); Phase 1
    wires ayla-ai-core's rewriter.
  * voice_check (lives in apps.orchestrator.safety.voice_check) —
    regex forbidden-phrases guard.
"""

from __future__ import annotations

from apps.voice.services import load_brand_voice

__all__ = ["load_brand_voice"]
