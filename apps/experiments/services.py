"""Experiment + holdout services (DRF-485 / DRF-486 / Sprint 4 / B2+B3).

Three entry points the orchestrator hits per turn:

  * :func:`ensure_holdout` — first-touch decision: is this user in
    the global 5% holdout? Deterministic sha256 hash, no global
    counter. Returns ``True`` if in holdout (caller short-circuits
    experiment treatment).
  * :func:`is_in_holdout` — read-only check (no row creation).
  * :func:`assign_variant` — sticky bucketing. Returns existing
    :class:`UserAssignment` if one exists; otherwise computes the
    variant via sha256(user_id + experiment_name), creates the row,
    emits ``experiment_assigned``. Raises :class:`InHoldoutError` if
    the user is in the holdout cohort.

### Deterministic hashes

Both holdout + variant use sha256 over stable strings so:
  - same user → same decision across N calls + worker restarts
  - no shared global state (no Redis counter, no DB autoinc)
  - bucketing distribution converges to weights over enough users
    (uniform-distribution assumption of SHA-256)

The holdout salt ``"holdout-salt-2026"`` is fixed: rotating it would
re-bucket every user and bias long-term baseline comparisons.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

from apps.audit.services import write_audit
from apps.events.services import emit
from apps.experiments.models import Experiment, Holdout, UserAssignment
from apps.tenancy.context import current_tenant

if TYPE_CHECKING:
    from apps.identity.models import BotUser

logger = logging.getLogger(__name__)

_HOLDOUT_SALT = "holdout-salt-2026"  # fixed — rotating biases baseline
_HOLDOUT_PERCENT = 5  # global; ADR-level constant


class InHoldoutError(Exception):
    """Raised by `assign_variant` when the bot_user is in the holdout cohort.

    Caller decides what to do — typically returns the control treatment
    without recording an assignment row.
    """

    def __init__(self, bot_user_id: str) -> None:
        self.bot_user_id = bot_user_id
        super().__init__(f"BotUser {bot_user_id} is in the global holdout")


def _is_in_holdout_cohort(bot_user_id: str) -> bool:
    """Pure function: deterministic 5% hash decision."""

    digest = hashlib.sha256(f"{bot_user_id}{_HOLDOUT_SALT}".encode()).hexdigest()
    # Take 8 hex chars (32 bits) → int → % 100 < 5.
    return (int(digest[:8], 16) % 100) < _HOLDOUT_PERCENT


def is_in_holdout(bot_user: "BotUser") -> bool:
    """Read-only holdout check. Does NOT create a Holdout row.

    Use this from skill-dispatch hot paths where you want the boolean
    decision without writing a row (e.g. SkillContext composition).
    """

    return _is_in_holdout_cohort(str(bot_user.id))


def ensure_holdout(bot_user: "BotUser") -> bool:
    """First-touch holdout decision. Returns True iff user is in holdout.

    If the hash decision says "yes" AND no `Holdout` row exists for the
    user, creates one + writes an audit row. Idempotent — second call
    returns the same boolean without re-creating.

    Args:
      bot_user: must belong to the current tenant scope.

    Returns:
      True if the user is in the global 5% holdout cohort.

    Raises:
      ValueError: missing tenant context.
    """

    tenant = current_tenant()
    if tenant is None:
        raise ValueError("experiments.ensure_holdout requires a tenant in scope.")

    in_holdout = _is_in_holdout_cohort(str(bot_user.id))
    if not in_holdout:
        return False

    # In the holdout — ensure a row exists. The OneToOne constraint
    # makes get_or_create the idempotent primitive.
    _, created = Holdout.all_tenants.get_or_create(bot_user=bot_user, defaults={"tenant": tenant})
    if created:
        write_audit(
            "experiments.holdout.created",
            target="Holdout",
            target_id=bot_user.id,
            payload={"bot_user_id": str(bot_user.id)},
        )
        logger.info(
            "experiments.holdout.created bot_user=%s tenant=%s",
            bot_user.id,
            tenant.id,
        )
    return True


def _pick_variant(bot_user_id: str, experiment_name: str, variants: list) -> str:
    """Pure: deterministic weighted bucketing by sha256.

    `variants` is a list of {"name": str, "weight": int} dicts.
    Total weight = sum of weights. Hash → int → % total_weight; pick
    the variant whose cumulative weight covers that bucket.
    """

    if not variants:
        raise ValueError("experiment has no variants")

    digest = hashlib.sha256(f"{bot_user_id}{experiment_name}".encode()).hexdigest()
    total = sum(int(v.get("weight", 0)) for v in variants)
    if total <= 0:
        raise ValueError(f"experiment variants have total weight {total} ≤ 0")
    bucket = int(digest[:8], 16) % total

    cumulative = 0
    for variant in variants:
        cumulative += int(variant.get("weight", 0))
        if bucket < cumulative:
            return str(variant["name"])
    # Defensive — math guarantees we hit a bucket above, but type-narrow
    # for static analysis.
    return str(variants[-1]["name"])


def assign_variant(bot_user: "BotUser", experiment: Experiment) -> UserAssignment:
    """Return the sticky variant assignment for (bot_user, experiment).

    Idempotent: if an assignment row already exists, returns it as-is
    (sticky). Otherwise computes the variant via deterministic hash,
    creates the row, emits ``experiment_assigned``.

    Raises:
      InHoldoutError: bot_user is in the global holdout — caller
        returns control treatment without creating an assignment row.
      ValueError: missing tenant context.
    """

    tenant = current_tenant()
    if tenant is None:
        raise ValueError("experiments.assign_variant requires a tenant in scope.")

    # Cheap lookup-first for the sticky path.
    existing = UserAssignment.all_tenants.filter(bot_user=bot_user, experiment=experiment).first()
    if existing is not None:
        return existing

    # No assignment yet — check holdout BEFORE creating.
    if _is_in_holdout_cohort(str(bot_user.id)):
        raise InHoldoutError(str(bot_user.id))

    variant = _pick_variant(str(bot_user.id), experiment.name, experiment.variants or [])
    assignment = UserAssignment.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        experiment=experiment,
        variant=variant,
    )

    emit(
        "experiment_assigned",
        distinct_id=str(bot_user.id),
        properties={
            "experiment_name": experiment.name,
            "variant": variant,
        },
    )
    logger.info(
        "experiments.assigned bot_user=%s experiment=%s variant=%s",
        bot_user.id,
        experiment.name,
        variant,
    )
    return assignment


def get_variant(bot_user: "BotUser", experiment_name: str) -> str | None:
    """Read-only lookup. Returns variant name or None if no assignment yet."""

    tenant = current_tenant()
    if tenant is None:
        raise ValueError("experiments.get_variant requires a tenant in scope.")

    ua = UserAssignment.all_tenants.filter(
        bot_user=bot_user, experiment__name=experiment_name, tenant=tenant
    ).first()
    return ua.variant if ua is not None else None
