"""The consumer starts the LLM warm-up (DRF-1445).

This is the mutation guard for the fix. Delete the
``start_background_warmup()`` call from ``consume_forever`` and
``test_consume_forever_starts_the_llm_warmup`` fails — the module-level
tests in ``apps/llm/tests/test_warmup.py`` would all still pass, because
they test a warm-up nobody calls.

The ordering assertion matters as much as the call: warm-up must be
kicked off after readiness is announced and must not delay entry into
the consume loop, or a slow answer is traded for a lost message.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.workers import consumer


@pytest.fixture
def _no_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the loop out of Redis — this file tests boot, not consumption."""

    def _stop(*_a: Any, **_kw: Any) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(consumer, "consume_once", _stop)
    monkeypatch.setattr(consumer, "registered_streams", lambda: ["ingress:max"])


def test_consume_forever_starts_the_llm_warmup(
    monkeypatch: pytest.MonkeyPatch, _no_streams: None
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        "apps.llm.warmup.start_background_warmup",
        lambda: calls.append(1),
    )

    consumer.consume_forever()

    assert calls == [1]


def test_warmup_runs_before_the_consume_loop_but_does_not_gate_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Order is observable: warm-up is kicked off, then we consume.

    ``consume_once`` here is a positive guard — if the loop were never
    reached the sequence would be missing its ``consumed`` entry and the
    assertion would fail rather than pass vacuously.
    """
    sequence: list[str] = []

    def _consume(*_a: Any, **_kw: Any) -> int:
        sequence.append("consumed")
        raise KeyboardInterrupt

    monkeypatch.setattr(consumer, "consume_once", _consume)
    monkeypatch.setattr(consumer, "registered_streams", lambda: ["ingress:max"])
    monkeypatch.setattr(
        "apps.llm.warmup.start_background_warmup",
        lambda: sequence.append("warmup_started"),
    )

    consumer.consume_forever()

    assert sequence == ["warmup_started", "consumed"]


def test_a_broken_warmup_does_not_stop_the_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``start_background_warmup`` swallows its own failures, but the
    consumer must not depend on that promise being kept.

    A drainer that refuses to start re-creates the #1010 symptom —
    stream accepted, never processed — which is strictly worse than the
    cold first answer warm-up exists to remove.
    """
    consumed: list[int] = []

    def _consume(*_a: Any, **_kw: Any) -> int:
        consumed.append(1)
        raise KeyboardInterrupt

    def _boom() -> None:
        raise RuntimeError("warm-up exploded")

    monkeypatch.setattr(consumer, "consume_once", _consume)
    monkeypatch.setattr(consumer, "registered_streams", lambda: ["ingress:max"])
    monkeypatch.setattr("apps.llm.warmup.start_background_warmup", _boom)

    consumer.consume_forever()

    assert consumed == [1]  # the loop still ran
