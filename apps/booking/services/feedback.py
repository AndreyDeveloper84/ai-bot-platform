"""Post-visit feedback submission (Phase 4 / F5).

Customer Mini App calls this through :func:`apps.miniapp_api.views.submit_feedback`
after the F5 rating form. Behaviour:

* Validates the booking belongs to the calling :class:`BotUser` AND is
  post-visit (``status=='confirmed' AND visit_at < now()``). Per the
  Phase 4 plan §risk: an explicit ``COMPLETED`` status will land when
  the YClients post-visit webhook ports over — for now, ``confirmed``
  past ``visit_at`` is treated as post-visit.
* Idempotent. A second call on the same booking raises
  :class:`AlreadyRated`; the API maps that to a 400.
* When ``rating <= 3`` and the booking has an attached
  :class:`apps.conversations.models.Conversation`, fires
  :func:`apps.handoff.services.create_admin_task` with
  ``TaskType.COMPLAINT`` — atomic flip of
  ``Conversation.state -> HUMAN_HANDOFF`` + AdminTask row + event
  emission. Priority = HIGH for ``rating <= 2``, NORMAL for ``rating == 3``.
* When the booking has no conversation (admin-webhook origin), the
  rating still saves but the handoff is skipped — degradation is
  logged at WARNING; UI gets ``handoff_created=False`` and the
  customer sees the neutral thank-you copy.

Why the rating still saves on degraded path: we want the analytics
signal even when we can't escalate. The strategic intent (memory
[[conversation-ownership-tiers]]) is to use AdminTask as the
escalation channel until the 3-tier ownership system ships.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.booking.models import BookingRequest


class FeedbackError(ValueError):
    """Base for service-level validation errors. Mapped to 400 by the view."""

    slug: str = "invalid"


class InvalidRating(FeedbackError):
    slug = "invalid_rating"


class AlreadyRated(FeedbackError):
    slug = "already_rated"


class NotCompletedYet(FeedbackError):
    slug = "not_completed"


@dataclass(frozen=True)
class FeedbackResult:
    """DTO returned to the view + serialised to JSON."""

    booking_id: str
    rating: int
    comment: str
    feedback_at: str  # ISO 8601
    handoff_created: bool
    task_id: Optional[str]  # AdminTask.id if handoff fired


_COMMENT_MAX = 500
_LOW_RATING_THRESHOLD = 3  # rating <= 3 → handoff


def submit_feedback(
    booking: BookingRequest, *, rating: int, comment: str, now: Optional[datetime] = None
) -> FeedbackResult:
    """Persist the F5 rating + maybe escalate to a HUMAN handoff.

    Caller responsibility: load ``booking`` already filtered to the
    calling bot_user (the view does this via ``BookingRequest.objects``
    with tenant scope + bot_user pk match).
    """
    if not isinstance(rating, int) or rating < 1 or rating > 5:
        raise InvalidRating(f"rating must be int 1..5, got {rating!r}")

    if not isinstance(comment, str):
        raise FeedbackError("comment must be a string")
    comment = comment.strip()[:_COMMENT_MAX]

    if booking.feedback_at is not None:
        raise AlreadyRated(f"booking {booking.id} already has rating={booking.rating}")

    if booking.status != "confirmed":
        # The plan §risk: treat 'confirmed && visit_at<now' as post-visit.
        raise NotCompletedYet(f"booking status={booking.status!r}, expected confirmed")

    moment = now or timezone.now()
    if booking.visit_at is None or booking.visit_at > moment:
        raise NotCompletedYet("visit hasn't happened yet")

    handoff_created = False
    task_id: Optional[UUID] = None

    with transaction.atomic():
        booking.rating = rating
        booking.feedback_comment = comment
        booking.feedback_at = moment
        booking.save(update_fields=["rating", "feedback_comment", "feedback_at"])

        if rating <= _LOW_RATING_THRESHOLD:
            task_id = _try_create_handoff(booking, rating=rating, comment=comment)
            handoff_created = task_id is not None

    return FeedbackResult(
        booking_id=str(booking.id),
        rating=rating,
        comment=comment,
        feedback_at=moment.isoformat(),
        handoff_created=handoff_created,
        task_id=str(task_id) if task_id else None,
    )


def _try_create_handoff(booking: BookingRequest, *, rating: int, comment: str) -> Optional[UUID]:
    """Fire ``create_admin_task`` with COMPLAINT + matching priority.

    Returns the new AdminTask.id, or None when the booking has no
    conversation to attach to (degradation path — see module docstring).
    """
    # Late import: handoff.services is heavy (audit + events). Don't pay
    # the cost when the rating is positive.
    import logging

    from apps.handoff.models import AdminTask
    from apps.handoff.services import create_admin_task

    log = logging.getLogger(__name__)

    if booking.conversation_id is None:
        log.warning(
            "feedback.handoff.no_conversation booking=%s rating=%s — escalation skipped",
            booking.id,
            rating,
        )
        return None

    priority = AdminTask.Priority.HIGH.value if rating <= 2 else AdminTask.Priority.NORMAL.value
    snippet = (comment or "no comment")[:200]
    task = create_admin_task(
        booking.conversation,
        task_type=AdminTask.TaskType.COMPLAINT.value,
        priority=priority,
        reason=f"[post-visit rating] {rating}/5: {snippet}",
    )
    return task.id
