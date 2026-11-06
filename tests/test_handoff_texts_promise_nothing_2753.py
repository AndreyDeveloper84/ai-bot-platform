"""A handoff reply states a fact — no term, no place (DRF-2753).

### What was wrong

«Передаю менеджеру — ответят в течение 30 минут.» went to every client whose
conversation was handed to a person. Nothing held those thirty minutes: one
notification to the operators at creation and one nudge after
``HANDOFF_PICKUP_SLA_MINUTES``; the pilot's own measurement
(``apps/handoff/escalation.py``) is «from 0 seconds to 20 hours».

The obvious replacement — «he will answer here, in this chat» — is a second
promise with no carrier. No code path delivers an operator's answer to the
person in the bot's chat, and a global request from a person with exactly one
salon is filed against that salon's conversation, another chat (DRF-2545; the
04.09 incident, DRF-1486, was precisely an answer «not in that chat»).

Owner decision 02.10.2026: the reply says what happened and promises neither.

### What this file holds

* the literal wording of the four replies that changed;
* a closed list of every client-facing «handed to a person» text, each free
  of a HARD term (a number with a unit of time, «в течение», «через N») and
  — a separate probe — free of a claim about WHERE the answer will come;
* a scan of the product's string constants: a new text that speaks of handing
  the person over must join the list, or this file goes red. Without it the
  list would go stale silently.

### The rule, stated: a soft «скоро» is allowed

Owner's word, 02.10.2026: what is removed is the HARD term and the unproven
PLACE. «Скоро» / «в ближайшее время» stay permitted — they name no number,
and one of them is the owner's own wording: ``REPLY_RESCHEDULE`` («Передал
администратору, скоро напишут.») is held verbatim by
``apps/bookings/tests/test_reschedule_handoff_2338.py`` (DRF-2338) and is not
touched here. A node below keeps the probes honest about that line: the hard
probe must NOT fire on «скоро», or the owner's wording would go red.

### What it does not hold

* The silence notices (``apps/handoff/silence.py``) are checked for a term
  only. They DO speak of place — «я напишу тебе сюда», «в другом нашем чате»
  — and rightly: that is the bot describing its own next message, which it
  does send.
* Model output. The concierge can still say «ответят через полчаса» in its
  own words; this file reads constants. ``apps/promptreg/voice_examples.py``
  carries an example with «в течение часа» — it has no reader in the product
  and is left alone, named here.
* Whether an operator answers at all, or where.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APPS = REPO / "apps"

#: Every client-facing text that tells a person their question went to a human.
HANDOFF_REPLIES: tuple[tuple[str, str], ...] = (
    ("apps.skills.human_handoff.skill", "_HANDOFF_REPLY"),
    ("apps.orchestrator.pipeline", "_FALLBACK_BLOCK"),
    ("apps.orchestrator.pipeline", "_FALLBACK_HANDOFF"),
    ("apps.channels.max.handler", "_HANDOFF_FALLBACK_TEXT"),
    ("apps.orchestrator.handoff", "SUPPORT_REQUEST_REPLY"),
    ("apps.channels.max.support_entry", "SUPPORT_ENTRY_TEXT"),
    ("apps.bookings.callbacks", "REPLY_RESCHEDULE"),
    ("apps.bookings.callbacks", "REPLY_BOOK_PARTIAL_FAILURE"),
    ("apps.bookings.callbacks", "REPLY_BOOK_CANCEL_FAILED"),
    ("apps.skills.booking.skill", "_FALLBACK_HANDOFF_TEXT"),
    ("apps.skills.faq.skill", "_FALLBACK_HANDOFF_TEXT"),
    ("apps.integrations.ayla.health_check", "HANDOFF_TEXT"),
    # Found by the scan below, not by reading: the first census missed both.
    ("apps.skills.cross_domain.skill", "CONVERT_ACK"),
    ("apps.skills.menu.replies", "HELP_TEXT"),
)

#: The bot's own «I am silent / I am back» lines. Term probe only — see above.
SILENCE_NOTICES: tuple[tuple[str, str], ...] = (
    ("apps.handoff.silence", "SILENCE_ANNOUNCED_HERE_TEXT"),
    ("apps.handoff.silence", "SILENCE_TRANSFERRED_TEXT"),
    ("apps.handoff.silence", "SILENCE_RELEASED_TEXT"),
)

_UNIT = r"(?:сек|мин|час|ч\b|дн|день|дня|суток|сутки|недел)"
TERM = re.compile(
    rf"\d+\s*{_UNIT}|в\s+течение|через\s+\S+\s*{_UNIT}|получас",
    re.IGNORECASE,
)
PLACE = re.compile(r"\bздесь\b|\bсюда\b|в\s+этом\s+чате|в\s+этот\s+чат|в\s+чате", re.IGNORECASE)

#: A string that tells the person a human now has their question.
SPEAKS_OF_HANDING_OVER = re.compile(
    r"(?:переда(?:ю|м|л|ла|ли|дим|ём)|переключаю)\b[^.!?]*"
    r"(?:менеджер|администратор|оператор|специалист|сотрудник|поддержк)",
    re.IGNORECASE,
)


def _text(module: str, name: str) -> str:
    value = getattr(importlib.import_module(module), name)
    assert isinstance(value, str) and value.strip(), (module, name)
    return value


class TestTheWordsThatChanged:
    """Literals, so an edit to any of them shows up here."""

    def test_the_handoff_reply(self) -> None:
        assert _text("apps.skills.human_handoff.skill", "_HANDOFF_REPLY") == (
            "Передаю твой вопрос менеджеру."
        )

    def test_the_pipeline_keeps_the_same_sentence_as_the_skill(self) -> None:
        assert _text("apps.orchestrator.pipeline", "_FALLBACK_HANDOFF") == _text(
            "apps.skills.human_handoff.skill", "_HANDOFF_REPLY"
        )

    def test_the_block_fallback(self) -> None:
        assert _text("apps.orchestrator.pipeline", "_FALLBACK_BLOCK") == (
            "Извини, я не могу ответить на этот запрос. Передаю его менеджеру."
        )

    def test_the_channel_fallback(self) -> None:
        assert _text("apps.channels.max.handler", "_HANDOFF_FALLBACK_TEXT") == (
            "Передаю твой вопрос менеджеру."
        )


class TestTheProbesCanSee:
    """A probe that cannot fail proves nothing: each one reddens on what it is for."""

    @pytest.mark.parametrize(
        "text",
        [
            "Передаю менеджеру — ответят в течение 30 минут.",
            "Передам менеджеру — ответит в течение часа.",
            "Менеджер ответит через 2 часа.",
            "Менеджер ответит через полчаса.",
            "Ответим за 15 мин.",
            "Свяжемся в течение дня.",
        ],
    )
    def test_the_term_probe_sees_a_term(self, text: str) -> None:
        assert TERM.search(text) is not None

    @pytest.mark.parametrize(
        "text",
        [
            "Он ответит здесь, в этом чате.",
            "Менеджер напишет сюда.",
            "Ответ придёт в этот чат.",
        ],
    )
    def test_the_place_probe_sees_a_place(self, text: str) -> None:
        assert PLACE.search(text) is not None

    @pytest.mark.parametrize(
        "text",
        [
            "Передал администратору, скоро напишут.",
            "Не получилось отменить запись — передал администратору, скоро уточнит.",
            "Передаю твой вопрос менеджеру — он ответит в ближайшее время.",
        ],
    )
    def test_a_soft_hint_is_not_a_hard_term(self, text: str) -> None:
        """The rule above, as a node: «скоро» is allowed and must not trip the probe."""
        assert TERM.search(text) is None
        assert PLACE.search(text) is None

    def test_the_scan_pattern_sees_a_handoff_sentence(self) -> None:
        assert SPEAKS_OF_HANDING_OVER.search("Передаю твой вопрос менеджеру.")
        assert SPEAKS_OF_HANDING_OVER.search("Не уверена — переключаю на менеджера, он уточнит.")
        assert SPEAKS_OF_HANDING_OVER.search("Передадим запрос специалисту")
        # …and not a sentence that merely mentions staff.
        assert SPEAKS_OF_HANDING_OVER.search("Напиши администратору салона.") is None


@pytest.mark.parametrize(("module", "name"), HANDOFF_REPLIES + SILENCE_NOTICES)
def test_no_handoff_text_names_a_term(module: str, name: str) -> None:
    text = _text(module, name)
    found = TERM.search(text)
    assert found is None, f"{module}.{name} обещает срок: «{found.group(0)}» в «{text}»"


def test_the_owners_booking_wording_is_on_the_list_and_untouched() -> None:
    """Listed, so it is probed for a hard term and a place like every other reply."""

    assert ("apps.bookings.callbacks", "REPLY_RESCHEDULE") in HANDOFF_REPLIES
    assert _text("apps.bookings.callbacks", "REPLY_RESCHEDULE") == (
        "Передал администратору, скоро напишут."
    )


@pytest.mark.parametrize(("module", "name"), HANDOFF_REPLIES)
def test_no_handoff_reply_says_where_the_answer_will_come(module: str, name: str) -> None:
    text = _text(module, name)
    found = PLACE.search(text)
    assert found is None, f"{module}.{name} обещает место ответа: «{found.group(0)}» в «{text}»"


def _string_constants() -> dict[str, str]:
    """``module.NAME`` → value for every module-level string constant in the product."""

    out: dict[str, str] = {}
    for path in sorted(APPS.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if "/tests/" in rel or "/migrations/" in rel or path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — the product does not ship unparsable files
            continue
        module = rel[: -len(".py")].replace("/", ".")
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    out[f"{module}.{target.id}"] = value.value
    return out


def test_every_text_that_speaks_of_handing_over_is_on_the_list() -> None:
    """The list is closed by hand; this is what keeps it from going stale in silence."""

    constants = _string_constants()
    listed = {f"{module}.{name}" for module, name in HANDOFF_REPLIES + SILENCE_NOTICES}
    # Positive pair: the scan really reads the product and finds the known ones.
    assert listed <= set(constants), sorted(listed - set(constants))

    speaking = {
        key for key, value in constants.items() if SPEAKS_OF_HANDING_OVER.search(value) is not None
    }
    assert "apps.skills.human_handoff.skill._HANDOFF_REPLY" in speaking
    unlisted = sorted(speaking - listed)
    assert unlisted == [], (
        "новый текст о передаче человеку — внеси его в HANDOFF_REPLIES, "
        "чтобы и он проверялся на срок и место"
    )
