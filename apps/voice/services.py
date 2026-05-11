"""Voice config loader (DRF-489 / Sprint 4 / C2).

Returns a stable dict-shape BrandVoice config keyed by tenant. Cached
keyed by ``(tenant.id, brand_voice.version)`` — when an operator saves
a new version, the cache key changes naturally without explicit pub/sub.

### Why dict-shape, not the model

The orchestrator + skills + voice_check all read voice config. They
don't need Django ORM semantics — just structured data. A dict insulates
callers from model schema drift (Sprint 5 cleanup, Phase 1 ayla-ai-core
migration). The dict shape matches the eventual ayla-ai-core
``BrandVoiceCfg`` dataclass — Phase 1 glue is a 5-line conversion.

### Default shape when no config

Tenants without a ``BrandVoiceConfig`` row get safe defaults — no
crash, just an empty voice profile. The orchestrator falls back to
generic prompt composition.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from apps.tenancy.models import Tenant


# Cache keyed by (tenant_id, version) — version bump = automatic miss.
_CACHE: dict[tuple[UUID, int], dict[str, Any]] = {}


def _default_voice() -> dict[str, Any]:
    """Safe-defaults voice profile for tenants without a BrandVoiceConfig row."""

    return {
        "persona": {},
        "tone_vector": {},
        "forbidden_phrases": [],
        "voice_examples": [],
        "tone_modulations": {},
        "version": 0,
    }


def load_brand_voice(tenant: "Tenant") -> dict[str, Any]:
    """Return the BrandVoice config for `tenant` as a dict.

    Reads the latest BrandVoiceConfig row; falls back to safe defaults
    if none exists. Cached by (tenant.id, version) — operator-side save
    that bumps ``version`` invalidates the cache automatically (no
    explicit pub/sub needed).

    Args:
      tenant: Tenant instance.

    Returns:
      Dict shape ``{persona, tone_vector, forbidden_phrases,
      voice_examples, tone_modulations, version}``.
    """

    # Late import — avoids the apps.voice ↔ apps.persona module-load
    # cycle and stays cheap (no DB ping at import time).
    from apps.persona.models import BrandVoiceConfig

    try:
        bv = BrandVoiceConfig.objects.get(tenant=tenant)
    except BrandVoiceConfig.DoesNotExist:
        logger.debug("voice.load.no_config tenant=%s", tenant.id)
        return _default_voice()

    cache_key = (tenant.id, bv.version)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    voice: dict[str, Any] = {
        "persona": bv.persona or {},
        "tone_vector": bv.tone_vector or {},
        "forbidden_phrases": bv.forbidden_phrases or [],
        "voice_examples": bv.voice_examples or [],
        "tone_modulations": bv.tone_modulations or {},
        "version": bv.version,
    }
    _CACHE[cache_key] = voice
    return voice


def reset_cache() -> None:
    """Test hook — flush the in-process cache."""

    _CACHE.clear()
