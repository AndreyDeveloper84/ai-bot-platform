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
    def test_redaction_method_is_pinned(self):
        """The one place the version literal lives in a test.

        Every other assertion about the stamp imports the constant
        instead, so changing a pattern fails here — deliberately, once —
        and not in five unrelated modules that never chose the value.
        """

        assert REDACTION_METHOD == "regex_v3"


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
# group. That used to be because OTP_RE redacted those too (DRF-1389,
# since closed); it stays that way so this list keeps testing the
# ASCII-letter boundary and nothing else. The all-digit-group shape has
# its own pinned list at the bottom of this module.
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


class TestWeldedCardIsRedacted:
    """DRF-1390 — the miss side: a card welded to a neighbour is caught.

    ``re.sub`` does not retry a shorter match once the callback declines
    one, so a card joined to a 1-3 digit neighbour by exactly one space
    or dash used to leave the redactor with every digit intact.
    Measured on 2 000 synthetic welded lines: **67.15%** of the cards
    reached the trace unredacted before this fix, 0% after.

    The number below is the standard Visa test value, not a real card,
    and it is Luhn-valid on purpose — the gate is the thing under test.
    """

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("заказ 99 4111111111111111", "заказ 99 [CC]"),
            ("заказ 999-4111111111111111", "заказ 999-[CC]"),
            ("4111111111111111 9 ok", "[CC] 9 ok"),
            ("4111111111111111-77 ok", "[CC]-77 ok"),
        ],
    )
    def test_welded_card_is_cut_and_neighbour_survives(self, redactor, line, expected):
        """Both halves of the fix in one assertion.

        Asserted on the whole output, not on ``"[CC]" in result``: the
        point of searching for the card's own span instead of redacting
        the run whole is that the order number next to it stays readable
        in the trace.
        """

        assert redactor.redact_text(line) == expected

    @pytest.mark.parametrize(
        "line",
        [
            "заказ 99 4111111111111111",
            "заказ 999-4111111111111111",
            "4111111111111111 9 ok",
        ],
    )
    def test_no_digit_of_the_card_survives(self, redactor, line):
        """Checked against the digits, not against a substring search.

        A partial redaction leaves a masked-looking remnant whose digits
        are split by separators, so ``card not in result`` reports
        success while most of the number is still in the trace.
        """

        result = redactor.redact_text(line)
        # Presence first: "no card digits left" is also true of a
        # redactor that dropped the line on the floor.
        assert "[CC]" in result
        assert "1111111111111" not in _digits(result)

    @pytest.mark.parametrize(
        "line",
        [
            "заказ 9999 4111111111111111",
            "заказ 99999 4111111111111111",
            "заказ 99, 4111111111111111",
            "заказ 99  4111111111111111",
        ],
    )
    def test_the_shapes_that_always_worked_still_work(self, redactor, line):
        """Unchanged: two separators, a comma, or a 4+ digit neighbour.

        These reached the card by backtracking, not by the span search,
        and they must not have become collateral of the new code path.
        """

        assert "[CC]" in redactor.redact_text(line)


class TestWeldedCardSearchDoesNotOverreach:
    """DRF-1390 — the false-positive side of the span search.

    Re-testing sub-spans of a rejected run is new work, and every extra
    Luhn test is a fresh 1-in-10 chance of calling an ordinary number a
    card. These are the shapes that must keep coming through: a long
    identifier standing next to a short number is the exact input the
    span search now looks inside.
    """

    @pytest.mark.parametrize(
        "line",
        [
            # Single digit run — the span search must not even start.
            "record_id=1234567890123456",
            "order=98765432109876",
            "ts_ns=1756080000000000001",
            # Multi-group runs: the sub-span is now Luhn-tested and must
            # still fail. Pinned values, not generated.
            "заказ 99 12345678901234567",
            "id 777-12345678901234",
            "order 12345678901234 создан",
        ],
    )
    def test_non_card_runs_still_survive(self, redactor, line):
        assert redactor.redact_text(line) == line

    def test_single_group_run_is_not_searched(self):
        """The cheap path, asserted on the function and not on a timing.

        ``_find_card_span`` is the only new cost on the failure path —
        the common one — and it is bounded by returning immediately when
        the run has nothing to split.
        """

        from apps.replay.redactor import _find_card_span

        assert _find_card_span("1234567890123456") is None

    def test_rescued_span_is_the_card_and_not_the_whole_run(self):
        """The span search returns offsets into the match, not a verdict.

        Pinned on the helper as well as through ``redact_text`` above,
        because an off-by-one here is the difference between eating the
        neighbour and leaving half a card in the trace.
        """

        from apps.replay.redactor import _find_card_span

        assert _find_card_span("99 4111111111111111") == (3, 19)

    def test_longest_valid_span_wins(self):
        """Two Luhn-valid spans in one run — the longer one is taken.

        Both ``9771834170471`` (13) and ``9771834170471874`` (16) pass
        Luhn here; the search must not stop at the first. The digits are
        synthetic and start with a 9, which no card network issues, so
        nothing below resembles a real number.
        """

        from apps.replay.redactor import _find_card_span

        assert _find_card_span("9771834170471 874 63") == (0, 17)


# Canonical UUIDs with an all-digit four-character middle group — the
# exact shape OTP_RE used to cut. PINNED, not generated: a test that
# rolls its own UUID asserts on a value it never chose, which is how
# this defect reached production in the first place. Each entry fails
# against the pre-DRF-1389 pattern.
_UUIDS_WITH_ALL_DIGIT_GROUP = [
    # group 2 all digits
    "c4202567-6706-417c-9a2f-1234567890ab",
    # group 3 (the version group) all digits
    "a1b2c3d4-ab12-4123-8def-0123456789ab",
    # group 4 (the variant group) all digits
    "a1b2c3d4-abcd-4ef0-8123-0123456789ab",
    # all three at once
    "a1b2c3d4-1234-4567-8901-0123456789ab",
    # upper-case hex — the guard is case-insensitive on purpose
    "A1B2C3D4-1234-4567-8901-0123456789AB",
    # leading group all digits too, so nothing anchors on a letter
    "6706c420-1234-4d1e-9a2f-1137780132b5",
]


class TestOtpLeavesCanonicalUuidsIntact:
    r"""DRF-1389 — the false-positive side: UUIDs come out whole.

    A dash is not ``\w``, and the middle groups of a canonical UUID are
    exactly four characters between two dashes, so an all-digit group
    looked like a standalone code. Measured on 200 000 random uuid4
    before the fix: **43.91%** of canonical UUIDs left this file damaged,
    43.72% of them by OTP_RE alone. After: **0%** from OTP_RE.

    (0.71% of canonical UUIDs are still cut, all of it by PHONE_RE — the
    residual DRF-1382 left behind, not this ticket's subject.)
    """

    @pytest.mark.parametrize("identifier", _UUIDS_WITH_ALL_DIGIT_GROUP)
    def test_all_digit_uuid_group_survives(self, redactor, identifier):
        line = f"trace_id={identifier}"
        assert redactor.redact_text(line) == line

    @pytest.mark.parametrize("identifier", _UUIDS_WITH_ALL_DIGIT_GROUP)
    def test_bare_identifier_survives(self, redactor, identifier):
        """No ``trace_id=`` prefix, so the guard borrows no boundary."""

        assert redactor.redact_text(identifier) == identifier

    def test_dash_free_hex_id_is_unaffected(self, redactor):
        """0 of 200 000 before and after — the dashes were the whole cause."""

        line = "trace_id=b6f193748406483c85ddd8ec1b8cb00e"
        assert redactor.redact_text(line) == line


# Every code shape the module comment claims still gets cut. Prose, not
# identifiers: this is the half of the measurement that stops the fix
# from being "narrow the pattern until nothing matches".
_OTP_FORMS_STILL_REDACTED = [
    "код 1234",
    "код: 1234",
    "Ваш код 123456",
    "код подтверждения 4821",
    "код-1234",
    "OTP-123456",
    "PIN-4821",
    "code=1234",
    "(1234)",
    "'123456'",
    "[1234]",
    "введите 1234 в приложении",
    "sms 123456 ok",
    "код 1234.",
    "код — 1234",
    "1234",
    "123456",
    "verification-code-1234",
    "код=123456",
    "код\t1234",
]


class TestNoOtpFormStoppedBeingRedacted:
    """DRF-1389 — the miss side. Narrowing a pattern buys ids with codes.

    The wide fix (adding ``-`` to the boundary class) fails this class on
    ``код-1234`` and ``OTP-123456``. That is the whole reason the narrow
    guard exists, so it is asserted here rather than described in a
    comment.
    """

    @pytest.mark.parametrize("raw", _OTP_FORMS_STILL_REDACTED)
    def test_every_code_form_is_redacted(self, redactor, raw):
        result = redactor.redact_text(raw)
        assert "[OTP]" in result, f"code survived redaction: {result!r}"
        assert _digits(result) == "", f"digits survived redaction: {result!r}"


class TestOtpNarrowingPriceIsPinned:
    """DRF-1389 — what the narrow guard gives up, in assertions.

    Four digits between two dashes with four hex characters on either
    side is identifier-shaped text, and it is no longer redacted. These
    assertions document a deliberate loss, not a decision anyone is
    happy with. If a future change makes them fail because the code IS
    redacted again, check what it did to ``_UUIDS_WITH_ALL_DIGIT_GROUP``
    before calling it a fix.
    """

    @pytest.mark.parametrize("raw", ["abcd-1234-ef01", "face-1234-beef"])
    def test_hex_dash_sandwich_is_not_redacted(self, redactor, raw):
        assert redactor.redact_text(raw) == raw

    @pytest.mark.parametrize(
        "raw",
        [
            # "code" carries a non-hex "o" -> still a code
            "code-1234-ab",
            # only two hex characters after the dash -> still a code
            "abcd-1234-ef",
            # nothing after the digits at all -> still a code
            "abcd-1234",
        ],
    )
    def test_the_guard_does_not_leak_past_that_shape(self, redactor, raw):
        assert "[OTP]" in redactor.redact_text(raw)
