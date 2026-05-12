"""Synthetic BookingFact generator (DRF-545 / Sprint 6 / I2).

Phase 0 has no real Booking model yet — `mysite/maxbot/` owns booking
state, and the lift into `apps/booking/` is Phase 1. Sprint 6 needs
something to feed the RFM/LTV/churn pipeline so the contract can be
exercised and ClientProfile rows can be populated for the exit-gate.

This module provides:
- :class:`BookingFact` — frozen dataclass mirroring the shape we'll get
  from real Booking once Phase 1 lifts it.
- :func:`generate_visits` — deterministic generator (seed-keyed) that
  produces realistic distributions across RFM segments.

### Design rules

- **No DB writes.** Pure data; persistence is the caller's concern.
- **Deterministic per seed.** Same seed → same output (essential for
  test repeatability).
- **Realistic skew.** Distributions match real salon traffic shape —
  few champions, many at_risk, long tail of hibernating.
- **Drop-in for Phase 1.** When Booking model lands, replace this
  module's role with a queryset adapter that yields `BookingFact`
  objects from real rows. The RFM/LTV/churn services don't change.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterator
from uuid import UUID


@dataclass(frozen=True)
class BookingFact:
    """One completed visit. Source of truth for RFM/LTV/churn computation.

    Phase 0: produced by :func:`generate_visits` for tests + demo.
    Phase 1: produced by a queryset adapter over the real Booking model.
    """

    bot_user_id: UUID
    visited_at: datetime
    amount: Decimal  # what the visit cost the customer
    service_id: str = ""  # optional, used for "favorite_service" tracking
    category_id: str = ""
    master_id: str = ""


# Segment templates — (rough_visit_count_range, recency_days_range,
# average_check_range). Tuned to produce sane RFM segments under default
# thresholds (will be tuned in P2 settings.RFM_THRESHOLDS).
_SEGMENT_TEMPLATES: dict[str, dict] = {
    "champion": {
        "visit_count": (10, 30),
        "recency_days": (0, 14),
        "avg_check": (Decimal("2500"), Decimal("5000")),
    },
    "loyal": {
        "visit_count": (5, 12),
        "recency_days": (14, 45),
        "avg_check": (Decimal("1800"), Decimal("3500")),
    },
    "at_risk": {
        "visit_count": (3, 8),
        "recency_days": (60, 180),
        "avg_check": (Decimal("1500"), Decimal("3000")),
    },
    "hibernating": {
        "visit_count": (1, 4),
        "recency_days": (180, 730),
        "avg_check": (Decimal("1000"), Decimal("2500")),
    },
    "new": {
        "visit_count": (1, 2),
        "recency_days": (0, 30),
        "avg_check": (Decimal("1500"), Decimal("3000")),
    },
}


@dataclass
class SegmentMix:
    """How many users of each segment to generate. Defaults match a
    realistic small-salon traffic shape — ~5% champions, ~15% loyal,
    ~20% at_risk, ~40% hibernating, ~20% new."""

    champion: int = 5
    loyal: int = 15
    at_risk: int = 20
    hibernating: int = 40
    new: int = 20

    @property
    def total(self) -> int:
        return self.champion + self.loyal + self.at_risk + self.hibernating + self.new


@dataclass(frozen=True)
class SyntheticBotUserSpec:
    """Header for one synthetic bot_user: id + segment + their visits."""

    bot_user_id: UUID
    segment: str
    visits: tuple[BookingFact, ...] = field(default_factory=tuple)


def generate_visits(
    *,
    mix: SegmentMix | None = None,
    seed: int = 0,
    as_of: datetime | None = None,
    bot_user_ids: list[UUID] | None = None,
) -> list[SyntheticBotUserSpec]:
    """Generate synthetic per-user visit histories.

    Args:
      mix: distribution across segments. Defaults to small-salon shape.
      seed: deterministic random seed. Same seed → same output.
      as_of: anchor datetime for recency calculations. Defaults to now.
      bot_user_ids: pre-allocated UUIDs in order. If shorter than total,
        new UUIDs are generated for the remainder. If None, all UUIDs
        are generated.

    Returns:
      One :class:`SyntheticBotUserSpec` per synthetic user, in segment
      order: champion → loyal → at_risk → hibernating → new.
    """

    mix = mix or SegmentMix()
    rng = random.Random(seed)
    as_of = as_of or datetime.now(UTC)
    bot_user_ids = list(bot_user_ids or [])

    specs: list[SyntheticBotUserSpec] = []
    segment_order = [
        ("champion", mix.champion),
        ("loyal", mix.loyal),
        ("at_risk", mix.at_risk),
        ("hibernating", mix.hibernating),
        ("new", mix.new),
    ]
    user_index = 0
    for segment, count in segment_order:
        template = _SEGMENT_TEMPLATES[segment]
        for _ in range(count):
            if user_index < len(bot_user_ids):
                uid = bot_user_ids[user_index]
            else:
                # Use rng to derive UUIDs deterministically.
                uid = UUID(int=rng.getrandbits(128))
            user_index += 1

            visit_count = rng.randint(*template["visit_count"])
            most_recent_days_ago = rng.randint(*template["recency_days"])
            avg_check_lo, avg_check_hi = template["avg_check"]

            visits: list[BookingFact] = []
            # Spread historical visits BEFORE the most recent one.
            most_recent = as_of - timedelta(days=most_recent_days_ago)
            for visit_i in range(visit_count):
                if visit_i == 0:
                    visited_at = most_recent
                else:
                    # Earlier visits at increasing intervals (gamma-ish).
                    gap_days = rng.randint(7, 60) * visit_i
                    visited_at = most_recent - timedelta(days=gap_days)
                amount = Decimal(rng.randint(int(avg_check_lo), int(avg_check_hi)))
                visits.append(
                    BookingFact(
                        bot_user_id=uid,
                        visited_at=visited_at,
                        amount=amount,
                        service_id=f"svc_{rng.choice(['relax', 'antistress', 'recovery', 'tone'])}",
                        category_id="massage",
                        master_id=f"master_{rng.randint(1, 5)}",
                    )
                )
            specs.append(
                SyntheticBotUserSpec(
                    bot_user_id=uid,
                    segment=segment,
                    visits=tuple(visits),
                )
            )

    return specs


def flatten_visits(specs: list[SyntheticBotUserSpec]) -> Iterator[BookingFact]:
    """Yield every visit across all synthetic users (for bulk-style tests)."""

    for spec in specs:
        yield from spec.visits
