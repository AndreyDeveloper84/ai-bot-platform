"""Chat-side 152-ФЗ memory commands (M-B4 / #1113).

Natural-language control of a user's own memory, per policy §8 + ADR-0011 §8
(subject rights). GREEN ONLY — yellow/red are never mentioned or touched here
(policy §8.1). Three commands:

1. **«покажи что знаешь обо мне»** → a green-only summary reply (§8.1).
2. **«забудь {X}»** → soft-delete the matching green fact; ambiguous → clarify
   (§8.2).
3. **«забудь всё»** → a **two-step in-chat confirmation** (anti-misclick, §5.9):
   the user must reply with the single word «удалить», which then records
   ``UPC.forget_all_requested_at``. Every operation is audited.

### Deviation from policy §8.3

Policy §8.3 says «забудь всё» in chat should *redirect to a settings UI* rather
than execute. The pilot is **chat-only** (MAX bot, no settings surface), so per
tech-lead direction the confirmation happens in-chat instead — preserving the
§5.9 anti-misclick guarantee (type «удалить») without a dead-end redirect. When
a settings UI ships, restore the §8.3 redirect.

This module is pure command logic; the caller (handler) gates interception on an
active memory identity (``ayla_user_id`` + PERSONAL_DATA consent), so with memory
dormant these commands never fire and the discovery happy-path is unchanged.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from apps.identity.services.memory_deleter import (
    request_forget_all,
    soft_delete_green_entries,
)
from apps.identity.services.memory_reader import read_green_entries
from apps.persona.memory_surface import describe_green_content

# Assistant turn that asks for the «забудь всё» confirmation carries this exact
# closing line; the next turn detects the pending state by matching it in the
# prior assistant message (short-term history), so no extra state store is
# needed.
_FORGET_ALL_MARKER = "напиши одним словом: удалить"

FORGET_ALL_PROMPT = (
    "Это серьёзный шаг: я забуду всё, что о тебе помню. Бронирования и оплаты "
    "останутся — это по закону, но всё личное Ayla забудет, и вернуть будет "
    f"нельзя.\nЧтобы подтвердить — {_FORGET_ALL_MARKER}"
)
_FORGET_ALL_DONE = "Готово — я забыла всё, что о тебе знала. Начнём с чистого листа 🙂"

_CONFIRM_WORD = "удалить"

# SHOW triggers (substring match on normalised text). Kept explicit for
# precision — a false SHOW hijacks a normal discovery turn.
_SHOW_TRIGGERS = (
    "покажи что знаешь",
    "покажи что ты знаешь",
    "что ты обо мне знаешь",
    "что ты знаешь обо мне",
    "что ты обо мне помнишь",
    "что ты помнишь обо мне",
    "что ты про меня знаешь",
    "какие данные обо мне",
    "что ты запомнила",
)

# «forget everything» — checked BEFORE «forget {field}» so «всё» is not treated
# as a field name. «удали/сотри меня» is deliberately EXCLUDED: it reads as
# account/newsletter removal, not memory reset — «forget me» is only honoured via
# the memory verb «забудь меня».
_FORGET_ALL_RE = re.compile(
    r"\b(?:забудь\s+(?:вс[её]|меня)|удали\s+вс[её]|сотри\s+(?:вс[её]|память))\b"
)

# «forget {field}» — a forget verb followed by a target that is not «всё/меня».
_FORGET_FIELD_RE = re.compile(
    r"\b(?:забудь|удали|сотри)\s+(?:про\s+|что\s+я\s+|мою?\s+|мои\s+)?(.+)"
)

# Demonstrative / non-field targets that must NOT be treated as a «forget {field}»
# command (avoids hijacking «забудь это», «удали меня из рассылки» → discovery).
_NON_FIELD_TARGETS = frozenset({"это", "этот", "эту", "эти", "то", "об этом", "про это"})

# Green-fact matchers for «забудь {X}»: (key, value) → keyword stems, plus
# per-key generic stems. Match if the target contains any stem.
_FACT_KEYWORDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("diet", "vegan"): ("веган",),
    ("diet", "vegetarian"): ("вегетар", "мяс"),
}
_KEY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "diet": ("диет", "питани", "еда", "ем "),
}


@dataclass(frozen=True)
class MemoryCommandResult:
    """A memory-command reply. ``action_type`` tags the assistant turn."""

    text: str
    action_type: str = ""


def _normalise(text: str) -> str:
    """Lowercase, strip, collapse spaces, fold ё→е for robust matching."""

    return re.sub(r"\s+", " ", (text or "").strip().lower().replace("ё", "е"))


def _entry_keywords(content: dict) -> tuple[str, ...]:
    key = content.get("key")
    value = content.get("value")
    kws: tuple[str, ...] = ()
    if isinstance(key, str) and isinstance(value, str):
        kws += _FACT_KEYWORDS.get((key, value), ())
    if isinstance(key, str):
        kws += _KEY_KEYWORDS.get(key, ())
    return kws


def _summary_line(entries: list) -> str:
    phrases = [p for e in entries if (p := describe_green_content(e.content))]
    if not phrases:
        return "Я пока ничего о тебе не запомнила."
    return "Помню, что ты " + "; ".join(phrases) + "."


def handle_memory_command(
    *,
    user_id: uuid.UUID,
    text: str,
    last_assistant_text: str | None = None,
) -> MemoryCommandResult | None:
    """Handle a memory command, or return None if `text` isn't one.

    Args:
      user_id: the canonical Ayla user id (memory key).
      text: the inbound user message.
      last_assistant_text: the previous assistant turn (for the «забудь всё»
        two-step confirmation).
    """

    norm = _normalise(text)
    if not norm:
        return None

    # 1. «забудь всё» confirmation — the single word «удалить» right after we
    #    asked for it. Highest priority so it can't be mistaken for a field.
    if norm.rstrip(".!") == _CONFIRM_WORD:
        if last_assistant_text and _FORGET_ALL_MARKER in _normalise(last_assistant_text):
            request_forget_all(user_id)
            return MemoryCommandResult(text=_FORGET_ALL_DONE)
        return None  # bare «удалить» with no pending prompt → not a command

    # 2. «забудь всё» request → the confirmation prompt (does NOT delete yet).
    if _FORGET_ALL_RE.search(norm):
        return MemoryCommandResult(text=FORGET_ALL_PROMPT, action_type="memory_forget_all_prompt")

    # 3. «забудь {field}» → soft-delete the matching green fact.
    field_match = _FORGET_FIELD_RE.search(norm)
    if field_match:
        target = field_match.group(1).strip(" .!?,")
        # Demonstratives («забудь это») and account/subscription phrasings
        # («удали меня из рассылки») are not memory-field commands → let discovery
        # handle them instead of hijacking the turn with a clarify prompt.
        if not target or target in _NON_FIELD_TARGETS or target.startswith("меня"):
            return None
        entries = read_green_entries(user_id)
        if not entries:
            return MemoryCommandResult(text="Мне пока нечего о тебе забывать.")
        matched = [e for e in entries if any(kw in target for kw in _entry_keywords(e.content))]
        if len(matched) == 1:
            label = describe_green_content(matched[0].content) or "это"
            soft_delete_green_entries(user_id, [matched[0].id])
            return MemoryCommandResult(text=f"Готово — забыла: {label}.")
        # 0 or >1 matches → clarify by showing what's remembered.
        return MemoryCommandResult(
            text="Не совсем поняла, что именно забыть. " + _summary_line(entries)
        )

    # 4. «покажи что знаешь обо мне» → green-only summary.
    if any(trigger in norm for trigger in _SHOW_TRIGGERS):
        return MemoryCommandResult(text=_summary_line(read_green_entries(user_id)))

    return None
