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
capitalisation, and the full set adds three more:

* six fixtures carry a bare YAML number (`- 250`, `- +79`) which loads as
  `int` and made `evaluate` raise `TypeError` instead of reporting a
  mismatch — and `+79`, a rule against leaking a phone number, had been
  quietly loading as the integer `79` all along;
* ten privacy fixtures assert `intent`, which no surface computes;
* two anketa fixtures assert a mid-flow reply on a turn that never enters
  the flow, and four negative fixtures cap the reply at 200 characters when
  the outcome they are asking for IS the 233-character main menu.

The tail of it reached the sets that were being gated. Folding case makes a
`forbidden` rule catch strictly more, and three adversarial fixtures turned
out to be forbidding a word the client says in their own first sentence —
passing only because the sentence starts with a capital.

Where it now stands: 80 executed, 47 asserted in full, 33 reported as
needing a model or the Ayla API and counted as covered by nobody.

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
from apps.channels.max import outbound as max_outbound
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

#: What this gate hands the system before the turn under test, spelled out
#: because a precondition granted in silence is indistinguishable from a
#: property the code actually has. Printed by `test_coverage_is_reported`
#: on every run, so a reader of the CI log sees the terms of the check
#: without opening this file.
#:
#: Every one of these is a state a real client is in by the time any golden
#: scenario happens. None of them is a branch a golden fixture documents:
#: the greeting has its own skill and its own tests, and the food-scanner
#: consent refusal has its own. If a fixture is ever written for one of
#: those branches, it has to stop being granted here.
GRANTED_PRECONDITIONS = (
    "BotUser.welcomed_at is set (WelcomeSkill intercepts the first message "
    "from an ungreeted user and would answer every fixture with the "
    "welcome copy)",
    "BotUser.food_scanner_consent_at is set (152-ФЗ gate; without it every "
    "food_scanner fixture gets the «открой Mini App» refusal)",
    "NUTRITION_ENABLED / FOOD_PHOTO_SCAN_ENABLED are on (their default is "
    "off, and off means the «функция готовится» placeholder)",
)


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
        # The typing indicator is delivery chrome, not the system under
        # test, and it is a real outbound HTTPS call to botapi.max.ru made
        # twice per turn before anything else happens. `send_message` was
        # already faked for exactly this reason; `send_chat_action` was not,
        # because it is imported inside the handler function and is easy to
        # miss.
        #
        # It stayed invisible until the network tripwire reported it: on a
        # runner where `MAX_BOT_TOKEN` resolves to something non-empty, every
        # single golden turn opened a connection to the MAX API — which
        # `replay.yml` promises in its header cannot happen — and every
        # fixture was consequently classified «needed the outside world» and
        # asserted by nobody. A green gate covering zero fixtures.
        monkeypatch.setattr(max_outbound, "send_chat_action", _no_chat_action)

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
                # Declared preconditions, not conveniences — see
                # GRANTED_PRECONDITIONS, which the coverage report prints so
                # nobody has to read this file to learn what the gate handed
                # the system for free.
                bot_user.welcomed_at = timezone.now()
                bot_user.food_scanner_consent_at = timezone.now()
                bot_user.save(update_fields=["welcomed_at", "food_scanner_consent_at"])

                with tripwire, model_probe:
                    for i, setup_text in enumerate(prior_texts(fixture)):
                        max_handler.handle_max_event(
                            build_max_payload(setup_text, user_id=uid, mid=f"replay-{uid}-pre{i}")
                        )
                    # Setup turns are not the fixture's subject: drop their
                    # replies and their skill match so the assertions below
                    # can only be satisfied by the turn under test.
                    sent.clear()
                    probe.skill_name = ""
                    tripwire.attempts.clear()
                    model_probe.requests.clear()

                    # Declared dialogue state — the same pattern as
                    # prior_texts, one level down. Some turns are only
                    # meaningful with state an earlier turn wrote: the
                    # correction callback is answered from the scanner's
                    # last-card stash (DRF-1454), and without it the honest
                    # answer is the stale-card refusal, not the prompt.
                    # ``input.skill_state`` is a free-form key the fixture
                    # schema already tolerates, like ``prior_texts``.
                    seed = fixture.input.get("skill_state") or {}
                    if seed:
                        from apps.conversations.services import (
                            resolve_active_conversation,
                            write_skill_state,
                        )

                        conversation = resolve_active_conversation(bot_user, create_if_missing=True)
                        assert conversation is not None
                        for subkey, value in seed.items():
                            write_skill_state(conversation, subkey, value)

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


def _no_chat_action(*args, **kwargs) -> None:
    """Swallow the MAX read/typing indicator. See where it is installed."""

    return None


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
            pytest.skip(f"{fixture.name}: needs the outside world — {result.outside_world_notes}")

        # The positive guard beside the negative claim, per fixture. «No
        # failures» is equally true of a fixture with no rules to check and
        # of one that passed every rule it has, and in a CI log the two are
        # the same green dot. `test_fixtures.py` already asserts this over
        # the whole set; asserted again here, on the one fixture this
        # parametrisation is about, so the claim cannot be satisfied by a
        # rule list that quietly emptied.
        assert fixture.must_pass or fixture.forbidden, (
            f"{fixture.name}: no must_pass and no forbidden — this fixture "
            "asserts nothing, and passing it proves nothing"
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
            if any("intent" in c for c in f.must_pass) or any("intent" in c for c in f.forbidden)
        ]
        assert not offenders, (
            "golden fixtures asserting on `intent`, which the per-tenant path "
            f"never computes: {offenders}. Assert on `skill_used` instead."
        )


class TestTheChannelIsNeverReallyCalled:
    """The delivery transport must be faked, and this says so by name.

    `test_coverage_is_reported` already catches this — it did, on the first
    CI run of this file — but it catches it as «no golden fixture was
    asserted in full», which reads like the classifier broke rather than
    like «every turn made two HTTPS requests to botapi.max.ru». A guard is
    worth more when its message names the defect.

    The defect it names: `_handle_max_event_inner` opens every turn with two
    `send_chat_action` calls (read receipt, typing indicator). They are real
    outbound requests whenever a bot token resolves, they are imported
    inside the handler function where a fixture is easy to forget, and
    `replay.yml` promises in its header that this job reaches no vendor.
    """

    #: Substrings of hosts that mean the channel transport was really called.
    CHANNEL_HOSTS = ("max.ru",)

    def test_no_turn_reaches_the_max_api(self, golden_run):
        offenders = []
        for fixture in ALL_FIXTURES:
            result = golden_run(fixture)
            for attempt in result.network_attempts:
                if any(host in attempt for host in self.CHANNEL_HOSTS):
                    offenders.append(f"{fixture.name} -> {attempt}")
        assert not offenders, (
            "the golden gate called the MAX API for real; some outbound "
            f"channel function is not faked: {offenders[:5]}"
        )


class TestTheGateCanStillGoRed:
    """Positive stress on the negative claim, on the live path, on real data.

    `test_deterministic_replies_are_held_to_every_rule` passing means «no
    mismatches». That sentence is also true of a gate that stopped comparing
    anything, and after a change that made a comparison *more* permissive
    that is the reading somebody should demand evidence against.

    So: take a fixture that really runs, keep the real reply from the real
    handler, and put a rule against it that is wrong by a word rather than by
    a capital letter. If the gate reports that, it is still checking. The
    subject is a fixture the change actually touched, so the proof is on the
    same data as the fix and not on a convenient toy.
    """

    SUBJECT = "food_clarify_cb_typo_ack"

    def _subject(self):
        for fixture in ALL_FIXTURES:
            if fixture.name == self.SUBJECT:
                return fixture
        raise AssertionError(
            f"{self.SUBJECT} is gone from the golden set — this proof has no "
            "subject and must be re-pointed, not deleted"
        )

    def test_the_real_reply_satisfies_the_real_rule(self):
        """The precondition. Without it the next test proves nothing."""

        fixture = self._subject()
        assert fixture.must_pass, f"{self.SUBJECT} has no must_pass to weaken"

    def test_a_wrong_word_is_reported_on_the_real_reply(self, golden_run):
        fixture = self._subject()
        result = golden_run(fixture)
        assert result.deterministic, (
            f"{self.SUBJECT} stopped being CI-checkable; this proof needs a "
            "fixture the gate actually asserts"
        )

        # Wrong by a word, not by a capital. «записала» is a real reply this
        # bot gives elsewhere, so this is a plausible regression, not gibberish.
        failures = evaluate(
            result.as_trace(),
            [{"response_contains_any": ["записала в дневник"]}],
            [],
        )
        assert failures, (
            "the gate accepted a reply that does not contain the required "
            "phrase — case folding turned the rule off instead of loosening it"
        )

    def test_a_wrong_skill_is_reported_on_the_real_reply(self, golden_run):
        """Identifiers stayed exact — the other half of «did not weaken»."""

        result = golden_run(self._subject())
        assert result.deterministic

        assert evaluate(result.as_trace(), [{"skill_used": "booking"}], [])
        # …and the same name in another case is still a different skill.
        assert evaluate(result.as_trace(), [{"skill_used": "FOOD_CLARIFY"}], [])

    def test_a_forbidden_phrase_in_the_real_reply_is_reported(self, golden_run):
        """However it is capitalised."""

        result = golden_run(self._subject())
        assert result.deterministic
        text = result.response_text
        assert text.strip(), "no reply to build the proof on"

        first_word = text.split()[0]
        assert evaluate(result.as_trace(), [], [{"response_contains_any": [first_word]}])
        assert evaluate(result.as_trace(), [], [{"response_contains_any": [first_word.upper()]}]), (
            "a forbidden phrase escaped by being written in another case"
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
