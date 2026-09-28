"""DRF-2599: the replay guard blocks and records EVERY model-provider call.

Pairs that must differ:

* a module that bound the SDK at import (``from openai import AsyncOpenAI``)
  is blocked and named — while the concierge canary, the regular stub, is not
  recorded at all. «A call raised» alone would pass on a broken stub too;
* a call inside ``except Exception: pass`` is still recorded — the callers of
  the model swallow errors, so the record is the evidence, not the raise.

The census test re-counts every SDK import site on each run: a new provider
module that the guard does not know about turns it red.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
from unittest.mock import MagicMock

import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI, OpenAI

from apps.replay.provider_guard import BLOCKED, ProviderCallForbidden, forbid_provider_calls

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _bound_at_import_call() -> None:
    """Like intent_resolution: a client from a name bound at import time."""
    client = AsyncOpenAI(api_key="sk-test-not-a-key")  # pragma: allowlist secret
    asyncio.run(client.chat.completions.create(model="x", messages=[]))


class TestEveryRouteIsBlockedAndNamed:
    def test_a_name_bound_at_import_is_blocked_with_its_address(self):
        with forbid_provider_calls() as guard, pytest.raises(ProviderCallForbidden):
            _bound_at_import_call()

        assert [c.sdk_method for c in guard.calls] == ["AsyncCompletions.create"]
        assert guard.calls[0].caller.startswith("apps/replay/tests/test_provider_guard_2599.py:")

    def test_the_regular_stub_is_not_recorded(self):
        """Pair: the canary concierge is our stub, not a provider — no record."""
        stub = MagicMock(return_value="canary")
        with forbid_provider_calls() as guard:
            stub("text")

        assert stub.called
        assert guard.calls == []

    def test_a_swallowed_call_is_still_recorded(self):
        with forbid_provider_calls() as guard:
            try:
                _bound_at_import_call()
            except Exception:  # noqa: BLE001 — the shape of every model caller
                pass

        assert len(guard.calls) == 1

    @pytest.mark.parametrize(
        "call",
        [
            lambda: OpenAI(api_key="sk-test").chat.completions.create(  # pragma: allowlist secret
                model="x", messages=[]
            ),
            lambda: asyncio.run(
                AsyncOpenAI(api_key="sk-test").embeddings.create(  # pragma: allowlist secret
                    model="x", input="t"
                )
            ),
            lambda: OpenAI(
                api_key="sk-test"
            ).audio.transcriptions.create(  # pragma: allowlist secret
                model="whisper-1", file=b""
            ),
            lambda: asyncio.run(
                AsyncAnthropic(api_key="sk-test").messages.create(  # pragma: allowlist secret
                    model="x", max_tokens=1, messages=[]
                )
            ),
        ],
        ids=["openai-sync-chat", "openai-async-embeddings", "openai-sync-audio", "anthropic-async"],
    )
    def test_each_sdk_route_is_blocked(self, call):
        with forbid_provider_calls() as guard, pytest.raises(ProviderCallForbidden):
            call()

        assert len(guard.calls) == 1

    def test_the_patch_is_undone_after_the_block(self):
        from openai.resources.chat.completions.completions import AsyncCompletions

        before = AsyncCompletions.__dict__["create"]
        with forbid_provider_calls():
            assert AsyncCompletions.__dict__["create"] is not before
        assert AsyncCompletions.__dict__["create"] is before


def test_census_every_sdk_import_is_covered():
    """Every module that imports a provider SDK uses classes the guard blocks.

    Counted by construction (AST), not by name. A new SDK (not openai /
    anthropic) imported under apps/ turns this red: the guard would not know it.
    """
    known_sdks = {m.split(".")[0] for m, _ in BLOCKED}
    importers: list[str] = []
    foreign: list[str] = []
    for path in (ROOT / "apps").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in rel or "/migrations/" in rel:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
            elif isinstance(node, ast.Import):
                top = node.names[0].name.split(".")[0]
            else:
                continue
            if top in known_sdks:
                importers.append(rel)
            elif top in {"mistralai", "cohere", "google", "groq", "together", "ollama"}:
                foreign.append(f"{rel}: {top}")
    # Presence: the known importers are seen (the census is not blind).
    assert "apps/orchestrator/llm/openai_provider.py" in importers
    assert "apps/speech/providers/openai_stt.py" in importers
    assert foreign == [], foreign
