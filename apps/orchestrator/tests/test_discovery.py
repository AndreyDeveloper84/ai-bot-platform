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


def test_build_discovery_prompt_injects_personal_context() -> None:
    msgs = discovery.build_discovery_prompt(
        "хочу маникюр", personal_context="Что ты уже знаешь: придерживается веганского питания."
    )
    assert "придерживается веганского питания" in msgs[0]["content"]


def test_build_discovery_prompt_without_personal_context_unchanged() -> None:
    msgs = discovery.build_discovery_prompt("хочу маникюр")
    # No surfacing block — the system message is only the base voice.
    assert "уже знаешь" not in msgs[0]["content"]


class _CapturingProvider(_FakeProvider):
    def __init__(self, *, text: str = "ok") -> None:
        super().__init__(text=text)
        self.messages: list[dict[str, str]] = []

    async def complete(self, messages, model: str = "", tools=None):  # noqa: ANN001
        self.messages = messages
        return await super().complete(messages, model=model, tools=tools)


def test_generate_discovery_reply_surfaces_personal_context(monkeypatch) -> None:
    from apps.identity.services.memory_reader import GreenFact, PersonalContextView

    provider = _CapturingProvider(text="Готова помочь!")
    monkeypatch.setattr(discovery, "get_router", lambda: _FakeRouter(provider))
    view = PersonalContextView(
        summary="Ищет маникюр",
        green_facts=[GreenFact(kind="lifestyle", content={"key": "diet", "value": "vegan"})],
    )
    discovery.generate_discovery_reply("привет", personal_context=view)
    system = provider.messages[0]["content"]
    assert "Ищет маникюр" in system
    assert "веганского питания" in system


def test_generate_discovery_reply_empty_context_no_block(monkeypatch) -> None:
    from apps.identity.services.memory_reader import PersonalContextView

    provider = _CapturingProvider(text="Готова помочь!")
    monkeypatch.setattr(discovery, "get_router", lambda: _FakeRouter(provider))
    discovery.generate_discovery_reply("привет", personal_context=PersonalContextView())
    assert "уже знаешь" not in provider.messages[0]["content"]


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
