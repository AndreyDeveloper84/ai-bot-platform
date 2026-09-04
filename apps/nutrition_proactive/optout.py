"""«Не пиши мне» -- the off switch, in words (DRF-1285).

The phrase set and the effect. Deliberately free of any dependency on
``apps.skills``: ``apps/channels/max/handler.py`` imports this module at
module scope for the global surface, and that file already carries a
documented module-load cycle with the skill registry. The registry-facing
wrapper lives next door in :mod:`~apps.nutrition_proactive.optout_skill`,
which may safely import both.

### Why this exists twice over

A proactive bot that cannot be stopped in the medium it speaks destroys
trust faster than it builds it. The Mini App toggle is not that switch: it
is a screen the person has to find, in an app they have to open, to stop
something that arrived while they were doing anything else. The reply box
is already open -- that is where the switch belongs.

And it has to be on **both** surfaces. ``apps.skills.registry`` is
dispatched only on the per-tenant path; the nationwide bot runs its own
ladder and never reaches the registry. The pilot IS the nationwide bot, so
a switch that lived only in the registry was a switch nobody on the pilot
could reach.

### What one message does

* Sets ``BotUser.proactive_messages_opt_out = True`` -- the platform-wide
  veto already honoured by ``apps.bookings.followups`` (B11 blocker #3), so
  one sentence silences the post-visit nudge as well as this feature.
* Turns both nutrition preferences off, so re-enabling the global flag
  later does not quietly resurrect a subscription the person cancelled.
* Stamps ``opted_out_at``.

Effect is immediate and unconditional: the write happens before the reply
is composed, and :func:`apps.nutrition_proactive.selection.base_queryset`
filters on the column, so the next beat tick -- including one already in
flight -- cannot select this person.

### Why the match set is closed

Matching is a fixed set of whole-message phrases, not a substring scan.
"стоп" inside "стоп, а во сколько вы работаете?" is not an opt-out request,
and a false positive here is silent: the person keeps writing to a bot that
has quietly unsubscribed them from everything. Whole-message equality after
normalisation makes that impossible to trigger by accident.

Re-subscribing deliberately has no chat phrase. Turning a proactive channel
back ON is exactly the decision that should require the deliberate route
(the Mini App), not a word that could be typed by mistake.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from django.utils import timezone as dj_timezone

from apps.nutrition_proactive import prefs

logger = logging.getLogger(__name__)

#: Whole-message phrases, normalised (lowercased, ё->е, punctuation and
#: repeated whitespace stripped). Deliberately short and unambiguous.
#:
#: Note what is NOT here: the «отпис-» family («отпиши меня», «отпишись»,
#: «отписаться»). In a salon bot those mean *take me off the appointment*,
#: not *stop messaging me* — «отпиши меня» and «отпишите меня» are both in
#: the owner's cancellation corpus (``apps/skills/booking/tests/
#: test_lookup_routing.py::OD_IR1_CANCEL_CORPUS``), and CI caught this skill
#: stealing the turn from the booking flow. Claiming an ambiguous phrase and
#: silently unsubscribing someone who wanted to cancel a visit is exactly the
#: failure mode the closed match set exists to prevent, so the phrase family
#: is dropped from this side rather than trimmed from the corpus.
#: :class:`TestDoesNotStealBookingCancellations` keeps it that way.
OPT_OUT_PHRASES: frozenset[str] = frozenset(
    {
        "не пиши мне",
        "не пиши мне больше",
        "больше не пиши",
        "больше не пиши мне",
        "не пиши первой",
        "не пиши",
        "отключи напоминания",
        "выключи напоминания",
        "отключи уведомления",
        "выключи уведомления",
        "стоп напоминания",
        "хватит писать",
        "перестань писать",
        "stop",
        "unsubscribe",
    }
)

_PUNCT_RE = re.compile(r"[!?.,;:\"'()\[\]«»…-]+")
_WS_RE = re.compile(r"\s+")

#: Longest phrase above is well under this; the cap keeps the normaliser off
#: full paragraphs that merely quote one of the phrases.
_MAX_LEN = 40

CONFIRMATION = (
    "Хорошо, больше не пишу первой. "
    "Напоминания о записях и подтверждения приходить не перестанут — "
    "это часть самой записи.\n"
    "Вернуть подсказки можно в профиле в мини-приложении."
)


# -- the «Не присылать» button (DRF-1468, policy R6) --------------------------
#
# Every proactive outbound carries a one-tap unsubscribe. Unlike the text
# opt-out above it silences ONE surface -- the person tapped under a
# specific message, not «never write to me» -- so the platform-wide veto
# stays unset and the other surface keeps its state.

#: The button's label. One label for every surface; the payload says which.
STOP_BUTTON_LABEL = "Не присылать"

#: The pref each known surface's button flips, and the honest one-line
#: confirmation the person gets back. Short, no guilt, the way back named
#: (editorial policy R6: opt-out is one tap and never hidden).
SURFACE_OPT_OUT_PREFS: dict[str, dict[str, Any]] = {
    "report": {"daily_report_time": prefs.REPORT_OFF},
    "water": {"water_reminders": False},
}

SURFACE_CONFIRMATIONS: dict[str, str] = {
    "report": ("Хорошо, итоги дня больше не присылаю. Вернуть можно в профиле в мини-приложении."),
    "water": (
        "Хорошо, напоминания о воде больше не присылаю. Вернуть можно в профиле в мини-приложении."
    ),
}

#: A tap whose surface the schema no longer knows: the button is stale.
#: The turn is still claimed (silence would read as a broken button), but
#: no state changes.
STALE_SURFACE_CONFIRMATION = (
    "Эта кнопка уже не действует, настройки не меняла. "
    "Подсказки настраиваются в профиле в мини-приложении."
)


def stop_callback(surface: str) -> str:
    """The deterministic callback payload for a surface's stop button."""
    return f"cb:nutri:stop:{surface}"


def parse_surface_stop(text: str) -> str | None:
    """The surface a ``cb:nutri:stop:{surface}`` payload names, or None.

    Returns the surface even when it is unknown (a stale button) -- the
    caller decides what to change (nothing) and what to say. Returns None
    for anything that is not exactly this button family's payload: a bare
    ``cb:nutri:stop`` without a surface is not a button we drew.
    """
    stripped = (text or "").strip()
    if not stripped.startswith("cb:"):
        return None
    from apps.orchestrator.ui.keyboards import parse_callback

    parsed = parse_callback(stripped)
    if parsed is None:
        return None
    if parsed["domain"] != "nutri" or parsed["action"] != "stop":
        return None
    return parsed.get("ref") or None


def apply_surface_opt_out(bot_user: Any, surface: str) -> str:
    """Silence ONE surface for ``bot_user`` and return the confirmation.

    The platform-wide veto is deliberately NOT set: the tap answered one
    message, not every future one. Same persistence shape as
    :func:`apply_opt_out` -- one ``.update()``, no full-model save, no
    tenant-context dependence -- so the two switches can never drift in
    how reliably they land.
    """
    from apps.identity.models import BotUser

    context_json = prefs.merge_prefs(bot_user, SURFACE_OPT_OUT_PREFS[surface])
    BotUser.all_tenants.filter(pk=bot_user.pk).update(context=context_json)
    bot_user.context = context_json

    logger.info(
        "nutrition_proactive.surface_opt_out bot_user=%s surface=%s",
        bot_user.pk,
        surface,
    )
    return SURFACE_CONFIRMATIONS[surface]


def try_handle_surface_stop(*, text: str, bot_user: Any) -> str | None:
    """Global-surface entry point for the stop button. None to fall through.

    Same contract as :func:`try_handle_opt_out`: the pilot IS the global
    bot, so the button must work there and not only in the registry skill.
    Never raises -- a failure must not cost the person their turn.
    """
    try:
        surface = parse_surface_stop(text)
        if surface is None:
            return None
        if surface not in SURFACE_OPT_OUT_PREFS:
            return STALE_SURFACE_CONFIRMATION
        return apply_surface_opt_out(bot_user, surface)
    except Exception:  # noqa: BLE001 -- must never break the turn
        logger.exception("nutrition_proactive.surface_stop_failed")
        return None


def normalise(text: str) -> str:
    """Lowercase, fold ё, drop punctuation, collapse whitespace."""
    cleaned = _PUNCT_RE.sub(" ", text.strip().lower().replace("ё", "е"))
    return _WS_RE.sub(" ", cleaned).strip()


def matches_opt_out(text: str) -> bool:
    """Is this whole message a request to stop being written to?"""
    body = text or ""
    if not body or len(body) > _MAX_LEN:
        return False
    return normalise(body) in OPT_OUT_PHRASES


def apply_opt_out(bot_user: Any) -> str:
    """Turn everything off for ``bot_user`` and return the confirmation.

    The single implementation behind BOTH entry points -- the registry skill
    (per-tenant surface) and the deterministic branch on the global surface.
    They must never drift: an off-switch that works on one bot and not the
    other is worse than none, because the person has already been told it
    worked.
    """
    from apps.identity.models import BotUser

    context_json = prefs.merge_prefs(
        bot_user,
        {
            "daily_report_time": prefs.REPORT_OFF,
            "water_reminders": False,
            "opted_out_at": dj_timezone.now().isoformat(),
        },
    )
    # ``all_tenants`` + ``.update()``: one statement, no full-model save that
    # could clobber a concurrent write to another column, and no dependence
    # on a tenant context being active -- the global surface runs at
    # ``current_tenant() is None``.
    BotUser.all_tenants.filter(pk=bot_user.pk).update(
        proactive_messages_opt_out=True,
        context=context_json,
    )
    # Keep the in-memory instance honest for anything later in the turn.
    bot_user.proactive_messages_opt_out = True
    bot_user.context = context_json

    logger.info("nutrition_proactive.opt_out bot_user=%s", bot_user.pk)
    return CONFIRMATION


def try_handle_opt_out(*, text: str, bot_user: Any) -> str | None:
    """Global-surface entry point. Returns the reply, or None to fall through.

    ``apps.skills.registry`` is dispatched only on the per-tenant surface
    (``apps/channels/max/handler.py::_handle_max_event_inner``); the global
    nationwide bot runs its own ladder and never reaches the registry. The
    pilot IS the global bot, so without this the off-switch shipped as a
    skill that nobody on the pilot could ever reach: «не пиши мне» would go
    to the concierge model, get a friendly answer, and turn nothing off.

    Never raises -- a failure here must not cost the person their turn, and
    the caller falls through to its normal ladder.
    """
    try:
        if not matches_opt_out(text):
            return None
        return apply_opt_out(bot_user)
    except Exception:  # noqa: BLE001 -- must never break the turn
        logger.exception("nutrition_proactive.opt_out_failed")
        return None
