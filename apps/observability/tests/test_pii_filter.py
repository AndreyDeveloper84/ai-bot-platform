"""Unit tests for :class:`apps.observability.pii_filter.PIIRedactingFilter`.

Phase 1 / PI8 / DRF-859 — covers phone / email / credit-card redaction,
opaque-id whitelist (named keys + ``_id``/``_uuid`` suffix), args
handling (tuple + dict + None), in-place mutation, idempotency, and a
performance benchmark (< 1 ms per record on average).
"""

from __future__ import annotations

import logging
import time

import pytest

from apps.observability.pii_filter import (
    PIIRedactingFilter,
    _luhn_valid,
)


def _make_record(
    msg: object,
    args: object = None,
    name: str = "apps.test",
) -> logging.LogRecord:
    """Build a minimal LogRecord matching the stdlib factory shape."""
    return logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname="x.py",
        lineno=1,
        msg=msg,  # type: ignore[arg-type]
        args=args,  # type: ignore[arg-type]
        exc_info=None,
    )


@pytest.fixture()
def pii_filter() -> PIIRedactingFilter:
    return PIIRedactingFilter()


# ---------------------------------------------------------------------------
# Phone redaction
# ---------------------------------------------------------------------------


class TestPhoneRedaction:
    def test_plus7_with_spaces(self, pii_filter: PIIRedactingFilter) -> None:
        record = _make_record("клиент +7 905 123 4567 звонил")
        pii_filter.filter(record)
        assert record.msg == "клиент [PHONE] звонил"

    def test_plus7_no_separators(self, pii_filter: PIIRedactingFilter) -> None:
        record = _make_record("phone +79051234567 saved")
        pii_filter.filter(record)
        assert record.msg == "phone [PHONE] saved"

    def test_eight_prefix_with_parens(self, pii_filter: PIIRedactingFilter) -> None:
        record = _make_record("call 8 (905) 123-45-67 now")
        pii_filter.filter(record)
        assert record.msg == "call [PHONE] now"

    def test_eight_prefix_no_separators(self, pii_filter: PIIRedactingFilter) -> None:
        record = _make_record("89051234567")
        pii_filter.filter(record)
        assert record.msg == "[PHONE]"

    def test_us_number_not_redacted(self, pii_filter: PIIRedactingFilter) -> None:
        """Out of scope per ticket — only RU numbers."""
        record = _make_record("+1 555 123 4567")
        pii_filter.filter(record)
        # Should remain visible (or at most: not turned into [PHONE]).
        assert "[PHONE]" not in str(record.msg)


# ---------------------------------------------------------------------------
# Email redaction
# ---------------------------------------------------------------------------


class TestEmailRedaction:
    def test_basic_email(self, pii_filter: PIIRedactingFilter) -> None:
        record = _make_record("sent to client@example.com today")
        pii_filter.filter(record)
        assert record.msg == "sent to [EMAIL] today"

    def test_email_with_plus_and_subdomain(self, pii_filter: PIIRedactingFilter) -> None:
        record = _make_record("notify info+filter@subdomain.example.co.uk asap")
        pii_filter.filter(record)
        assert record.msg == "notify [EMAIL] asap"

    def test_bare_at_username_not_redacted(self, pii_filter: PIIRedactingFilter) -> None:
        """Telegram-style @username has no domain, must NOT match."""
        record = _make_record("@username on telegram")
        pii_filter.filter(record)
        assert "[EMAIL]" not in str(record.msg)
        assert record.msg == "@username on telegram"


# ---------------------------------------------------------------------------
# Credit card redaction (Luhn-gated)
# ---------------------------------------------------------------------------


class TestCreditCardRedaction:
    def test_visa_with_spaces(self, pii_filter: PIIRedactingFilter) -> None:
        # 4111 1111 1111 1111 — canonical Luhn-valid Visa test number.
        record = _make_record("card 4111 1111 1111 1111 declined")
        pii_filter.filter(record)
        assert record.msg == "card [CARD] declined"

    def test_visa_no_separators(self, pii_filter: PIIRedactingFilter) -> None:
        record = _make_record("card=4111111111111111")
        pii_filter.filter(record)
        assert record.msg == "card=[CARD]"

    def test_yclients_record_id_not_card_passes_through(
        self, pii_filter: PIIRedactingFilter
    ) -> None:
        """16-digit non-Luhn id must NOT be redacted (Luhn gate)."""
        # 1234567890123456 — fails Luhn.
        assert not _luhn_valid("1234567890123456")
        record = _make_record("YClients record_id=1234567890123456")
        pii_filter.filter(record)
        assert "[CARD]" not in str(record.msg)
        assert "1234567890123456" in str(record.msg)


# ---------------------------------------------------------------------------
# Args handling (tuple, dict, whitelist)
# ---------------------------------------------------------------------------


class TestTupleArgs:
    def test_tuple_string_value_redacted(self, pii_filter: PIIRedactingFilter) -> None:
        record = _make_record("phone: %s", ("+7 905 123 4567",))
        pii_filter.filter(record)
        # After redaction, the format-and-render step gives the final text.
        assert record.getMessage() == "phone: [PHONE]"

    def test_tuple_non_string_passes_through(self, pii_filter: PIIRedactingFilter) -> None:
        record = _make_record("count: %d", (42,))
        pii_filter.filter(record)
        assert record.getMessage() == "count: 42"

    def test_tuple_mixed(self, pii_filter: PIIRedactingFilter) -> None:
        record = _make_record(
            "user %s phone %s count %d",
            ("alice", "+7 905 123 4567", 3),
        )
        pii_filter.filter(record)
        assert record.getMessage() == "user alice phone [PHONE] count 3"


class TestDictArgs:
    def test_dict_value_redacted(self, pii_filter: PIIRedactingFilter) -> None:
        # Stdlib Logger wraps a positional-dict arg as ``({...},)`` then
        # LogRecord.__init__ unwraps the single dict — constructing
        # LogRecord directly we must pass the tuple form so the unwrap
        # branch runs.
        record = _make_record(
            "user %(name)s phone %(phone)s",
            ({"name": "X", "phone": "+7 905 123 4567"},),
        )
        pii_filter.filter(record)
        assert record.getMessage() == "user X phone [PHONE]"

    def test_dict_whitelisted_key_not_redacted(self, pii_filter: PIIRedactingFilter) -> None:
        # trace_id is on the whitelist — it's an opaque identifier
        # whose value happens to look digit-heavy.
        record = _make_record(
            "trace %(trace_id)s",
            ({"trace_id": "abc-123-def-456"},),
        )
        pii_filter.filter(record)
        assert record.getMessage() == "trace abc-123-def-456"

    def test_dict_suffix_id_not_redacted(self, pii_filter: PIIRedactingFilter) -> None:
        # ``yclients_record_id`` ends in _id → opaque.
        # Value is a Luhn-valid 16-digit string — proves the whitelist
        # branch wins before the CC regex even runs against the value.
        # 4111 1111 1111 1111 IS Luhn-valid, but is a dict-arg keyed by
        # a whitelisted suffix, so passes through.
        record = _make_record(
            "record %(yclients_record_id)s",
            ({"yclients_record_id": "4111111111111111"},),
        )
        pii_filter.filter(record)
        assert record.getMessage() == "record 4111111111111111"

    def test_dict_uuid_suffix_not_redacted(self, pii_filter: PIIRedactingFilter) -> None:
        record = _make_record(
            "session %(session_uuid)s",
            ({"session_uuid": "550e8400-e29b-41d4-a716-446655440000"},),
        )
        pii_filter.filter(record)
        # Stays as-is (UUID is opaque).
        assert "446655440000" in record.getMessage()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_no_pii_in_record_passes_through(self, pii_filter: PIIRedactingFilter) -> None:
        record = _make_record("ordinary log line about cats")
        pii_filter.filter(record)
        assert record.msg == "ordinary log line about cats"

    def test_filter_returns_true(self, pii_filter: PIIRedactingFilter) -> None:
        record = _make_record("anything")
        assert pii_filter.filter(record) is True

    def test_in_place_mutation(self, pii_filter: PIIRedactingFilter) -> None:
        """Filter must mutate the record object the caller already holds."""
        record = _make_record("phone +7 905 123 4567")
        same_ref = record
        pii_filter.filter(record)
        assert same_ref is record
        assert "[PHONE]" in str(same_ref.msg)

    def test_non_string_args_pass_through(self, pii_filter: PIIRedactingFilter) -> None:
        record = _make_record("data: %s %s %s", (None, 42, [1, 2, 3]))
        pii_filter.filter(record)
        # None, int, list survive (rendered by % formatting).
        assert record.args == (None, 42, [1, 2, 3])

    def test_empty_msg(self, pii_filter: PIIRedactingFilter) -> None:
        record = _make_record("")
        pii_filter.filter(record)
        assert record.msg == ""

    def test_args_none(self, pii_filter: PIIRedactingFilter) -> None:
        record = _make_record("plain", None)
        pii_filter.filter(record)
        assert record.msg == "plain"
        assert record.args is None

    def test_idempotent(self, pii_filter: PIIRedactingFilter) -> None:
        """Running the filter twice on the same record is a no-op the
        second time (placeholders don't re-match the patterns)."""
        record = _make_record("phone +7 905 123 4567 email a@b.co")
        pii_filter.filter(record)
        first = record.msg
        pii_filter.filter(record)
        assert record.msg == first

    def test_non_string_msg(self, pii_filter: PIIRedactingFilter) -> None:
        """Some loggers pass non-string msg (e.g. an exception). Don't crash."""
        exc = ValueError("phone +7 905 123 4567")
        record = _make_record(exc)
        pii_filter.filter(record)
        # Non-string msg is left alone (str() conversion happens in formatter).
        assert record.msg is exc


# ---------------------------------------------------------------------------
# Performance benchmark (1 ms / record average)
# ---------------------------------------------------------------------------


class TestPerformance:
    def test_10k_records_under_10s(self, pii_filter: PIIRedactingFilter) -> None:
        """Budget: < 1 ms per record on average. 10k iterations = < 10 s."""
        msgs = [
            "ordinary log line about cats",  # no-PII short-circuit
            "phone +7 905 123 4567 saved",
            "email user@example.com sent",
            "YClients record_id=1234567890123456",  # 16-digit non-card
            "card 4111 1111 1111 1111 declined",
        ]
        start = time.perf_counter()
        for i in range(10_000):
            record = _make_record(msgs[i % len(msgs)])
            pii_filter.filter(record)
        elapsed = time.perf_counter() - start
        assert elapsed < 10.0, f"10k records took {elapsed:.2f}s (budget 10s)"


# ---------------------------------------------------------------------------
# Luhn helper sanity (small standalone block, kept here for proximity)
# ---------------------------------------------------------------------------


class TestLuhnHelper:
    def test_valid_visa(self) -> None:
        assert _luhn_valid("4111111111111111") is True

    def test_invalid_short(self) -> None:
        assert _luhn_valid("4111") is False

    def test_invalid_long(self) -> None:
        assert _luhn_valid("4" * 25) is False

    def test_invalid_non_digit(self) -> None:
        assert _luhn_valid("4111-1111-1111-1111") is False  # caller must strip

    def test_random_16_digit_fails(self) -> None:
        assert _luhn_valid("1234567890123456") is False
