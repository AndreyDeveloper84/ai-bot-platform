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

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from apps.identity.services.memory_deleter import (
    request_forget_all,
    soft_delete_green_entries,
)
from apps.identity.services.memory_key_policy import read_current_view, select_current_facts
from apps.identity.services.memory_reader import read_green_entries
from apps.persona.memory_surface import describe_green_content

logger = logging.getLogger(__name__)

# Assistant turn that asks for the «забудь всё» confirmation carries this exact
# closing line; the next turn detects the pending state by matching it in the
# prior assistant message (short-term history), so no extra state store is
# needed.
_FORGET_ALL_MARKER = "напиши одним словом: удалить"

# DRF-1370 — the promise was wider than the action. «Всё личное Ayla забудет»
# covers, to the person reading it, the four notification toggles and the
# birthday on their profile screen. Nothing in the forget-all path touched
# either — and nothing should: those are standing instructions and a form the
# person maintains themselves, not things Ayla remembers about them behind
# their back. Erasing them would also flip notify_reminders / notify_retention
# / notify_birthday back to their `True` defaults, so «забудь всё» would switch
# ON the nudges someone had deliberately switched off.
#
# So the promise was narrowed to the action rather than the action widened to
# the promise: these two sentences now name what stays and where to change it.
# What is genuinely erased is the bot-side memory (tombstoned by
# apps.identity.services.forget_all_sweep) and the Ayla-side declared profile.
# What still stands is now visible: the export carries the preferences and
# declares its own composition (apps.identity.export_coverage).
FORGET_ALL_PROMPT = (
    "Это серьёзный шаг: я забуду всё, что запомнила о тебе из наших разговоров, "
    "и анкету предпочтений — вернуть будет нельзя.\n"
    "Останутся бронирования и оплаты (это по закону) и настройки уведомлений "
    "с датой рождения — ими ты управляешь сам на экране профиля.\n"
    f"Чтобы подтвердить — {_FORGET_ALL_MARKER}"
)
_FORGET_ALL_DONE = (
    "Готово — я забыла всё, что о тебе помнила. Настройки уведомлений и дата "
    "рождения остались на экране профиля: их меняешь ты, не я 🙂"
)

# DRF-1367: said when the bot-side memory is gone but Ayla — the owner of the
# declared profile (OD_MEMORY.md §1) — did not confirm the erasure. Claiming
# «я забыла всё» in that state is a false statement about a legal request, and
# the person disproves it on the next turn when the prompt still names their
# budget.
_FORGET_ALL_PARTIAL = (
    "Я забыла всё, что помнила сама, но до анкеты в профиле сейчас не достучалась — "
    "там ещё остались твои предпочтения. Напиши «забудь всё» ещё раз чуть позже, "
    "и я доведу до конца."
)

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
    "что ты про меня помнишь",
    "что ты обо мне запомнила",
    "какие данные обо мне",
    "что ты запомнила",
    "покажи мою память",
)

# «forget everything» — checked BEFORE «forget {field}» so «всё» is not treated
# as a field name. The negative lookahead keeps «забудь всё ПРО моё питание»
# OUT of forget-all: that is a DOMAIN forget (DRF-1261 proof step 4 — removes
# the diet domain only, never the whole memory). «удали/сотри меня» is
# deliberately EXCLUDED: it reads as account/newsletter removal, not memory
# reset — «forget me» is only honoured via the memory verb «забудь меня».
_FORGET_ALL_RE = re.compile(
    r"\b(?:забудь\s+меня"
    r"|забудь\s+вс[её]\b(?!\s+(?:про|о|об)\b)"
    r"|удали\s+вс[её]\b(?!\s+(?:про|о|об)\b)"
    r"|сотри\s+(?:вс[её]\b(?!\s+(?:про|о|об)\b)|память))\b"
)

# «forget {field}» — a forget verb followed by a target that is not «всё/меня».
_FORGET_FIELD_RE = re.compile(
    r"\b(?:забудь|удали|сотри)\s+(?:про\s+|что\s+я\s+|мою?\s+|мои\s+)?(.+)"
)

# Demonstrative / non-field targets that must NOT be treated as a «forget {field}»
# command (avoids hijacking «забудь это», «удали меня из рассылки» → discovery).
_NON_FIELD_TARGETS = frozenset({"это", "этот", "эту", "эти", "то", "об этом", "про это"})

# Green-fact matchers for «забудь {X}»: (key, value) → keyword stems for a
# SPECIFIC fact («забудь, что я веган»), plus per-key DOMAIN stems («забудь
# всё про питание» removes the whole domain). Match if the target contains
# any stem.
_FACT_KEYWORDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("diet", "vegan"): ("веган",),
    ("diet", "vegetarian"): ("вегетар",),
    ("diet", "keto"): ("кето",),
    ("diet", "halal"): ("халял",),
    ("diet", "kosher"): ("кошер",),
}
_KEY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "diet": ("диет", "питани", "пищ", "еда", "ем "),
    "preferred_time_slots": ("времен", "удобное время"),
    "preferred_districts": ("район", "метро", "округ", "географ"),
    "price_range": ("бюджет", "цен", "стоимост", "деньг", "рубл"),
    "favorite_masters": ("мастер",),
}

# Human label per domain for the domain-forget acknowledgement — naming the
# DOMAIN, not a stored row (the first live row may be a superseded value and
# would mislabel what was forgotten).
_DOMAIN_LABELS: dict[str, str] = {
    "diet": "питание",
    "preferred_time_slots": "удобное время",
    "preferred_districts": "районы",
    "price_range": "бюджет",
    "favorite_masters": "любимых мастеров",
}


@dataclass(frozen=True)
class MemoryCommandResult:
    """A memory-command reply. ``action_type`` tags the assistant turn.

    ``action_data`` (DRF-1305) carries the channel-agnostic chip list in the
    flat ``{"buttons": [{label, callback}]}`` shape the MAX handler's
    ``_build_attachments`` reads. It exists because the owner ruling that
    produced this module -- «запоминать молча и показывать в списке» -- is a
    ruling about a list the person can ACT on, and until now the show reply
    was text the person had to know how to answer.
    """

    text: str
    action_type: str = ""
    action_data: dict | None = None


def _normalise(text: str) -> str:
    """Lowercase, strip, collapse spaces, fold ё→е for robust matching."""

    return re.sub(r"\s+", " ", (text or "").strip().lower().replace("ё", "е"))


def _fact_keywords(content: dict) -> tuple[str, ...]:
    """Value-specific stems only («забудь, что я веган» → the vegan row)."""

    key = content.get("key")
    value = content.get("value")
    if isinstance(key, str) and isinstance(value, str):
        return _FACT_KEYWORDS.get((key, value), ())
    return ()


def _entry_key(entry: Any) -> str | None:
    """The entry's memory key, or None when content carries no usable key."""

    content = entry.content if isinstance(entry.content, dict) else {}
    key = content.get("key")
    return key if isinstance(key, str) and key else None


def _summary_line(facts: list, declared_phrases: dict[str, str] | None = None) -> str:
    """Render «помню, что ты …» from an ALREADY conflict-resolved fact list.

    DRF-1262: callers must pass the CURRENT view (`read_current_view` /
    `select_current_facts`), never raw live rows. The write path keeps history
    — a changed fact lands as a new live row and the old one stays live — so
    raw rows render «ты веган; ты на кето» while the prompt, which does go
    through the key policy, sees exactly one of them. Showing the person
    something other than what the system uses is the defect.

    ``declared_phrases`` (memory_key → phrase) merges the Ayla-side declared
    prefs into the SAME list (silent-remember ruling 2026-08-23: the person
    must see the FULL active memory). A declared phrase is shown only when no
    local fact of that key was rendered — the bridged copy must not duplicate
    the local row.

    Accepts anything with a ``.content`` dict: `MemoryEntry` rows or
    `GreenFact` view items.
    """

    phrases: list[str] = []
    seen_keys: set[str] = set()
    for f in facts:
        phrase = describe_green_content(f.content)
        if phrase:
            phrases.append(phrase)
            key = f.content.get("key")
            if isinstance(key, str):
                seen_keys.add(key)
    for key, phrase in (declared_phrases or {}).items():
        if key not in seen_keys and phrase not in phrases:
            phrases.append(phrase)
    if not phrases:
        return "Я пока ничего о тебе не запомнила."
    return "Помню, что ты " + "; ".join(phrases) + "."


#: Cap on «Забыть: X» chips. Past three the list stops being a list the
#: person reads and becomes a wall of buttons they scroll past.
MAX_FORGET_CHIPS = 3


def _user_id_of(bot_user) -> uuid.UUID | None:
    """The Ayla canonical id memory is keyed on, or ``None``.

    Every memory read takes ``ayla_user_id``, NOT ``BotUser.id`` -- the two
    are different identifiers and confusing them reads another person's row.
    """

    raw = getattr(bot_user, "ayla_user_id", None)
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        # An unparseable id is «no memory identity», never a crash: this runs
        # after the turn's idempotency key is claimed.
        logger.warning("persona.memory_commands.bad_ayla_user_id")
        return None


def render_memory_summary(bot_user, *, user_id: uuid.UUID | None = None) -> str:
    """The «помню, что ты …» line, as a PUBLIC entry point (DRF-1305).

    The concierge tool (:func:`apps.orchestrator.personal_surface.render_memory`)
    calls this so the model-routed answer and the deterministic
    «покажи что знаешь» answer are the SAME sentence about the same person.
    Two renderers would have been two answers, and the person would have had
    no way to tell which one the system actually uses.

    Empty memory returns the honest line, never a placeholder fact.
    """

    resolved = user_id or _user_id_of(bot_user)
    if resolved is None:
        return _summary_line([], None)
    return _summary_line(
        read_current_view(resolved).green_facts,
        _declared_phrases_for_show(bot_user),
    )


def memory_show_chips(bot_user, *, user_id: uuid.UUID | None = None) -> list[dict[str, str]]:
    """«Забыть: {домен}» chips for the memory list -- at most three.

    Each callback is the literal text «забудь {домен}», which
    :func:`handle_memory_command` branch 3 already claims: the label is the
    domain word whose stem lives in ``_KEY_KEYWORDS``, so the tap performs a
    real domain forget, not a clarify prompt. The chip contract on this path
    is that a tap IS a typed message, so no new callback plumbing exists to
    drift out of sync with the matcher.

    Built from LOCAL fact keys only. An Ayla-side declared pref with no local
    row would give a chip whose tap lands on «мне пока нечего о тебе забывать»
    -- branch 3 reads ``read_green_entries`` and stops when it is empty -- and
    a chip that answers the wrong thing is worse than no chip.

    No «Забыть всё» chip: that command is deliberately two-step
    (policy §5.9 anti-misclick), and putting the first step one tap away is
    the misclick the two steps exist to prevent.

    No «Исправить» chip either: there is no correction COMMAND to put behind
    one. Correction here is implicit -- a new explicit statement supersedes
    the old fact through ``record_explicit_green_facts`` -- so the honest
    affordance is a sentence telling the person to say the new value, which
    :mod:`apps.orchestrator.personal_surface` prints instead.
    """

    resolved = user_id or _user_id_of(bot_user)
    if resolved is None:
        return []
    keys: list[str] = []
    for fact in read_current_view(resolved).green_facts:
        content = fact.content if isinstance(fact.content, dict) else {}
        key = content.get("key")
        if isinstance(key, str) and key in _DOMAIN_LABELS and key not in keys:
            keys.append(key)
    return [
        {"label": f"Забыть: {_DOMAIN_LABELS[key]}", "callback": f"забудь {_DOMAIN_LABELS[key]}"}
        for key in keys[:MAX_FORGET_CHIPS]
    ]


def _declared_phrases_for_show(bot_user) -> dict[str, str]:
    """Best-effort Ayla declared prefs for the show merge; {} on any failure."""

    if bot_user is None:
        return {}
    try:
        from apps.identity.services.personal_context import (
            GateStatus,
            get_declared_prefs,
        )
        from apps.persona.memory_surface import describe_declared_prefs

        result = get_declared_prefs(bot_user)
        if result.status is not GateStatus.OK or result.context is None:
            return {}
        return describe_declared_prefs(result.context.context)
    except Exception:  # noqa: BLE001 — the show must never break the turn
        logger.exception("persona.memory_commands.declared_show_failed")
        return {}


def _bridge_clear(bot_user, memory_keys: list[str]) -> None:
    """Clear the Ayla declared fields for a DOMAIN forget. Best-effort.

    «Забудь про питание» — one domain, named on purpose. The whole-profile
    «забудь всё» does NOT come through here; see :func:`_bridge_erase`.
    """

    if bot_user is None:
        return
    try:
        from apps.orchestrator.memory.ayla_bridge import clear_declared_fields

        clear_declared_fields(bot_user, memory_keys)
    except Exception:  # noqa: BLE001 — forget must never break the turn
        logger.exception("persona.memory_commands.bridge_clear_failed")


def _bridge_erase(bot_user) -> bool:
    """«Забудь всё» → ask Ayla to erase the profile it owns. Erased?

    DRF-1367: the previous implementation walked ``_KEY_KEYWORDS`` and asked
    the bridge to clear the fields it knows how to write — three of the twelve
    on the row. The person heard «я забыла всё, что о тебе знала» and the very
    next prompt still carried their budget, favourite master, home and work
    districts, days off and minimum rating.

    ``False`` here means the upstream profile is still populated. The caller
    must not then claim everything was forgotten.
    """

    if bot_user is None:
        # Bot-local command (no channel user passed): there is no Ayla side to
        # erase and nothing upstream was ever written under this call path.
        return True
    try:
        from apps.orchestrator.memory.ayla_bridge import erase_declared_profile

        return erase_declared_profile(bot_user)
    except Exception:  # noqa: BLE001 — forget must never break the turn
        logger.exception("persona.memory_commands.bridge_erase_failed")
        return False


def handle_memory_command(
    *,
    user_id: uuid.UUID,
    text: str,
    last_assistant_text: str | None = None,
    bot_user=None,
) -> MemoryCommandResult | None:
    """Handle a memory command, or return None if `text` isn't one.

    Args:
      user_id: the canonical Ayla user id (memory key).
      text: the inbound user message.
      last_assistant_text: the previous assistant turn (for the «забудь всё»
        two-step confirmation).
      bot_user: the channel user — enables the Ayla-side half of the loop
        (declared-prefs merge into «покажи», declared-field clearing on
        «забудь»). Optional for backwards compatibility; without it the
        command works bot-locally only.
    """

    norm = _normalise(text)
    if not norm:
        return None

    # 1. «забудь всё» confirmation — the single word «удалить» right after we
    #    asked for it. Highest priority so it can't be mistaken for a field.
    if norm.rstrip(".!") == _CONFIRM_WORD:
        if last_assistant_text and _FORGET_ALL_MARKER in _normalise(last_assistant_text):
            request_forget_all(user_id)
            # Forget must be REAL, not a mark (silent-remember ruling). Ayla
            # owns the declared profile (OD_MEMORY.md §1), so this asks the
            # owner to erase the whole row — one verb, no field list. Listing
            # fields is the DRF-1367 defect: it emptied three of twelve and
            # could not clear the price at all.
            erased = _bridge_erase(bot_user)
            if not erased:
                # The upstream profile survived. Saying «я забыла всё» here
                # would be a false statement about a 152-ФЗ erasure — and the
                # person would catch us out on the next turn, when the prompt
                # still names their budget.
                return MemoryCommandResult(text=_FORGET_ALL_PARTIAL)
            return MemoryCommandResult(text=_FORGET_ALL_DONE)
        return None  # bare «удалить» with no pending prompt → not a command

    # 2. «забудь всё» request → the confirmation prompt (does NOT delete yet).
    if _FORGET_ALL_RE.search(norm):
        return MemoryCommandResult(text=FORGET_ALL_PROMPT, action_type="memory_forget_all_prompt")

    # 3. «забудь {field}» → soft-delete the matching green fact(s).
    field_match = _FORGET_FIELD_RE.search(norm)
    if field_match:
        target = field_match.group(1).strip(" .!?,")
        # Demonstratives («забудь это») and account/subscription phrasings
        # («удали меня из рассылки») are not memory-field commands → let discovery
        # handle them instead of hijacking the turn with a clarify prompt.
        if not target or target in _NON_FIELD_TARGETS or target.startswith("меня"):
            return None
        # Matching runs over ALL live rows, NOT the current view: 152-ФЗ
        # erasure targets what is STORED, not what is surfaced, so «забудь,
        # что я веган» must still reach a row that a newer fact has already
        # displaced. Narrowing the matcher would also RESURRECT it — deleting
        # the winning row would put the superseded one back in front of the
        # person. Only what is SHOWN goes through the key policy.
        entries = read_green_entries(user_id)
        if not entries:
            return MemoryCommandResult(text="Мне пока нечего о тебе забывать.")

        # A SPECIFIC fact named («забудь, что я веган») → delete the rows whose
        # value-stem matches. A DOMAIN named («забудь всё про моё питание») →
        # delete EVERY live row of the matching key(s) — proof step 4: the
        # whole domain goes, history rows and other domains stay untouched.
        fact_matched = [e for e in entries if any(kw in target for kw in _fact_keywords(e.content))]
        if fact_matched:
            keys = sorted({k for e in fact_matched if (k := _entry_key(e)) is not None})
            soft_delete_green_entries(user_id, [e.id for e in fact_matched])
            _bridge_clear(bot_user, keys)
            label = describe_green_content(fact_matched[0].content) or "это"
            return MemoryCommandResult(text=f"Готово — забыла: {label}.")

        domain_keys = sorted(
            {
                k
                for e in entries
                if (k := _entry_key(e)) is not None
                and any(kw in target for kw in _KEY_KEYWORDS.get(k, ()))
            }
        )
        if len(domain_keys) == 1:
            doomed = [e for e in entries if _entry_key(e) == domain_keys[0]]
            soft_delete_green_entries(user_id, [e.id for e in doomed])
            _bridge_clear(bot_user, domain_keys)
            label = _DOMAIN_LABELS.get(domain_keys[0], domain_keys[0])
            return MemoryCommandResult(text=f"Готово — забыла всё, что знала: {label}.")

        # 0 or several domains → clarify by showing what's remembered (DRF-1262:
        # the current view, so the clarification itself is not a contradiction).
        return MemoryCommandResult(
            text="Не совсем поняла, что именно забыть. "
            + _summary_line(
                select_current_facts(entries),
                _declared_phrases_for_show(bot_user),
            )
        )

    # 4. «покажи что знаешь обо мне» → the FULL active memory, humanly:
    #    bot-local facts through the SAME conflict-resolving read the prompt
    #    uses (DRF-1262) + Ayla declared prefs merged in (silent-remember
    #    ruling 2026-08-23). No internal analytics, no proposals, no
    #    confidence scores — only what the person can correct or forget.
    if any(trigger in norm for trigger in _SHOW_TRIGGERS):
        # DRF-1305: the list the owner asked for is a list the person can act
        # on. The chips come from the same read as the text, so a domain named
        # in the sentence is a domain the button can really forget.
        chips = memory_show_chips(bot_user, user_id=user_id)
        return MemoryCommandResult(
            text=_summary_line(
                read_current_view(user_id).green_facts,
                _declared_phrases_for_show(bot_user),
            ),
            action_data={"buttons": chips} if chips else None,
        )

    return None
