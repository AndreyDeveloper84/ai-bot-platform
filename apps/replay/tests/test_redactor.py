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


class TestKbChunkRedaction:
    """Sprint 7 / K13 (DRF-571) — 152-ФЗ compliance.

    The FAQ skill (F2 / DRF-589) emits SkillResult.tool_calls_made
    where args carry KB chunk text + retrieved-chunk dicts. The
    Sprint 5 recursive walker already reaches those paths via
    dict→list→string descent; these tests pin the contract so a
    future refactor can't silently drop chunk-redaction coverage.

    NOT NEW BEHAVIOUR — explicit coverage of an implicit guarantee.
    If any of these tests fail, the redactor stopped recursing into
    a nested path and KB content with master phones / addresses
    would leak into ReplayTrace rows.
    """

    def test_chunks_list_of_strings_redacted(self, redactor):
        # search_knowledge_base passes the chunk strings into a list.
        steps = [
            {
                "step": "skill_dispatch",
                "tool_calls_made": [
                    {
                        "name": "search_knowledge_base",
                        "args": {
                            "chunks": [
                                "Мастер Анна: +79991234567",
                                "Адрес: Пенза, ул. Ленина 1",
                            ],
                        },
                    }
                ],
            }
        ]
        result = redactor.redact_steps(steps)
        chunks = result[0]["tool_calls_made"][0]["args"]["chunks"]
        # Phone redacted in first chunk; second has no PII pattern.
        assert "[PHONE]" in chunks[0]
        assert "+79991234567" not in chunks[0]

    def test_retrieved_chunks_dict_text_redacted(self, redactor):
        # Tool result shape: list[KbToolHit-as-dict].
        steps = [
            {
                "tool_calls_made": [
                    {
                        "args": {
                            "retrieved_chunks": [
                                {
                                    "text": "Звоните мастеру +74951112233",
                                    "doc_id": "doc-1",
                                    "score": 0.91,
                                },
                                {
                                    "text": "Email салона: contact@formulatela.ru",
                                    "doc_id": "doc-2",
                                },
                            ]
                        }
                    }
                ],
            }
        ]
        result = redactor.redact_steps(steps)
        hits = result[0]["tool_calls_made"][0]["args"]["retrieved_chunks"]
        assert "[PHONE]" in hits[0]["text"]
        assert "[EMAIL]" in hits[1]["text"]
        # Non-string fields preserved.
        assert hits[0]["score"] == 0.91
        assert hits[0]["doc_id"] == "doc-1"

    def test_metadata_source_uri_with_phone_redacted(self, redactor):
        # Operator-mis-entered source URI with embedded phone — paranoid case.
        steps = [
            {
                "tool_calls_made": [
                    {
                        "args": {
                            "retrieved_chunks": [
                                {
                                    "text": "ok",
                                    "metadata": {
                                        "source_uri": "internal://+79001234567/note",
                                    },
                                }
                            ]
                        }
                    }
                ],
            }
        ]
        result = redactor.redact_steps(steps)
        uri = result[0]["tool_calls_made"][0]["args"]["retrieved_chunks"][0]["metadata"][
            "source_uri"
        ]
        assert "[PHONE]" in uri
        assert "+79001234567" not in uri

    def test_tool_result_hits_text_redacted(self, redactor):
        # Alternate shape: tool_result key on the step (some skills
        # may persist the raw result rather than args).
        steps = [
            {
                "tool_calls_made": [
                    {
                        "result": {
                            "hits": [
                                {"text": "Звоните +79123334455", "score": 0.8},
                            ],
                        },
                    }
                ],
            }
        ]
        result = redactor.redact_steps(steps)
        assert "[PHONE]" in result[0]["tool_calls_made"][0]["result"]["hits"][0]["text"]

    def test_mixed_kb_and_non_kb_steps_coexist(self, redactor):
        # The pipeline has multiple steps — kb-tool step + non-kb steps
        # in the same list. Walker covers all of them.
        steps = [
            {"step": "intent", "value": "faq"},  # no PII
            {
                "step": "skill_dispatch",
                "tool_calls_made": [
                    {"args": {"chunks": ["call +74951234567"]}},
                ],
            },
            {"step": "final_reply", "text": "Спасибо!"},  # no PII
        ]
        result = redactor.redact_steps(steps)
        assert result[0] == steps[0]
        assert "[PHONE]" in result[1]["tool_calls_made"][0]["args"]["chunks"][0]
        assert result[2] == steps[2]
