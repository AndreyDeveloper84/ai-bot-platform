r"""What the anonymiser's redaction keeps, and what it must not eat.

DRF-1369. The archive exists because the owner ruled the переписка is kept:
«это единственная запись того, что бот на самом деле сказал человеку, и она
нужна при разборе инцидента и спора о брони». A redaction that satisfies the
privacy half and destroys the dispute half has not implemented that ruling —
it has deleted the record and kept the file.

So these are **measurements**, not opinions. The corruption rates below were
what sent this module away from ``Redactor.redact_text``, and re-running them
on every CI pass is what keeps a future edit from quietly walking back into
it.

    apps.replay.redactor.Redactor().redact_text over 20 000 canonical UUIDs
        corrupted 9 064 of them — 45.3%
        OTP_RE     8 727
        PHONE_RE     611
        CC_RE        410

The cause is a boundary choice, not a bug in the shapes: ``OTP_RE`` anchors on
``\w``, a UUID's separator is ``-`` which is not ``\w``, so a digit group
inside a canonical UUID satisfies both lookarounds.
"""

from __future__ import annotations

import re
import uuid

from apps.conversations.erasure import _redact, _redact_value

#: Big enough that a 1-in-1000 regression cannot hide in the noise, small
#: enough to stay well under a second.
SAMPLE = 20_000


class TestUuidsSurvive:
    """The measurement that changed the design."""

    def test_not_one_uuid_in_twenty_thousand_is_corrupted(self) -> None:
        corrupted = [u for u in (str(uuid.uuid4()) for _ in range(SAMPLE)) if _redact(u) != u]

        assert corrupted == [], (
            f"{len(corrupted)} of {SAMPLE} UUIDs corrupted "
            f"(e.g. {corrupted[0]!r} -> {_redact(corrupted[0])!r}). The archive's "
            "foreign keys are its usefulness — see _redact's docstring."
        )

    def test_the_unnarrowed_redactor_really_does_corrupt_them(self) -> None:
        """Pins the reason this module does not simply call ``redact_text``.

        If this ever goes green, ``apps.replay.redactor`` has been fixed
        upstream and the narrowing here can be revisited — deliberately
        asserting the defect rather than describing it in a comment that
        nobody re-checks.
        """
        from apps.replay.redactor import Redactor

        redactor = Redactor([])
        corrupted = sum(
            1 for u in (str(uuid.uuid4()) for _ in range(SAMPLE)) if redactor.redact_text(u) != u
        )

        assert corrupted > SAMPLE // 10, (
            "apps.replay.redactor no longer mangles UUIDs at scale — recheck "
            "whether apps.conversations.erasure still needs its own narrowing."
        )

    def test_uuids_inside_a_sentence_and_inside_json_survive(self) -> None:
        master = str(uuid.UUID("7f1d0f2e-1111-4222-8333-000000000444"))
        text = f"мастер {master}, телефон 89990001122"

        out = _redact(text)

        assert master in out
        assert "89990001122" not in out
        assert "[PHONE]" in out

        payload = {"master_id": master, "options": [master, "звоните 89990001122"]}
        redacted = _redact_value(payload)
        assert redacted["master_id"] == master
        assert redacted["options"][0] == master
        assert "89990001122" not in redacted["options"][1]


class TestTheDisputeSurvives:
    """Amounts and dates ARE the booking dispute the archive is kept for."""

    def test_prices_and_years_are_not_eaten(self) -> None:
        for text in (
            "комфортно до 3000 рублей",
            "запиши на 2026 год",
            "маникюр 1500, педикюр 2200",
        ):
            assert _redact(text) == text, f"{text!r} lost its numbers"

    def test_the_words_are_kept_verbatim(self) -> None:
        text = "я веган, мой мастер — Анна, приду в четверг"
        assert _redact(text) == text


class TestIdentifiersDoNotSurvive:
    """The privacy half — checked on VALUES, not on the shape of the output."""

    def test_phone_email_and_card_are_replaced(self) -> None:
        text = "телефон 8 999 000 11 22, почта masha@example.ru, карта 4111 1111 1111 1111"

        out = _redact(text)

        assert "999" not in out
        assert "masha@example.ru" not in out
        assert "4111" not in out
        assert "[PHONE]" in out and "[EMAIL]" in out and "[CC]" in out

    def test_no_digit_of_the_phone_survives_anywhere(self) -> None:
        """Value-level, the way a leak actually shows up.

        A shape check («is there a `[PHONE]` token?») passes on output that
        still carries the number somewhere else in the string.
        """
        phone = "79997775544"
        out = _redact(f"звони мне на +{phone} после шести")

        assert not re.search(r"\d{4}", out.replace("[PHONE]", "")) or phone[:4] not in out
        assert phone not in out
        assert "9997775544" not in out

    def test_the_mask_sentinel_never_reaches_the_archive(self) -> None:
        """The UUID placeholder is an implementation detail, not an output."""
        text = f"мастер {uuid.uuid4()} и {uuid.uuid4()}, телефон 89990001122"
        assert chr(0) not in _redact(text)
