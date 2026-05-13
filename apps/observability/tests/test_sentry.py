"""Sentry SDK + PII scrubber tests (DRF-710 / Sprint 8 / E1)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from apps.observability import sentry


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    sentry.reset_sentry_for_tests()
    yield
    sentry.reset_sentry_for_tests()


class TestConfigureSentry:
    def test_empty_dsn_noop(self, settings: Any) -> None:
        settings.SENTRY_DSN = ""
        assert sentry.configure_sentry() is False

    def test_dsn_present_initializes(self, settings: Any) -> None:
        settings.SENTRY_DSN = "https://public@sentry.example.com/1"
        settings.SENTRY_ENVIRONMENT = "test"
        settings.SENTRY_TRACES_SAMPLE_RATE = 0.1

        captured: dict[str, Any] = {}
        with patch("sentry_sdk.init") as mock_init:
            mock_init.side_effect = lambda **kwargs: captured.update(kwargs)
            result = sentry.configure_sentry()

        assert result is True
        assert captured.get("dsn") == "https://public@sentry.example.com/1"
        assert captured.get("environment") == "test"
        assert captured.get("traces_sample_rate") == 0.1
        assert captured.get("before_send") is sentry.scrub_event

    def test_idempotent(self, settings: Any) -> None:
        settings.SENTRY_DSN = ""
        assert sentry.configure_sentry() is False
        assert sentry.configure_sentry() is False


class TestScrubEvent:
    def test_phone_redacted_in_message(self) -> None:
        event = {"message": "user reported +79991234567 broke flow"}
        result = sentry.scrub_event(event)
        assert "+79991234567" not in result["message"]

    def test_email_redacted_in_exception(self) -> None:
        event = {
            "exception": {
                "values": [{"type": "LLMError", "value": "rate-limit for user@example.com"}]
            }
        }
        result = sentry.scrub_event(event)
        # Recursive walk redacts every str leaf.
        assert "user@example.com" not in result["exception"]["values"][0]["value"]

    def test_non_string_leaves_preserved(self) -> None:
        """Numeric / boolean fields must NOT be touched by the redactor."""
        event = {
            "level": "error",
            "extra": {"retry_count": 3, "succeeded": False, "score": 0.92},
        }
        result = sentry.scrub_event(event)
        assert result["extra"]["retry_count"] == 3
        assert result["extra"]["succeeded"] is False
        assert result["extra"]["score"] == 0.92

    def test_tags_attached_for_otel_context(self) -> None:
        """When an OTel span is current, scrub_event sets `trace_id` /
        `span_id` tags on the event.
        """
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider()
        trace.set_tracer_provider(provider)
        tracer = trace.get_tracer("test-e1")

        event: dict[str, Any] = {"message": "x"}
        with tracer.start_as_current_span("test-span"):
            result = sentry.scrub_event(event)

        # tags injected; values are hex strings
        assert "trace_id" in result.get("tags", {})
        assert "span_id" in result.get("tags", {})
        assert len(result["tags"]["trace_id"]) == 32  # OTel trace_id hex
        assert len(result["tags"]["span_id"]) == 16  # OTel span_id hex
