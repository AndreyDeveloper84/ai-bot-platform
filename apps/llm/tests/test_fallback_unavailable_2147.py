"""DRF-2147 — the router's one hop fires on UNAVAILABILITY, not only on quota.

Incident 2026-09-15: the Anthropic proxy was down for 45 minutes. Every
turn went primary → retry → ``RetriableLLMError`` → the static «сейчас не
могу ответить» line, while OpenAI (behind a different proxy) was healthy
the whole time. The hop existed — but only for
:class:`LLMProviderQuotaExceeded`.

What this file pins, guard by guard (the leaf's list, verbatim):

* Anthropic timeout → the answer comes from OpenAI, the client does not
  get the stub, operators get exactly ONE page;
* both vendors down → ``RetriableLLMError`` propagates as before, so the
  pipeline serves its stub + «retry exhausted» telemetry unchanged;
* a 400 from Anthropic → NO hop (a bad request is ours, not theirs);
* quota → hop (the positive pair that already existed, kept);
* the deliberately-false input: a fallback loaded WITHOUT the PII
  wrapper ships raw personal data — the assertion that catches it is
  shown to go red, then the real chain is shown to pass it;
* the concierge tool specs serialise for the fallback vendor and a tool
  call made THROUGH the fallback parses back.

The stubs and the stub-loading router come from
``test_vendor_quota_exhaustion`` — same harness, so a change to the
router's contract breaks both files the same way.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.core.cache import cache

from apps.llm.protocol import (
    CompletionResult,
    LLMError,
    LLMProviderQuotaExceeded,
    LLMQuotaError,
    LLMTransportError,
    LLMVendorCreditsExhausted,
)
from apps.llm.retry import RetriableLLMError
from apps.llm.router import reset_router_cache
from apps.llm.tests.test_vendor_quota_exhaustion import (
    PLACEHOLDER_KEY,
    StubProvider,
    _router_with,
)

# ---------------------------------------------------------------------------
# Failure shapes, named exactly as the SDKs name them
# ---------------------------------------------------------------------------


def _sdk_exc(name: str, *, status: int | None = None) -> Exception:
    """An exception whose CLASS NAME is what the SDK would raise.

    The provider layer and :mod:`apps.llm.retry` classify by class name
    (no hard dependency on ``anthropic``/``openai`` internals), so the
    double only needs the name — and, for the unified
    ``APIStatusError`` shape, a ``status_code``.
    """
    klass = type(name, (Exception,), {})
    exc = klass(f"{name} from the vendor")
    if status is not None:
        exc.status_code = status  # type: ignore[attr-defined]
    return exc


def _retry_exhausted(last: Exception, attempts: int = 2) -> RetriableLLMError:
    """What ``run_with_retry`` raises after the primary's retry budget is spent."""
    return RetriableLLMError(attempts=attempts, last_error=last)


def _timeout_exhausted() -> RetriableLLMError:
    return _retry_exhausted(_sdk_exc("APITimeoutError"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def anthropic_primary(settings: Any) -> Any:
    """The pilot's shape after the owner's word: Anthropic first, OpenAI second."""
    settings.OPENAI_API_KEY = PLACEHOLDER_KEY
    settings.ANTHROPIC_API_KEY = PLACEHOLDER_KEY
    settings.LLM_PROVIDER = "anthropic"
    settings.SKILL_LLM_PROVIDER = {}
    settings.LLM_FALLBACK_ORDER = ["anthropic", "openai"]
    settings.LLM_QUOTA_FALLBACK_ENABLED = True
    reset_router_cache()
    yield
    reset_router_cache()


@pytest.fixture
def pages(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record every ``alerting.page`` call instead of sending it."""
    sent: list[dict[str, Any]] = []

    def _page(severity: str, title: str, body: str, *, dedup_key: str | None = None) -> bool:
        sent.append(
            {"severity": severity, "title": title, "body": body, "dedup_key": dedup_key}
        )
        return True

    monkeypatch.setattr("apps.observability.alerting.page", _page)
    return sent


def _down_and_healthy(primary_raises: Exception) -> dict[str, StubProvider]:
    return {
        "anthropic": StubProvider("anthropic", raises=primary_raises),
        "openai": StubProvider("openai"),
    }


async def _ask(stubs: dict[str, StubProvider]) -> CompletionResult:
    provider = _router_with(stubs).get_provider(None, skill="concierge", op="complete")
    return await provider.complete([{"role": "user", "content": "привет"}], model="smart")


# ---------------------------------------------------------------------------
# Guard 1 — Anthropic timeout → answer from OpenAI, one page
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("anthropic_primary")
@pytest.mark.django_db(transaction=True)
class TestUnavailabilityHops:
    @pytest.mark.asyncio
    async def test_timeout_after_retries_hops_to_openai(self, pages: list[dict[str, Any]]) -> None:
        """THE incident, replayed: the primary's retry budget is spent on
        timeouts; the answer still arrives — from the other vendor."""
        stubs = _down_and_healthy(_timeout_exhausted())

        result = await _ask(stubs)

        assert result.text == "answer from openai"
        assert result.provider == "openai"
        assert stubs["anthropic"].calls == 1
        assert stubs["openai"].calls == 1

    @pytest.mark.asyncio
    async def test_the_hop_pages_operators_exactly_once(
        self, pages: list[dict[str, Any]]
    ) -> None:
        await _ask(_down_and_healthy(_timeout_exhausted()))

        assert len(pages) == 1
        assert pages[0]["severity"] == "warning"
        assert pages[0]["title"] == "llm fallback"
        assert "anthropic" in pages[0]["body"] and "openai" in pages[0]["body"]
        assert "APITimeoutError" in pages[0]["body"]
        # A stable key, so the alerting layer's 5-minute window collapses
        # a 45-minute outage into nine pages, not nine hundred.
        assert pages[0]["dedup_key"] == "llm_fallback:anthropic:openai"

    @pytest.mark.asyncio
    async def test_the_result_names_where_it_came_from(
        self, pages: list[dict[str, Any]]
    ) -> None:
        """The turn metric reads ``provider`` (used) and ``fallback_from``
        off the result — the hop has to leave both there."""
        result = await _ask(_down_and_healthy(_timeout_exhausted()))

        assert result.provider == "openai"
        assert result.fallback_from == "anthropic"

    @pytest.mark.asyncio
    async def test_no_hop_leaves_fallback_from_empty(self, pages: list[dict[str, Any]]) -> None:
        stubs = {"anthropic": StubProvider("anthropic"), "openai": StubProvider("openai")}

        result = await _ask(stubs)

        assert result.provider == "anthropic"
        assert result.fallback_from == ""
        assert pages == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(_retry_exhausted(_sdk_exc("APIConnectionError")), id="connection"),
            pytest.param(_retry_exhausted(_sdk_exc("InternalServerError")), id="5xx-class"),
            pytest.param(
                _retry_exhausted(_sdk_exc("APIStatusError", status=503)), id="5xx-status"
            ),
            pytest.param(LLMTransportError("anthropic.complete: APIConnectionError"), id="transport"),
            pytest.param(_sdk_exc("BreakerOpenError"), id="breaker-open"),
        ],
    )
    async def test_every_unavailability_shape_hops(
        self, failure: Exception, pages: list[dict[str, Any]]
    ) -> None:
        stubs = _down_and_healthy(failure)

        result = await _ask(stubs)

        assert result.provider == "openai"
        assert stubs["openai"].calls == 1
        assert len(pages) == 1

    @pytest.mark.asyncio
    async def test_hop_writes_an_unavailable_fallback_audit_row(
        self, pages: list[dict[str, Any]]
    ) -> None:
        from apps.audit.models import AuditLog
        from apps.llm.router import EVENT_QUOTA_FALLBACK_USED

        await _ask(_down_and_healthy(_timeout_exhausted()))

        rows = await sync_to_async(
            lambda: list(AuditLog.all_tenants.filter(action=EVENT_QUOTA_FALLBACK_USED)),
            thread_sensitive=False,
        )()
        assert len(rows) == 1
        payload = rows[0].payload
        assert payload["from_provider"] == "anthropic"
        assert payload["chosen_provider"] == "openai"
        assert payload["source"] == "unavailable_fallback"
        assert payload["kind"] == "unavailable"
        assert "APITimeoutError" in payload["reason"]


# ---------------------------------------------------------------------------
# Guard 2 — both down → as today
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("anthropic_primary")
@pytest.mark.django_db(transaction=True)
class TestBothUnavailable:
    @pytest.mark.asyncio
    async def test_retry_exhausted_propagates_when_the_fallback_is_down_too(
        self, pages: list[dict[str, Any]]
    ) -> None:
        """The pipeline's ``except RetriableLLMError`` — stub to the
        client, «retry exhausted» audit, DRF-2130 alert — keeps working
        unchanged, because the exception that reaches it is the same."""
        stubs = {
            "anthropic": StubProvider("anthropic", raises=_timeout_exhausted()),
            "openai": StubProvider(
                "openai", raises=_retry_exhausted(_sdk_exc("APIConnectionError"))
            ),
        }

        with pytest.raises(RetriableLLMError) as caught:
            await _ask(stubs)

        assert type(caught.value.last_error).__name__ == "APIConnectionError", (
            "the SECOND vendor's failure is what surfaces — the log must say "
            "both were tried"
        )
        assert stubs["anthropic"].calls == 1
        assert stubs["openai"].calls == 1
        # ONE hop, never a walk: nothing was tried a third time.
        assert len(pages) == 1

    @pytest.mark.asyncio
    async def test_no_second_vendor_means_no_hop_and_no_page(
        self, settings: Any, pages: list[dict[str, Any]]
    ) -> None:
        settings.OPENAI_API_KEY = ""
        reset_router_cache()
        stubs = _down_and_healthy(_timeout_exhausted())

        with pytest.raises(RetriableLLMError):
            await _ask(stubs)

        assert stubs["openai"].calls == 0
        assert pages == []


# ---------------------------------------------------------------------------
# Guard 3 — a 400 is not a hop
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("anthropic_primary")
@pytest.mark.django_db(transaction=True)
class TestBadRequestDoesNotHop:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "failure",
        [
            # What ``_reraise_as_llm_error`` produces for BadRequestError /
            # UnprocessableEntityError: the base class, nothing more specific.
            pytest.param(LLMError("anthropic.complete: BadRequestError: 400"), id="400"),
            pytest.param(
                LLMError("anthropic.complete: UnprocessableEntityError: 422"), id="422"
            ),
            # A vendor rate-limit that did NOT go through the retry layer:
            # «slow down», which is the retry layer's job, not a hop.
            pytest.param(LLMQuotaError("anthropic.complete: rate-limited"), id="429-direct"),
            pytest.param(ValueError("not an LLM error at all"), id="foreign"),
        ],
    )
    async def test_content_errors_stay_on_the_primary(
        self, failure: Exception, pages: list[dict[str, Any]]
    ) -> None:
        stubs = _down_and_healthy(failure)

        with pytest.raises(type(failure)):
            await _ask(stubs)

        assert stubs["openai"].calls == 0, "a bad request is ours — the other vendor gets it too"
        assert pages == []


# ---------------------------------------------------------------------------
# Guard 4 — quota still hops (the positive pair), and pages now
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("anthropic_primary")
@pytest.mark.django_db(transaction=True)
class TestQuotaStillHops:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(LLMVendorCreditsExhausted("anthropic: no credits"), id="vendor-credits"),
            pytest.param(LLMProviderQuotaExceeded("daily cap"), id="our-cap"),
        ],
    )
    async def test_quota_hops_and_pages(
        self, failure: Exception, pages: list[dict[str, Any]]
    ) -> None:
        stubs = _down_and_healthy(failure)

        result = await _ask(stubs)

        assert result.provider == "openai"
        assert result.fallback_from == "anthropic"
        assert len(pages) == 1
        assert type(failure).__name__ in pages[0]["body"]

    @pytest.mark.asyncio
    async def test_quota_audit_row_keeps_its_source(self, pages: list[dict[str, Any]]) -> None:
        """Panels filter on ``source="quota_fallback"``; a quota hop must
        still say so, and only an unavailability hop says otherwise."""
        from apps.audit.models import AuditLog
        from apps.llm.router import EVENT_QUOTA_FALLBACK_USED

        await _ask(_down_and_healthy(LLMVendorCreditsExhausted("anthropic: no credits")))

        rows = await sync_to_async(
            lambda: list(AuditLog.all_tenants.filter(action=EVENT_QUOTA_FALLBACK_USED)),
            thread_sensitive=False,
        )()
        assert len(rows) == 1
        assert rows[0].payload["source"] == "quota_fallback"
        assert rows[0].payload["kind"] == "quota"


# ---------------------------------------------------------------------------
# Guard 5 — the page is deduplicated by the alerting layer (5 min)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("anthropic_primary")
@pytest.mark.django_db(transaction=True)
class TestPageDedup:
    """Through the REAL ``alerting.page`` — the dedup lives there, and a
    recording double would prove nothing about it."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> Any:
        cache.clear()
        yield
        cache.clear()

    @pytest.mark.asyncio
    async def test_two_hops_inside_the_window_page_once(self) -> None:
        from apps.audit.models import AuditLog

        for _ in range(2):
            await _ask(_down_and_healthy(_timeout_exhausted()))

        def _count(action: str) -> int:
            return AuditLog.all_tenants.filter(
                action=action, payload__dedup_key="llm_fallback:anthropic:openai"
            ).count()

        paged = await sync_to_async(_count, thread_sensitive=False)("observability.alert.paged")
        deduped = await sync_to_async(_count, thread_sensitive=False)(
            "observability.alert.deduped"
        )
        assert (paged, deduped) == (1, 1)

    @pytest.mark.asyncio
    async def test_page_failure_never_costs_the_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*args: Any, **kwargs: Any) -> bool:
            raise RuntimeError("telegram is on fire")

        monkeypatch.setattr("apps.observability.alerting.page", _boom)

        result = await _ask(_down_and_healthy(_timeout_exhausted()))

        assert result.provider == "openai"


# ---------------------------------------------------------------------------
# Guard 6 — the fallback is PII-wrapped (the deliberately-false input)
# ---------------------------------------------------------------------------


_PHONE = "+7 999 123 45 67"


class _RecordingVendor:
    """A concrete-provider double that keeps the messages it was sent.

    Instantiated by ``_load_provider`` through the registry, so the PII
    wrap around it is the production one — or, in the false-input test,
    deliberately NOT.
    """

    def __init__(self, name: str, *, raises: Exception | None = None) -> None:
        self.name = name
        self.default_completion_model = f"{name}-smart"
        self.default_fast_model = f"{name}-fast"
        self._raises = raises
        self.seen: list[list[dict[str, Any]]] = []

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> CompletionResult:
        self.seen.append([dict(m) for m in messages])
        if self._raises is not None:
            raise self._raises
        return CompletionResult(text="ok", provider=self.name, model=str(kwargs.get("model")))

    async def embedding(self, text: str, **kwargs: Any) -> list[float]:
        return [0.0]


@pytest.mark.django_db(transaction=True)
class TestFallbackIsPIIProtected:
    @pytest.fixture(autouse=True)
    def _real_load_path(self, settings: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
        settings.OPENAI_API_KEY = PLACEHOLDER_KEY
        settings.ANTHROPIC_API_KEY = PLACEHOLDER_KEY
        settings.LLM_PROVIDER = "anthropic"
        settings.SKILL_LLM_PROVIDER = {}
        settings.LLM_FALLBACK_ORDER = ["anthropic", "openai"]
        settings.LLM_QUOTA_FALLBACK_ENABLED = True
        settings.PII_TOKENIZER_ENABLED = True

        from apps.llm.providers import anthropic_provider, openai_provider

        down = _RecordingVendor("anthropic", raises=_timeout_exhausted())
        healthy = _RecordingVendor("openai")
        monkeypatch.setattr(anthropic_provider, "AnthropicProvider", lambda: down)
        monkeypatch.setattr(openai_provider, "OpenAIProvider", lambda: healthy)
        monkeypatch.setattr("apps.observability.alerting.page", lambda *a, **k: True)

        reset_router_cache()
        yield {"anthropic": down, "openai": healthy}
        reset_router_cache()

    @pytest.fixture
    def fake_redis(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        from apps.llm import pii_tokenizer
        from apps.llm.tests.test_pii_tokenizer import _FakeRedis

        fake = _FakeRedis()
        monkeypatch.setattr(pii_tokenizer, "_redis_client", lambda: fake)
        pii_tokenizer._invalidate_script_cache()
        yield fake
        pii_tokenizer._invalidate_script_cache()

    @staticmethod
    def _assert_fallback_saw_no_raw_pii(vendor: _RecordingVendor) -> None:
        assert vendor.seen, "the fallback vendor was never called"
        content = vendor.seen[-1][0]["content"]
        assert _PHONE not in content, f"raw phone reached the fallback vendor: {content!r}"
        assert "<PHONE_" in content

    @pytest.mark.asyncio
    async def test_the_hop_carries_tokenized_pii_to_the_fallback(
        self, _real_load_path: dict[str, _RecordingVendor], fake_redis: Any
    ) -> None:
        from uuid import uuid4

        from apps.llm import pii_tokenizer
        from apps.llm.router import LLMRouter

        provider = LLMRouter().get_provider(None, skill="concierge", op="complete")
        with pii_tokenizer.pii_context(str(uuid4())):
            result = await provider.complete(
                [{"role": "user", "content": f"Звоните {_PHONE}"}], model="smart"
            )

        assert result.provider == "openai"
        self._assert_fallback_saw_no_raw_pii(_real_load_path["openai"])

    @pytest.mark.asyncio
    async def test_false_input_a_raw_fallback_is_caught(
        self, _real_load_path: dict[str, _RecordingVendor], fake_redis: Any
    ) -> None:
        """The same assertion, against a wrapper whose fallback loader
        hands out the BARE vendor. It must go red — otherwise the test
        above would pass on a hop that ships raw personal data."""
        from uuid import uuid4

        from apps.llm import pii_tokenizer
        from apps.llm.router import FallbackProvider, LLMRouter

        router = LLMRouter()
        wrapped_primary = router._load_provider("anthropic")

        async def _nothing(*args: Any, **kwargs: Any) -> None:
            return None

        # ``load`` bypasses ``_load_provider`` — the bare double is the
        # fallback. This is the mis-wiring the guard exists to catch.
        bare = FallbackProvider(
            primary=wrapped_primary,
            primary_name="anthropic",
            load=lambda name: _real_load_path[name],  # type: ignore[return-value]
            candidates=["openai"],
            audit=_nothing,
            page=_nothing,
        )
        with pii_tokenizer.pii_context(str(uuid4())):
            await bare.complete([{"role": "user", "content": f"Звоните {_PHONE}"}], model="smart")

        with pytest.raises(AssertionError):
            self._assert_fallback_saw_no_raw_pii(_real_load_path["openai"])


# ---------------------------------------------------------------------------
# Guard 7 — the concierge tools serialise for the fallback vendor
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestToolsThroughTheFallback:
    """``CONCIERGE_TOOL_SPECS`` are written in the canonical (OpenAI)
    shape; the Anthropic adapter converts. A hop sends the SAME specs to
    the other vendor — so both envelopes must be JSON-serialisable and a
    tool call made through the fallback must parse back."""

    def test_every_concierge_tool_serialises_for_both_vendors(self) -> None:
        from apps.llm.providers.anthropic_provider import _to_anthropic_tool
        from apps.orchestrator.concierge import CONCIERGE_TOOL_SPECS

        assert CONCIERGE_TOOL_SPECS
        for spec in CONCIERGE_TOOL_SPECS:
            openai_envelope = {"type": "function", "function": spec}
            json.dumps(openai_envelope)
            anthropic_envelope = _to_anthropic_tool(spec)
            json.dumps(anthropic_envelope)
            assert anthropic_envelope["name"] == spec["name"]
            assert anthropic_envelope["input_schema"] == spec["parameters"]

    @pytest.mark.asyncio
    async def test_tool_call_made_through_the_fallback_parses(
        self, settings: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Anthropic is down; the REAL ``OpenAIProvider`` (SDK client
        mocked) receives the concierge tools and answers with a tool call.
        The wrapper must hand back a parsed :class:`ToolCall`, not the
        vendor's raw shape."""
        from unittest.mock import AsyncMock, MagicMock

        from apps.llm.providers import anthropic_provider
        from apps.llm.providers.openai_provider import OpenAIProvider
        from apps.llm.providers.tests.test_openai_provider import (
            _make_completion_response,
            _tool_call,
        )
        from apps.llm.router import LLMRouter
        from apps.orchestrator.concierge import CONCIERGE_TOOL_SPECS

        settings.OPENAI_API_KEY = PLACEHOLDER_KEY
        settings.ANTHROPIC_API_KEY = PLACEHOLDER_KEY
        settings.LLM_PROVIDER = "anthropic"
        settings.SKILL_LLM_PROVIDER = {}
        settings.LLM_FALLBACK_ORDER = ["anthropic", "openai"]
        settings.LLM_QUOTA_FALLBACK_ENABLED = True
        settings.PII_TOKENIZER_ENABLED = False
        monkeypatch.setattr("apps.observability.alerting.page", lambda *a, **k: True)

        down = _RecordingVendor("anthropic", raises=_timeout_exhausted())
        monkeypatch.setattr(anthropic_provider, "AnthropicProvider", lambda: down)

        real_openai = OpenAIProvider(api_key=PLACEHOLDER_KEY)
        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(
            return_value=_make_completion_response(
                content="",
                tool_calls=[_tool_call("call_1", "show_masters", json.dumps({"city": "Пенза"}))],
                finish_reason="tool_calls",
            )
        )
        real_openai._client = fake_client  # type: ignore[attr-defined]
        monkeypatch.setattr(
            "apps.llm.providers.openai_provider.OpenAIProvider", lambda: real_openai
        )
        reset_router_cache()
        try:
            provider = LLMRouter().get_provider(None, skill="concierge", op="complete")
            result = await provider.complete(
                [{"role": "user", "content": "покажи мастеров в Пензе"}],
                model="smart",
                tools=list(CONCIERGE_TOOL_SPECS),
            )
        finally:
            reset_router_cache()

        assert result.provider == "openai"
        assert result.fallback_from == "anthropic"
        assert [tc.name for tc in result.tool_calls] == ["show_masters"]
        assert result.tool_calls[0].arguments == {"city": "Пенза"}

        sent = fake_client.chat.completions.create.await_args.kwargs
        assert sent["tools"] == [{"type": "function", "function": s} for s in CONCIERGE_TOOL_SPECS]
        json.dumps(sent["tools"])
        assert down.seen and down.seen[0][0]["content"] == "покажи мастеров в Пензе"
