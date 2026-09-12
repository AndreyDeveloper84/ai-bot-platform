"""One gate in front of the person's context (owner 11.09 §2.4, DRF-1700, S2-2).

§2.4, verbatim: «SHADOW не даёт права использовать глобальные цели, питание,
health-контекст или межсалонную память». This module is that sentence as
a function, and every reader of those four things asks it first.

### What the gate knows, and what it deliberately does not

It knows one thing: the shell's standing with Ayla — `customer_status`
(S2-1). A `global_bot` shell is the client contour's own side of the
relationship and passes. A salon shell passes only when it is `LINKED`.
`SHADOW` refuses; so does `UNRESOLVED` — «the rule has not been applied»
is not a permission, and a default that permitted would be the silent
default every fail-closed construction here forbids.

It does NOT know which surface is asking. Whether a *salon* surface may
even reach these readers is a separate rule — import contract S2.6 in
``tools/lint/import_boundaries.py`` — and keeping the two apart is
deliberate: a gate that also judged surfaces would have two reasons to
refuse, and the second would hide the first.

### The refusal has a name, and the names are counted apart

Outward, every refusal is the same thing — no context. Inward, `shadow`
and `unresolved` are different facts needing different people: a SHADOW
is a product decision working as designed; an UNRESOLVED on a live turn
means the classification command has not been run, or a new shell was
created past it. One name outward, separate counters inward
(`persona.person_context.refused reason=…`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.models import BotUser

logger = logging.getLogger(__name__)

REASON_SHADOW = "shadow"
REASON_UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why the person's context is closed to this shell. Never a permission."""

    reason: str
    bot_user_id: str

    def __bool__(self) -> bool:
        # A refusal is truthy so `if refused:` reads the way it sounds. The
        # gate returns None when access is open.
        return True


def _tenant_slug(bot_user: BotUser) -> str:
    # `tenant` may be loaded or not; `tenant_id` always is. One query at
    # most, and none when the caller already selected the relation.
    tenant = getattr(bot_user, "tenant", None)
    slug = getattr(tenant, "slug", None)
    if slug is not None:
        return slug
    return (
        BotUser.all_tenants.filter(pk=bot_user.pk).values_list("tenant__slug", flat=True).first()
        or ""
    )


def person_context_access(bot_user: BotUser) -> Refusal | None:
    """``None`` — the shell may use the person's context; else a named refusal."""
    if _tenant_slug(bot_user) == GLOBAL_BOT_TENANT_SLUG:
        return None
    status = bot_user.customer_status
    if status == BotUser.CustomerStatus.LINKED:
        return None
    reason = REASON_SHADOW if status == BotUser.CustomerStatus.SHADOW else REASON_UNRESOLVED
    logger.info(
        "persona.person_context.refused reason=%s bot_user=%s tenant=%s",
        reason,
        bot_user.pk,
        bot_user.tenant_id,
    )
    return Refusal(reason=reason, bot_user_id=str(bot_user.pk))


__all__ = ["REASON_SHADOW", "REASON_UNRESOLVED", "Refusal", "person_context_access"]
