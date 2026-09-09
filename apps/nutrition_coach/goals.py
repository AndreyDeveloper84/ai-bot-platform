"""Active-goal reader over Ayla's goal layer (DRF-1464, T2).

One question: «какая у человека сейчас активная цель». The answer comes
from the same decision-context document the goal screen renders
(:func:`apps.integrations.ayla.goals_client.fetch_decision_context`),
read through that client's circuit breaker — used, never bypassed.

Fail-closed everywhere. For the proactive surface «нет цели» means
«молчим» (copy policy R7: proactive only onto an explicit goal), so
every degradation lands on ``None``:

* Ayla unreachable, circuit open, 4xx, misconfigured token — an outage
  is deliberately indistinguishable from a person without a goal. That
  is the right side of the error: a hint sent on a stale guess would be
  worse than silence.
* empty document, no ``known.goal`` — no goal was ever chosen.
* archived goal — Ayla filters archived goals out of the document
  (``is_active`` filter in ``goals/decision_context.py``), so an archive
  arrives as ``goal: None``; an explicit ``is_active: False`` is honoured
  the same way. Owner decision Q-NUTRITION-08: архив = тишина.

``goal_text`` is free-form text the person typed into Ayla — untrusted
input heading for an LLM prompt later (through
:func:`apps.orchestrator.ayla_adapter.build_safe_inputs`). It is already
cut down here (control chars stripped, length capped) so every consumer
of :class:`Goal` holds a safe value, not a promise to sanitize later.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from apps.orchestrator.ayla_adapter import sanitize_freeform

logger = logging.getLogger(__name__)

#: Longest goal text we carry. Matches Ayla's own cap
#: (``goals/decision_context.py`` truncates ``goal_text`` at 200), so a
#: text that survives her side survives ours unchanged.
MAX_GOAL_TEXT_CHARS: Final[int] = 200

#: Goal keys are curated slugs (``more_energy``); 64 chars is generous.
MAX_GOAL_KEY_CHARS: Final[int] = 64


@dataclass(frozen=True)
class Goal:
    """The person's active goal, as far as the coach ever carries it.

    ``key`` is the curated slug, ``""`` for a free-text goal. ``text`` is
    the person's own wording (sanitized), ``None`` when the goal exists
    only as a key.
    """

    key: str
    text: str | None


def active_goal(
    bot_user: Any,
    *,
    fetch: Callable[..., dict[str, Any]] | None = None,
) -> Goal | None:
    """The active goal for ``bot_user``, or ``None``. Never raises.

    ``None`` here means TWO different states, and they are deliberately
    not distinguished to the caller: «человек цели не выбрал» и «спросить
    не удалось». Для коуча исход один — молчать, — поэтому наружу идёт
    одно имя. Различаются они ВНУТРЬ, счётчиками в логе:
    ``nutrition_coach.goals.unavailable reason=…`` и
    ``nutrition_coach.goals.disabled`` пишутся только на втором, и по ним
    одним видно, молчит ли коуч потому, что цели нет, или потому, что до
    слоя целей не достучались. Отлаживая тишину коуча, смотреть надо
    туда: по возвращаемому значению эти два случая неразличимы.

    ``fetch`` is the test seam (same shape as ``fetch=`` on
    :func:`apps.nutrition_proactive.tasks.plan_daily_reports`): injected
    it replaces the Ayla call; default is
    :func:`apps.integrations.ayla.goals_client.fetch_decision_context`,
    breaker and all.
    """
    try:
        from apps.integrations.ayla import external_user_id_for

        external_id = external_user_id_for(bot_user)
    except Exception:  # noqa: BLE001 — unconfigured env is not an error
        logger.debug("nutrition_coach.goals.disabled")
        return None

    from apps.integrations.ayla.goals_client import (
        GoalsBadRequest,
        GoalsConfigError,
        GoalsUnavailable,
        fetch_decision_context,
    )

    if fetch is None:
        fetch = fetch_decision_context

    try:
        document = fetch(external_user_id=external_id)
    except GoalsConfigError as exc:
        logger.debug("nutrition_coach.goals.disabled: %s", exc)
        return None
    except (GoalsUnavailable, GoalsBadRequest) as exc:
        logger.info("nutrition_coach.goals.unavailable reason=%s", exc)
        return None
    except Exception:  # noqa: BLE001 — fail-closed: no proven goal, no coach
        logger.exception("nutrition_coach.goals.fetch_failed")
        return None

    return _goal_from_document(document)


def _goal_from_document(document: Any) -> Goal | None:
    """``known.goal`` out of the decision-context document, or ``None``.

    Pure and defensive: every shape that does not prove an active goal
    reads as «no goal».
    """
    if not isinstance(document, dict):
        return None
    known = document.get("known")
    goal = known.get("goal") if isinstance(known, dict) else None
    if not isinstance(goal, dict):
        return None
    if goal.get("is_active") is False:
        # Q-NUTRITION-08: архив = тишина. Live documents already omit
        # archived goals; this is the explicit-flag shape, honoured the
        # same way.
        return None

    key = _clean_key(goal.get("goal_key"))
    text = _clean_text(goal.get("goal_text"))
    if not key and text is None:
        # A row with neither key nor text proves nothing.
        return None
    return Goal(key=key, text=text)


def _clean_key(raw: Any) -> str:
    """Curated slug, or ``""``. A non-string key is malformed, not a goal."""
    if not isinstance(raw, str):
        return ""
    return raw.strip()[:MAX_GOAL_KEY_CHARS]


def _clean_text(raw: Any) -> str | None:
    """Sanitized free text, or ``None`` when there is none worth carrying."""
    if not isinstance(raw, str):
        return None
    cleaned = sanitize_freeform(raw, max_len=MAX_GOAL_TEXT_CHARS, field_name="goal_text")
    return cleaned or None
