"""LLM availability probe + warm-up tests (DRF-1054 / DRF-1056).

Covers the acceptance criteria from the brief:

* probe against a live provider -> UP, silent;
* probe against a dead provider -> DOWN, exactly one MAX message;
* one isolated failure below the threshold -> still silent;
* state transition down -> up -> recovery message, logged under its own
  slug;
* repeated failures while already DOWN -> NO further messages (the
  anti-spam requirement: an alert that repeats is an alert that gets
  muted);
* recipients unset -> no send attempted at all;
* proxy credentials never reach a message, an audit row, or a log line;
* the probe never raises, never leaks its httpx client, and is bounded
  by its outer ceiling.

The MAX transport is stubbed at ``apps.handoff.notify.send_message`` --
the same seam DRF-1029's own tests use -- so these tests exercise the
real notification primitive rather than a mock of it.
"""

from __future__ import annotations

import asyncio

import pytest

from apps.audit.models import AuditLog
from apps.llm import health
from apps.llm.health import (
    AUDIT_HEALTH_DOWN,
    AUDIT_HEALTH_RECOVERED,
    CACHE_KEY_STATE,
    SKIP_DISABLED,
    SKIP_NO_API_KEY,
    STATE_DOWN,
    STATE_UP,
    TRANSITION_DOWN,
    TRANSITION_NONE,
    TRANSITION_UP,
    ProbeResult,
    build_down_message,
    check_llm_availability,
    evaluate_probe,
    probe_llm,
    redact_secrets,
)

pytestmark = pytest.mark.django_db

NOTIFY_SEND = "apps.handoff.notify.send_message"

# Shape-alike of a real proxy URL with credentials. NOT a real secret --
# the point of these tests is that a string of this shape never escapes.
FAKE_PROXY = "http://probeuser:probepass@proxy.invalid:3128"  # pragma: allowlist secret

# Message fragments asserted on. Kept as named constants so the Cyrillic
# lives in exactly one place per phrase.
TXT_DOWN_HEADER = "\N{LARGE RED CIRCLE} LLM недоступна"
TXT_UP_HEADER = "\N{LARGE GREEN CIRCLE} LLM снова доступна"
TXT_FAILURES_PREFIX = "Неудачных проверок подряд:"
TXT_FALLBACK_HINT = "аварийным текстом"


class SendRecorder:
    """Stand-in for ``channels.max.outbound.send_message``."""

    def __init__(self, exc: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.exc = exc

    def __call__(self, *, chat_id, text, attachments=None, timeout=10.0):
        self.calls.append({"chat_id": chat_id, "text": text})
        if self.exc is not None:
            raise self.exc
        return {}

    @property
    def texts(self) -> list[str]:
        return [c["text"] for c in self.calls]


@pytest.fixture(autouse=True)
def _isolated_cache(settings):
    """LocMem cache so the state machine needs no Redis, and no test
    inherits another's UP/DOWN state."""

    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "llm-health-tests",
        }
    }
    health.reset_state()
    yield
    health.reset_state()


@pytest.fixture(autouse=True)
def _health_settings(settings):
    settings.LLM_HEALTH_PROBE_ENABLED = True
    settings.LLM_HEALTH_FAILURE_THRESHOLD = 2
    settings.LLM_HEALTH_STATE_TTL_S = 3600
    settings.LLM_HEALTH_PROBE_TIMEOUT_S = 60.0
    settings.LLM_HEALTH_PROBE_MODEL = ""
    settings.OPENAI_API_KEY = "sk-test-not-a-real-key"  # pragma: allowlist secret
    settings.OPENAI_PROXY = FAKE_PROXY
    settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = ["opchat-1"]


@pytest.fixture
def recorder(monkeypatch) -> SendRecorder:
    rec = SendRecorder()
    monkeypatch.setattr(NOTIFY_SEND, rec)
    return rec


def _ok(latency: float = 1.1) -> ProbeResult:
    return ProbeResult(ok=True, latency_s=latency)


def _fail(cls: str = "APITimeoutError", msg: str = "Request timed out.") -> ProbeResult:
    return ProbeResult(ok=False, latency_s=30.0, error_class=cls, error_message=msg)


def _boom_if_called(**kwargs):
    raise AssertionError("probe must not run when the tick is skipped")


# ---------------------------------------------------------------------------
# State machine -- the DRF-1054 core
# ---------------------------------------------------------------------------


def test_live_provider_stays_up_and_stays_silent(recorder):
    """Healthy probe: no transition, no message, no audit row."""

    assert evaluate_probe(_ok()) == TRANSITION_NONE
    assert evaluate_probe(_ok()) == TRANSITION_NONE

    assert recorder.calls == []
    assert not AuditLog.all_tenants.filter(action__startswith="llm.health").exists()


def test_single_failure_below_threshold_does_not_alert(recorder):
    """One blip must not page anyone -- that is what the threshold is for."""

    from django.core.cache import cache

    assert evaluate_probe(_fail()) == TRANSITION_NONE
    assert recorder.calls == []
    # State has NOT flipped: a below-threshold failure is not an outage.
    assert (cache.get(CACHE_KEY_STATE) or STATE_UP) == STATE_UP


def test_threshold_failures_flip_to_down_and_alert_once(recorder):
    """Two consecutive failures -> DOWN, exactly one MAX message."""

    from django.core.cache import cache

    assert evaluate_probe(_fail()) == TRANSITION_NONE
    assert evaluate_probe(_fail()) == TRANSITION_DOWN

    assert cache.get(CACHE_KEY_STATE) == STATE_DOWN
    assert len(recorder.calls) == 1
    text = recorder.texts[0]
    assert TXT_DOWN_HEADER in text
    assert "APITimeoutError" in text
    assert recorder.calls[0]["chat_id"] == "opchat-1"

    row = AuditLog.all_tenants.get(action=AUDIT_HEALTH_DOWN)
    assert row.payload["error_class"] == "APITimeoutError"
    assert row.payload["failures"] == 2


def test_no_spam_while_already_down(recorder):
    """The requirement that makes the monitor usable: signal on
    transition, not on every iteration."""

    evaluate_probe(_fail())
    assert evaluate_probe(_fail()) == TRANSITION_DOWN
    assert len(recorder.calls) == 1

    # Eight more failing ticks -- 40 minutes of outage at the 5-min cadence.
    for _ in range(8):
        assert evaluate_probe(_fail()) == TRANSITION_NONE

    assert len(recorder.calls) == 1, "a repeating alert is a muted alert"
    assert AuditLog.all_tenants.filter(action=AUDIT_HEALTH_DOWN).count() == 1


def test_down_then_up_transition_recovers_and_notifies(recorder):
    """down -> up: recovery is announced, and audited under its own slug."""

    from django.core.cache import cache

    evaluate_probe(_fail())
    evaluate_probe(_fail())
    assert len(recorder.calls) == 1

    assert evaluate_probe(_ok(latency=0.9)) == TRANSITION_UP

    assert cache.get(CACHE_KEY_STATE) == STATE_UP
    assert len(recorder.calls) == 2
    recovery = recorder.texts[1]
    assert TXT_UP_HEADER in recovery
    assert "0.9" in recovery

    assert AuditLog.all_tenants.filter(action=AUDIT_HEALTH_RECOVERED).count() == 1


def test_recovery_is_not_debounced(recorder):
    """Slow to alarm, fast to clear -- one success is enough to recover."""

    evaluate_probe(_fail())
    evaluate_probe(_fail())
    assert evaluate_probe(_ok()) == TRANSITION_UP

    # And the failure counter reset, so the NEXT outage needs a full
    # threshold again rather than firing on a single failure.
    assert evaluate_probe(_fail()) == TRANSITION_NONE
    assert len(recorder.calls) == 2


def test_full_cycle_up_down_up_down_alerts_each_transition(recorder):
    """Two separate outages produce two separate down-alerts."""

    for _ in range(2):
        evaluate_probe(_fail())
        evaluate_probe(_fail())
        evaluate_probe(_ok())

    assert len(recorder.calls) == 4  # down, up, down, up
    assert AuditLog.all_tenants.filter(action=AUDIT_HEALTH_DOWN).count() == 2
    assert AuditLog.all_tenants.filter(action=AUDIT_HEALTH_RECOVERED).count() == 2


def test_recovery_from_cold_cache_is_not_announced(recorder):
    """A fresh deploy with an empty cache must not announce a recovery
    nobody was waiting for -- the assumed initial state is UP."""

    assert evaluate_probe(_ok()) == TRANSITION_NONE
    assert recorder.calls == []


def test_no_recipients_means_no_send(monkeypatch, settings):
    """Empty HANDOFF_NOTIFY_MAX_CHAT_IDS = mechanism off, exactly as for
    escalations. The state machine still runs and still audits."""

    settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = []
    rec = SendRecorder()
    monkeypatch.setattr(NOTIFY_SEND, rec)

    evaluate_probe(_fail())
    assert evaluate_probe(_fail()) == TRANSITION_DOWN

    assert rec.calls == []
    assert AuditLog.all_tenants.filter(action=AUDIT_HEALTH_DOWN).count() == 1


def test_notification_failure_does_not_break_the_state_machine(monkeypatch):
    """MAX being down must not stop us recording that the LLM is down."""

    from apps.channels.max.outbound import MaxAPIError

    monkeypatch.setattr(NOTIFY_SEND, SendRecorder(exc=MaxAPIError(500, "boom")))

    evaluate_probe(_fail())
    assert evaluate_probe(_fail()) == TRANSITION_DOWN
    assert AuditLog.all_tenants.filter(action=AUDIT_HEALTH_DOWN).count() == 1


# ---------------------------------------------------------------------------
# Secret hygiene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        f"Error connecting to {FAKE_PROXY}",
        "proxy error: probeuser:probepass@proxy.invalid:3128 refused",
        "auth failed for sk-test-not-a-real-key",
    ],
)
def test_redact_secrets_strips_credentials(settings, raw):
    out = redact_secrets(raw)
    assert "probepass" not in out
    assert "probeuser:probepass" not in out
    assert "sk-test-not-a-real-key" not in out


def test_redact_secrets_leaves_ordinary_text_alone():
    text = "APITimeoutError: Request timed out after 30s (attempt 1/1)"
    assert redact_secrets(text) == text


def test_alert_text_never_carries_proxy_credentials(recorder):
    """End-to-end: a connection error quoting the proxy URL must reach
    the operator chat with the credentials gone."""

    leaky = _fail(
        cls="APIConnectionError",
        msg=redact_secrets(f"Connection error while reaching {FAKE_PROXY}"),
    )
    evaluate_probe(leaky)
    evaluate_probe(leaky)

    text = recorder.texts[0]
    assert "probepass" not in text
    assert "***@proxy.invalid" in text

    row = AuditLog.all_tenants.get(action=AUDIT_HEALTH_DOWN)
    assert "probepass" not in str(row.payload)


def test_down_message_shape():
    text = build_down_message(_fail(), failures=2)
    assert text.splitlines()[0] == TXT_DOWN_HEADER
    assert f"{TXT_FAILURES_PREFIX} 2" in text
    assert TXT_FALLBACK_HINT in text


# ---------------------------------------------------------------------------
# The probe itself -- DRF-1056 warm-up call + DRF-1054 detection semantics
# ---------------------------------------------------------------------------


class _FakeTimeout(Exception):
    """Stands in for openai's APITimeoutError (matched by class NAME in
    apps.llm.retry, so the name is the only thing that matters)."""


_FakeTimeout.__name__ = "APITimeoutError"


@pytest.fixture
def patched_provider(monkeypatch):
    """Patch OpenAIProvider.complete / aclose; record aclose calls so the
    leak guard is testable."""

    from apps.llm.providers.openai_provider import OpenAIProvider

    state: dict = {"closed": 0, "kwargs": None}

    async def _aclose(self):
        state["closed"] += 1

    monkeypatch.setattr(OpenAIProvider, "aclose", _aclose)

    def install(complete):
        async def _complete(self, messages, **kwargs):
            state["kwargs"] = {"messages": messages, **kwargs}
            return await complete()

        monkeypatch.setattr(OpenAIProvider, "complete", _complete)

    state["install"] = install
    return state


@pytest.mark.asyncio
async def test_probe_success(patched_provider):
    async def _ok_call():
        return object()

    patched_provider["install"](_ok_call)

    result = await probe_llm()

    assert result.ok is True
    assert result.error_class == ""
    assert result.latency_s >= 0
    # Cheapest possible call -- one token out, deterministic.
    assert patched_provider["kwargs"]["max_tokens"] == 1
    assert patched_provider["kwargs"]["temperature"] == 0.0
    # And the client was closed: a fresh provider per tick that is never
    # closed leaks an httpx pool into the worker every 5 minutes.
    assert patched_provider["closed"] == 1


@pytest.mark.asyncio
async def test_probe_unwraps_retriable_error(patched_provider):
    """The alert should name APITimeoutError, not our RetriableLLMError
    wrapper -- 'the tunnel hung' vs 'the proxy said no' is the whole
    diagnostic value."""

    from apps.llm.retry import RetriableLLMError

    async def _boom():
        raise RetriableLLMError(attempts=1, last_error=_FakeTimeout("Request timed out."))

    patched_provider["install"](_boom)

    result = await probe_llm()

    assert result.ok is False
    assert result.error_class == "APITimeoutError"
    assert "timed out" in result.error_message
    assert patched_provider["closed"] == 1


@pytest.mark.asyncio
async def test_probe_redacts_error_text(settings, patched_provider):
    async def _boom():
        raise RuntimeError(f"cannot reach {FAKE_PROXY}")

    patched_provider["install"](_boom)

    result = await probe_llm()

    assert result.ok is False
    assert "probepass" not in result.error_message
    assert "***@proxy.invalid" in result.error_message


@pytest.mark.asyncio
async def test_probe_is_bounded_by_outer_ceiling(settings, patched_provider):
    """A hung request must not hold the beat task open indefinitely."""

    settings.LLM_HEALTH_PROBE_TIMEOUT_S = 0.05

    async def _hang():
        await asyncio.sleep(30)

    patched_provider["install"](_hang)

    result = await probe_llm()

    assert result.ok is False
    assert result.latency_s < 5
    # Closed even on the timeout path.
    assert patched_provider["closed"] == 1


# ---------------------------------------------------------------------------
# Task entry point
# ---------------------------------------------------------------------------


def test_check_skips_when_disabled(settings, recorder, monkeypatch):
    settings.LLM_HEALTH_PROBE_ENABLED = False
    monkeypatch.setattr(health, "run_probe_sync", _boom_if_called)

    assert check_llm_availability() == {"skipped": SKIP_DISABLED}
    assert recorder.calls == []


def test_check_skips_without_api_key(settings, recorder, monkeypatch):
    """No key: a dev box, or a broken deploy. We cannot tell from here,
    so we refuse to page anyone rather than cry wolf on every laptop."""

    settings.OPENAI_API_KEY = ""
    monkeypatch.setattr(health, "run_probe_sync", _boom_if_called)

    assert check_llm_availability() == {"skipped": SKIP_NO_API_KEY}
    assert recorder.calls == []


def test_check_reports_transition(monkeypatch, recorder):
    monkeypatch.setattr(health, "run_probe_sync", lambda **kw: _fail())

    first = check_llm_availability()
    assert first["ok"] is False
    assert first["transition"] == TRANSITION_NONE

    second = check_llm_availability()
    assert second["transition"] == TRANSITION_DOWN
    assert second["error_class"] == "APITimeoutError"
    assert len(recorder.calls) == 1


def test_task_never_raises(monkeypatch, recorder):
    """A monitor that can crash the scheduler is worse than no monitor."""

    from apps.llm.tasks import probe_llm_availability

    def _explode(**kwargs):
        raise RuntimeError("probe layer blew up")

    monkeypatch.setattr(health, "run_probe_sync", _explode)

    assert probe_llm_availability() == {"skipped": "error"}


def test_task_happy_path(monkeypatch, recorder):
    from apps.llm.tasks import probe_llm_availability

    monkeypatch.setattr(health, "run_probe_sync", lambda **kw: _ok(0.8))

    out = probe_llm_availability()
    assert out["ok"] is True
    assert out["transition"] == TRANSITION_NONE
    assert recorder.calls == []
