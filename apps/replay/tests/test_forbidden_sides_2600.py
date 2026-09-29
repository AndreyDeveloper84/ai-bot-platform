"""DRF-2600: a prohibition has a side of the conversation.

``forbidden`` — words neither side says: its contract (echo baseline in
``test_fixtures.py``) is that they never overlap the person's input.
``forbidden_in_reply`` — words the PERSON may say and the reply must not
(«слот», owner's decision 28.09 п.10). Its contract is the inverse: every
phrase occurs in the input, otherwise it belongs in ``forbidden`` and keeps
the stronger check.

Pairs that must differ:

* the same fixture, a reply without the word — green; with the word — red;
* the echo of the person's own words trips ``forbidden_in_reply`` (that is
  why the word could not live in ``forbidden``) and never trips ``forbidden``.
"""

from __future__ import annotations

import ast
import pathlib
from collections import Counter

import pytest

from apps.replay.assertions import evaluate
from apps.replay.fixtures.loader import load_fixture_set

ROOT = pathlib.Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "apps" / "replay" / "fixtures"
ALL_FIXTURES = load_fixture_set(FIXTURE_ROOT)

#: Only text rules make sense on the person's side; a skill or a safety
#: decision is never something the person «said».
TEXT_KEYS = frozenset({"response_contains_any", "response_contains_all"})


def _trace(text: str) -> dict:
    return {
        "intent": "",
        "skill_used": "",
        "safety_decision": "allow",
        "tool_calls": [],
        "response_text": text,
    }


def _slot_fixture():
    return next(f for f in ALL_FIXTURES if f.name == "voice_no_internal_slot_word")


class TestTheReplySideIsJudged:
    def test_a_reply_without_the_word_is_green(self):
        f = _slot_fixture()
        assert "слоты" in f.input["text"]  # presence: the person does say it
        assert (
            evaluate(
                _trace("Завтра после обеда свободно в 15:00 и в 17:30."),
                f.must_pass,
                f.reply_forbidden,
            )
            == []
        )

    def test_the_same_reply_with_the_word_is_red(self):
        f = _slot_fixture()
        failures = evaluate(
            _trace("Завтра после обеда есть два Слота: 15:00 и 17:30."),
            f.must_pass,
            f.reply_forbidden,
        )
        assert failures and "слот" in " ".join(failures), failures


class TestEachSideKeepsItsContract:
    def test_the_echo_trips_the_reply_side_rule(self):
        """Why two fields and not one. The echo baseline in ``test_fixtures``
        is exactly ``evaluate(echo, [], forbidden)``; the same rule under
        ``forbidden`` would turn it red — the echo of the person's own words
        trips it here."""
        f = _slot_fixture()
        tripped = evaluate(_trace(f.input["text"]), [], f.forbidden_in_reply)
        assert tripped and "слот" in " ".join(tripped), tripped

    @pytest.mark.parametrize(
        "fixture", [f for f in ALL_FIXTURES if f.forbidden_in_reply], ids=lambda f: f.name
    )
    def test_every_reply_side_phrase_is_in_the_input(self, fixture):
        """Inverse of the echo baseline. A phrase the person does not say
        belongs in ``forbidden``, where the overlap check still guards it."""
        said = fixture.input["text"].casefold()
        for constraint in fixture.forbidden_in_reply:
            assert set(constraint) <= TEXT_KEYS, (fixture.name, sorted(constraint))
            for key, expected in constraint.items():
                needles = expected if isinstance(expected, list) else [expected]
                missing = [n for n in needles if str(n).casefold() not in said]
                assert not missing, (
                    f"{fixture.name}: {key} {missing} not in the input — "
                    "move it to `forbidden`, which checks it against the echo"
                )

    def test_the_census_sees_reply_side_fixtures(self):
        """Presence: the parametrisation above is not an empty run."""
        assert [f.name for f in ALL_FIXTURES if f.forbidden_in_reply] == [
            "voice_no_internal_slot_word"
        ]


#: Every place allowed to read ``.forbidden`` on its own, with the count of
#: reads — exact, so a new read in an excused function is red too. Anything
#: else that reads the attribute judges a reply with half the prohibition,
#: and ``forbidden_in_reply`` silently never runs there.
FORBIDDEN_ALONE = {
    # the field's own plumbing
    ("apps/replay/fixtures/schema.py", "Fixture.reply_forbidden"): 1,
    ("apps/replay/fixtures/schema.py", "to_dict"): 1,
    # the input side by design: the echo contracts and the shape of the sets
    ("apps/replay/tests/test_fixtures.py", "TestFixtureShape.test_minimal"): 1,
    (
        "apps/replay/tests/test_fixtures.py",
        "TestAdversarialFixtureSet.test_all_have_forbidden_rules",
    ): 1,
    (
        "apps/replay/tests/test_fixtures.py",
        "TestAdversarialFixtureSet.test_all_pass_against_echo_baseline",
    ): 1,
    (
        "apps/replay/tests/test_fixtures.py",
        "TestVoiceFixtureSet.test_all_pass_against_echo_baseline",
    ): 1,
}


def _forbidden_reads(tree: ast.AST, rel: str) -> Counter[tuple[str, str]]:
    """Every ``<anything>.forbidden`` read, by (file, qualified function).

    By attribute, not by call shape: a keyword argument, an alias of
    ``evaluate``, ``list(f.forbidden)``, a loop like the canary's — all are
    an ``Attribute(attr="forbidden")``. Not seen: ``getattr(f, "forbidden")``
    and ``to_dict(f)["forbidden"]`` (strings, not attributes).
    """
    found: Counter[tuple[str, str]] = Counter()

    def visit(node: ast.AST, scope: list[str]) -> None:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            scope = [*scope, node.name]
        if isinstance(node, ast.Attribute) and node.attr == "forbidden":
            found[(rel, ".".join(scope) or "<module>")] += 1
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, [])
    return found


def _census() -> tuple[Counter[tuple[str, str]], set[str]]:
    reads: Counter[tuple[str, str]] = Counter()
    scanned: set[str] = set()
    for path in (ROOT / "apps").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        scanned.add(rel)
        reads += _forbidden_reads(ast.parse(path.read_text(encoding="utf-8-sig")), rel)
    return reads, scanned


def test_every_reply_judge_reads_both_sides():
    """Counted by construction (AST) over all of ``apps/``."""
    reads, scanned = _census()
    # Presence: the scan saw the reply judges it claims to cover.
    assert {
        "apps/replay/runner.py",
        "apps/replay/live_path.py",
        "apps/replay/tests/test_live_path_gate.py",
        "apps/replay/tests/test_golden_gate.py",
        "apps/channels/tests/test_global_anketa_history.py",
        "apps/channels/tests/test_global_callback_history.py",
    } <= scanned
    assert dict(reads) == FORBIDDEN_ALONE


def test_the_census_sees_every_shape_of_a_read():
    """Self-check with the census's own function on the shapes it claims."""
    tree = ast.parse(
        "from apps.replay.assertions import evaluate as ev\n"
        "def judge(t, f):\n"
        "    ev(t, f.must_pass, forbidden=f.forbidden)\n"
        "    list(f.forbidden)\n"
        "    for c in f.forbidden: pass\n"
        "    f.reply_forbidden\n"
    )
    assert _forbidden_reads(tree, "x.py") == Counter({("x.py", "judge"): 3})
