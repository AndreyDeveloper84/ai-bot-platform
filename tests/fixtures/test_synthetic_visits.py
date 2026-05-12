"""Tests for synthetic visit generator (DRF-545 / Sprint 6 / I2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from tests.fixtures.synthetic_visits import (
    BookingFact,
    SegmentMix,
    flatten_visits,
    generate_visits,
)


class TestGenerator:
    def test_default_mix_produces_5_segments(self):
        specs = generate_visits(seed=42)
        segments = {s.segment for s in specs}
        assert segments == {"champion", "loyal", "at_risk", "hibernating", "new"}

    def test_default_mix_count(self):
        specs = generate_visits(seed=42)
        # Default SegmentMix.total = 5+15+20+40+20 = 100
        assert len(specs) == 100

    def test_segment_distribution_matches_mix(self):
        specs = generate_visits(seed=42)
        by_segment = {}
        for s in specs:
            by_segment[s.segment] = by_segment.get(s.segment, 0) + 1
        assert by_segment == {
            "champion": 5,
            "loyal": 15,
            "at_risk": 20,
            "hibernating": 40,
            "new": 20,
        }

    def test_custom_mix(self):
        mix = SegmentMix(champion=2, loyal=3, at_risk=5, hibernating=10, new=5)
        specs = generate_visits(seed=42, mix=mix)
        assert len(specs) == mix.total == 25

    def test_deterministic_same_seed(self):
        as_of = datetime(2026, 5, 12, tzinfo=UTC)
        a = generate_visits(seed=7, as_of=as_of)
        b = generate_visits(seed=7, as_of=as_of)
        # bot_user_id, segment, visit counts and timestamps must all match.
        assert len(a) == len(b)
        for sa, sb in zip(a, b):
            assert sa.bot_user_id == sb.bot_user_id
            assert sa.segment == sb.segment
            assert len(sa.visits) == len(sb.visits)
            for va, vb in zip(sa.visits, sb.visits):
                assert va.visited_at == vb.visited_at
                assert va.amount == vb.amount

    def test_different_seed_different_output(self):
        a = generate_visits(seed=1)
        b = generate_visits(seed=2)
        # Almost certainly differ on at least one visit timestamp.
        amounts_a = [v.amount for s in a for v in s.visits]
        amounts_b = [v.amount for s in b for v in s.visits]
        assert amounts_a != amounts_b


class TestRecency:
    def test_champion_recency_is_recent(self):
        as_of = datetime(2026, 5, 12, tzinfo=UTC)
        specs = generate_visits(seed=42, as_of=as_of)
        champions = [s for s in specs if s.segment == "champion"]
        for spec in champions:
            most_recent = max(v.visited_at for v in spec.visits)
            recency_days = (as_of - most_recent).days
            assert 0 <= recency_days <= 14, (
                f"Champion {spec.bot_user_id} recency {recency_days}d out of range"
            )

    def test_hibernating_recency_is_old(self):
        as_of = datetime(2026, 5, 12, tzinfo=UTC)
        specs = generate_visits(seed=42, as_of=as_of)
        hibernating = [s for s in specs if s.segment == "hibernating"]
        for spec in hibernating:
            most_recent = max(v.visited_at for v in spec.visits)
            recency_days = (as_of - most_recent).days
            assert 180 <= recency_days <= 730, (
                f"Hibernating {spec.bot_user_id} recency {recency_days}d out of range"
            )


class TestVisitShape:
    def test_visits_use_decimal_amount(self):
        specs = generate_visits(seed=42)
        first_visit = next(iter(specs[0].visits))
        assert isinstance(first_visit.amount, Decimal)

    def test_visits_have_bot_user_id_match(self):
        specs = generate_visits(seed=42)
        for spec in specs:
            for visit in spec.visits:
                assert visit.bot_user_id == spec.bot_user_id

    def test_visits_have_service_metadata(self):
        specs = generate_visits(seed=42)
        for spec in specs:
            for visit in spec.visits:
                assert visit.service_id.startswith("svc_")
                assert visit.category_id == "massage"
                assert visit.master_id.startswith("master_")


class TestBotUserIds:
    def test_uses_supplied_ids_when_provided(self):
        ids = [uuid4() for _ in range(5)]
        mix = SegmentMix(champion=1, loyal=1, at_risk=1, hibernating=1, new=1)
        specs = generate_visits(seed=42, mix=mix, bot_user_ids=ids)
        actual_ids = [s.bot_user_id for s in specs]
        assert actual_ids == ids

    def test_generates_ids_beyond_supplied(self):
        ids = [uuid4()]
        mix = SegmentMix(champion=2, loyal=0, at_risk=0, hibernating=0, new=0)
        specs = generate_visits(seed=42, mix=mix, bot_user_ids=ids)
        assert specs[0].bot_user_id == ids[0]
        # Second one is rng-derived.
        assert specs[1].bot_user_id != ids[0]


class TestFlattenVisits:
    def test_flattens_all_visits(self):
        mix = SegmentMix(champion=2, loyal=0, at_risk=0, hibernating=0, new=0)
        specs = generate_visits(seed=42, mix=mix)
        expected_total = sum(len(s.visits) for s in specs)
        flat = list(flatten_visits(specs))
        assert len(flat) == expected_total
        assert all(isinstance(v, BookingFact) for v in flat)


class TestEdgeCases:
    def test_empty_mix_returns_empty(self):
        empty = SegmentMix(champion=0, loyal=0, at_risk=0, hibernating=0, new=0)
        specs = generate_visits(seed=42, mix=empty)
        assert specs == []

    def test_as_of_anchors_recency(self):
        old_anchor = datetime(2025, 1, 1, tzinfo=UTC)
        specs = generate_visits(seed=42, as_of=old_anchor)
        # No visit should be after the anchor.
        for spec in specs:
            for visit in spec.visits:
                assert visit.visited_at <= old_anchor + timedelta(seconds=1)
