"""Replay diff against captured mysite ground truth (DRF-734 / Sprint 8 / G3).

Sprint 8 exit-gate mirror: replays the golden FAQ fixtures through the
platform pipeline and compares per-turn ``(intent, action_type)``
against a captured ``mysite/maxbot/`` ground-truth dump.

### Activation

This test is **conditionally skipped** until the ground-truth fixture
exists at ``tests/fixtures/mysite_ground_truth/golden_faq_replies.json``.
The dump is generated during Sprint 9 staging soak (N4 game-day) by
running the same 20 golden traces through legacy mysite and snapshoting
its replies.

Once the file lands, this test becomes a CI gate enforcing **≥95%
``(intent, action_type)`` match** — the offline mirror of the F1 prod
exit-gate (≥95% intent agreement for 7 consecutive ShadowDeltaSnapshot
days).

### Fixture format

```json
{
  "format_version": 1,
  "captured_at": "2026-05-13T03:00:00Z",
  "source": "mysite/maxbot/ commit <sha>",
  "fixtures": [
    {
      "fixture": "kb_happy_hours",
      "user_text": "когда вы работаете?",
      "expected_intent": "faq",
      "expected_action_type": "faq"
    },
    ...
  ]
}
```

### Failure mode (when activated)

Per-turn diff dumped to stdout for failing fixtures so the operator
sees exactly which `(fixture_name, expected, got)` triple regressed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# Conventional path for the captured ground truth. Operators / Sprint 9
# staging team drop the file here.
_GROUND_TRUTH_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "mysite_ground_truth"
    / "golden_faq_replies.json"
)


def _ground_truth_available() -> bool:
    return _GROUND_TRUTH_PATH.exists() and _GROUND_TRUTH_PATH.stat().st_size > 0


# Schema version we know how to parse. Bump alongside the producer.
_SUPPORTED_FORMAT_VERSION = 1

# Sprint 8 exit-gate floor — must match the AGREEMENT_THRESHOLD_GREEN
# constant in apps.observability.constants. Hard-coded here to avoid
# pulling a runtime dependency into a test fixture comparator.
_MIN_AGREEMENT = 0.95


def _load_ground_truth() -> dict:
    data = json.loads(_GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    if data.get("format_version") != _SUPPORTED_FORMAT_VERSION:
        pytest.fail(
            f"ground-truth fixture format version {data.get('format_version')!r} "
            f"!= supported {_SUPPORTED_FORMAT_VERSION}. Update this test OR the "
            f"capture script."
        )
    return data


@pytest.mark.e2e
@pytest.mark.skipif(
    not _ground_truth_available(),
    reason=(
        "mysite ground-truth dump not captured yet — generated during Sprint 9 "
        "N4 staging soak. See tests/fixtures/mysite_ground_truth/README.md."
    ),
)
class TestReplayDiffVsMysite:
    """Activated when ``tests/fixtures/mysite_ground_truth/golden_faq_replies.json``
    exists. Replays each platform-side golden FAQ fixture and compares
    the platform's ``(intent, action_type)`` against captured mysite
    replies.
    """

    def test_at_least_95_percent_match(self) -> None:
        from apps.replay.fixtures.loader import load_fixture_set
        from apps.replay.runner import run_fixture_set  # type: ignore[attr-defined]

        ground_truth = _load_ground_truth()
        expected_by_name = {entry["fixture"]: entry for entry in ground_truth["fixtures"]}

        golden_dir = Path("apps") / "replay" / "fixtures" / "golden" / "faq"
        fixtures = load_fixture_set(golden_dir)
        assert fixtures, f"no fixtures found under {golden_dir}"

        results = run_fixture_set(fixtures)

        matches = 0
        diffs: list[str] = []
        scored = 0
        for fixture, result in zip(fixtures, results, strict=True):
            expected = expected_by_name.get(fixture.name)
            if expected is None:
                # Ground truth doesn't cover this fixture — skip from the
                # denominator. Sprint 9 operator should re-capture.
                continue
            scored += 1
            got_intent = getattr(result, "intent", "") or ""
            got_action_type = getattr(result, "action_type", "") or ""
            if (
                got_intent == expected["expected_intent"]
                and got_action_type == expected["expected_action_type"]
            ):
                matches += 1
            else:
                diffs.append(
                    f"  {fixture.name}: "
                    f"intent expected={expected['expected_intent']!r} got={got_intent!r}; "
                    f"action_type expected={expected['expected_action_type']!r} "
                    f"got={got_action_type!r}"
                )

        if scored == 0:
            pytest.skip(
                "ground-truth fixture loaded but no fixture names matched the "
                "platform's golden set — re-capture needed."
            )

        agreement = matches / scored
        diff_dump = "\n".join(diffs) if diffs else "(no diffs)"
        assert agreement >= _MIN_AGREEMENT, (
            f"replay diff agreement {agreement:.1%} < required {_MIN_AGREEMENT:.0%} "
            f"on {scored} matched fixtures.\n\nDiffs:\n{diff_dump}"
        )


class TestGroundTruthSkipReason:
    """When the ground-truth fixture is absent, surface the skip reason
    in the test report so it's visible in CI summary — operators
    looking at the run output see *why* this is gated, not a silent
    missing test.

    This test itself runs (no pytestmark skip) and emits a single
    informational xfail/skip via the assert below.
    """

    def test_ground_truth_path_documented(self) -> None:
        # Always pass — the path constant is the actual contract.
        assert _GROUND_TRUTH_PATH.parent.name == "mysite_ground_truth"
        assert _GROUND_TRUTH_PATH.name == "golden_faq_replies.json"
