"""ULID generator — shape + time-sortability."""

from __future__ import annotations

import time

from apps.eventbus.ulid import is_valid, new_ulid


class TestULID:
    def test_shape_is_26_crockford(self):
        u = new_ulid()
        assert len(u) == 26
        assert is_valid(u)

    def test_is_valid_rejects_wrong_length(self):
        assert not is_valid("ABC")
        assert not is_valid("A" * 25)
        assert not is_valid("A" * 27)

    def test_is_valid_rejects_non_crockford_chars(self):
        # I, L, O, U are excluded from Crockford alphabet.
        assert not is_valid("I" * 26)
        assert not is_valid("0" * 25 + "U")

    def test_monotonic_within_same_ms_is_at_least_sortable(self):
        # Without a strict monotonic component we don't guarantee strict
        # ordering inside the same ms, but two ULIDs ms apart must sort.
        a = new_ulid()
        time.sleep(0.005)
        b = new_ulid()
        assert a < b

    def test_no_duplicates_in_batch(self):
        ids = {new_ulid() for _ in range(1000)}
        assert len(ids) == 1000
