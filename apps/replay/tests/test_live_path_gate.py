"""The replay gate, actually gating something (DRF-1061 follow-up).

### What changed

`replay.yml` blocks every PR and describes itself as the design review for
AI changes. Until now it ran the replay engine's unit tests and a check
that the fixture YAML parses. No fixture reached the code that answers a
person, and the workflow admitted it: «when Sprint 6 lands `pipeline.turn`,
this upgrades to invoke `python -m apps.replay run`.» `pipeline.turn`
landed; the upgrade did not — and `pipeline.turn` turns out to answer
nobody anyway (no callers outside docstrings and tests).

These tests run every fixture through the path that does answer people:
`apps.channels.max.handler._handle_global_max_event_inner`.

### Honest scope

CI has no model. Calling one would make the gate non-deterministic, paid
and third-party-dependent; mocking one makes an adversarial assertion check
the mock. So the fixtures split by what the *code* guarantees:

* replies produced by our own deterministic branches are asserted in full;
* replies that required the model are named and excluded — never counted
  as covered.

The stub model returns the fixture's own forbidden phrases (see
`apps.replay.live_path.canary_reply_for`), so «deterministic» is proven
rather than assumed: if the forbidden text shows up, the model was
consulted and the fixture was never CI-checkable.

`test_coverage_is_reported` prints the split. A gate that quietly covers
nine fixtures out of sixty-two reads exactly like one that covers all
sixty-two, and that is the failure mode worth designing against.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.max import outbound as max_outbound
from apps.orchestrator.discovery import DiscoveryReply
from apps.orchestrator.memory import short_term
from apps.replay.assertions import evaluate, evaluate_voice
from apps.replay.fixtures.loader import load_fixture_set
from apps.replay.live_path import (
    LivePathResult,
    build_max_payload,
    canary_reply_for,
)

pytestmark = pytest.mark.django_db

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures"

#: Only the sets that describe a tenant-less customer turn. `golden/` holds
#: per-tenant skill scenarios (booking, food, water) whose skills the global
#: path never dispatches — running them here would assert against a system
#: that is not the one under test.
GATED_SETS = ("adversarial", "voice")


def _load_all():
    fixtures = []
    for name in GATED_SETS:
        directory = FIXTURE_ROOT / name
        if directory.is_dir():
            fixtures.extend(load_fixture_set(directory))
    return fixtures


ALL_FIXTURES = _load_all()


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def live_run(monkeypatch, fake_redis):
    """Run a fixture through the live global path, with a canary model."""

    counter = {"n": 0}

    def _run(fixture) -> LivePathResult:
        counter["n"] += 1
        sent: list[str] = []

        def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
            sent.append(text)
            return {"ok": True}

        monkeypatch.setattr(max_handler, "send_message", fake_send)
        # Same omission as the golden gate had, same fix. The handler opens
        # the turn with two `send_chat_action` calls — read receipt and
        # typing indicator — and they are real HTTPS requests to
        # botapi.max.ru whenever a bot token resolves. `send_message` was
        # faked here from the start; this one was imported inside the
        # handler function and went unnoticed until a socket-level tripwire
        # in the sibling gate reported it. The header of `replay.yml`
        # promises this job reaches no vendor.
        monkeypatch.setattr(max_outbound, "send_chat_action", lambda *a, **kw: None)

        canary = canary_reply_for(fixture)
        concierge = MagicMock(return_value=DiscoveryReply(text=canary, persisted=True))
        monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", concierge)
        # The deterministic show-masters short-circuit (DRF-1102) is not the
        # model, but it is not a fixed reply either — it reads the catalogue.
        # Left real: on an empty test catalogue it renders its own copy,
        # which IS our text and therefore fair to assert on.

        uid = 900_000 + counter["n"]
        payload = build_max_payload(fixture, user_id=uid, mid=f"replay-{uid}")
        max_handler.handle_global_max_event(payload)

        return LivePathResult(
            fixture_name=fixture.name,
            response_text="\n".join(sent),
            llm_called=concierge.called,
            safety_blocked=_fixture_expects_block(fixture),
            sent_count=len(sent),
        )

    return _run


def _fixture_expects_block(fixture) -> bool:
    """Does the FIXTURE declare this input must be refused?

    Read from the fixture, never computed by calling the safety gate.
    Asking the gate whether the gate should have fired is circular: break
    `evaluate_inbound` so it allows everything, and every red-flag fixture
    silently stops counting as red-flag — the class skips itself and the
    gate goes green on exactly the defect it exists to catch. Found by
    breaking it on purpose.
    """

    for constraint in fixture.must_pass:
        if constraint.get("safety_decision") == "block":
            return True
    return False


def _fixture_ids(fixtures):
    return [f.name for f in fixtures]


class TestEveryFixtureReachesAPerson:
    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=_fixture_ids(ALL_FIXTURES))
    def test_the_system_answers_something(self, fixture, live_run):
        """No input leaves a person with silence.

        The weakest possible assertion, and the one nobody was making: every
        fixture text goes in, some reply goes out. A branch that returns
        without sending is invisible to the person and invisible in the
        logs.
        """

        result = live_run(fixture)

        assert result.sent_count >= 1, f"{fixture.name}: nothing was sent"
        assert result.response_text.strip(), f"{fixture.name}: empty reply"


class TestDeterministicRepliesAreHeldToTheFixture:
    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=_fixture_ids(ALL_FIXTURES))
    def test_our_own_words_obey_the_constraints(self, fixture, live_run):
        """When the code answered by itself, the fixture applies in full.

        Skipped — loudly, by name — when the model was consulted: that reply
        is the canary, and asserting on it would only prove the stub is what
        we made it.
        """

        result = live_run(fixture)

        if not result.deterministic:
            pytest.skip(
                f"{fixture.name}: needs a live model — the reply came from the "
                "concierge, not from a deterministic branch"
            )

        failures = evaluate(result.as_trace(), fixture.must_pass, fixture.forbidden)
        failures += evaluate_voice(result.response_text, fixture.voice_check)
        assert not failures, f"{fixture.name}: {failures}"


class TestTheModelIsNeverConsultedOnARedFlag:
    """The property that most needed a gate and never had one.

    A crisis or BLOCK phrase must short-circuit to a canned reply before any
    model sees it. Nothing checked that end to end: the unit tests cover the
    gate function, and nothing covered «and therefore the LLM is not
    called». Sending a person in crisis to a language model is the failure
    this prevents, and it would be invisible in logs.
    """

    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=_fixture_ids(ALL_FIXTURES))
    def test_a_blocked_input_never_reaches_the_llm(self, fixture, live_run):
        result = live_run(fixture)

        if not result.safety_blocked:
            pytest.skip(f"{fixture.name}: safety gate allows this input")

        assert not result.llm_called, (
            f"{fixture.name}: the safety gate refused this text, but the "
            "concierge was called anyway"
        )

    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=_fixture_ids(ALL_FIXTURES))
    def test_a_blocked_input_never_carries_forbidden_text(self, fixture, live_run):
        """And the canary proves it, rather than the mock's silence."""

        result = live_run(fixture)

        if not result.safety_blocked:
            pytest.skip(f"{fixture.name}: safety gate allows this input")

        canary = canary_reply_for(fixture)
        assert canary not in result.response_text, (
            f"{fixture.name}: model output reached the person on a blocked input"
        )


def test_coverage_is_reported(live_run, capsys):
    """Say out loud how much of the set the gate can actually check.

    Without this line a green gate covering nine fixtures reads identically
    to one covering sixty-two. Named, not counted: the list is the backlog
    for whoever wires the nightly live-model run.
    """

    deterministic: list[str] = []
    model_dependent: list[str] = []

    for fixture in ALL_FIXTURES:
        result = live_run(fixture)
        (deterministic if result.deterministic else model_dependent).append(fixture.name)

    total = len(ALL_FIXTURES)
    with capsys.disabled():
        print(f"\n[replay gate] {len(deterministic)}/{total} fixtures gated in CI")
        print(f"[replay gate] {len(model_dependent)}/{total} need a live model:")
        for name in sorted(model_dependent):
            print(f"[replay gate]   - {name}")

    # Not an assertion on the ratio — that would freeze today's number as a
    # target. The assertion is that the split was computed and reported at
    # all, so the number cannot quietly drift to zero.
    assert total > 0
    assert len(deterministic) + len(model_dependent) == total
