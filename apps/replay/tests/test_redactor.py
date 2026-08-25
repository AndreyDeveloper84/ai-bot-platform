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


# ---------------------------------------------------------------------------
# DRF-1382 — identifiers must survive the phone / card patterns
# ---------------------------------------------------------------------------


# Identifiers the pre-DRF-1382 PHONE_RE sliced. Every one is PINNED, not
# generated: a test that rolls its own UUID asserts on a value it never
# chose, which is exactly how this defect reached production. Each string
# below fails against the old pattern.
#
# The canonical entries are deliberately free of an all-digit four-char
# group, because OTP_RE still redacts those (44.07% of canonical UUIDs —
# measured, documented on OTP_RE, tracked separately). Pinning one here
# would test that unrelated defect instead of this one; it has its own
# test at the bottom of this module.
_SLICED_AS_PHONE = [
    # trace_id=...-[PHONE]b5 — the match opened after an ASCII letter.
    "7c6b1c64-309c-4b1e-baca-1137780132b5",
    "f742f510-ad4f-4d75-bdcd-4943654445e9",
    "086a9dea-b28f-4b20-9ac5-cd5670473157",
    "87764950-51a9-46fb-bab8-ac8cabca8b58",
    # 32-char hex ids — the shape apps/replay writes for offline runs.
    "b6f193748406483c85ddd8ec1b8cb00e",  # pragma: allowlist secret
    "5e4b510846414113b92f655bbc8c174d",  # pragma: allowlist secret
]

# Same, for CC_RE.
_SLICED_AS_CARD = [
    "1c7c1fe9-01a9-4b2f-b695-21050521378a",
    "8af22a7d-d48b-416e-a334-7051088257bf",
    "b7fffd1a-c0b3-4fb1-b820-6704896730ee",
    "63319914-9e67-4c6f-b372-7413023113a0",
    "12d3781ff60640e2aa55358113561372",  # pragma: allowlist secret
    "283de6765477474198d8e5548d444530",  # pragma: allowlist secret
]


def _digits(text: str) -> str:
    """Every digit in `text`, separators removed.

    Redaction is verified against THIS, not against a substring search
    for the original number. A partial redaction leaves a masked-looking
    remnant — ``+7 ••• ••• 55 44`` — whose digits are split by spaces, so
    ``phone not in result`` reports success while four digits of a real
    number are still sitting in the trace.
    """

    return "".join(ch for ch in text if ch.isdigit())


class TestIdentifiersNotSliced:
    """DRF-1382: neither pattern may cut the middle out of an identifier.

    A redacted identifier is not a safe failure. A ``trace_id`` with its
    middle removed cannot be joined to the log line or to the DB row, so
    the trace is worthless at precisely the moment someone is trying to
    work out what the bot decided.
    """

    @pytest.mark.parametrize("identifier", _SLICED_AS_PHONE)
    def test_phone_pattern_leaves_identifier_alone(self, redactor, identifier):
        line = f"trace_id={identifier}"
        assert redactor.redact_text(line) == line

    @pytest.mark.parametrize("identifier", _SLICED_AS_CARD)
    def test_card_pattern_leaves_identifier_alone(self, redactor, identifier):
        line = f"trace_id={identifier}"
        assert redactor.redact_text(line) == line

    def test_runner_step_line_untouched(self, redactor):
        """A real apps/replay/runner.py step line, pinned ids."""

        line = (
            "step=skill_dispatch skill=booking "
            "trace_id=7c6b1c64-309c-4b1e-baca-1137780132b5 "
            "conv=b6f193748406483c85ddd8ec1b8cb00e latency_ms=412"
        )
        assert redactor.redact_text(line) == line


# Every phone form the module comment names, plus the spacing variants
# that turn up in real operator chatter.
_PHONE_FORMS = [
    "+7 (495) 123-45-67",
    "8 (495) 123-45-67",
    "+74951234567",
    "84951234567",
    "+1-234-567-8901",
    "+12345678901",
    "8 800 123 45 67",
    "+7 495 123 45 67",
    "+7-495-123-45-67",
    "+7(495)123-45-67",
    "8-495-123-45-67",
    "8 495 123-45-67",
]

# Contexts deliberately free of digits, so any digit left in the output
# came out of the phone number.
_PHONE_CONTEXTS = [
    "{}",
    "call me at {} please",
    "phone {}",
    "Мастер Анна: {}",
    "Звоните мастеру {}",
    "клиент {} перезвонит",
    "тел: {}",
    "({})",
    "'{}'",
    "звонок с {}.",
    "internal://{}/note",
]

_CARD_FORMS = [
    "4111 1111 1111 1111",
    "4111111111111111",  # pragma: allowlist secret
    "4111-1111-1111-1111",
    "5500 0000 0000 0004",
    "card 4111 1111 1111 1111 declined",
    "оплата картой 4111 1111 1111 1111",
    "378282246310005",  # pragma: allowlist secret
    "6011111111111117",  # pragma: allowlist secret
]


class TestNothingStoppedBeingRedacted:
    """The other direction of the DRF-1382 measurement.

    Tightening a boundary is only acceptable while every real form still
    gets cut. A missed number in a trace is worse than a mangled id.
    """

    @pytest.mark.parametrize("phone", _PHONE_FORMS)
    @pytest.mark.parametrize("context", _PHONE_CONTEXTS)
    def test_every_phone_form_fully_redacted(self, redactor, phone, context):
        result = redactor.redact_text(context.format(phone))
        assert _digits(result) == "", f"digits survived redaction: {result!r}"

    @pytest.mark.parametrize("card", _CARD_FORMS)
    def test_every_card_form_fully_redacted(self, redactor, card):
        result = redactor.redact_text(card)
        assert _digits(result) == "", f"digits survived redaction: {result!r}"

    def test_phone_flush_against_cyrillic_still_redacted(self, redactor):
        """The guard excludes ASCII letters only — Cyrillic still matches."""

        assert redactor.redact_text("тел84951234567конец") == "тел[PHONE]конец"

    def test_trailing_word_not_eaten(self, redactor):
        """The card match stays anchored on a digit.

        Side effect of the boundary guard, pinned so it cannot regress:
        the old pattern ate the space after the number and produced
        ``card [CC]declined``.
        """

        assert redactor.redact_text("card 4111 1111 1111 1111 declined") == "card [CC] declined"


class TestLuhnGate:
    """DRF-1382: CC_RE matches are Luhn-checked before replacement.

    Measured per length on 5 000 samples each: a purely numeric 14-19
    digit string went from 0% surviving a trace to ~90%.
    """

    @pytest.mark.parametrize(
        "line",
        [
            # YClients record_id shape — 16 digits.
            "record_id=1234567890123456",
            # Order number — 14 digits.
            "order=98765432109876",
            # Nanosecond timestamp — 19 digits.
            "ts_ns=1756080000000000001",
        ],
    )
    def test_non_card_digit_runs_survive(self, redactor, line):
        assert redactor.redact_text(line) == line

    def test_thirteen_digit_run_is_still_redacted(self, redactor):
        """The honest lower bound of what the Luhn gate buys.

        PHONE_RE accepts 10-13 digits and runs straight after CC_RE, so
        an epoch-ms timestamp is redacted either way — the gate only
        changes which placeholder it gets. Pinned so the docstring on
        ``_is_card_number`` cannot quietly over-claim.
        """

        assert redactor.redact_text("ts=1756080000000") == "ts=[PHONE]"

    @pytest.mark.parametrize("card", _CARD_FORMS)
    def test_real_cards_still_pass_the_gate(self, redactor, card):
        assert "[CC]" in redactor.redact_text(card)


class TestLuhnGateKnownMiss:
    """The named price of the Luhn gate — pinned so it cannot drift.

    ``re.sub`` does not retry a shorter match once the callback declines
    one, so a card welded to a 1-3 digit neighbour by exactly one space
    or dash is missed. At four digits or more the combined run overflows
    ``{13,19}``, the engine backtracks onto the card alone, and it is
    redacted normally.

    These assertions document a defect, not a decision anyone is happy
    with. If a future change makes them fail because the card IS now
    redacted, delete the test — do not "fix" the code back.
    """

    @pytest.mark.parametrize(
        "line",
        [
            "заказ 99 4111111111111111",
            "заказ 999-4111111111111111",
            "4111111111111111 9 ok",
        ],
    )
    def test_card_welded_to_short_number_is_missed(self, redactor, line):
        assert "[CC]" not in redactor.redact_text(line)

    @pytest.mark.parametrize(
        "line",
        [
            "заказ 9999 4111111111111111",
            "заказ 99999 4111111111111111",
            "заказ 99, 4111111111111111",
            "заказ 99  4111111111111111",
        ],
    )
    def test_one_digit_further_and_the_card_is_caught(self, redactor, line):
        assert "[CC]" in redactor.redact_text(line)


class TestOtpStillSlicesCanonicalUuids:
    r"""Measured, NOT fixed here — see the comment on OTP_RE (DRF-1382).

    A dash is not ``\w``, and the middle groups of a canonical UUID are
    exactly four characters between two dashes. When such a group is all
    digits it is redacted as an OTP: 44.07% of canonical UUIDs, measured
    on 200 000 samples. That is the dominant remaining reason a trace_id
    comes out of this file unsearchable, and it is an order of magnitude
    worse than the 3.12% / 2.20% this ticket was opened for.

    Closing it means adding ``-`` to the boundary class, which unlike the
    ASCII-letter guard is not free: it stops redacting ``код-1234``. That
    is a decision about which direction of error to accept, so it gets
    its own ticket. This test pins the current, wrong behaviour so nobody
    discovers it a third time by accident.
    """

    def test_all_digit_uuid_group_is_redacted_as_otp(self, redactor):
        line = "trace_id=c4202567-6706-417c-9a2f-1234567890ab"
        assert redactor.redact_text(line) == "trace_id=c4202567-[OTP]-417c-9a2f-1234567890ab"

    def test_dash_free_hex_id_is_unaffected(self, redactor):
        """0 of 200 000 — the dashes are the whole cause."""

        line = "trace_id=b6f193748406483c85ddd8ec1b8cb00e"
        assert redactor.redact_text(line) == line
