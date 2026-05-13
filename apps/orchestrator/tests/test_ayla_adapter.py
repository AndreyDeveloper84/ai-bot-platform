"""ayla_adapter tests (Sprint 7 / Track A / A1)."""

from __future__ import annotations

import logging
from datetime import date, datetime

import pytest

from apps.orchestrator.ayla_adapter import (
    MAX_CANDIDATES,
    MAX_CLIENT_NAME_LEN,
    MAX_EXTRA_HINT_LEN,
    SafeRenderInputs,
    WindowOverflowError,
    assert_window,
    build_safe_inputs,
    escape_braces,
    sanitize_freeform,
    wrap_untrusted,
)


class TestSanitizeFreeform:
    def test_strips_control_chars(self):
        result = sanitize_freeform("hello\x00\x07\x1f\x7fworld", max_len=100, field_name="x")
        assert result == "helloworld"

    def test_preserves_tab_newline_return(self):
        result = sanitize_freeform("a\tb\nc\rd", max_len=100, field_name="x")
        assert result == "a\tb\nc\rd"

    def test_preserves_braces_unchanged(self):
        # Braces are escape_braces()'s responsibility, not sanitize's.
        result = sanitize_freeform("hello {evil} world", max_len=100, field_name="x")
        assert result == "hello {evil} world"

    def test_strips_outer_whitespace(self):
        result = sanitize_freeform("  hello  ", max_len=100, field_name="x")
        assert result == "hello"

    def test_returns_empty_for_none(self):
        assert sanitize_freeform(None, max_len=100, field_name="x") == ""

    def test_truncates_at_max_len(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = sanitize_freeform("a" * 50, max_len=10, field_name="extra_hint")
        assert len(result) == 10
        assert "ayla_adapter.truncated" in caplog.text
        assert "extra_hint" in caplog.text
        assert "orig_len=50" in caplog.text


class TestEscapeBraces:
    def test_doubles_curly(self):
        assert escape_braces("hi {name} cost 2}") == "hi {{name}} cost 2}}"

    def test_empty_string(self):
        assert escape_braces("") == ""

    def test_no_braces_unchanged(self):
        assert escape_braces("hello world") == "hello world"

    def test_preserves_user_data_through_format(self):
        # End-to-end: escape, then format, gives original literal back.
        original = "{evil}"
        escaped = escape_braces(original)
        rendered = "<{x}>".format(x=escaped)
        # str.format() sees double-braces in `x` as a literal value;
        # downstream rendering re-applies format semantics, so
        # double-braces ARE collapsed to single ONLY if the renderer
        # re-formats. Direct format produces double-braced literal:
        assert rendered == "<{{evil}}>"
        # A second .format() pass DOES collapse, proving the escape works
        # for any downstream consumer that runs .format() once on the
        # rendered template (which is what ayla.prompts.SYSTEM_PROMPT_TEMPLATE
        # does internally).
        final = rendered.format()
        assert final == "<{evil}>"


class TestWrapUntrusted:
    def test_wraps_nonempty(self):
        wrapped = wrap_untrusted("data")
        assert wrapped.startswith("<<<UNTRUSTED_CONTEXT>>>")
        assert wrapped.endswith("<<<UNTRUSTED_CONTEXT>>>")
        assert "data" in wrapped

    def test_passthrough_on_empty(self):
        assert wrap_untrusted("") == ""

    def test_passthrough_on_none_safe(self):
        # wrap_untrusted is only called after sanitize, which never
        # returns None — but the falsy check covers it defensively.
        assert wrap_untrusted("") == ""


class TestAssertWindow:
    def test_passes_at_cap(self):
        # Exactly hard_cap candidates → no raise.
        assert_window([object()] * MAX_CANDIDATES)

    def test_passes_under_cap(self):
        assert_window([object()] * 3)

    def test_raises_over_cap(self):
        with pytest.raises(WindowOverflowError) as exc_info:
            assert_window([object()] * (MAX_CANDIDATES + 1))
        assert str(MAX_CANDIDATES) in str(exc_info.value)
        assert str(MAX_CANDIDATES + 1) in str(exc_info.value)

    def test_custom_cap(self):
        with pytest.raises(WindowOverflowError):
            assert_window([object()] * 5, hard_cap=3)


class TestBuildSafeInputs:
    def test_returns_safe_render_inputs(self):
        result = build_safe_inputs(
            today=date(2026, 1, 1),
            client_name="Alice",
            bookings_count=3,
            extra_hint="some hint",
        )
        assert isinstance(result, SafeRenderInputs)
        assert result.today == date(2026, 1, 1)
        assert result.bookings_count == 3

    def test_with_frozen_now(self):
        # today=None + frozen_now provided → use frozen.
        result = build_safe_inputs(
            today=None,
            client_name="x",
            bookings_count=0,
            extra_hint="",
            frozen_now=datetime(2026, 5, 1, 12, 0, 0),
        )
        assert result.today == date(2026, 5, 1)

    def test_prod_path_falls_back_to_today(self):
        # today=None + frozen_now=None → date.today() (just verify it's a date).
        result = build_safe_inputs(
            today=None,
            client_name="x",
            bookings_count=0,
            extra_hint="",
        )
        assert isinstance(result.today, date)

    def test_explicit_today_wins_over_frozen_now(self):
        # If caller passes today explicitly, frozen_now is ignored.
        result = build_safe_inputs(
            today=date(2027, 7, 7),
            client_name="x",
            bookings_count=0,
            extra_hint="",
            frozen_now=datetime(2026, 1, 1),
        )
        assert result.today == date(2027, 7, 7)

    def test_clamps_negative_bookings(self):
        result = build_safe_inputs(
            today=date(2026, 1, 1),
            client_name="x",
            bookings_count=-5,
            extra_hint="",
        )
        assert result.bookings_count == 0

    def test_escapes_client_name_braces(self):
        result = build_safe_inputs(
            today=date(2026, 1, 1),
            client_name="{evil}",
            bookings_count=0,
            extra_hint="",
        )
        # Adapter escapes via doubling; final .format() inside ayla
        # collapses back to single braces.
        assert result.client_name == "{{evil}}"

    def test_wraps_extra_hint(self):
        result = build_safe_inputs(
            today=date(2026, 1, 1),
            client_name="x",
            bookings_count=0,
            extra_hint="external instruction",
        )
        assert "<<<UNTRUSTED_CONTEXT>>>" in result.extra_hint
        assert "external instruction" in result.extra_hint

    def test_empty_extra_hint_not_wrapped(self):
        result = build_safe_inputs(
            today=date(2026, 1, 1),
            client_name="x",
            bookings_count=0,
            extra_hint="",
        )
        assert result.extra_hint == ""

    def test_strips_ctrl_chars_in_client_name(self):
        result = build_safe_inputs(
            today=date(2026, 1, 1),
            client_name="Al\x00ice",
            bookings_count=0,
            extra_hint="",
        )
        assert result.client_name == "Alice"

    def test_truncates_long_extra_hint(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = build_safe_inputs(
                today=date(2026, 1, 1),
                client_name="x",
                bookings_count=0,
                extra_hint="X" * (MAX_EXTRA_HINT_LEN + 100),
            )
        # Wrapped, but inner content is capped.
        assert "ayla_adapter.truncated" in caplog.text
        inner = result.extra_hint.replace("<<<UNTRUSTED_CONTEXT>>>", "").strip()
        assert len(inner) == MAX_EXTRA_HINT_LEN

    def test_truncates_long_client_name(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = build_safe_inputs(
                today=date(2026, 1, 1),
                client_name="X" * (MAX_CLIENT_NAME_LEN + 100),
                bookings_count=0,
                extra_hint="",
            )
        assert "ayla_adapter.truncated" in caplog.text
        assert len(result.client_name) == MAX_CLIENT_NAME_LEN

    def test_combined_malicious_input(self):
        # End-to-end: control chars + brace injection + length attack.
        result = build_safe_inputs(
            today=date(2026, 1, 1),
            client_name="\x00{evil}<script>",
            bookings_count=-5,
            extra_hint="x" * (MAX_EXTRA_HINT_LEN * 2),
        )
        assert "\x00" not in result.client_name
        assert "{{" in result.client_name  # escaped
        assert result.bookings_count == 0
        assert "<<<UNTRUSTED_CONTEXT>>>" in result.extra_hint
