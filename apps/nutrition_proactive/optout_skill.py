"""«Не пиши мне» -- the off switch, in words (DRF-1285).

A proactive bot that cannot be stopped in the medium it speaks destroys
trust faster than it builds it. The Mini App toggle is not that switch: it
is a screen the person has to find, in an app they have to open, to stop
something that arrived while they were doing anything else. The reply box is
already open -- that is where the switch belongs.

### What one message does

* Sets ``BotUser.proactive_messages_opt_out = True`` -- the platform-wide
  veto already honoured by ``apps.bookings.followups`` (B11 blocker #3), so
  one sentence silences the post-visit nudge as well as this feature.
* Turns both nutrition preferences off, so re-enabling the global flag later
  does not quietly resurrect a subscription the person cancelled.
* Stamps ``opted_out_at``.

Effect is immediate and unconditional: the write happens before the reply is
composed, and :func:`apps.nutrition_proactive.selection.base_queryset`
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
from typing import ClassVar

from django.utils import timezone as dj_timezone

from apps.nutrition_proactive import prefs
from apps.skills.base import SkillContext, SkillResult
from apps.skills.registry import register

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


def normalise(text: str) -> str:
    """Lowercase, fold ё, drop punctuation, collapse whitespace."""
    cleaned = _PUNCT_RE.sub(" ", text.strip().lower().replace("ё", "е"))
    return _WS_RE.sub(" ", cleaned).strip()


@register
class ProactiveOptOutSkill:
    """Stop every bot-initiated message, from one message."""

    name: ClassVar[str] = "proactive_opt_out"

    def matches(self, context: SkillContext) -> bool:
        text = context.message_text or ""
        if not text or len(text) > _MAX_LEN:
            return False
        return normalise(text) in OPT_OUT_PHRASES

    def handle(self, context: SkillContext) -> SkillResult:
        from apps.identity.models import BotUser

        bot_user = context.bot_user
        context_json = prefs.merge_prefs(
            bot_user,
            {
                "daily_report_time": prefs.REPORT_OFF,
                "water_reminders": False,
                "opted_out_at": dj_timezone.now().isoformat(),
            },
        )
        # ``all_tenants`` + ``.update()``: one statement, no full-model save
        # that could clobber a concurrent write to another column, and no
        # dependence on a tenant context being active.
        BotUser.all_tenants.filter(pk=bot_user.pk).update(
            proactive_messages_opt_out=True,
            context=context_json,
        )
        # Keep the in-memory instance honest for anything later in the turn.
        bot_user.proactive_messages_opt_out = True
        bot_user.context = context_json

        logger.info("nutrition_proactive.opt_out bot_user=%s", bot_user.pk)
        return SkillResult(
            reply_text=CONFIRMATION,
            action_type="proactive_opt_out",
            meta={"reply_kind": "proactive_opt_out"},
        )
