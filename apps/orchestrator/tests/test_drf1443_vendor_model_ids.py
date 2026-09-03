"""The model id that reaches the vendor, on the live conversation path (DRF-1443).

### Why this file exists and why it does not call a provider directly

On 2026-09-01 the fix for the previous ticket was declared working on the
strength of a probe that called ``provider.complete(...)`` with **no
model argument**. The provider filled in its own default, the vendor
answered ``200``, and the probe went green — on a stack that was still
404'ing every real turn, because the real turn does pass a model. The
probe could not fail. It was measuring the wrong thing.

So every test here enters through the seam production enters through and
stops one layer short of the network:

* the concierge's :class:`~apps.orchestrator.concierge.RouterLLMClient`,
  called exactly the way ``ayla_ai_core.AIConcierge`` calls it — with
  ``ayla_ai_core.orchestrator.DEFAULT_MODEL_NAME``, imported from the
  library rather than retyped, so a bump that changes it changes this
  test too;
* :func:`apps.orchestrator.intent_resolution.resolve_intent`, the
  post-reply resolver, with the real ``INTENT_RESOLUTION_MODEL`` setting.

Everything between the call site and the SDK runs for real: the router's
three-tier resolution, ``PIITokenizingProvider``,
``QuotaFallbackProvider``, and the concrete provider's own model
resolution. Only ``_get_client`` is replaced, by a recorder that reports
the model id the vendor SDK was about to be handed.

### The three claims, and the pair that makes each falsifiable

1. **Anthropic primary** — the id reaching the SDK belongs to Anthropic.
2. **OpenAI primary** — the id reaching the SDK is the one production
   used before this ticket. This cannot be checked against the live
   vendor right now (the OpenAI balance is empty), which is exactly why
   it is asserted here: the switch has two sides and the owner intends
   to switch back.
3. **A wrong name still travels.** An operator who pins a model that
   does not exist must still get the vendor's ``404``. Without this
   claim, 1 and 2 are equally satisfied by a stack that throws the
   caller's ``model=`` away — and "it works" would again mean "the name
   is ignored".
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from ayla_ai_core.orchestrator import DEFAULT_MODEL_NAME as AI_CORE_DEFAULT_MODEL

from apps.llm.providers.anthropic_provider import AnthropicProvider
from apps.llm.providers.openai_provider import OpenAIProvider
from apps.llm.router import reset_router_cache

# Not a credential — a non-empty placeholder so `provider_is_configured`
# sees a key present. Deliberately not key-shaped: no vendor prefix, no
# entropy, no length that resembles one.
PLACEHOLDER_KEY = "unit-test-placeholder"


# ---------------------------------------------------------------------------
# Recorders — the last layer before the network
# ---------------------------------------------------------------------------


class ModelRecorder:
    """Captures the ``model`` each vendor SDK was about to be called with."""

    def __init__(self) -> None:
        self.anthropic: list[str] = []
        self.openai: list[str] = []

    @property
    def only(self) -> str:
        """The single recorded id, whichever vendor took the call."""
        seen = self.anthropic + self.openai
        assert len(seen) == 1, f"expected exactly one vendor call, recorded {seen!r}"
        return seen[0]


def _anthropic_response() -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = '{"intent": "SMALL_TALK"}'
    response = MagicMock()
    response.content = [block]
    response.model = "claude-recorded"
    response.stop_reason = "end_turn"
    usage = MagicMock()
    usage.input_tokens = 5
    usage.output_tokens = 5
    response.usage = usage
    return response


def _openai_response() -> MagicMock:
    message = MagicMock()
    message.content = '{"intent": "SMALL_TALK"}'
    message.tool_calls = None
    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "stop"
    response = MagicMock()
    response.choices = [choice]
    response.model = "gpt-recorded"
    usage = MagicMock()
    usage.prompt_tokens = 5
    usage.completion_tokens = 5
    response.usage = usage
    return response


@pytest.fixture
def recorder(monkeypatch, settings) -> Iterator[ModelRecorder]:
    """Both vendors reachable, both stubbed one layer above the socket."""
    rec = ModelRecorder()

    settings.ANTHROPIC_API_KEY = PLACEHOLDER_KEY
    settings.OPENAI_API_KEY = PLACEHOLDER_KEY
    settings.SKILL_LLM_PROVIDER = {}
    reset_router_cache()

    async def _anthropic_create(**kwargs: Any) -> MagicMock:
        rec.anthropic.append(kwargs["model"])
        return _anthropic_response()

    async def _openai_create(**kwargs: Any) -> MagicMock:
        rec.openai.append(kwargs["model"])
        return _openai_response()

    def _fake_anthropic_client(_self: AnthropicProvider) -> MagicMock:
        client = MagicMock()
        client.messages.create = AsyncMock(side_effect=_anthropic_create)
        return client

    def _fake_openai_client(_self: OpenAIProvider) -> MagicMock:
        client = MagicMock()
        client.chat.completions.create = AsyncMock(side_effect=_openai_create)
        return client

    monkeypatch.setattr(AnthropicProvider, "_get_client", _fake_anthropic_client)
    monkeypatch.setattr(OpenAIProvider, "_get_client", _fake_openai_client)

    yield rec

    reset_router_cache()


def _run_concierge_completion(model: str) -> None:
    """One concierge LLM call, entered the way ``AIConcierge`` enters it."""
    from apps.orchestrator.concierge import CONCIERGE_SKILL, RouterLLMClient

    client = RouterLLMClient(skill=CONCIERGE_SKILL)
    asyncio.run(
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "привет"}],
        )
    )


# ---------------------------------------------------------------------------
# Claim 1 + 2 — both sides of the LLM_PROVIDER switch
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConciergePathBothVendors:
    """The path the owner's own message travels."""

    def test_anthropic_primary_receives_an_anthropic_model(self, recorder, settings) -> None:
        """The outage, reproduced end to end and then fixed.

        Before DRF-1443 this recorded ``gpt-4o-mini`` — an OpenAI id
        posted to ``api.anthropic.com``, answered ``404 not_found_error``
        on every turn while the owner was typing into the pilot.
        """
        settings.LLM_PROVIDER = "anthropic"
        reset_router_cache()

        _run_concierge_completion(AI_CORE_DEFAULT_MODEL)

        assert recorder.anthropic == [recorder.only]
        assert recorder.only.startswith("claude-")

    def test_openai_primary_receives_the_model_production_already_used(
        self, recorder, settings
    ) -> None:
        """The other side of the switch.

        Unverifiable against the live vendor today — the OpenAI balance
        is empty — and therefore the half most in need of a test. The
        owner intends to switch back once he tops it up; this asserts he
        gets ``gpt-4o-mini`` and not a Claude id when he does.
        """
        settings.LLM_PROVIDER = "openai"
        reset_router_cache()

        _run_concierge_completion(AI_CORE_DEFAULT_MODEL)

        assert recorder.openai == [recorder.only]
        assert recorder.only == "gpt-4o-mini"

    def test_the_two_sides_disagree(self, recorder, settings) -> None:
        """The pair that makes the two tests above mean something.

        A stack that hard-coded one id would pass exactly one of them and
        a stack that ignored the vendor entirely could pass neither
        honestly. Asserting that the same call yields a DIFFERENT id per
        vendor is what proves the vendor is being consulted.
        """
        settings.LLM_PROVIDER = "anthropic"
        reset_router_cache()
        _run_concierge_completion(AI_CORE_DEFAULT_MODEL)
        under_anthropic = recorder.only

        recorder.anthropic.clear()
        recorder.openai.clear()

        settings.LLM_PROVIDER = "openai"
        reset_router_cache()
        _run_concierge_completion(AI_CORE_DEFAULT_MODEL)
        under_openai = recorder.only

        assert under_anthropic.startswith("claude-")
        assert under_openai.startswith("gpt-")
        assert under_anthropic != under_openai


# ---------------------------------------------------------------------------
# Claim 3 — the positive guard
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAWrongNameStillReachesTheVendor:
    """Mutation guard for everything above.

    Each test names a model the vendor will refuse and asserts it arrives
    UNCHANGED. If the stack ever starts repairing ids, these fail — and
    they must, because a stack that repairs ids makes the green in
    :class:`TestConciergePathBothVendors` unfalsifiable.
    """

    def test_a_bogus_anthropic_id_is_delivered_verbatim(self, recorder, settings) -> None:
        settings.LLM_PROVIDER = "anthropic"
        reset_router_cache()

        _run_concierge_completion("claude-does-not-exist-9")

        assert recorder.only == "claude-does-not-exist-9"

    def test_a_bogus_openai_id_is_delivered_verbatim(self, recorder, settings) -> None:
        settings.LLM_PROVIDER = "openai"
        reset_router_cache()

        _run_concierge_completion("gpt-4o-does-not-exist")

        assert recorder.only == "gpt-4o-does-not-exist"

    def test_an_operator_pin_survives_the_whole_stack(self, recorder, settings) -> None:
        """A real, existing, non-default model stays pinned.

        The counterpart to the two above: they prove nothing is repaired,
        this proves nothing is flattened to the tier default either.
        """
        settings.LLM_PROVIDER = "anthropic"
        reset_router_cache()

        _run_concierge_completion("claude-haiku-4-5")

        assert recorder.only == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# The post-reply resolver — the second in-repo hard-coded id
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIntentResolverModel:
    """``INTENT_RESOLUTION_MODEL`` — read by the resolver since DRF-1385,
    absent from ``config/settings/base.py`` until DRF-1443.

    The absence was the whole problem: the ``getattr`` fallback behind it
    was ``"gpt-4o-mini"``, so on the Anthropic pilot every resolver pass
    404'd and no environment variable existed that could stop it.
    """

    def _resolve(self) -> None:
        from apps.orchestrator.concierge import CONCIERGE_SKILL, RouterLLMClient
        from apps.orchestrator.intent_resolution import resolve_intent

        resolve_intent(
            "хочу маникюр",
            message_id="m1",
            trace_id="t1",
            llm_client=RouterLLMClient(skill=CONCIERGE_SKILL),
        )

    def test_default_resolves_to_the_called_vendors_cheap_model(self, recorder, settings) -> None:
        settings.LLM_PROVIDER = "anthropic"
        settings.INTENT_RESOLUTION_MODEL = "fast"
        reset_router_cache()

        self._resolve()

        assert recorder.only == "claude-haiku-4-5"

    def test_the_setting_is_now_reachable_and_is_obeyed(self, recorder, settings) -> None:
        """The override exists AND it is honoured, wrong value included.

        Pinning a nonexistent id and seeing it arrive is what proves the
        setting is wired to the vendor call rather than merely read.
        """
        settings.LLM_PROVIDER = "anthropic"
        settings.INTENT_RESOLUTION_MODEL = "claude-does-not-exist-9"
        reset_router_cache()

        self._resolve()

        assert recorder.only == "claude-does-not-exist-9"
