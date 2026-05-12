"""Fixture runner (DRF-514 / Sprint 5 / D1).

Glues a fixture's input through a pipeline_fn callable, captures the
resulting trace, runs assertions, returns a structured report.

### Why DI on pipeline_fn

Sprint 6 ships ``apps/orchestrator/pipeline.py::turn()`` — the full
production pipeline. Sprint 5 ships a runner that doesn't depend on
that being ready. The DI signature lets:

- Sprint 5 pass a thin wrapper around existing skill dispatch (Sprint 3
  / D1) → live replay works today.
- Sprint 6 swap to ``pipeline.turn`` without touching the runner.
- Tests pass a mock callable that returns a deterministic trace.

### Trace dict contract

`pipeline_fn(input_dict)` must return a dict with:

    {
        "intent": str,
        "skill_used": str,
        "safety_decision": "allow" | "block",
        "tool_calls": [{"name": str, "args": dict}, ...],
        "response_text": str,
        "latency_ms": float,         # optional
        "tokens": int,               # optional
    }

The runner preserves missing optional keys; the assertion engine
treats missing required keys as failures (gives operator a clear
"missing key in trace" failure rather than crash).

### Recorder coupling

Runner invokes ``TraceRecorder.capture`` if available — the trace
ends up in the ReplayTrace table for offline diff. Capture errors
are swallowed by the recorder (Sprint 5 / A2 contract); failures
never block fixture runs.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from apps.replay.assertions import evaluate, evaluate_voice

if TYPE_CHECKING:
    from apps.replay.fixtures.schema import Fixture

logger = logging.getLogger(__name__)

PipelineFn = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class RunReport:
    """Result of running one fixture through the pipeline.

    `passed` = True iff `failures` + `voice_check_failures` are both
    empty. Caller renders the report to console / JSON / CI log.
    """

    fixture_name: str
    passed: bool
    trace_id: str
    failures: list[str] = field(default_factory=list)
    voice_check_failures: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)


class Runner:
    """Stateless fixture executor.

    Construct once per CLI invocation; ``run(fixture, ...)`` is safe
    to call concurrently across fixtures (no shared state on the
    instance).
    """

    def __init__(self, recorder: Any | None = None) -> None:
        """Initialize with optional recorder override.

        Args:
          recorder: instance of ``apps.replay.recorder.TraceRecorder``
            (or compatible). None → late-build at first run using
            settings defaults. Tests pass a stub to verify capture
            invocation without DB writes.
        """

        self._recorder = recorder

    def run(
        self,
        fixture: "Fixture",
        *,
        pipeline_fn: PipelineFn,
    ) -> RunReport:
        """Execute one fixture and return the report.

        Args:
          fixture: parsed YAML Fixture.
          pipeline_fn: callable that maps `fixture.input` dict to a
            trace dict (see module docstring).

        Returns:
          RunReport. Even if pipeline_fn raises, returns a structured
          failure report (assertion engine never crashes on missing
          keys).
        """

        trace_id = str(uuid.uuid4())
        try:
            trace = pipeline_fn(dict(fixture.input))
            if not isinstance(trace, dict):
                trace = {"_error": f"pipeline_fn returned {type(trace).__name__}, expected dict"}
        except Exception as exc:  # noqa: BLE001 — runner is the safety boundary
            logger.exception(
                "replay.runner.pipeline_error fixture=%s",
                fixture.name,
            )
            trace = {"_error": f"pipeline raised {type(exc).__name__}: {exc}"}

        # Persist via recorder (best-effort — recorder swallows its own errors).
        recorder = self._recorder or self._lazy_recorder()
        if recorder is not None:
            try:
                # Convert dict trace to list[step] shape for ReplayTrace.
                # Minimal shape: one step capturing the whole trace dict.
                recorder.capture(trace_id, [trace])
            except Exception:  # noqa: BLE001
                logger.exception("replay.runner.recorder_failed trace_id=%s", trace_id)

        # Assertion phase — pure logic; never crashes.
        failures = evaluate(trace, fixture.must_pass, fixture.forbidden)
        voice_failures = evaluate_voice(str(trace.get("response_text", "")), fixture.voice_check)

        # Pipeline-error marker propagates as a failure.
        if "_error" in trace:
            failures.insert(0, f"pipeline_error: {trace['_error']}")

        passed = not failures and not voice_failures
        return RunReport(
            fixture_name=fixture.name,
            passed=passed,
            trace_id=trace_id,
            failures=failures,
            voice_check_failures=voice_failures,
            trace=trace,
        )

    def _lazy_recorder(self) -> Any | None:
        """Build a default recorder lazily.

        Imported here (not at module load) so test code that doesn't
        touch the recorder doesn't drag in Django models.
        """

        try:
            from apps.replay.recorder import TraceRecorder

            return TraceRecorder()
        except Exception:  # noqa: BLE001
            logger.exception("replay.runner.recorder_init_failed")
            return None
