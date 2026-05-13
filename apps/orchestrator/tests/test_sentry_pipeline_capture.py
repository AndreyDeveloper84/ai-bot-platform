"""Sentry pipeline error capture (DRF-711 / Sprint 8 / E2)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from apps.orchestrator.pipeline import ChannelMessage, turn


pytestmark = pytest.mark.django_db


def _msg(tenant_slug: str = "ghost") -> ChannelMessage:
    return ChannelMessage(
        tenant_slug=tenant_slug,
        channel="max",
        channel_user_id="e2-uid",
        chat_id="e2-chat",
        text="boom",
    )


class TestUnhandledPipelineError:
    def test_capture_called_with_tags(self) -> None:
        """When turn() crashes, sentry_sdk.capture_exception is called
        with tags for trace_id, tenant_slug, channel, pipeline_step.
        """
        # Force the very first step (tenant resolve) to crash.
        with (
            patch(
                "apps.orchestrator.pipeline._resolve_tenant",
                side_effect=RuntimeError("synthetic boom"),
            ),
            patch("sentry_sdk.capture_exception") as mock_capture,
            patch("sentry_sdk.new_scope") as mock_scope,
        ):
            scope = mock_scope.return_value.__enter__.return_value
            captured_tags: dict[str, str] = {}
            scope.set_tag.side_effect = lambda k, v: captured_tags.update({k: v})

            result = asyncio.run(turn(_msg()))

        assert result.ok is False
        mock_capture.assert_called_once()
        # The exception passed to capture is the RuntimeError raised in resolver.
        captured_exc = mock_capture.call_args.args[0]
        assert isinstance(captured_exc, RuntimeError)
        # Tags applied via scope.
        assert captured_tags.get("trace_id")  # any non-empty string
        assert captured_tags.get("tenant_slug") == "ghost"
        assert captured_tags.get("channel") == "max"
        assert captured_tags.get("pipeline_step") == "turn"


class TestSentryNeverBreaksRequest:
    def test_sentry_import_error_swallowed(self) -> None:
        """If sentry_sdk is uninstalled, the pipeline still returns
        a graceful TurnResult.
        """

        real_import = (
            __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__  # type: ignore[name-defined]
        )

        def _failing_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "sentry_sdk" or name.startswith("sentry_sdk."):
                raise ImportError("simulated")
            return real_import(name, *args, **kwargs)

        with (
            patch(
                "apps.orchestrator.pipeline._resolve_tenant",
                side_effect=RuntimeError("synthetic"),
            ),
            patch("builtins.__import__", side_effect=_failing_import),
        ):
            result = asyncio.run(turn(_msg()))

        assert result.ok is False
        assert "unhandled" in result.error


class TestNonFatalPathsDoNotCapture:
    def test_unknown_tenant_does_not_call_sentry(self) -> None:
        """Unknown-tenant short-circuit is an EXPECTED failure (TurnResult.ok=False
        with error='unknown_tenant'), not an unhandled exception. Sentry
        capture must NOT fire — otherwise the dashboard fills with noise
        from misconfigured channel tokens.
        """
        with patch("sentry_sdk.capture_exception") as mock_capture:
            result = asyncio.run(turn(_msg(tenant_slug="this-tenant-does-not-exist")))

        assert result.ok is False
        assert result.error == "unknown_tenant"
        mock_capture.assert_not_called()
