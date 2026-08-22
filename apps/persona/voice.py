"""Who the assistant says it is — one place, for every surface.

### The problem

The product introduced itself under three names at once, decided in three
unrelated files:

* the customer concierge said **Ayla**, from the frozen
  ``AYLA_MARKETPLACE_VOICE`` in ayla-ai-core;
* a master's AI draft said **«Помощник», единый голос ассистента салона**,
  hardcoded in ``master_api.services.ai_drafts``;
* the FAQ and booking skills said whatever ``brand_voice.persona`` held,
  per tenant.

A customer who writes to the bot and then receives a reply the master sent
from a draft meets two different beings. Nobody chose that; three files
each chose reasonably on their own.

### What this module does and does not decide

It does **not** rename anything. The salon surface keeps «Помощник» because
that was a deliberate call (master-mobile §M6: one assistant identity, a
master's authorship recorded in metadata rather than the signature), and
the marketplace keeps «Ayla». What changes is that the difference is now
one table in one file — a decision someone can read and revise — instead of
three independent constants that drifted into place.

Renaming a surface is a product call. When it comes, it is an edit here.

### The mirror that could drift

``ayla-ai-core`` is an optional extra in some environments, so a fallback
copy of the frozen values has to exist. Copies rot: nothing compared this
one against the original, and a divergence would show up as «CI says one
thing, production says another». ``tests/test_voice.py`` now asserts the
two are identical whenever the library is installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Surface = Literal["marketplace", "salon"]

#: The customer-facing marketplace bot — the nationwide Ayla DM.
SURFACE_MARKETPLACE: Surface = "marketplace"

#: Everything inside a salon's own correspondence: AI drafts a master
#: sends, and the staff-facing assistant. One identity per §M6, so a
#: customer never has to work out which employee is behind a reply.
SURFACE_SALON: Surface = "salon"


@dataclass(frozen=True)
class AssistantIdentity:
    """What the assistant calls itself on one surface."""

    name: str
    business_name: str
    domain: str
    off_topic_redirect: str


#: Verbatim copy of the frozen ``AYLA_MARKETPLACE_VOICE`` fields, for
#: environments without the ``[ai-core]`` extra. Kept honest by
#: ``test_voice.py::TestTheMirrorMatchesTheLibrary``.
_FROZEN_MIRROR: dict[str, str] = {
    "assistant_name": "Ayla",
    "business_name": "Ayla — AI Self-Care",
    "domain": "beauty-услуги",
    "off_topic_redirect": "Я помогаю с записями к beauty-мастерам, чем помочь?",
}

#: Name per surface. The marketplace name comes from the frozen library;
#: the salon name is a product decision recorded here rather than in a
#: prompt string halfway down a service module.
_SURFACE_NAMES: dict[str, str] = {
    SURFACE_MARKETPLACE: "",  # empty → take the library's assistant_name
    SURFACE_SALON: "Помощник",
}


def frozen_voice_fields() -> dict[str, str]:
    """The frozen marketplace voice, or the mirror when the lib is absent.

    Consumes the constant, never modifies it.
    """

    try:
        from ayla_ai_core import AYLA_MARKETPLACE_VOICE as voice
    except Exception:  # pragma: no cover — lib not installed in this env
        return dict(_FROZEN_MIRROR)
    return {
        "assistant_name": voice.assistant_name,
        "business_name": voice.business_name,
        "domain": voice.domain,
        "off_topic_redirect": voice.off_topic_redirect,
    }


def assistant_identity(surface: Surface = SURFACE_MARKETPLACE) -> AssistantIdentity:
    """How the assistant introduces itself on ``surface``.

    Unknown surfaces fall back to the marketplace identity rather than
    raising: a mis-typed surface should give a person the wrong-but-sane
    name, not a stack trace mid-conversation.
    """

    fields = frozen_voice_fields()
    override = _SURFACE_NAMES.get(surface, "")
    return AssistantIdentity(
        name=override or fields["assistant_name"],
        business_name=fields["business_name"],
        domain=fields["domain"],
        off_topic_redirect=fields["off_topic_redirect"],
    )


def known_surfaces() -> tuple[str, ...]:
    """Every surface that has a declared identity."""

    return tuple(_SURFACE_NAMES)


__all__ = [
    "SURFACE_MARKETPLACE",
    "SURFACE_SALON",
    "AssistantIdentity",
    "assistant_identity",
    "frozen_voice_fields",
    "known_surfaces",
]
