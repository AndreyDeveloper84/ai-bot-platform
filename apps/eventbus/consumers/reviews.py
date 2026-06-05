"""Reviews event consumer (#445 — review.created).

Per event-contract.md §3.9. One handler:

* ``review.created`` — when a customer leaves a review after
  ``booking.completed``, update the ClientProfile snapshot with the
  rating-derived sentiment + flag low-rating customers for admin
  outreach.

### Hard rules

* **Contract §3.9 step 1 — idempotent on review_id**. The dispatcher's
  ``IngestDedupe`` table dedupes on event_id; this consumer adds a
  SECOND idempotency layer via :class:`ReviewProcessedDedupe` keyed by
  ``(tenant_id, review_id)`` so an operator manual re-fire with a
  fresh ULID cannot double-count the sentiment update.
* **Contract §3.9 step 3 — do NOT fetch review text**. Free-text
  comment is PII §7-sensitive; only the rating + flags travel through
  the envelope.
* **ADR-0009 Rule #5** — handler mutates ONLY the local ClientProfile
  mirror's review-derived fields. Canonical review state lives in
  Ayla.

### Sentiment derivation

Per tech-lead Q1 decision:

| rating | sentiment_score |
|--------|-----------------|
| 5      | +1.0            |
| 4      | +0.5            |
| 3      |  0.0            |
| 2      | -0.5            |
| 1      | -1.0            |

``low_rating_flag = (rating <= 2)``. Sticky-vs-resetting semantics is
a discussion item in PR review; current behaviour SETS the flag
without ever resetting it (sticky by default — admin must clear).

### Migration ordering

W4 ships ClientProfile review fields in a parallel mini-PR. This
consumer uses ``hasattr`` checks to gracefully degrade pre-W4:
review_id dedupe still applies, but ClientProfile mutation is
skipped with a warning until the fields land. After W4 ff-merge,
mutation resumes.

### Handler registration

Module-level :func:`register_reviews_handlers` — called from
``apps/eventbus/apps.py::EventBusConfig.ready``.
"""

from __future__ import annotations

import logging
from typing import Final
from uuid import UUID

from django.db import IntegrityError, transaction

from apps.eventbus.ingest_dispatcher import register
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.ingest_tenancy import assert_envelope_tenant_authorized
from apps.eventbus.models import ReviewProcessedDedupe


logger = logging.getLogger(__name__)


# Sentiment lookup per tech-lead Q1 spec. Range [-1.0, +1.0].
_SENTIMENT_BY_RATING: Final[dict[int, float]] = {
    5: 1.0,
    4: 0.5,
    3: 0.0,
    2: -0.5,
    1: -1.0,
}


# ─── handlers ──────────────────────────────────────────────────────────────


def handle_review_created(envelope: IngestEnvelope) -> None:
    """``review.created`` — event-contract.md §3.9.

    Steps:
      1. Verify envelope tenant authorization (A3).
      2. Parse ``review_id`` + ``rating`` from data.
      3. Per-review dedupe via :class:`ReviewProcessedDedupe`:
         INSERT-or-IntegrityError pattern. On collision → already
         processed → short-circuit.
      4. Locate ClientProfile via BotUser.ayla_user_id =
         envelope.user_id, filtered by tenant.
      5. Update ClientProfile review-derived fields if W4 fields are
         present; gracefully skip with warning otherwise.
    """
    assert_envelope_tenant_authorized(envelope)

    data = envelope.data
    try:
        review_id = UUID(data["review_id"])
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning(
            "eventbus.consumer.reviews.review_created.bad_review_id event_id=%s data=%r exc=%s",
            envelope.event_id,
            data,
            exc,
        )
        return

    rating_raw = data.get("rating")
    if not isinstance(rating_raw, int) or rating_raw < 1 or rating_raw > 5:
        logger.warning(
            "eventbus.consumer.reviews.review_created.bad_rating event_id=%s rating=%r",
            envelope.event_id,
            rating_raw,
        )
        return
    rating: int = rating_raw

    tenant_id_str = envelope.tenant_id
    if not tenant_id_str:
        logger.warning(
            "eventbus.consumer.reviews.review_created.null_tenant event_id=%s",
            envelope.event_id,
        )
        return

    # Round-1 review PRE_PILOT #1 fix: per event-contract §5.1, the
    # dedupe row + side-effect MUST commit in the SAME transaction.
    # Otherwise a worker death between dedupe-commit and profile-save
    # leaves dedupe saying «processed» while only some profiles got
    # the update (multi-channel customer with MAX+TG profiles).
    # Replay then short-circuits at the IntegrityError branch without
    # finishing. Outer atomic wraps EVERYTHING that mutates DB state.
    sentiment = _SENTIMENT_BY_RATING[rating]
    low_rating = rating <= 2

    try:
        user_id = UUID(envelope.user_id)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "eventbus.consumer.reviews.review_created.bad_envelope_user_id "
            "event_id=%s user_id=%r exc=%s",
            envelope.event_id,
            envelope.user_id,
            exc,
        )
        return

    # Lazy import — avoids circular import via apps.identity.
    from apps.identity.models import BotUser, ClientProfile

    try:
        with transaction.atomic():
            # Per-review dedupe: INSERT-or-IntegrityError pattern.
            # Closes «distinct event_ids same review_id» replay
            # vector that dispatcher IngestDedupe doesn't cover.
            ReviewProcessedDedupe.objects.create(
                tenant_id=tenant_id_str,
                review_id=review_id,
                event_id=envelope.event_id,
            )

            # Locate ClientProfile via BotUser → bot_user_id PK on
            # ClientProfile. The customer may have multiple BotUsers
            # under this tenant (multi-channel: MAX + Telegram); we
            # update profiles for ALL of them. A review event's
            # user_id = the customer (not the master).
            bot_user_ids = list(
                BotUser.all_tenants.filter(
                    tenant_id=tenant_id_str,
                    ayla_user_id=user_id,
                ).values_list("id", flat=True)
            )

            if not bot_user_ids:
                logger.info(
                    "eventbus.consumer.reviews.review_created.no_bot_users "
                    "ayla_user_id=%s tenant_id=%s — Ayla mobile-only "
                    "customer, no ClientProfile to update",
                    user_id,
                    tenant_id_str,
                )
                return  # commits the dedupe row alone — that's fine

            profiles = list(
                ClientProfile.all_tenants.filter(
                    tenant_id=tenant_id_str,
                    bot_user_id__in=bot_user_ids,
                )
            )

            if not profiles:
                logger.info(
                    "eventbus.consumer.reviews.review_created.no_profiles "
                    "ayla_user_id=%s tenant_id=%s bot_user_count=%d — "
                    "ClientProfile not yet computed",
                    user_id,
                    tenant_id_str,
                    len(bot_user_ids),
                )
                return

            # Migration-ordering defence: if W4's ClientProfile review
            # fields are not yet applied, gracefully skip with warning.
            # After W4 merges, the hasattr check passes and the update
            # fires normally.
            sample = profiles[0]
            required_fields = (
                "last_review_rating",
                "last_review_at",
                "low_rating_flag",
                "sentiment_score",
            )
            missing = [f for f in required_fields if not hasattr(sample, f)]
            if missing:
                logger.warning(
                    "eventbus.consumer.reviews.review_created.fields_missing "
                    "missing=%s — W4 ClientProfile migration not yet "
                    "applied, skipping ClientProfile update (review_id "
                    "dedupe still recorded). tenant_id=%s review_id=%s",
                    missing,
                    tenant_id_str,
                    review_id,
                )
                return

            updated_count = 0
            for profile in profiles:
                # Idempotency by value: if all 4 fields already equal
                # what we'd write, no UPDATE fires. Per-review dedupe
                # above is the primary guard; this is belt-and-suspenders.
                update_fields: list[str] = []
                if getattr(profile, "last_review_rating") != rating:
                    setattr(profile, "last_review_rating", rating)
                    update_fields.append("last_review_rating")
                if getattr(profile, "last_review_at") != envelope.occurred_at:
                    setattr(profile, "last_review_at", envelope.occurred_at)
                    update_fields.append("last_review_at")
                if low_rating and not getattr(profile, "low_rating_flag"):
                    # Sticky: only set True; never reset to False
                    # here. Admin workflow clears the flag externally.
                    setattr(profile, "low_rating_flag", True)
                    update_fields.append("low_rating_flag")
                if getattr(profile, "sentiment_score") != sentiment:
                    setattr(profile, "sentiment_score", sentiment)
                    update_fields.append("sentiment_score")

                if update_fields:
                    profile.save(update_fields=update_fields)
                    updated_count += 1

            logger.info(
                "eventbus.consumer.reviews.review_created.applied "
                "tenant_id=%s review_id=%s rating=%d sentiment=%.1f "
                "low_rating=%s profiles_updated=%d event_id=%s",
                tenant_id_str,
                review_id,
                rating,
                sentiment,
                low_rating,
                updated_count,
                envelope.event_id,
            )
    except IntegrityError:
        logger.info(
            "eventbus.consumer.reviews.review_created.already_processed "
            "tenant_id=%s review_id=%s event_id=%s",
            tenant_id_str,
            review_id,
            envelope.event_id,
        )
        return


# ─── registration ──────────────────────────────────────────────────────────


def register_reviews_handlers() -> None:
    """Register reviews handlers with the ingest dispatcher.

    Called from :func:`apps.eventbus.apps.EventBusConfig.ready`.
    """
    try:
        register(
            event_name="review.created",
            event_version=1,
            handler=handle_review_created,
        )
    except ValueError:
        # Duplicate registration — silently OK on autoreload.
        pass
