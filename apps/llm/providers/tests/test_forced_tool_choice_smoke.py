"""DRF-1286 — live vendor proof that forced tool use actually forces.

Marked ``smoke``: excluded from the default suite, hits the REAL vendor
API with the REAL concierge prompt and tool specs. Run it with credentials
present::

    pytest -m smoke apps/llm/providers/tests/test_forced_tool_choice_smoke.py -v

Why this file exists as a test rather than a one-off script: the whole
point of DRF-1286's forced retry is that it must keep working after an
operator flips ``SKILL_LLM_PROVIDER`` — a decision made in config, not in
code review. The mocked tests prove our translation
(``"required"`` → ``{"type": "any"}`` on Anthropic); only a live call
proves the vendor honours it. Anything short of that is a reading, and a
reading is how this project previously concluded the Anthropic adapter
supported ``role="tool"`` when it does not.

Each test skips loudly when its key is absent — a skip is an honest
"unverified", never a green "works".
"""

from __future__ import annotations

import os

import pytest

from apps.llm.providers.anthropic_provider import AnthropicProvider
from apps.llm.providers.openai_provider import OpenAIProvider

pytestmark = pytest.mark.smoke


# A turn that a correctly-behaving concierge answers with a tool call, and
# that the observed failure answers with prose ("сейчас подберу...").
_USER_TURN = "покажи массажистов в пензе"

_TOOLS = [
    {
        "name": "show_masters",
        "description": "Показать клиенту подходящих мастеров по городу и услуге.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "Город поиска"},
                "specialization": {"type": "string", "description": "Услуга/специализация"},
            },
            "required": ["city"],
        },
    },
    {
        "name": "ask_clarification",
        "description": "Задать клиенту уточняющий вопрос с вариантами ответа.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["question"],
        },
    },
]

# Deliberately prose-friendly: this is the shape of prompt that produces
# the promise-without-tool answer in the first place.
_SYSTEM = (
    "Ты — Айла, тёплый AI-помощник маркетплейса красоты. Отвечай коротко и "
    "по-человечески. Помогаешь клиенту подобрать мастера."
)

_MESSAGES = [
    {"role": "system", "content": _SYSTEM},
    {"role": "user", "content": _USER_TURN},
]


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — forced tool use on Anthropic is UNVERIFIED here.",
)
async def test_anthropic_required_forces_a_tool_call() -> None:
    """`tool_choice="required"` must reach Anthropic as `{"type": "any"}`
    and come back as a real tool_use block."""
    provider = AnthropicProvider()
    result = await provider.complete(
        _MESSAGES,
        model="claude-haiku-4-5",
        tools=_TOOLS,
        max_tokens=512,
        tool_choice="required",
    )
    assert result.tool_calls, (
        "Anthropic returned no tool_call under tool_choice=required. If this "
        "fails reproducibly, forced tool use is NOT available on this "
        "provider and DRF-1286 must degrade to a single attempt here — see "
        "the issue's third constraint."
    )
    assert result.tool_calls[0].name in {"show_masters", "ask_clarification"}


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set — forced tool use on OpenAI is UNVERIFIED here.",
)
async def test_openai_required_forces_a_tool_call() -> None:
    provider = OpenAIProvider()
    result = await provider.complete(
        _MESSAGES,
        model="gpt-4o-mini",
        tools=_TOOLS,
        tool_choice="required",
    )
    assert result.tool_calls, "OpenAI returned no tool_call under tool_choice=required."
    assert result.tool_calls[0].name in {"show_masters", "ask_clarification"}
