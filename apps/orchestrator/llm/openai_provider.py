"""OpenAI provider wrapper (DRF-428 / D1).

Sole LLM provider in Sprint 1. Wrapped in the circuit breaker from
``apps.orchestrator.llm.breaker``. When the breaker is open the call is
REFUSED — :class:`LLMOutageError` is raised, no HTTP call is attempted
(latency <10ms).

**Why it raises instead of answering (DRF-1512).** Until this ticket the
open breaker returned ``LLMResponse(content=get_fallback(...),
is_fallback=True)`` — the «Извини, у меня сейчас короткий технический
сбой — отвечу через минуту» line, delivered as an ordinary, successful
completion. A dead upstream arrived at the caller wearing the shape of a
working one, carrying the one sentence in the product that promises the
bot will come back.

That promise is only true where a «Повторить» button is drawn under it,
and the flag that draws it is ``DiscoveryReply.outage`` (DRF-1489,
:mod:`apps.orchestrator.llm.templates`). Nothing in an ``LLMResponse``
can carry that flag, so every caller wired to a client reply would have
had to invent it — and the DRF-1489 measurement, which reads
``apps/orchestrator/concierge.py``, would have gone on reporting zero
because this module was never in its scope.

So this module now produces no human-facing text at all. It reports
the outage as a typed failure and leaves the wording — and the button —
to the layer that owns both. The exception carries ``outage = True`` for
exactly that hand-off, which is also what the production router does
(``apps.llm.router`` raises ``BreakerOpenError``); the two providers now
fail the same way.

Shape designed to extract into an ``LLMProvider`` Protocol when Sprint
6 multi-provider routing arrives — ``complete()`` is the method name
that protocol will require.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings

from apps.audit.services import write_audit
from apps.llm.model_tiers import resolve_model
from apps.orchestrator.llm.breaker import BreakerOpenError, with_circuit_breaker

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Provider-agnostic response shape.

    Sprint 1 has just OpenAI; Sprint 6 generalises this into the
    ``LLMProvider`` Protocol's return type and adds tool-call fields.

    ``is_fallback`` is read by ``apps.orchestrator.intent_router``'s
    legacy path. Since DRF-1512 this provider never sets it: a degraded
    turn leaves here as :class:`LLMOutageError`, never as a response.
    An ``LLMResponse`` from this module is always an answer the model
    actually gave.
    """

    content: str
    model: str
    is_fallback: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


_BREAKER_NAME = "openai.complete"


class LLMOutageError(BreakerOpenError):
    """The call was refused because the circuit breaker is open (DRF-1512).

    A subclass of :class:`~apps.orchestrator.llm.breaker.BreakerOpenError`
    so a caller that already catches the breaker's own refusal keeps
    catching this one, and so ``except BreakerOpenError`` remains the
    single vocabulary for «the provider would not even try».

    ``outage`` is the whole point of the type. It is the same fact
    ``DiscoveryReply.outage`` carries one layer up: the turn produced no
    answer at all, the only remedy is the same message sent again, and a
    text that offers that remedy («отвечу через минуту», «Попробовать ещё
    раз?») may be shown ONLY together with the button that performs it.
    A caller that turns this failure into a client reply has the fact in
    its hands and cannot serve the promise without it.
    """

    #: This failure IS an outage — see the class docstring. Read by
    #: callers that map provider failures onto ``DiscoveryReply.outage``.
    outage = True

    def __init__(self, *, model: str, reason: str = "breaker_open") -> None:
        self.model = model
        self.reason = reason
        super().__init__(
            f"OpenAI call refused: circuit breaker {_BREAKER_NAME!r} is open "
            f"(model={model}, reason={reason}). No completion was produced.",
        )


class OpenAIProvider:
    """Async OpenAI ChatCompletion wrapper with circuit breaker.

    Usage:

        provider = OpenAIProvider()
        response = await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o-mini",
        )

    On breaker-open (5 failures in 60s by default): raises
    :class:`LLMOutageError` immediately without making an HTTP call, and
    writes an audit row for the refusal. It does NOT return a reply —
    see the module docstring (DRF-1512).
    """

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "gpt-4o-mini",
    ):
        self.api_key = api_key or getattr(settings, "OPENAI_API_KEY", "")
        self.default_model = default_model

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Run an OpenAI chat completion through the circuit breaker.

        Returns ``LLMResponse`` — an answer the model actually gave.

        Raises:
          LLMOutageError: the breaker is open; nothing was attempted and
            nothing is returned. ``.outage`` is True.
        """

        # DRF-1443 — this wrapper is OpenAI-only by construction, so a
        # vendor id needs no translation here. It still runs the shared
        # resolver for one reason: the vendor-neutral tier names are now
        # the vocabulary of ``apps.orchestrator.intent_router``, and that
        # module reaches BOTH the router-resolved provider and this one.
        # Without this line a legacy-path classify would post the literal
        # string "fast" to OpenAI and 400.
        chosen_model = resolve_model(
            model,
            vendor="openai",
            fast=self.default_model,
            smart=self.default_model,
        )
        try:
            return await with_circuit_breaker(
                _BREAKER_NAME,
                self._call_openai,
                messages,
                chosen_model,
                **kwargs,
            )
        except BreakerOpenError as exc:
            # Short-circuit: the breaker is open. Log and journal the
            # refusal, then hand it upstream as a failure. write_audit is
            # sync (Django ORM), so wrap with sync_to_async — we're
            # inside an async method.
            logger.warning("llm.openai.outage_refused model=%s reason=breaker_open", chosen_model)
            # thread_sensitive=False — Django's sync_to_async runs the
            # call on a fresh worker thread without an asyncio event loop,
            # which is what the Django ORM requires.
            #
            # DRF-1512 renamed this action from ``llm.openai.fallback_served``.
            # Nothing is served any more: the old name told a reader of the
            # journal that a person had been given a reply, which was the
            # defect itself written down.
            await sync_to_async(write_audit, thread_sensitive=False)(
                "llm.openai.outage_refused",
                target="OpenAIProvider",
                payload={"model": chosen_model, "reason": "breaker_open"},
            )
            raise LLMOutageError(model=chosen_model, reason="breaker_open") from exc

    def _build_client(self) -> Any:
        """Build the SDK client the way the production provider does.

        DRF-1436. Sprint 1 built ``AsyncOpenAI(api_key=...)`` and nothing
        else, and the Sprint 6 router that was meant to replace it never
        took this call site over. On the hosts this runs on that omission
        is not cosmetic:

        * ``api.openai.com`` refuses Russian addresses outright
          (``unsupported_country_region_territory``), so without
          ``OPENAI_PROXY`` the call cannot succeed at all;
        * without a timeout a stalled path — a proxy that accepts the TCP
          connection and then never completes ``CONNECT``, the 2026-08-13
          incident — hangs for the SDK default rather than failing, and a
          hang writes nothing to any journal.

        Both settings are read exactly as
        :meth:`apps.llm.providers.openai_provider.OpenAIProvider._get_client`
        reads them, so the two implementations cannot drift apart on the
        road they travel. ``max_retries=0`` for the same reason it is
        zero there: the circuit breaker around this call owns the retry
        and failure accounting, and a second, invisible SDK-level retry
        loop would multiply every outage by three.
        """

        # Local import — avoid loading openai client at module import
        # time (it pulls heavy deps; we don't always need it).
        from openai import AsyncOpenAI

        timeout = getattr(settings, "LLM_REQUEST_TIMEOUT_S", 30.0)
        client_kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "timeout": timeout,
            "max_retries": 0,
        }
        proxy = getattr(settings, "OPENAI_PROXY", "") or ""
        if proxy:
            import httpx

            # The proxy client carries the same timeout, or the bound we
            # just set on the SDK would stop at the SDK's own layer while
            # the transport underneath it hung on indefinitely.
            client_kwargs["http_client"] = httpx.AsyncClient(proxy=proxy, timeout=timeout)
        return AsyncOpenAI(**client_kwargs)

    async def _call_openai(
        self,
        messages: list[dict[str, Any]],
        model: str,
        **kwargs: Any,
    ) -> LLMResponse:
        """Actual OpenAI API call.

        Sprint 1 used the async OpenAI SDK directly and Sprint 6 was to
        swap this for a router. The router arrived
        (:mod:`apps.llm.router`) and took over the pipeline and the
        concierge, but not this wrapper, which the intent router still
        falls back to when it is handed no tenant. So it stays, and it
        travels the same road as the router's provider — see
        :meth:`_build_client`.
        """

        client = self._build_client()
        try:
            response = await client.chat.completions.create(
                messages=messages,  # type: ignore[arg-type]
                model=model,
                **kwargs,
            )
        except Exception as exc:
            # DRF-1436 — an LLM refusal must never be silent. The circuit
            # breaker logs only on state TRANSITIONS, so before this line
            # the opening failures of an outage produced no journal entry
            # at all: the owner learned of the outage from the alert in
            # the chat and there was nothing to corroborate it with.
            # WARNING, not exception: the breaker and the caller decide
            # severity; this line only guarantees the failure left a mark.
            from apps.llm.health import redact_secrets

            logger.warning(
                "orchestrator.llm.openai.call_failed model=%s exc=%s msg=%s",
                model,
                type(exc).__name__,
                redact_secrets(str(exc))[:300],
            )
            raise
        finally:
            await self._aclose_client(client)

        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            is_fallback=False,
            tokens_in=response.usage.prompt_tokens if response.usage else 0,
            tokens_out=response.usage.completion_tokens if response.usage else 0,
        )

    @staticmethod
    async def _aclose_client(client: Any) -> None:
        """Close the per-call SDK client and the httpx pool under it.

        The client is built per call (this provider is constructed per
        call by the intent router), so without this every classification
        leaked a connection pool. Best-effort: teardown must never turn a
        served answer into an error.
        """

        close = getattr(client, "close", None)
        if close is None:
            return
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 — teardown must never break the caller
            logger.warning("orchestrator.llm.openai.aclose_failed", exc_info=True)
