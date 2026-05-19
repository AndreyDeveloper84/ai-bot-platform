"""Integration test for the PII redacting log filter.

Configures Python logging programmatically with the filter attached to a
``StreamHandler`` writing to a ``StringIO``. Logs a line containing PII
through a real logger, then asserts the captured stream is redacted.

Phase 1 / PI8 / DRF-859 — verifies the wiring contract that
``config/settings/base.py``'s ``LOGGING`` dict-config relies on.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator

import pytest

from apps.observability.pii_filter import PIIRedactingFilter


@pytest.fixture()
def captured_logger() -> Iterator[tuple[logging.Logger, io.StringIO]]:
    """Build a fresh logger + StringIO sink with the PII filter attached.

    Yields ``(logger, stream)``. Cleans up the handler in a finally to
    avoid leaking state between tests.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(PIIRedactingFilter())

    # Use a uniquely-named logger so the test never picks up unrelated
    # handlers from the global root.
    logger = logging.getLogger("apps.observability.tests.pii_filter_integration")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)

    try:
        yield logger, stream
    finally:
        logger.removeHandler(handler)


def test_filter_redacts_pii_through_real_handler(
    captured_logger: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = captured_logger
    logger.info(
        "client phone %s email %s card %s",
        "+7 905 123 4567",
        "client@example.com",
        "4111 1111 1111 1111",
    )

    output = stream.getvalue()
    assert "[PHONE]" in output
    assert "[EMAIL]" in output
    assert "[CARD]" in output
    # And the raw secrets must not survive.
    assert "+7 905 123 4567" not in output
    assert "client@example.com" not in output
    assert "4111 1111 1111 1111" not in output


def test_filter_preserves_whitelisted_dict_keys(
    captured_logger: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = captured_logger
    logger.info(
        "record %(yclients_record_id)s for tenant %(tenant_id)s",
        {
            "yclients_record_id": "1234567890123456",
            "tenant_id": "abc-tenant-123",
        },
    )

    output = stream.getvalue()
    # Both values are opaque IDs — must NOT be redacted.
    assert "1234567890123456" in output
    assert "abc-tenant-123" in output
    assert "[CARD]" not in output
    assert "[PHONE]" not in output
