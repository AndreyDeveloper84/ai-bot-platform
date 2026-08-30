"""The golden set, actually executed (DRF-1373 follow-up).

### What was wrong

`replay.yml` names its job «replay fixtures (golden + adversarial +
voice)». Two of those three were true. `golden/` had eighty fixtures and a
test file that checked they parse, that there are eighty, and that each has
a rule — nothing that ran one. The set was meant to be run by hand, and by
hand nobody ran it.

Consequence measured, not assumed. On first execution against
`handle_max_event`, asserting every rule on every fixture, **39 of 80 did
not pass**. Split honestly — a fixture whose reply could only come from a
language model or the Ayla API is not checkable in CI and must not be
counted either way — **48 are checkable and 19 of those fail**, while the
remaining 32 were never checkable at all.

The four failures that had been found by hand were attributed to one cause:
`response_contains_any` compares case-sensitively and the skills answer with
a capital letter. Executing all eighty shows that explanation covers exactly
one of the four (`food_clarify_cb_typo_ack`, «Поняла» against «поняла») plus
one nobody had found (`cross_dismiss`, «Ок» against «ок»). The other three
named fixtures fail for three different reasons that have nothing to do with
capitalisation, and the full set adds two more — one of which crashes the
assertion engine with a `TypeError` rather than reporting a mismatch.

### The shape of the gate

### The shape of the gate

Three questions, in order of how weak they are:

1. every fixture reaches the code and gets an answer out (`TestEveryFixture…`);
2. when the answer was ours alone, the fixture applies in full
   (`TestDeterministic…`);
3. how much of the set that actually is (`test_coverage_is_reported`).

Question 3 is not decoration. «No mismatches» is a claim an empty set also
satisfies, and a skip is not a pass: the report prints how many fixtures
were loaded, how many ran, how many were asserted and which were not, and
the test fails if those numbers stop adding up.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from apps.channels.max import handler as max_handler
from apps.orchestrator.memory import short_term
from apps.replay.assertions import evaluate, evaluate_voice
from apps.replay.fixtures.loader import load_fixture_set
from apps.replay.golden_path import (
    GoldenPathResult,
    ModelSeamProbe,
    NetworkTripwire,
    SkillNameProbe,
    build_max_payload,
    prior_texts,
)

pytestmark = pytest.mark.django_db

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "golden"

ALL_FIXTURES = load_fixture_set(FIXTURE_ROOT)

#: Feature flags the golden set describes as ON. `food_scanner` and the
#: nutrition diary are gated by settings that default to False; running
#: their fixtures with the feature off would assert against the
#: «функция готовится» placeholder — a system that is not the one those
#: fixtures document. Declared here so the assumption is visible instead of
#: being whatever the settings module happens to default to today.
FEATURE_FLAGS = {
    "NUTRITION_ENABLED": True,
    "FOOD_PHOTO_SCAN_ENABLED": True,
}

#: Every Ayla endpoint is pinned to a name under `.invalid` — the TLD RFC
#: 2606 reserves precisely so that it can never resolve.
#:
#: Two reasons, and the second is not obvious. The first: a client that
#: cannot start because `AYLA_BASE_URL` is empty raises a `ValueError`
#: through the handler and the person gets silence, so CI must not depend on
#: whoever configured the workflow env.
#:
#: The second is what makes the classification work at all. It has to be a
#: NAME, not an IP. `anyio` skips name resolution when the host is already
#: an IP literal and hands the address straight to the event loop — and on
#: Windows that loop opens the connection with an overlapped `ConnectEx`
#: that never enters Python's `socket` module. Pinned to `192.0.2.1` the
#: `water` fixtures came back «our own deterministic code» on a developer
#: box and «needs the Ayla API» in Linux CI. A name forces a lookup, the
#: tripwire answers that lookup itself, and both machines agree.
SERVICE_ENDPOINTS = {
    "AYLA_BASE_URL": "http://ayla-api.invalid",
    "AYLA_INTERNAL_API_TOKEN": "replay-golden-not-a-real-token",
    "NUTRITION_SERVICE_TOKEN": "replay-golden-not-a-real-token",
}


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def golden_tenant(db):
    from apps.tenancy.models import Tenant

    return Tenant.objects.create(slug="replay-golden", name="Replay Golden")


@pytest.fixture
def golden_run(monkeypatch, fake_redis, golden_tenant, settings):
    """Run one fixture through the per-tenant path that answers clients."""

    for flag, value in FEATURE_FLAGS.items():
        setattr(settings, flag, value)
    for name, value in SERVICE_ENDPOINTS.items():
        setattr(settings, name, value)

    counter = {"n": 0}

    def _run(fixture) -> GoldenPathResult:
        from django.utils import timezone

        from apps.identity.services import resolve_or_create_bot_user
        from apps.integrations.ayla.nutrition_client import reset_nutrition_client
        from apps.tenancy.context import tenant_scope

        # Rebuilt per fixture, for two reasons. It is a module-level
        # singleton assembled from settings at first use, so one built
        # earlier in the process would keep whatever base URL was in force
        # then — including an empty one. And it carries an in-process
        # circuit breaker: without the reset the breaker opened during the
        # `food_scanner` fixtures and every later `water` turn returned the
        # Ayla-is-down line without touching the network, which made those
        # fixtures look like our own deterministic code and got them
        # asserted against an error branch. One fixture must never be able
        # to change the verdict on the next.
        reset_nutrition_client()

        counter["n"] += 1
        uid = 700_000 + counter["n"]
        sent: list[str] = []

        def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
            sent.append(text)
            return {"ok": True}

        monkeypatch.setattr(max_handler, "send_message", fake_send)

        probe = SkillNameProbe()
        registry_logger = logging.getLogger("apps.skills.registry")
        registry_logger.addHandler(probe)

        error = ""
        tripwire = NetworkTripwire()
        model_probe = ModelSeamProbe()
        try:
            with tenant_scope(golden_tenant):
                bot_user = resolve_or_create_bot_user(
                    channel="max", channel_user_id=str(uid), chat_id=str(uid)
                )
                # Declared precondition, not a convenience: golden fixtures
                # are mid-conversation turns, and WelcomeSkill intercepts
                # the first message from an ungreeted BotUser.
                bot_user.welcomed_at = timezone.now()
                bot_user.save(update_fields=["welcomed_at"])

                with tripwire, model_probe:
                    for i, setup_text in enumerate(prior_texts(fixture)):
                        max_handler.handle_max_event(
                            build_max_payload(
                                setup_text, user_id=uid, mid=f"replay-{uid}-pre{i}"
                            )
                        )
                    # Setup turns are not the fixture's subject: drop their
                    # replies and their skill match so the assertions below
                    # can only be satisfied by the turn under test.
                    sent.clear()
                    probe.skill_name = ""
                    tripwire.attempts.clear()
                    model_probe.requests.clear()

                    try:
                        max_handler.handle_max_event(
                            build_max_payload(
                                str(fixture.input.get("text", "")),
                                user_id=uid,
                                mid=f"replay-{uid}",
                                has_attachments=bool(fixture.input.get("has_attachments")),
                            )
                        )
                    except Exception as exc:  # noqa: BLE001 — reported, not raised
                        error = f"{type(exc).__name__}: {exc}"
        finally:
            registry_logger.removeHandler(probe)

        return GoldenPathResult(
            fixture_name=fixture.name,
            response_text="\n".join(sent),
            skill_used=probe.skill_name,
            sent_count=len(sent),
            network_attempts=list(tripwire.attempts),
            model_requests=list(model_probe.requests),
            error=error,
        )

    return _run


def _fixture_ids(fixtures):
    return [f.name for f in fixtures]


class TestEveryFixtureReachesTheCode:
    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=_fixture_ids(ALL_FIXTURES))
    def test_the_turn_does_not_raise(self, fixture, golden_run):
        """A client turn must never end in an exception.

        The weakest claim in the file and the one that already caught
        something: `water` and `cross_domain` turns raised a config
        `ValueError` out of the handler, so the person got silence — no
        reply, no error copy, nothing. The CLI never ran them, so nothing
        said so.
        """

        result = golden_run(fixture)

        assert not result.error, f"{fixture.name}: the handler raised — {result.error}"


class TestOurOwnWordsObeyTheFixture:
    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=_fixture_ids(ALL_FIXTURES))
    def test_deterministic_replies_are_held_to_every_rule(self, fixture, golden_run):
        """When the code answered by itself, the fixture applies in full.

        Skipped — loudly, by name — when the turn had to reach outside this
        process. That reply came from an error branch of a refused
        connection, and asserting on it would only prove the tripwire works.
        """

        result = golden_run(fixture)

        if not result.deterministic:
            pytest.skip(
                f"{fixture.name}: needs the outside world — "
                f"{result.outside_world_notes}"
            )

        failures = evaluate(result.as_trace(), fixture.must_pass, fixture.forbidden)
        failures += evaluate_voice(result.response_text, fixture.voice_check)
        assert not failures, f"{fixture.name}: {failures}"


class TestNoFixtureAssertsOnSomethingNobodyComputes:
    """`intent` is not a field this system produces on any surface.

    Ten privacy fixtures asserted `intent: delete` / `intent: export`
    against a path that has never classified an intent — `classify_intent`
    has no callers outside its own tests. Those assertions could only ever
    fail, and because the set never ran, they never did. This test keeps the
    next one from being written.
    """

    def test_no_golden_fixture_asserts_on_intent(self):
        offenders = [
            f.name
            for f in ALL_FIXTURES
            if any("intent" in c for c in f.must_pass)
            or any("intent" in c for c in f.forbidden)
        ]
        assert not offenders, (
            "golden fixtures asserting on `intent`, which the per-tenant path "
            f"never computes: {offenders}. Assert on `skill_used` instead."
        )


def test_coverage_is_reported(golden_run, capsys):
    """Say out loud how much of the set this gate actually checks.

    The positive guard beside «no mismatches». A green run of an empty set,
    a run where every fixture skipped, and a run that genuinely checked
    eighty fixtures are three different things that look identical in a CI
    log unless somebody prints the difference.
    """

    on_disk = len(load_fixture_set(FIXTURE_ROOT))
    asserted: list[str] = []
    needs_outside: list[str] = []
    raised: list[str] = []

    for fixture in ALL_FIXTURES:
        result = golden_run(fixture)
        if result.error:
            raised.append(f"{fixture.name} ({result.error})")
        elif result.deterministic:
            asserted.append(fixture.name)
        else:
            needs_outside.append(f"{fixture.name} -> {', '.join(result.outside_world_notes)}")

    total = len(ALL_FIXTURES)
    with capsys.disabled():
        print(f"\n[golden gate] {on_disk} fixtures on disk, {total} executed")
        print(f"[golden gate] {len(asserted)}/{total} asserted in full")
        print(f"[golden gate] {len(needs_outside)}/{total} need a model or the Ayla API:")
        for line in sorted(needs_outside):
            print(f"[golden gate]   - {line}")
        if raised:
            print(f"[golden gate] {len(raised)}/{total} RAISED:")
            for line in sorted(raised):
                print(f"[golden gate]   ! {line}")

    # Every fixture on disk was executed, and every executed fixture landed
    # in exactly one bucket. Without this an empty `golden/` — or a loader
    # that quietly started returning nothing — reads as a clean run.
    assert on_disk == total == len(ALL_FIXTURES)
    assert total > 0, "the golden set is empty; a green gate here means nothing"
    assert len(asserted) + len(needs_outside) + len(raised) == total
    assert not raised, f"fixtures whose turn raised instead of answering: {raised}"
    # Not a ratio target — that would freeze today's number. The claim is
    # that the covered set is not empty, so «no mismatches» cannot be
    # satisfied by asserting nothing at all.
    assert asserted, "no golden fixture was asserted in full — the gate checks nothing"
