"""Process warm-up for vendor SDK clients (DRF-1445).

The behaviour under test is the one the pilot measured: the first LLM
call in a fresh process pays ~34 s of SDK import + client construction
before a single byte leaves the box, and a human pays it. These tests
pin the four properties that make moving that cost safe:

* it warms **the instance the router serves**, not a private copy;
* it needs **no vendor call**;
* it **cannot block** the consumer's readiness;
* it **cannot crash** a booting process, whatever the vendor is doing.

The mutation guard for the call site itself lives next to the consumer:
``apps/workers/tests/test_consumer_warmup.py``.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from apps.llm import warmup
from apps.llm.pii_protected_provider import PIITokenizingProvider
from apps.llm.providers.anthropic_provider import AnthropicProvider
from apps.llm.providers.openai_provider import OpenAIProvider
from apps.llm.router import LLMRouter, get_router, reset_router_cache


@pytest.fixture(autouse=True)
def _isolated(settings: Any):
    settings.LLM_WARMUP_ENABLED = True
    settings.LLM_WARMUP_PROVIDERS = []
    settings.LLM_PROVIDER = "anthropic"
    settings.SKILL_LLM_PROVIDER = {}
    # Both keys present by default so key-gating is asserted explicitly
    # by the test that cares, not implied by a developer's environment.
    settings.ANTHROPIC_API_KEY = "sk-ant-test"  # pragma: allowlist secret
    settings.OPENAI_API_KEY = "sk-test"  # pragma: allowlist secret
    settings.ANTHROPIC_PROXY = ""
    settings.OPENAI_PROXY = ""
    reset_router_cache()
    warmup.reset_warmup_state()
    yield
    reset_router_cache()
    warmup.reset_warmup_state()


class _SpyProvider:
    """Stand-in for a concrete provider — records that it was warmed."""

    def __init__(self, name: str = "spy") -> None:
        self.name = name
        self.warm_calls = 0

    def warm_up(self) -> None:
        self.warm_calls += 1


# ---------------------------------------------------------------------------
# Which vendors get warmed
# ---------------------------------------------------------------------------


class TestProviderSelection:
    def test_follows_the_router_own_settings(self, settings: Any) -> None:
        """Warm exactly what the router will resolve — no more."""
        settings.LLM_PROVIDER = "anthropic"
        settings.SKILL_LLM_PROVIDER = {"faq": "openai", "intent": "anthropic"}

        # Order: org default first, then skill values; "anthropic"
        # appears twice in the inputs and once in the output.
        assert warmup.warmup_provider_names() == ["anthropic", "openai"]

    def test_explicit_pin_wins(self, settings: Any) -> None:
        settings.LLM_PROVIDER = "anthropic"
        settings.SKILL_LLM_PROVIDER = {"faq": "anthropic"}
        settings.LLM_WARMUP_PROVIDERS = ["openai"]

        assert warmup.warmup_provider_names() == ["openai"]

    def test_vendor_without_a_key_is_skipped(self, settings: Any) -> None:
        """Building a client we can never use spends the import for nothing."""
        settings.LLM_PROVIDER = "anthropic"
        settings.ANTHROPIC_API_KEY = ""

        assert warmup.warmup_provider_names() == []

    def test_unknown_vendor_name_is_dropped_not_raised(self, settings: Any) -> None:
        """A typo in an env var must not stop a process from booting."""
        settings.LLM_WARMUP_PROVIDERS = ["antropic", "openai"]

        assert warmup.warmup_provider_names() == ["openai"]


# ---------------------------------------------------------------------------
# What warming actually does
# ---------------------------------------------------------------------------


class TestWarmLLMClients:
    def test_warms_the_instance_the_router_will_serve(self, settings: Any) -> None:
        """A private copy would warm nothing the human's turn can reuse.

        Positive guard first: the provider really is cold before, so a
        broken warm-up cannot pass by warming an already-warm object.
        """
        settings.LLM_PROVIDER = "anthropic"
        router = get_router()

        cold = router.preload("anthropic")
        assert isinstance(cold, PIITokenizingProvider)
        assert cold._wrapped._client is None  # positive guard

        timings = warmup.warm_llm_clients()

        assert list(timings) == ["anthropic"]
        served = router.preload("anthropic")
        assert served is cold  # same cached object the serving path gets
        assert served._wrapped._client is not None

    def test_needs_no_vendor_call(self, settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Warm-up must not spend a request at every restart.

        The measured cost is local (import + SSL context), so anything
        reaching ``messages.create`` is the wrong fix. Blowing up on the
        vendor surface is the only way to assert "did not call it" that
        survives a refactor of how the call is made.
        """
        settings.LLM_PROVIDER = "anthropic"

        provider = AnthropicProvider()
        provider.warm_up()

        client = provider._client
        assert client is not None

        def _explode(*_a: Any, **_kw: Any) -> None:
            raise AssertionError("warm-up must not call the vendor")

        monkeypatch.setattr(client.messages, "create", _explode)
        # Warming again goes nowhere near the network either.
        provider.warm_up()

    def test_is_idempotent_and_leaves_the_warm_client_alone(self) -> None:
        """Positive guard for the steady state: warming twice must not
        replace a live client (and its connection pool) with a new one.
        """
        provider = AnthropicProvider()
        provider.warm_up()
        first = provider._client
        provider.warm_up()

        assert provider._client is first

    def test_openai_provider_warms_the_same_way(self) -> None:
        provider = OpenAIProvider()
        assert provider._client is None  # positive guard
        provider.warm_up()
        assert provider._client is not None

    def test_pii_wrapper_forwards_the_hook(self) -> None:
        """The wrapper sits between the router and every provider — a
        warm-up that stopped here would warm nothing.
        """
        spy = _SpyProvider()
        PIITokenizingProvider(spy).warm_up()

        assert spy.warm_calls == 1

    def test_wrapper_tolerates_a_provider_without_the_hook(self) -> None:
        class _NoHook:
            name = "nohook"

        PIITokenizingProvider(_NoHook()).warm_up()  # must not raise

    def test_a_broken_vendor_does_not_abort_the_rest(
        self, settings: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One dead SDK must not cost the other vendor its warm-up."""
        settings.LLM_WARMUP_PROVIDERS = ["anthropic", "openai"]

        class _BrokenProvider:
            name = "anthropic"

            def warm_up(self) -> None:
                raise RuntimeError("SDK not installed")

        broken = _BrokenProvider()
        healthy = _SpyProvider("openai")

        monkeypatch.setattr(
            LLMRouter,
            "preload",
            lambda _self, name: broken if name == "anthropic" else healthy,
        )

        timings = warmup.warm_llm_clients()

        assert list(timings) == ["openai"]
        assert healthy.warm_calls == 1

    def test_provider_that_cannot_be_constructed_is_survived(
        self, settings: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings.LLM_WARMUP_PROVIDERS = ["anthropic"]

        def _boom(_self: Any, _name: str) -> None:
            raise RuntimeError("provider init failed")

        monkeypatch.setattr(LLMRouter, "preload", _boom)

        assert warmup.warm_llm_clients() == {}


# ---------------------------------------------------------------------------
# The background thread
# ---------------------------------------------------------------------------


class TestBackgroundStart:
    def test_returns_before_the_work_finishes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Readiness must not wait on warm-up.

        A worker that answers slowly is the bug being fixed; a worker
        that is not yet accepting messages is a worse one.
        """
        release = threading.Event()
        entered = threading.Event()

        def _slow() -> dict[str, float]:
            entered.set()
            release.wait(timeout=5)
            return {}

        monkeypatch.setattr(warmup, "warm_llm_clients", _slow)

        started = time.monotonic()
        thread = warmup.start_background_warmup()
        elapsed = time.monotonic() - started
        assert thread is not None

        try:
            assert thread.daemon is True
            assert entered.wait(timeout=5)  # positive guard: work began
            assert thread.is_alive()  # ...and was still running when we returned
            assert elapsed < 1.0
        finally:
            release.set()
            thread.join(timeout=5)

    def test_starts_at_most_once_per_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(warmup, "warm_llm_clients", lambda: {})

        first = warmup.start_background_warmup()
        second = warmup.start_background_warmup()

        assert first is not None
        assert second is None
        first.join(timeout=5)

    def test_kill_switch_stops_it(self, settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """The mutation lever: with warm-up off, nothing is warmed."""
        settings.LLM_WARMUP_ENABLED = False
        called: list[int] = []

        def _record() -> dict[str, float]:
            called.append(1)
            return {}

        monkeypatch.setattr(warmup, "warm_llm_clients", _record)

        assert warmup.start_background_warmup() is None
        assert called == []

    def test_thread_body_swallows_everything(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An exception escaping the thread would print a traceback at
        every boot and teach operators to ignore worker stderr.
        """

        def _boom() -> dict[str, float]:
            raise RuntimeError("nope")

        monkeypatch.setattr(warmup, "warm_llm_clients", _boom)

        warmup._run_warmup()  # must not raise
