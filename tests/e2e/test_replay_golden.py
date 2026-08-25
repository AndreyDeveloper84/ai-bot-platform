"""Sprint 5 / G2 — exit-gate e2e for replay infrastructure (DRF-524).

Runs Runner against 3 sample golden fixtures (one per category — privacy,
handoff, faq) with a synthetic pipeline_fn, asserts:

  1. All 3 fixtures pass (Runner returns passed=True).
  2. A ReplayTrace row was captured per fixture (recorder wired correctly).
  3. The captured trace is redacted (no PII leakage).

This is the Sprint 5 *exit gate* — proves the whole stack (loader →
schema → runner → recorder → redactor → assertions) is wired end-to-end
against the production fixture files we shipped in C3.

When Sprint 6 lands ``apps.orchestrator.pipeline.turn``, this test
upgrades to drop the synthetic ``pipeline_fn`` and call the real pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from apps.replay.fixtures.loader import load_fixture
from apps.replay.runner import Runner
from apps.replay.models import ReplayTrace
from apps.replay.redactor import REDACTION_METHOD
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


_GOLDEN_ROOT = Path("apps/replay/fixtures/golden")


@pytest.fixture
def g2_tenant(settings):
    """Tenant for the e2e run + opt-in to recorder writes.

    Test-env sample rate defaults to 0.0 so unit tests don't pollute the
    DB; e2e flips it to 1.0 here to exercise the persistence path.
    """

    t = Tenant.objects.create(slug="g2-replay", name="G2 Replay")
    settings.REPLAY_SAMPLE_RATE_TEST = 1.0
    return t


def _make_pipeline_fn(fixture_to_trace: dict[str, dict[str, Any]]):
    """Build a deterministic pipeline_fn keyed by fixture input text."""

    def pipeline_fn(input_dict: dict[str, Any]) -> dict[str, Any]:
        text = str(input_dict.get("text", ""))
        return fixture_to_trace.get(text, {"response_text": text})

    return pipeline_fn


def test_three_golden_fixtures_pass_end_to_end(g2_tenant):
    """One fixture per category through the runner; all must pass."""

    # Real golden fixtures (one per category)
    privacy_fx = load_fixture(_GOLDEN_ROOT / "privacy" / "delete_ru_short.yaml")
    handoff_fx = load_fixture(_GOLDEN_ROOT / "handoff" / "operator_ru_basic.yaml")
    faq_fx = load_fixture(_GOLDEN_ROOT / "faq" / "hours_simple.yaml")

    # Synthetic pipeline traces matching golden assertions
    pipeline_fn = _make_pipeline_fn(
        {
            privacy_fx.input["text"]: {
                "intent": "delete",
                "skill_used": "privacy_consent",
                "safety_decision": "allow",
                "tool_calls": [],
                "response_text": "Все ваши данные удалены. Удалено диалогов: 0. Спасибо!",
                "latency_ms": 42.0,
                "tokens": 10,
            },
            handoff_fx.input["text"]: {
                "intent": "",
                "skill_used": "human_handoff",
                "safety_decision": "allow",
                "tool_calls": [],
                "response_text": "Передаю менеджеру — ответят в течение 30 минут.",
                "latency_ms": 55.0,
                "tokens": 12,
            },
            faq_fx.input["text"]: {
                "intent": "",
                "skill_used": "",
                "safety_decision": "allow",
                "tool_calls": [],
                "response_text": "Мы работаем с 9 до 21 каждый день. Адрес можем подсказать.",
                "latency_ms": 70.0,
                "tokens": 18,
            },
        }
    )

    runner = Runner()
    with tenant_scope(g2_tenant):
        privacy_report = runner.run(privacy_fx, pipeline_fn=pipeline_fn)
        handoff_report = runner.run(handoff_fx, pipeline_fn=pipeline_fn)
        faq_report = runner.run(faq_fx, pipeline_fn=pipeline_fn)

    # All three pass — operator-readable failure list if they don't.
    for report in (privacy_report, handoff_report, faq_report):
        assert report.passed, (
            f"{report.fixture_name}: failures={report.failures} voice={report.voice_check_failures}"
        )

    # Each run persisted a ReplayTrace row under this tenant.
    captured = ReplayTrace.all_tenants.filter(tenant=g2_tenant)
    captured_ids = {str(t.trace_id) for t in captured}
    assert privacy_report.trace_id in captured_ids
    assert handoff_report.trace_id in captured_ids
    assert faq_report.trace_id in captured_ids
    assert captured.count() == 3


def test_pipeline_crash_produces_structured_failure(g2_tenant):
    """pipeline_fn raising must not crash the runner — fixture fails cleanly."""

    fx = load_fixture(_GOLDEN_ROOT / "privacy" / "delete_ru_short.yaml")

    def boom(_input: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("synthetic pipeline crash")

    runner = Runner()
    with tenant_scope(g2_tenant):
        report = runner.run(fx, pipeline_fn=boom)

    assert report.passed is False
    assert any("pipeline_error" in f for f in report.failures)
    # The crash is captured as a trace row too — operators need the
    # evidence of the failure mode in their forensic log.
    assert ReplayTrace.all_tenants.filter(tenant=g2_tenant, trace_id=report.trace_id).exists()


def test_pii_in_response_redacted_before_persistence(g2_tenant):
    """A trace containing a phone number is redacted before write."""

    fx = load_fixture(_GOLDEN_ROOT / "faq" / "hours_simple.yaml")

    pipeline_fn = _make_pipeline_fn(
        {
            fx.input["text"]: {
                "intent": "",
                "skill_used": "",
                "safety_decision": "allow",
                "tool_calls": [],
                "response_text": "Звоните по номеру +79991234567 — работаем с 9 до 21.",
            },
        }
    )

    runner = Runner()
    with tenant_scope(g2_tenant):
        report = runner.run(fx, pipeline_fn=pipeline_fn)

    row = ReplayTrace.all_tenants.get(tenant=g2_tenant, trace_id=report.trace_id)
    persisted_blob = str(row.pipeline_steps)
    # Raw phone number must NOT be in the persisted blob.
    assert "+79991234567" not in persisted_blob
    # Redactor stamps the method version so re-redaction migrations
    # can be reasoned about (Phase 1).
    assert row.redaction_method == REDACTION_METHOD
