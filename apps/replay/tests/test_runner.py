"""Runner.run tests (DRF-514 / Sprint 5 / D1)."""

from __future__ import annotations

from typing import Any

from apps.replay.fixtures.schema import Fixture
from apps.replay.runner import Runner


def _fix(**overrides: Any) -> Fixture:
    base: dict[str, Any] = {
        "name": "t",
        "description": "",
        "input": {"channel": "max", "text": "hi"},
    }
    base.update(overrides)
    return Fixture(**base)  # type: ignore[arg-type]


class _StubRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list]] = []

    def capture(self, trace_id: str, steps: list) -> None:
        self.calls.append((trace_id, steps))


class TestRunnerHappyPath:
    def test_passing_fixture(self):
        rec = _StubRecorder()
        r = Runner(recorder=rec)

        def fake_pipeline(input_dict):
            return {
                "intent": "faq",
                "skill_used": "faq",
                "safety_decision": "allow",
                "response_text": "price is 2000",
                "tool_calls": [],
            }

        f = _fix(
            must_pass=[
                {"intent": "faq"},
                {"response_contains_any": ["price"]},
            ],
        )
        report = r.run(f, pipeline_fn=fake_pipeline)
        assert report.passed is True
        assert report.failures == []
        assert report.voice_check_failures == []
        # Recorder invoked once.
        assert len(rec.calls) == 1

    def test_failing_must_pass(self):
        rec = _StubRecorder()
        r = Runner(recorder=rec)

        def fake_pipeline(_):
            return {"intent": "small_talk", "response_text": "идём гулять"}

        f = _fix(must_pass=[{"intent": "faq"}])
        report = r.run(f, pipeline_fn=fake_pipeline)
        assert report.passed is False
        assert len(report.failures) == 1
        assert "intent" in report.failures[0]

    def test_voice_check_failure(self):
        rec = _StubRecorder()
        r = Runner(recorder=rec)

        def fake_pipeline(_):
            return {
                "intent": "faq",
                "response_text": "we guarantee 100% success",
            }

        f = _fix(
            voice_check={"forbidden_phrases": [r"\bguarantee\b"]},
        )
        report = r.run(f, pipeline_fn=fake_pipeline)
        assert report.passed is False
        assert len(report.voice_check_failures) == 1


class TestPipelineError:
    def test_pipeline_exception_caught(self):
        rec = _StubRecorder()
        r = Runner(recorder=rec)

        def boom(_):
            raise RuntimeError("pipeline crashed")

        f = _fix()
        report = r.run(f, pipeline_fn=boom)
        assert report.passed is False
        assert any("pipeline_error" in f for f in report.failures)
        assert "RuntimeError" in report.failures[0]

    def test_pipeline_returns_non_dict(self):
        rec = _StubRecorder()
        r = Runner(recorder=rec)

        def bad(_):
            return "not a dict"  # type: ignore[return-value]

        f = _fix()
        report = r.run(f, pipeline_fn=bad)
        assert report.passed is False
        assert any("returned str" in f or "pipeline_error" in f for f in report.failures)


class TestRecorderResilience:
    def test_recorder_exception_swallowed(self):
        class _BoomRecorder:
            def capture(self, *_args, **_kwargs):
                raise RuntimeError("recorder down")

        r = Runner(recorder=_BoomRecorder())

        def ok_pipeline(_):
            return {"intent": "faq", "response_text": "ok"}

        f = _fix(must_pass=[{"intent": "faq"}])
        report = r.run(f, pipeline_fn=ok_pipeline)
        # Recorder fail doesn't break runner.
        assert report.passed is True


class TestUniqueTraceIds:
    def test_each_run_gets_fresh_trace_id(self):
        rec = _StubRecorder()
        r = Runner(recorder=rec)

        def ok(_):
            return {"intent": "x"}

        f = _fix()
        r1 = r.run(f, pipeline_fn=ok)
        r2 = r.run(f, pipeline_fn=ok)
        assert r1.trace_id != r2.trace_id
        assert len(rec.calls) == 2
