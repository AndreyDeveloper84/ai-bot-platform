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

import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI, OpenAI

from apps.replay.provider_guard import BLOCKED, ProviderCallForbidden, forbid_provider_calls
from apps.replay.tests.test_live_path_gate import fake_redis, live_run  # noqa: F401

ROOT = pathlib.Path(__file__).resolve().parents[3]

#: Not a key. One constant, one pragma: a formatter that splits a call can
#: no longer separate the value from its allowlist marker.
FAKE_KEY = "sk-test-not-a-key"  # pragma: allowlist secret


def _bound_at_import_call() -> None:
    """Like intent_resolution: a client from a name bound at import time."""
    client = AsyncOpenAI(api_key=FAKE_KEY)
    asyncio.run(client.chat.completions.create(model="x", messages=[]))


class TestEveryRouteIsBlockedAndNamed:
    def test_a_name_bound_at_import_is_blocked_with_its_address(self):
        with forbid_provider_calls() as guard, pytest.raises(ProviderCallForbidden):
            _bound_at_import_call()

        assert [c.sdk_method for c in guard.calls] == ["AsyncCompletions.create"]
        assert guard.calls[0].caller.startswith("apps/replay/tests/test_provider_guard_2599.py:")

    @pytest.mark.django_db
    def test_a_deterministic_turn_records_no_provider_call(self, live_run):  # noqa: F811 — pytest fixture
        """Pair: the same guard, the live gate's own run, a red-flag input that
        our code answers itself — a reply is sent, and nothing is recorded.
        Against the bound-at-import call above, which IS recorded."""
        from apps.replay.tests.test_live_path_gate import ALL_FIXTURES, _fixture_expects_block

        fixture = next(f for f in ALL_FIXTURES if _fixture_expects_block(f))
        result = live_run(fixture)

        assert result.sent_count >= 1  # presence: the turn ran and answered
        assert result.provider_calls == []
        assert result.llm_called is False

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
            lambda: OpenAI(api_key=FAKE_KEY).chat.completions.create(model="x", messages=[]),
            lambda: asyncio.run(
                AsyncOpenAI(api_key=FAKE_KEY).embeddings.create(model="x", input="t")
            ),
            lambda: OpenAI(api_key=FAKE_KEY).audio.transcriptions.create(
                model="whisper-1", file=b""
            ),
            lambda: asyncio.run(
                AsyncAnthropic(api_key=FAKE_KEY).messages.create(
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

    @pytest.mark.parametrize("raise_inside", [False, True], ids=["clean-exit", "exception"])
    def test_every_patch_is_undone(self, raise_inside):
        import importlib

        classes = [getattr(importlib.import_module(m), c) for m, c in BLOCKED]
        before = [cls.__dict__["create"] for cls in classes]

        try:
            with forbid_provider_calls():
                assert all(cls.__dict__["create"] is not b for cls, b in zip(classes, before))
                if raise_inside:
                    raise RuntimeError("boom")
        except RuntimeError:
            pass

        assert [cls.__dict__["create"] for cls in classes] == before


#: SDK entry points that do NOT go through a blocked ``create`` (openai
#: ``Completions.parse`` and anthropic ``stream``/``parse`` call ``_post``
#: directly; ``responses`` / legacy ``completions`` are separate resources).
UNBLOCKED_ENTRY_POINTS = frozenset(
    {"responses", "with_raw_response", "with_streaming_response", "parse", "stream", "beta"}
)
FOREIGN_SDKS = frozenset(
    {
        "mistralai",
        "cohere",
        "groq",
        "together",
        "ollama",
        "litellm",
        "langchain",
        "langchain_openai",
        "langchain_anthropic",
        "llama_index",
        "vertexai",
        "google",
    }
)


def _sdk_census() -> tuple[set[str], list[str], list[str]]:
    known_sdks = {m.split(".")[0] for m, _ in BLOCKED}
    importers: set[str] = set()
    foreign: list[str] = []
    unblocked: list[str] = []
    for path in (ROOT / "apps").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in rel or "/migrations/" in rel:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        tops: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                tops.append(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                tops.extend(a.name.split(".")[0] for a in node.names)  # EVERY name
        if any(t in known_sdks for t in tops):
            importers.add(rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in UNBLOCKED_ENTRY_POINTS:
                    unblocked.append(f"{rel}:{node.lineno}: .{node.attr}")
                # legacy text completions: `.completions` NOT preceded by `.chat`
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "completions"
                    and not (isinstance(node.value, ast.Attribute) and node.value.attr == "chat")
                ):
                    unblocked.append(f"{rel}:{node.lineno}: .completions (legacy)")
        foreign.extend(f"{rel}: {t}" for t in tops if t in FOREIGN_SDKS)
    return importers, foreign, unblocked


def test_census_every_sdk_import_is_covered():
    """Every module that imports a provider SDK reaches it only through
    classes the guard blocks — counted by construction (AST).

    Red on: a foreign model SDK imported under apps/; an SDK entry point that
    bypasses the blocked ``create`` (``responses``, legacy ``completions``,
    ``with_raw_response``, ``parse``, ``stream``, ``beta``) used in a module
    that imports openai/anthropic.
    """
    importers, foreign, unblocked = _sdk_census()
    # Presence: the known importers are seen (the census is not blind).
    assert {
        "apps/llm/providers/openai_provider.py",
        "apps/llm/providers/anthropic_provider.py",
        "apps/orchestrator/llm/openai_provider.py",
        "apps/speech/providers/openai_stt.py",
    } <= importers, sorted(importers)
    assert foreign == [], foreign
    assert unblocked == [], unblocked


def test_the_census_sees_an_unblocked_entry_point():
    """Self-check: the census recognises the bypasses it claims to catch."""
    tree = ast.parse(
        "import os, openai\n"
        "client.responses.create()\n"
        "client.completions.create()\n"
        "client.chat.completions.create()\n"
    )
    found = [
        n.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute)
        and (
            n.attr in UNBLOCKED_ENTRY_POINTS
            or (
                n.attr == "completions"
                and not (isinstance(n.value, ast.Attribute) and n.value.attr == "chat")
            )
        )
    ]
    assert sorted(found) == ["completions", "responses"]
