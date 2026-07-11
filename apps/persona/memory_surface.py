"""Render remembered personal context into a system-prompt block (M-C1 / #1101).

Pure bot-layer surfacing: turns a
:class:`apps.identity.services.memory_reader.PersonalContextView` into a short
Russian system-prompt paragraph so the concierge can say «помню, что ты…».

Read/surfacing only — this module never writes and never touches yellow/red
(the reader already scopes to green + summary). Returns ``None`` when there is
nothing to surface, so the caller injects nothing and the happy-path prompt is
unchanged.
"""

from __future__ import annotations

from apps.identity.services.memory_reader import GreenFact, PersonalContextView

# Known green (key, value) → natural Russian phrase. The pilot writes `diet`
# (see the deterministic extractor); other keys fall back to a stored `display`
# string or are skipped, so an unrenderable fact is never surfaced as raw JSON.
_DIET_PHRASES = {
    "vegan": "придерживается веганского питания",
    "vegetarian": "придерживается вегетарианского питания",
}

_FACT_RENDERERS = {
    "diet": lambda value: _DIET_PHRASES.get(value),
}


def describe_green_content(content: dict) -> str | None:
    """Render a green fact's ``content`` dict to a natural phrase, or None.

    Shared by prompt surfacing (M-C1) and the chat memory commands (M-B4) so
    «помню, что ты…» reads identically wherever it appears. Known (key, value)
    → a fixed phrase; otherwise a writer-stored ``display`` string; else None
    (an unrenderable fact is never surfaced as raw JSON).
    """

    if not isinstance(content, dict):
        return None
    key = content.get("key")
    value = content.get("value")
    renderer = _FACT_RENDERERS.get(key) if isinstance(key, str) else None
    if renderer is not None and isinstance(value, str):
        phrase = renderer(value)
        if phrase:
            return phrase
    # Fallback: a writer may store a ready-made human display string.
    display = content.get("display")
    return display if isinstance(display, str) and display.strip() else None


def _render_fact(fact: GreenFact) -> str | None:
    """Render one green fact to a phrase, or None if it can't be phrased."""

    return describe_green_content(fact.content)


def render_personal_context(view: PersonalContextView) -> str | None:
    """Render `view` into a system-prompt paragraph, or None if nothing to surface."""

    if view.is_empty():
        return None

    parts: list[str] = []
    if view.summary:
        parts.append(view.summary.strip())
    for fact in view.green_facts:
        phrase = _render_fact(fact)
        if phrase:
            parts.append(phrase)

    if not parts:
        return None

    body = "; ".join(parts)
    return (
        "Что ты уже знаешь об этом клиенте (используй естественно и только когда "
        "уместно — например «помню, что ты…»; НЕ перечисляй списком и НЕ "
        f"придумывай ничего сверх этого): {body}."
    )
