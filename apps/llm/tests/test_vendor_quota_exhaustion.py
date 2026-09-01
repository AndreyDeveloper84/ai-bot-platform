"""Vendor credit exhaustion → recognition and provider hop (DRF-1437).

Reference incident: 2026-08-31, pilot bot silent from 00:05 UTC. The
OpenAI account balance was drained; the SDK answered every call with

    RateLimitError: Error code: 429 - {'error': {
        'message': 'You have no credits remaining',
        'type': 'insufficient_quota',
        'code': 'credit_balance_exhausted'}}

98 consecutive refusals, zero hops onto another vendor.

### What these tests are for

Two claims, each with its NEGATIVE half asserted in the same class,
because a "does it fall back?" test that never checks the
*doesn't*-fall-back case passes on a provider that hops on every error
— which would be a different, worse bug (double spend, vendor thrash,
and the real error buried).

  1. Recognition — this SPECIFIC error shape maps to
     ``LLMVendorCreditsExhausted`` and is NOT retriable; an ordinary
     rate-limit with no billing discriminator stays retriable and maps
     to ``LLMQuotaError``.
  2. Hop — a primary raising exhaustion yields an answer from the
     SECOND provider; a primary raising an ordinary quota error yields
     no hop at all.

### Why the errors are constructed from the real SDK classes

``openai.RateLimitError`` / ``anthropic.RateLimitError`` are built here
exactly as the SDKs build them (``message``, ``response=``, ``body=``),
so the attribute surface under test — ``.code``, ``.type``, ``.body``,
``.status_code`` — is the SDK's own, not a hand-rolled stub that could
agree with our reader by construction.

No network, no API key, no live call: there is no Anthropic key to test
with, so the second provider is a stub and the errors are recorded
shapes.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from apps.llm.protocol import (
    CompletionResult,
    LLMProvider,
    LLMProviderQuotaExceeded,
    LLMQuotaError,
    LLMVendorCreditsExhausted,
)
from apps.llm.retry import (
    RetriableLLMError,
    RetryPolicy,
    is_retriable_anthropic,
    is_retriable_openai,
    is_vendor_quota_exhausted,
    run_with_retry,
)
from apps.llm.router import LLMRouter, reset_router_cache

# Not a credential — a non-empty placeholder so ``provider_is_configured``
# sees a key present. Deliberately not key-shaped: no vendor prefix, no
# entropy.
PLACEHOLDER_KEY = "unit-test-placeholder"


# ---------------------------------------------------------------------------
# SDK error builders — the recorded shapes
# ---------------------------------------------------------------------------


def _response(status: int, body: dict[str, Any], url: str) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", url), json=body)


def openai_credits_exhausted() -> Exception:
    """The shape logged on the pilot, 2026-08-31 00:05 UTC."""
    import openai

    body = {
        "error": {
            "message": "You have no credits remaining",
            "type": "insufficient_quota",
            "param": None,
            "code": "credit_balance_exhausted",
        }
    }
    return openai.RateLimitError(
        f"Error code: 429 - {body}",
        response=_response(429, body, "https://api.openai.com/v1/chat/completions"),
        body=body["error"],
    )


def openai_quota_documented_shape() -> Exception:
    """OpenAI's documented billing refusal — ``code`` also
    ``insufficient_quota`` rather than the credit-balance spelling.
    Covered so the fix does not depend on which of the two spellings the
    account happens to emit.
    """
    import openai

    body = {
        "error": {
            "message": (
                "You exceeded your current quota, please check your plan and billing details."
            ),
            "type": "insufficient_quota",
            "param": None,
            "code": "insufficient_quota",
        }
    }
    return openai.RateLimitError(
        f"Error code: 429 - {body}",
        response=_response(429, body, "https://api.openai.com/v1/chat/completions"),
        body=body["error"],
    )


def openai_ordinary_rate_limit() -> Exception:
    """THE GUARD. Same class, same 429, no billing discriminator — a
    genuine "slow down". Must stay retriable and must NOT trigger a hop,
    or the fix would move traffic to another vendor on every blip.
    """
    import openai

    body = {
        "error": {
            "message": (
                "Rate limit reached for gpt-4o-mini in organization org-x "
                "on requests per min (RPM): Limit 500, Used 500."
            ),
            "type": "requests",
            "param": None,
            "code": "rate_limit_exceeded",
        }
    }
    return openai.RateLimitError(
        f"Error code: 429 - {body}",
        response=_response(429, body, "https://api.openai.com/v1/chat/completions"),
        body=body["error"],
    )


def openai_exhausted_code_only() -> Exception:
    """Structured code present, prose deliberately UNRECOGNISABLE.

    Isolates the ``_VENDOR_EXHAUSTION_CODES`` path. Mutation testing
    found that every other fixture here also matches by message, so
    disabling the code lookup entirely left the suite green — the
    structured path, which is the reliable one, was riding on the
    degraded one. This fixture is the only thing that fails when the
    code lookup breaks.
    """
    import openai

    body = {"error": {"message": "Error code: 429", "type": None, "code": "insufficient_quota"}}
    return openai.RateLimitError(
        "Error code: 429",
        response=_response(429, body, "https://api.openai.com/v1/chat/completions"),
        body=body["error"],
    )


def openai_exhausted_message_only() -> Exception:
    """Prose present, structured code STRIPPED.

    The degraded path: a proxy or an older SDK that hands back the
    message and nothing else. Symmetric partner to the fixture above —
    between them, neither detection layer can be removed unnoticed.
    """
    import openai

    body = {"error": {"message": "You have no credits remaining"}}
    return openai.RateLimitError(
        "You have no credits remaining",
        response=_response(429, body, "https://api.openai.com/v1/chat/completions"),
        body=body["error"],
    )


def anthropic_credits_exhausted() -> Exception:
    """Anthropic's equivalent — same defect class, other vendor, so the
    hop works in both directions once the owner has both keys.
    """
    import anthropic

    body = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "code": "credit_balance_too_low",
            "message": (
                "Your credit balance is too low to access the Anthropic API. "
                "Please go to Plans & Billing to upgrade or purchase credits."
            ),
        },
    }
    return anthropic.RateLimitError(
        f"Error code: 429 - {body}",
        response=_response(429, body, "https://api.anthropic.com/v1/messages"),
        body=body,
    )


# ---------------------------------------------------------------------------
# Claim 1 — recognition
# ---------------------------------------------------------------------------


class TestRecognisesVendorExhaustion:
    """The pilot's error form maps to exhaustion, not to "retry me"."""

    def test_pilot_error_is_recognised_as_exhaustion(self) -> None:
        assert is_vendor_quota_exhausted(openai_credits_exhausted()) is True

    def test_documented_openai_billing_shape_is_recognised(self) -> None:
        assert is_vendor_quota_exhausted(openai_quota_documented_shape()) is True

    def test_anthropic_credit_shape_is_recognised(self) -> None:
        assert is_vendor_quota_exhausted(anthropic_credits_exhausted()) is True

    def test_structured_code_alone_is_enough(self) -> None:
        """Neither detection layer may be load-bearing alone: this one
        fails if the code lookup is removed…"""
        assert is_vendor_quota_exhausted(openai_exhausted_code_only()) is True

    def test_message_alone_is_enough(self) -> None:
        """…and this one fails if the message fallback is removed."""
        assert is_vendor_quota_exhausted(openai_exhausted_message_only()) is True

    # -- the guard ---------------------------------------------------------

    def test_ordinary_rate_limit_is_not_exhaustion(self) -> None:
        """Without this the predicate could ``return True`` unconditionally
        and every test above would still pass.
        """
        assert is_vendor_quota_exhausted(openai_ordinary_rate_limit()) is False

    def test_unrelated_exception_is_not_exhaustion(self) -> None:
        assert is_vendor_quota_exhausted(ValueError("boom")) is False


class TestExhaustionIsNotRetriable:
    """Retrying a drained balance burns the budget and delays the turn by
    the full backoff before failing anyway.
    """

    def test_pilot_error_is_not_retriable(self) -> None:
        assert is_retriable_openai(openai_credits_exhausted()) is False

    def test_anthropic_exhaustion_is_not_retriable(self) -> None:
        assert is_retriable_anthropic(anthropic_credits_exhausted()) is False

    # -- the guard ---------------------------------------------------------

    def test_ordinary_rate_limit_stays_retriable(self) -> None:
        """The 95%-transient case the retry layer exists for. If this
        flips, the change has broken retry rather than fixed fallback.
        """
        assert is_retriable_openai(openai_ordinary_rate_limit()) is True

    @pytest.mark.asyncio
    async def test_exhaustion_spends_exactly_one_attempt(self) -> None:
        """The pilot symptom, measured: pre-fix each turn spent the whole
        retry budget against a known-empty balance.
        """
        import openai

        calls = 0
        raised: list[BaseException] = []

        async def _fail() -> None:
            nonlocal calls
            calls += 1
            exc = openai_credits_exhausted()
            raised.append(exc)
            raise exc

        # pytest.raises pins the TYPE positively — a wrapped or swallowed
        # error fails here, by name, before any absence assertion runs.
        with pytest.raises(openai.RateLimitError) as caught:
            await run_with_retry(
                _fail,
                policy=RetryPolicy(max_attempts=5, base_delay_s=0, jitter=0),
                is_retriable=is_retriable_openai,
            )

        assert calls == 1, "a drained balance must not consume the retry budget"
        assert len(raised) == 1

        # Presence before absence, on the same object. `not isinstance(...)`
        # alone is true for None and for any unrelated value, so on its own
        # it would pass even if nothing meaningful came out at all.
        assert is_vendor_quota_exhausted(caught.value), (
            "the propagated error must still be recognisable as exhaustion"
        )
        assert caught.value is raised[0], (
            "the SDK error must arrive as the very same object — identity is "
            "what proves it was re-raised rather than reconstructed"
        )
        # Only now is this meaningful: NOT wrapped, so the provider's
        # mapper sees the SDK error and can classify it.
        assert not isinstance(caught.value, RetriableLLMError)

    @pytest.mark.asyncio
    async def test_ordinary_rate_limit_spends_the_whole_budget(self) -> None:
        """The guard for the test above: the retry layer still retries
        what it should.
        """
        calls = 0

        async def _fail() -> None:
            nonlocal calls
            calls += 1
            raise openai_ordinary_rate_limit()

        with pytest.raises(RetriableLLMError):
            await run_with_retry(
                _fail,
                policy=RetryPolicy(max_attempts=5, base_delay_s=0, jitter=0),
                is_retriable=is_retriable_openai,
            )

        assert calls == 5


class TestProviderMapsExhaustionToTypedError:
    """The mapping the router's fallback contract keys on."""

    def _map(self, provider: Any, exc: Exception) -> BaseException:
        with pytest.raises(Exception) as caught:  # noqa: PT011 — asserted below
            provider._reraise_as_llm_error(exc, op="complete", model="m")
        return caught.value

    def test_openai_exhaustion_maps_to_vendor_credits_exhausted(self) -> None:
        from apps.llm.providers.openai_provider import OpenAIProvider

        mapped = self._map(OpenAIProvider(), openai_credits_exhausted())
        assert isinstance(mapped, LLMVendorCreditsExhausted)
        # Subclassing is what makes the router's existing fallback
        # contract pick it up without touching the contract.
        assert isinstance(mapped, LLMProviderQuotaExceeded)

    def test_anthropic_exhaustion_maps_to_vendor_credits_exhausted(self) -> None:
        from apps.llm.providers.anthropic_provider import AnthropicProvider

        mapped = self._map(AnthropicProvider(), anthropic_credits_exhausted())
        assert isinstance(mapped, LLMVendorCreditsExhausted)

    # -- the guard ---------------------------------------------------------

    def test_ordinary_rate_limit_still_maps_to_plain_quota_error(self) -> None:
        """``LLMQuotaError`` means "vendor says slow down". It must NOT
        become a fallback trigger, or a 60-second RPM blip would move
        every tenant onto the other vendor.
        """
        from apps.llm.providers.openai_provider import OpenAIProvider

        mapped = self._map(OpenAIProvider(), openai_ordinary_rate_limit())
        assert isinstance(mapped, LLMQuotaError)
        assert not isinstance(mapped, LLMProviderQuotaExceeded)


# ---------------------------------------------------------------------------
# Claim 2 — the hop
# ---------------------------------------------------------------------------


class StubProvider:
    """Records what it was asked and answers, or raises what it was told to."""

    def __init__(self, name: str, *, raises: Exception | None = None) -> None:
        self.name = name
        self.default_completion_model = f"{name}-model"
        self._raises = raises
        self.calls = 0
        self.last_kwargs: dict[str, Any] = {}

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> CompletionResult:
        self.calls += 1
        self.last_kwargs = dict(kwargs)
        if self._raises is not None:
            raise self._raises
        return CompletionResult(
            text=f"answer from {self.name}",
            provider=self.name,
            model=str(kwargs.get("model", "")),
        )

    async def embedding(self, text: str, **kwargs: Any) -> list[float]:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return [0.0]


class _StubLoadingRouter(LLMRouter):
    """A real ``LLMRouter`` with provider CONSTRUCTION stubbed.

    Everything under test — tier resolution, the configured-key filter,
    the fallback wrapper, the audit hop — is the production code path.
    Only the two SDK-backed classes are swapped out, because there is no
    Anthropic key to build a real one with.

    Overriding the method beats assigning over it on the instance: the
    override is type-checked against the base signature, so a change to
    ``_load_provider``'s contract breaks here loudly instead of being
    silenced by a ``type: ignore``.

    NOTE what this deliberately skips: the real ``_load_provider`` wraps
    each provider in ``PIITokenizingProvider``. That wrapper is where the
    DRF-1437 model-swap defect actually lived, and stubs like these are
    what hid it — so the wrap chain has its own test that does NOT stub
    this method (``TestHopThroughTheRealWrapperChain``).
    """

    def __init__(self, stubs: dict[str, StubProvider]) -> None:
        super().__init__()
        self._stubs = stubs

    def _load_provider(self, name: str) -> LLMProvider:
        return self._stubs[name]


def _router_with(stubs: dict[str, StubProvider]) -> LLMRouter:
    return _StubLoadingRouter(stubs)


def _exhausted_and_healthy() -> dict[str, StubProvider]:
    return {
        "openai": StubProvider(
            "openai",
            raises=LLMVendorCreditsExhausted("openai.complete: vendor credits exhausted"),
        ),
        "anthropic": StubProvider("anthropic"),
    }


@pytest.fixture
def both_keys_present(settings: Any) -> Any:
    settings.OPENAI_API_KEY = PLACEHOLDER_KEY
    settings.ANTHROPIC_API_KEY = PLACEHOLDER_KEY
    settings.LLM_PROVIDER = "openai"
    settings.SKILL_LLM_PROVIDER = {}
    settings.LLM_FALLBACK_ORDER = []
    settings.LLM_QUOTA_FALLBACK_ENABLED = True
    reset_router_cache()
    yield
    reset_router_cache()


@pytest.mark.usefixtures("both_keys_present")
@pytest.mark.django_db(transaction=True)
class TestQuotaFallbackHop:
    @pytest.mark.asyncio
    async def test_answer_comes_from_the_second_provider(self) -> None:
        """THE headline claim: OpenAI is out of credits, the user still
        gets an answer, and it came from Anthropic.
        """
        stubs = _exhausted_and_healthy()

        provider = _router_with(stubs).get_provider(None, skill="faq", op="complete")
        result = await provider.complete([{"role": "user", "content": "привет"}], model="m")

        assert result.text == "answer from anthropic"
        assert result.provider == "anthropic"
        assert stubs["openai"].calls == 1
        assert stubs["anthropic"].calls == 1

    @pytest.mark.asyncio
    async def test_our_own_daily_cap_also_hops(self) -> None:
        """The parent exception — OUR Redis counter — keeps the behaviour
        the Sprint 7 docstring always promised.
        """
        stubs = {
            "openai": StubProvider("openai", raises=LLMProviderQuotaExceeded("daily cap")),
            "anthropic": StubProvider("anthropic"),
        }

        provider = _router_with(stubs).get_provider(None, op="complete")
        result = await provider.complete([], model="m")

        assert result.provider == "anthropic"

    # -- the guard ---------------------------------------------------------

    @pytest.mark.asyncio
    async def test_ordinary_rate_limit_does_not_hop(self) -> None:
        """Without this assertion the hop test above passes on a wrapper
        that falls back on ANY exception.
        """
        stubs = {
            "openai": StubProvider("openai", raises=LLMQuotaError("openai.complete: rate-limited")),
            "anthropic": StubProvider("anthropic"),
        }

        provider = _router_with(stubs).get_provider(None, op="complete")
        with pytest.raises(LLMQuotaError):
            await provider.complete([], model="m")

        assert stubs["openai"].calls == 1
        assert stubs["anthropic"].calls == 0, (
            "a vendor rate-limit must not move traffic to another vendor"
        )

    @pytest.mark.asyncio
    async def test_transport_error_does_not_hop(self) -> None:
        from apps.llm.protocol import LLMTransportError

        stubs = {
            "openai": StubProvider("openai", raises=LLMTransportError("connection reset")),
            "anthropic": StubProvider("anthropic"),
        }

        provider = _router_with(stubs).get_provider(None, op="complete")
        with pytest.raises(LLMTransportError):
            await provider.complete([], model="m")

        assert stubs["anthropic"].calls == 0

    @pytest.mark.asyncio
    async def test_success_never_touches_the_second_provider(self) -> None:
        stubs = {"openai": StubProvider("openai"), "anthropic": StubProvider("anthropic")}

        provider = _router_with(stubs).get_provider(None, op="complete")
        result = await provider.complete([], model="m")

        assert result.provider == "openai"
        assert stubs["anthropic"].calls == 0

    @pytest.mark.asyncio
    async def test_hop_writes_an_audit_row(self) -> None:
        from apps.audit.models import AuditLog
        from apps.llm.router import EVENT_QUOTA_FALLBACK_USED

        provider = _router_with(_exhausted_and_healthy()).get_provider(
            None, skill="faq", op="complete"
        )
        await provider.complete([], model="m")

        from asgiref.sync import sync_to_async

        row = await sync_to_async(
            lambda: AuditLog.all_tenants.filter(action=EVENT_QUOTA_FALLBACK_USED).first()
        )()
        assert row is not None
        assert row.payload["from_provider"] == "openai"
        assert row.payload["chosen_provider"] == "anthropic"
        assert row.payload["reason"] == "LLMVendorCreditsExhausted"

    @pytest.mark.asyncio
    async def test_hop_retargets_the_model_to_the_fallback_vendor(self) -> None:
        """Without this the hop trades one failure for another: model ids
        do not cross vendors, and ``gpt-4o-mini`` sent to Anthropic comes
        back ``404 not_found_error``. The user would still see the static
        fallback — now with a second vendor's bill attached.
        """
        stubs = _exhausted_and_healthy()

        provider = _router_with(stubs).get_provider(None, op="complete")
        result = await provider.complete([], model="gpt-4o-mini")

        assert stubs["anthropic"].last_kwargs["model"] == "anthropic-model"
        assert result.model == "anthropic-model"

    # -- the guard ---------------------------------------------------------

    @pytest.mark.asyncio
    async def test_no_hop_means_the_model_is_passed_through_untouched(self) -> None:
        """The retarget must fire ONLY on the hop. Rewriting the model on
        the ordinary path would override every caller's deliberate choice
        — silently and on 100%% of traffic.
        """
        stubs = {"openai": StubProvider("openai"), "anthropic": StubProvider("anthropic")}

        provider = _router_with(stubs).get_provider(None, op="complete")
        result = await provider.complete([], model="gpt-4o-mini")

        assert stubs["openai"].last_kwargs["model"] == "gpt-4o-mini"
        assert result.model == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_other_kwargs_survive_the_hop(self) -> None:
        """Only ``model`` is rewritten. Tools, temperature and tool_choice
        carry the caller's intent and must reach the fallback intact.
        """
        stubs = _exhausted_and_healthy()
        tools = [{"name": "search", "description": "", "parameters": {}}]

        provider = _router_with(stubs).get_provider(None, op="complete")
        await provider.complete([], model="gpt-4o-mini", tools=tools, temperature=0.7)

        forwarded = stubs["anthropic"].last_kwargs
        assert forwarded["tools"] == tools
        assert forwarded["temperature"] == 0.7

    def test_embedding_ops_are_not_wrapped_at_all(self) -> None:
        """Anthropic has no embeddings API, so an embedding op has nowhere
        to hop to — ``op="embedding"`` already resolves to the only
        embedding-capable vendor. Wrapping it would add a frame that can
        only ever re-raise, and would mislead anyone reading a traceback
        into thinking a fallback was attempted.

        Operational consequence, stated here so it is not discovered in
        production: while OpenAI's balance is empty the FAQ knowledge-base
        search CANNOT fail over. It degrades to a handoff (the skill
        catches ``LLMError``), it does not 500 — but Claude cannot cover
        it.
        """
        from apps.llm.router import QuotaFallbackProvider

        stubs = _exhausted_and_healthy()
        provider = _router_with(stubs).get_provider(None, op="embedding")

        # Presence before absence, on the same object. `not isinstance(...)`
        # is true for None and for any unrelated value, so it needs proof
        # that a real provider was resolved at all.
        assert provider.name == "openai", "embedding must resolve to the embedding-capable vendor"
        assert provider is stubs["openai"], (
            "identity: the raw resolved provider, with nothing wrapped around it"
        )
        assert not isinstance(provider, QuotaFallbackProvider)

    @pytest.mark.asyncio
    async def test_wrapper_keeps_the_primary_vendor_name(self) -> None:
        """Cost attribution, audit and telemetry all read ``.name``. A
        wrapper that shadowed it would silently re-label every call.
        """
        provider = _router_with(_exhausted_and_healthy()).get_provider(None, op="complete")

        assert provider.name == "openai"
        assert provider.default_completion_model == "openai-model"


@pytest.mark.django_db(transaction=True)
class TestFallbackNeedsAConfiguredKey:
    """The state the owner is in TODAY: no Anthropic key. The hop must be
    absent — and absent for the stated reason, not by accident.
    """

    @pytest.mark.asyncio
    async def test_no_anthropic_key_means_no_hop(self, settings: Any) -> None:
        settings.OPENAI_API_KEY = PLACEHOLDER_KEY
        settings.ANTHROPIC_API_KEY = ""
        settings.LLM_PROVIDER = "openai"
        settings.SKILL_LLM_PROVIDER = {}
        settings.LLM_FALLBACK_ORDER = []
        reset_router_cache()

        stubs = _exhausted_and_healthy()
        provider = _router_with(stubs).get_provider(None, op="complete")
        with pytest.raises(LLMVendorCreditsExhausted):
            await provider.complete([], model="m")

        assert stubs["anthropic"].calls == 0, "must not hop onto a vendor that would 401"
        reset_router_cache()

    @pytest.mark.asyncio
    async def test_adding_the_key_is_the_only_change_needed(self, settings: Any) -> None:
        """The owner's acceptance test, run twice against the same code:
        the ONLY difference between silence and an answer is a non-empty
        ``ANTHROPIC_API_KEY``.
        """
        settings.OPENAI_API_KEY = PLACEHOLDER_KEY
        settings.LLM_PROVIDER = "openai"
        settings.SKILL_LLM_PROVIDER = {}
        settings.LLM_FALLBACK_ORDER = []

        def _build() -> Any:
            reset_router_cache()
            return _router_with(_exhausted_and_healthy()).get_provider(None, op="complete")

        settings.ANTHROPIC_API_KEY = ""
        with pytest.raises(LLMVendorCreditsExhausted):
            await _build().complete([], model="m")

        settings.ANTHROPIC_API_KEY = PLACEHOLDER_KEY
        result = await _build().complete([], model="m")
        assert result.provider == "anthropic"

        reset_router_cache()


@pytest.mark.django_db(transaction=True)
class TestKillSwitch:
    @pytest.mark.asyncio
    async def test_disabled_switch_suppresses_the_hop(self, settings: Any) -> None:
        settings.OPENAI_API_KEY = PLACEHOLDER_KEY
        settings.ANTHROPIC_API_KEY = PLACEHOLDER_KEY
        settings.LLM_PROVIDER = "openai"
        settings.SKILL_LLM_PROVIDER = {}
        settings.LLM_FALLBACK_ORDER = []
        settings.LLM_QUOTA_FALLBACK_ENABLED = False
        reset_router_cache()

        stubs = _exhausted_and_healthy()
        provider = _router_with(stubs).get_provider(None, op="complete")

        with pytest.raises(LLMVendorCreditsExhausted):
            await provider.complete([], model="m")
        assert stubs["anthropic"].calls == 0
        reset_router_cache()


@pytest.mark.django_db(transaction=True)
class TestHopThroughTheRealWrapperChain:
    """The hop, exercised through the REAL ``_load_provider``.

    ### Why this class exists

    Every other hop test in this file stubs ``LLMRouter._load_provider``
    and gets a bare ``StubProvider`` back. Production does not: it wraps
    each provider in ``PIITokenizingProvider`` first. That wrapper did
    not forward ``default_completion_model``, so the fallback's model
    swap read ``""`` off it, skipped, and forwarded the caller's OpenAI
    model id to Anthropic — a guaranteed ``404 not_found_error`` on the
    one call site that passes a concrete model
    (``apps/orchestrator/intent_router.py``, ``"gpt-4o-mini"``).

    The stubs hid it because ``StubProvider`` carries the attribute
    directly. mypy found it, not the tests: ``LLMProvider`` had no
    ``default_completion_model``, which was true — and the reason the
    wrapper could drop it unnoticed.

    So this class stubs one layer LOWER: the provider CLASSES in their
    modules, leaving ``_load_provider``'s importlib lookup and its PII
    wrapping to run for real.
    """

    @pytest.fixture(autouse=True)
    def _real_load_path(self, settings: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
        settings.OPENAI_API_KEY = PLACEHOLDER_KEY
        settings.ANTHROPIC_API_KEY = PLACEHOLDER_KEY
        settings.LLM_PROVIDER = "openai"
        settings.SKILL_LLM_PROVIDER = {}
        settings.LLM_FALLBACK_ORDER = []
        settings.LLM_QUOTA_FALLBACK_ENABLED = True

        from apps.llm.providers import anthropic_provider, openai_provider

        exhausted = StubProvider(
            "openai", raises=LLMVendorCreditsExhausted("openai.complete: no credits")
        )
        exhausted.default_completion_model = "gpt-4o-mini"
        healthy = StubProvider("anthropic")
        healthy.default_completion_model = "claude-sonnet-4-6"

        monkeypatch.setattr(openai_provider, "OpenAIProvider", lambda: exhausted)
        monkeypatch.setattr(anthropic_provider, "AnthropicProvider", lambda: healthy)

        reset_router_cache()
        yield {"openai": exhausted, "anthropic": healthy}
        reset_router_cache()

    @pytest.mark.asyncio
    async def test_model_is_retargeted_through_the_pii_wrapper(
        self, _real_load_path: dict[str, StubProvider]
    ) -> None:
        """The regression. ``intent_router`` hard-codes ``gpt-4o-mini``;
        after the hop, Anthropic must receive its OWN model id.
        """
        from apps.llm.pii_protected_provider import PIITokenizingProvider
        from apps.llm.router import QuotaFallbackProvider

        provider = LLMRouter().get_provider(None, skill="intent", op="complete")

        # Presence first: this is the REAL chain, PII wrapper included —
        # if it silently degraded to a bare provider, the assertions
        # below would prove nothing about production.
        assert isinstance(provider, QuotaFallbackProvider)
        assert isinstance(provider._primary, PIITokenizingProvider)
        assert provider.default_completion_model == "gpt-4o-mini"

        result = await provider.complete(
            [{"role": "user", "content": "привет"}], model="gpt-4o-mini"
        )

        assert result.provider == "anthropic"
        assert _real_load_path["anthropic"].last_kwargs["model"] == "claude-sonnet-4-6", (
            "an OpenAI model id reaching api.anthropic.com is a 404 — the swap must fire "
            "through the PII wrapper, not just through bare stubs"
        )

    def test_pii_wrapper_satisfies_the_provider_protocol(self) -> None:
        """The contract hole itself, asserted directly.

        ``LLMProvider`` now requires ``default_completion_model``. The
        wrapper the router puts around every provider must satisfy that,
        or the quota fallback loses the only value it can retarget to.
        """
        from apps.llm.protocol import LLMProvider as ProviderProtocol

        loaded = LLMRouter()._load_provider("anthropic")

        assert isinstance(loaded, ProviderProtocol)
        assert loaded.default_completion_model == "claude-sonnet-4-6"
