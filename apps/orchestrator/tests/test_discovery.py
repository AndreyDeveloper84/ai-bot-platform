"""Tenant-less discovery reply generator (#1026 / EPIC #1014).

Engine-agnostic — the LLM router/provider is mocked, so these don't hit a live
LLM and don't need a DB.
"""

from __future__ import annotations


from apps.llm.protocol import CompletionResult, LLMTransportError
from apps.orchestrator import discovery


class _FakeProvider:
    default_completion_model = "fake-model"

    def __init__(self, *, text: str = "", err: Exception | None = None) -> None:
        self._text = text
        self._err = err

    async def complete(self, messages, model: str = "", tools=None):  # noqa: ANN001
        if self._err is not None:
            raise self._err
        return CompletionResult(text=self._text)


class _FakeRouter:
    def __init__(self, provider: _FakeProvider) -> None:
        self._provider = provider

    def get_provider(self, tenant=None, *, skill: str = "", op: str = "complete"):  # noqa: ANN001
        # Discovery must route tenant-less.
        assert tenant is None
        return self._provider


def test_build_discovery_prompt_structure() -> None:
    msgs = discovery.build_discovery_prompt(
        "хочу маникюр",
        history=[
            {"role": "user", "content": "привет"},
            {"role": "assistant", "content": "здравствуйте"},
        ],
    )
    assert msgs[0]["role"] == "system"
    assert "Ayla" in msgs[0]["content"]
    assert {"role": "user", "content": "привет"} in msgs
    assert msgs[-1] == {"role": "user", "content": "хочу маникюр"}


def test_generate_discovery_reply_returns_llm_text(monkeypatch) -> None:
    monkeypatch.setattr(
        discovery, "get_router", lambda: _FakeRouter(_FakeProvider(text="Готова помочь!"))
    )
    assert discovery.generate_discovery_reply("привет").text == "Готова помочь!"


def test_generate_discovery_reply_falls_back_on_llm_error(monkeypatch) -> None:
    monkeypatch.setattr(
        discovery,
        "get_router",
        lambda: _FakeRouter(_FakeProvider(err=LLMTransportError("provider down"))),
    )
    out = discovery.generate_discovery_reply("привет")
    assert out.text.strip() and out.action_data is None  # non-empty fallback, no raise


def test_generate_discovery_reply_falls_back_on_empty_completion(monkeypatch) -> None:
    monkeypatch.setattr(discovery, "get_router", lambda: _FakeRouter(_FakeProvider(text="   ")))
    out = discovery.generate_discovery_reply("привет")
    assert out.text.strip()
