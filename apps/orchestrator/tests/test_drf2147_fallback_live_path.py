"""DRF-2147 — the fallback hop on the LIVE concierge path, plus its metric.

``apps/llm/tests/test_fallback_unavailable_2147.py`` proves the hop at the
router. This file proves what the client and the operator see when it
happens on the path that actually serves the pilot:

* the primary vendor times out → the reply is the fallback vendor's
  answer, NOT the outage line (``outage=False``, no «Повторить» button);
* the turn metric row carries ``llm_provider`` = the vendor that answered
  and ``llm_fallback_from`` = the vendor that did not;
* both vendors down → the outage line, ``outage=True``, exactly as before.

The router is the real ``LLMRouter`` with only provider CONSTRUCTION
stubbed (``_StubLoadingRouter``), so the wrapper, its audit closure and
its page closure run for real.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from apps.llm.retry import RetriableLLMError
from apps.llm.router import reset_router_cache
from apps.llm.tests.test_vendor_quota_exhaustion import PLACEHOLDER_KEY, StubProvider, _router_with
from apps.orchestrator import concierge
from apps.orchestrator.concierge import generate_concierge_reply
from apps.orchestrator.llm.templates import get_fallback

TRACE_ID = str(uuid.uuid4())


def _timeout_exhausted() -> RetriableLLMError:
    klass = type("APITimeoutError", (Exception,), {})
    return RetriableLLMError(attempts=2, last_error=klass("timed out"))


def _bot_user_and_conversation() -> tuple[Any, Any]:
    from apps.conversations.services import resolve_active_global_conversation
    from apps.identity.services import resolve_or_create_global_bot_user

    bot_user = resolve_or_create_global_bot_user(
        channel="max",
        channel_user_id="drf2147-uid",
        chat_id="drf2147-chat",
    )
    return bot_user, resolve_active_global_conversation(bot_user)


def _metrics() -> list[Any]:
    from apps.observability.models import AIRequestMetric

    return list(AIRequestMetric.all_tenants.filter(request_id=uuid.UUID(TRACE_ID)))


@pytest.mark.django_db(transaction=True)
class TestFallbackOnTheLivePath:
    @pytest.fixture(autouse=True)
    def _anthropic_primary(self, settings: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
        settings.OPENAI_API_KEY = PLACEHOLDER_KEY
        settings.ANTHROPIC_API_KEY = PLACEHOLDER_KEY
        settings.LLM_PROVIDER = "anthropic"
        settings.SKILL_LLM_PROVIDER = {}
        settings.LLM_FALLBACK_ORDER = ["anthropic", "openai"]
        settings.LLM_QUOTA_FALLBACK_ENABLED = True
        settings.PII_TOKENIZER_ENABLED = False
        self.pages: list[dict[str, Any]] = []

        def _page(severity: str, title: str, body: str, *, dedup_key: str | None = None) -> bool:
            self.pages.append({"severity": severity, "title": title, "dedup_key": dedup_key})
            return True

        monkeypatch.setattr("apps.observability.alerting.page", _page)
        reset_router_cache()
        yield
        reset_router_cache()

    def _wire(self, monkeypatch: pytest.MonkeyPatch, stubs: dict[str, StubProvider]) -> None:
        router = _router_with(stubs)
        monkeypatch.setattr(concierge, "get_router", lambda: router)

    def test_client_gets_the_fallback_vendors_answer_not_the_outage_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stubs = {
            "anthropic": StubProvider("anthropic", raises=_timeout_exhausted()),
            "openai": StubProvider("openai"),
        }
        self._wire(monkeypatch, stubs)
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "привет", bot_user=bot_user, conversation=conversation, trace_id=TRACE_ID
        )

        assert reply.text == "answer from openai"
        assert reply.outage is False
        assert stubs["anthropic"].calls == 1
        assert stubs["openai"].calls == 1
        assert [p["title"] for p in self.pages] == ["llm fallback"]

    def test_metric_row_names_the_vendor_used_and_the_one_it_replaced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stubs = {
            "anthropic": StubProvider("anthropic", raises=_timeout_exhausted()),
            "openai": StubProvider("openai"),
        }
        self._wire(monkeypatch, stubs)
        bot_user, conversation = _bot_user_and_conversation()

        generate_concierge_reply(
            "привет", bot_user=bot_user, conversation=conversation, trace_id=TRACE_ID
        )

        rows = _metrics()
        assert len(rows) == 1
        assert rows[0].outcome == "success"
        assert rows[0].llm_provider == "openai"
        assert rows[0].llm_fallback_from == "anthropic"
        # The hop is a vendor swap, not the promise-without-tool retry —
        # that flag keeps its one meaning.
        assert rows[0].fallback_triggered is False

    def test_no_hop_leaves_fallback_from_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stubs = {"anthropic": StubProvider("anthropic"), "openai": StubProvider("openai")}
        self._wire(monkeypatch, stubs)
        bot_user, conversation = _bot_user_and_conversation()

        generate_concierge_reply(
            "привет", bot_user=bot_user, conversation=conversation, trace_id=TRACE_ID
        )

        rows = _metrics()
        assert len(rows) == 1
        assert rows[0].llm_provider == "anthropic"
        assert rows[0].llm_fallback_from == ""
        assert self.pages == []

    def test_both_down_is_the_outage_line_as_before(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stubs = {
            "anthropic": StubProvider("anthropic", raises=_timeout_exhausted()),
            "openai": StubProvider("openai", raises=_timeout_exhausted()),
        }
        self._wire(monkeypatch, stubs)
        bot_user, conversation = _bot_user_and_conversation()

        reply = generate_concierge_reply(
            "привет", bot_user=bot_user, conversation=conversation, trace_id=TRACE_ID
        )

        assert reply.outage is True
        assert reply.text == get_fallback("ru")
        assert stubs["openai"].calls == 1
        rows = _metrics()
        assert len(rows) == 1
        assert rows[0].outcome == "error"


def test_transcript_line_names_the_hop() -> None:
    """``dialog_transcript`` is the operator's after-the-fact view of a
    turn; a hop must be readable there without opening the audit log."""
    from types import SimpleNamespace

    from apps.conversations.management.commands.dialog_transcript import format_metric

    row = SimpleNamespace(
        llm_pass_index=1,
        skill_selected="concierge",
        llm_model="gpt-4o-mini",
        outcome="success",
        fallback_triggered=False,
        latency_total_ms=1200,
        llm_provider="openai",
        llm_fallback_from="anthropic",
    )

    assert "anthropic→openai" in format_metric(row)  # type: ignore[arg-type]
