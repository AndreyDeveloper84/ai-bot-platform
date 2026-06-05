"""Pin defensive parsing for ``IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS``.

Issue #693 (follow-up from #659 review): the previous binding —

    IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS = int(
        os.environ.get("IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS", "60")
    )

— would raise ``ValueError`` at module-load time if an operator set the
env var to a non-integer string (e.g. ``IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS=abc``).
Because settings load happens during Django boot, the crash takes down
every worker simultaneously.  The fix wraps the parse in try/except and
falls back to the 60s default while logging a WARNING.

These tests pin the defensive behaviour so a future refactor can't
silently regress to the crash-on-bad-input variant.
"""

from __future__ import annotations

import logging
from unittest import mock


class TestIdleActiveDraftSuppressWindowParsing:
    def test_idle_active_draft_suppress_window_uses_default_when_unset(
        self, monkeypatch: object
    ) -> None:
        # No env var set → default 60s.  We invoke the parser function
        # directly rather than reading the module-level binding so the
        # assertion stays decoupled from whatever the test-env's
        # settings file has cached at boot.
        import config.settings.base as base_settings

        monkeypatch.delenv(  # type: ignore[attr-defined]
            "IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS", raising=False
        )
        assert base_settings._parse_idle_active_draft_suppress_window() == 60

    def test_idle_active_draft_suppress_window_parses_valid_integer(self) -> None:
        import config.settings.base as base_settings

        with mock.patch.dict("os.environ", {"IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS": "120"}):
            assert base_settings._parse_idle_active_draft_suppress_window() == 120

    def test_idle_active_draft_suppress_window_accepts_zero_as_disable(self) -> None:
        # 0 is a legitimate operator setting — disables the suppress
        # entirely (regression escape hatch).  Must parse cleanly, not
        # fall into the fallback path.
        import config.settings.base as base_settings

        with mock.patch.dict("os.environ", {"IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS": "0"}):
            assert base_settings._parse_idle_active_draft_suppress_window() == 0

    def test_idle_active_draft_suppress_window_falls_back_on_bad_env_value(
        self, caplog: object
    ) -> None:
        """Operator typo (non-integer) must NOT crash Django boot.

        Falls back to the documented 60s default and logs a WARNING so
        the misconfiguration is visible in logs / Sentry without taking
        the workers down at module-load time.
        """

        import config.settings.base as base_settings

        with mock.patch.dict(
            "os.environ", {"IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS": "not-a-number"}
        ):
            # caplog is the pytest fixture; type: ignore-style cast.
            assert hasattr(caplog, "at_level"), "pytest caplog fixture required"
            with caplog.at_level(logging.WARNING, logger="config.settings.base"):  # type: ignore[attr-defined]
                value = base_settings._parse_idle_active_draft_suppress_window()
            assert value == 60
            warning_lines = [
                r.getMessage()
                for r in caplog.records  # type: ignore[attr-defined]
                if "IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS" in r.getMessage()
                and r.levelno >= logging.WARNING
            ]
            assert warning_lines, "must log a WARNING with the bad value"
            assert "not-a-number" in warning_lines[0]

    def test_idle_active_draft_suppress_window_setting_is_int(self) -> None:
        """The module-level binding must always be an int (never a
        string from the env, never a None from a failed parse).  Type
        coercion is what consumers downstream (the suppress gate `if
        suppress_window_seconds > 0:` comparison) depend on."""

        from django.conf import settings as django_settings

        assert isinstance(django_settings.IDLE_ACTIVE_DRAFT_SUPPRESS_WINDOW_SECONDS, int)
