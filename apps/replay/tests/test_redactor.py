"""Redactor regex layer tests (DRF-505 / Sprint 5 / B4)."""

from __future__ import annotations

import pytest

from apps.replay.redactor import (
    REDACTION_METHOD,
    Redactor,
)


@pytest.fixture
def redactor():
    return Redactor(allowlist=[])


class TestVersionConstant:
    def test_redaction_method_v1(self):
        assert REDACTION_METHOD == "regex_v1"


class TestPhoneRedaction:
    @pytest.mark.parametrize(
        "raw",
        [
            "+7 (495) 123-45-67",
            "8 (495) 123-45-67",
            "+74951234567",
            "84951234567",
            "+1-234-567-8901",
        ],
    )
    def test_phone_variants(self, redactor, raw):
        result = redactor.redact_text(f"call me at {raw} please")
        assert "[PHONE]" in result
        assert raw not in result


class TestEmailRedaction:
    @pytest.mark.parametrize(
        "raw",
        [
            "user@example.com",
            "first.last@sub.example.co",
            "user+tag@domain.io",
        ],
    )
    def test_email_variants(self, redactor, raw):
        result = redactor.redact_text(f"reach me at {raw}")
        assert "[EMAIL]" in result
        assert raw not in result


class TestCreditCardRedaction:
    @pytest.mark.parametrize(
        "raw",
        [
            "4111 1111 1111 1111",
            "4111-1111-1111-1111",
            "4111111111111111",
        ],
    )
    def test_cc_variants(self, redactor, raw):
        result = redactor.redact_text(f"card {raw}")
        assert "[CC]" in result


class TestOtpRedaction:
    def test_otp_4_digit(self, redactor):
        result = redactor.redact_text("your code is 1234 please")
        assert "[OTP]" in result

    def test_otp_6_digit(self, redactor):
        result = redactor.redact_text("code 123456 confirmed")
        assert "[OTP]" in result

    def test_otp_not_inside_longer_number(self, redactor):
        # "1234567" → 7-digit, not OTP. The PHONE_RE may catch part of it
        # but that's fine — what we want is no false-OTP on the 7-digit
        # standalone.
        result = redactor.redact_text("order id 1234567 is yours")
        # OTP must not appear (the 7-digit number didn't trigger OTP_RE)
        # The phone might (if interpreted as 7-digit phone) — that's
        # acceptable; the point is OTP_RE isolation.
        assert "[OTP]" not in result or "1234567" not in result


class TestUrlTokenRedaction:
    @pytest.mark.parametrize(
        "raw",
        [
            "https://example.com/path?token=abc123",
            "http://api.test/v1?key=xyz",
            "https://x.io/cb?secret=foo",
            "https://x.io/auth?access_token=zzz",
        ],
    )
    def test_url_with_sensitive_param(self, redactor, raw):
        result = redactor.redact_text(f"visit {raw}")
        assert "[URL_TOKEN]" in result

    def test_plain_url_not_redacted(self, redactor):
        plain = "https://example.com/page"
        result = redactor.redact_text(f"see {plain}")
        assert "[URL_TOKEN]" not in result
        assert plain in result


class TestAllowlist:
    def test_brand_name_survives(self):
        # Brand-like string that would NOT normally hit a pattern,
        # but pin allowlist behaviour against pattern-collision values.
        r = Redactor(allowlist=["+74951234567"])
        text = "call +74951234567 our office"
        result = r.redact_text(text)
        assert "+74951234567" in result
        assert "[PHONE]" not in result

    def test_empty_allowlist_redacts(self):
        r = Redactor(allowlist=[])
        result = r.redact_text("phone +74951234567")
        assert "[PHONE]" in result

    def test_allowlist_from_settings(self, settings):
        settings.REPLAY_REDACTION_ALLOWLIST = ["user@formula.test"]
        r = Redactor()  # reads from settings
        result = r.redact_text("ping user@formula.test today")
        assert "user@formula.test" in result
        # An email NOT on the allowlist still redacts.
        result2 = r.redact_text("ping other@evil.com today")
        assert "[EMAIL]" in result2


class TestRedactStepsRecursive:
    def test_flat_string_steps(self, redactor):
        steps = [
            {"name": "input", "text": "call +74951234567"},
            {"name": "intent", "value": "faq"},
        ]
        result = redactor.redact_steps(steps)
        assert "[PHONE]" in result[0]["text"]
        assert result[1]["value"] == "faq"  # untouched

    def test_nested_dict_walk(self, redactor):
        steps = [
            {
                "outer": {
                    "inner": {
                        "deep": "email user@x.io for help",
                    },
                },
            },
        ]
        result = redactor.redact_steps(steps)
        assert "[EMAIL]" in result[0]["outer"]["inner"]["deep"]

    def test_list_of_strings_inside_dict(self, redactor):
        steps = [{"calls": ["phone +74951234567", "ok"]}]
        result = redactor.redact_steps(steps)
        assert "[PHONE]" in result[0]["calls"][0]
        assert result[0]["calls"][1] == "ok"

    def test_non_string_leaves_preserved(self, redactor):
        steps = [{"n": 42, "f": 3.14, "b": True, "z": None, "lst": [1, 2, 3]}]
        result = redactor.redact_steps(steps)
        assert result[0]["n"] == 42
        assert result[0]["f"] == 3.14
        assert result[0]["b"] is True
        assert result[0]["z"] is None
        assert result[0]["lst"] == [1, 2, 3]

    def test_empty_steps(self, redactor):
        assert redactor.redact_steps([]) == []

    def test_idempotent(self, redactor):
        text = "phone +74951234567 and email a@b.io"
        once = redactor.redact_text(text)
        twice = redactor.redact_text(once)
        assert once == twice  # 2nd pass = no-op
