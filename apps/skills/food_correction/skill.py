"""Food-correction skill — prompts for the 3 correction fields, and remembers them.

Sprint 9 / P5 (DRF-822) shipped the **prompt** half: entered via the ``✏️
Уточнить`` button on a food_scanner (P1) recognition card, each of the three
callbacks (built by D2 ``correction_choice_keyboard``) turns into the matching
follow-up prompt:

* ``cb:food:correct:grams:{scan_id}``  → "Введи правильный вес в граммах"
* ``cb:food:correct:name:{scan_id}``   → "Напиши, что было — поправлю"
* ``cb:food:correct:macros:{scan_id}`` → "Какие БЖУ нужны?"

## What DRF-1454 adds — the answer stops falling on the floor

P5's own docstring named the two things missing from the apply path. One of them
has since landed: ``apps.conversations.services.write_skill_state`` is the
atomic skill-state write-back P5 was waiting on. So the person's answer no
longer «falls through to the AI Concierge» — this skill now claims the reply
turn, parses it, and hands it to
:mod:`apps.orchestrator.memory.food` to remember.

The other missing piece — an Ayla «update diary entry» endpoint — is still
missing, and this ticket deliberately does not fake it. The distinction the
memory module draws is the same one: we remember the **calibration** (this
person says this dish weighs 500 г), not the **diary event**. Applying the
correction to the logged meal remains the Phase-1 follow-up (DRF-825); what
changes today is that the correction is no longer forgotten the moment it is
typed.

## Why the free-text turn is claimed narrowly

While a correction is pending, this skill matches plain text — and it registers
above nutrition_anketa, food_clarify and faq, so a loose match here would
shadow them. Three guards keep it tight:

1. the pending state must exist (set on the same conversation by the callback
   turn) and be **fresh** (:data:`_PENDING_TTL_SECONDS`);
2. the text must match the *answer shape* of the pending field — a number for
   grams, ``Б/Ж/У`` for macros, a short name for name;
3. anything else falls through untouched to the skills below.

A message that is not an answer therefore reaches its normal handler, and the
pending state simply ages out.

## The sensitive perimeter

«Что не подошло» arrives on exactly this turn — the ✏️ prompt asks «что не
так?», and people answer «у меня непереносимость лактозы» as readily as «500 г».
Such an answer is special-category (or, for a plain exclusion, a channel to one)
and is **not** stored: :func:`apps.orchestrator.memory.food.note_refusal`
classifies it, counts it, and drops it. The reply says so rather than pretending
to remember — an honest «пока не храню» is the difference between a helper and a
form that quietly files your diagnosis.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

from apps.orchestrator.memory import food as food_memory
from apps.orchestrator.ui.keyboards import parse_callback
from apps.skills.base import SkillContext, SkillResult
from apps.skills.registry import register

logger = logging.getLogger(__name__)


# ─── prompts ──────────────────────────────────────────────────────────────


_PROMPTS: dict[str, str] = {
    "grams": ("Сколько на самом деле было в граммах? Напиши число, пересчитаю калории и БЖУ."),
    "name": ("Что было на фото? Напиши коротко — поправлю в дневнике."),
    "macros": (
        "Окей, ручной ввод. Напиши макросы через слэш: "
        "белки/жиры/углеводы (грамм). Например: 12/8/32"
    ),
}

# The «не переспрашиваю» half: when memory already holds this person's answer
# for this dish, the prompt states it and asks only about a change. Wording is
# gender-neutral on purpose — the bot does not know, and guessing reads worse
# than the plain form.
_KNOWN_PROMPTS: dict[str, str] = {
    "grams": "В прошлый раз для «{dish}» было {value} г. Оставляем? Если нет — напиши число.",
    "name": "В прошлый раз это было «{value}». Оставляем? Если нет — напиши, что было.",
    "macros": "В прошлый раз БЖУ были {value}. Оставляем? Если нет — напиши новые: 12/8/32.",
}

REMEMBERED_ACK: dict[str, str] = {
    "grams": "Запомнила: «{dish}» — {value} г. Про вес этого блюда больше не спрошу.",
    "name": "Запомнила: это «{value}». Больше не буду переспрашивать.",
    "macros": "Запомнила БЖУ: {value}. Больше не буду переспрашивать.",
}

# Stored nothing (no consent / no link / write failed). A soft ack, never a
# promise we did not keep.
NOT_REMEMBERED_ACK = "Поняла, учла."

# The sensitive perimeter, said out loud. Storing this needs the yellow/red
# consent flow that is not in the pilot — so we say what we do instead of
# implying memory we do not have.
REFUSAL_ACK = (
    "Поняла, сейчас учту. Запоминать такое пока не буду — это чувствительные "
    "данные, для них нужно отдельное согласие."
)


# ─── pending-answer state ─────────────────────────────────────────────────

# skill_state sub-key. The scanner writes «last card» under its own key; this
# one is the «waiting for a correction value» flag.
_STATE_KEY = "food_correction"
# How long an unanswered prompt keeps claiming plain text. Long enough for a
# person to type a number, short enough that a forgotten prompt never swallows
# an unrelated turn tomorrow.
_PENDING_TTL_SECONDS = 600

# Answer shapes — the *matching* gate (stricter than the parser, which only has
# to read a value once the turn is already ours). ``[^\d\n]`` rather than ``\D``
# throughout: ``\D`` matches a newline, so «Ок\n300» read as an answer about
# weight when it was two sentences, only one of which was.
_SHAPE_GRAMS = re.compile(r"^[^\d\n]{0,6}\d{1,4}\s*(?:г|гр|грамм\w*)?\.?$", re.IGNORECASE)
_SHAPE_MACROS = re.compile(
    r"^[^\d\n]{0,10}\d{1,4}\s*[/|]\s*[^\d\n]{0,3}\d{1,4}\s*[/|]\s*[^\d\n]{0,3}\d{1,4}[^\d\n]{0,10}$"
)

_SHAPES: dict[str, re.Pattern[str]] = {
    "grams": _SHAPE_GRAMS,
    "macros": _SHAPE_MACROS,
}

# ── the name answer, which has no shape of its own ────────────────────────
#
# «Что было на фото?» is answered with free text, so there is no pattern that
# separates a dish from a sentence. A permissive «any 2-40 chars without
# digits» let «что я ел сегодня» and «мой дневник» be stored as the name of a
# dish and printed back on every future card — and, worse, took those turns
# away from the diary handler and the concierge that own them.
#
# Three cheap constraints instead, all of which a dish name satisfies and a
# request does not: it is short, it is at most three words, and it does not
# open with the vocabulary of asking for something.
_SHAPE_NAME = re.compile(r"^(?=.*[^\W\d_]{2})[^\d\n?!]{2,30}$", re.UNICODE)
_MAX_NAME_WORDS = 3
_NOT_A_DISH_RE = re.compile(
    r"^\s*(?:что|чего|как|где|когда|почему|зачем|кто|какие|какой|сколько"
    r"|хочу|хотел\w*|можно|нужно|надо|покажи|дай|скажи|расскажи|помоги|давай"
    r"|забудь|запиши|запомни|отмени|перенеси|открой|пройти|начать|стоп"
    r"|спасибо|привет|здравствуй\w*|пока|ок|окей|да|нет|ага|угу"
    r"|мой|моя|мои|моё|мне|меня)\b",
    re.IGNORECASE,
)


def _deterministic_chip_texts() -> frozenset[str]:
    """Callback texts of the chips that MUST always execute (DRF-1302/DRF-1268).

    On this path a tap IS a typed message, so «💧 Записать стакан воды» arrives
    as the plain text «стакан воды» — which is also a perfectly good answer to
    «что было на фото?». The chip wins: a button that silently becomes a dish
    name is a button that stopped working, and the person has no way to tell.
    Read from ``personal_surface`` rather than copied, so a re-worded chip
    cannot drift out of this list.
    """

    try:
        from apps.orchestrator.personal_surface import CHIP_ANKETA, CHIP_DIARY, CHIP_WATER

        return frozenset(
            str(chip["callback"]).strip().lower() for chip in (CHIP_ANKETA, CHIP_DIARY, CHIP_WATER)
        )
    except Exception:  # noqa: BLE001 — a matcher must never break the turn
        logger.exception("food_correction.chip_texts_unavailable")
        return frozenset()


def _looks_like_dish(text: str) -> bool:
    """Is this plain text plausibly a dish name rather than a request?"""

    stripped = text.strip()
    if stripped.startswith("/"):  # a command, whoever owns it
        return False
    if stripped.lower() in _deterministic_chip_texts():
        return False
    return bool(
        _SHAPE_NAME.match(text)
        and len(text.split()) <= _MAX_NAME_WORDS
        and not _NOT_A_DISH_RE.match(text)
    )


def _state_of(conversation: Any) -> dict[str, Any]:
    raw = getattr(conversation, "skill_state", None)
    return raw if isinstance(raw, dict) else {}


def _skill_state(context: SkillContext) -> dict[str, Any]:
    return _state_of(context.conversation)


def has_pending_correction(conversation: Any) -> bool:
    """Is a fresh correction prompt still waiting for an answer on this conversation?

    The global dispatcher (:mod:`apps.orchestrator.nutrition_global`) asks this to
    decide whether a plain-text turn is structured — the same question it already
    asks about an in-flight anketa FSM. It is deliberately **freshness-aware**
    rather than a bare truthiness check on the sub-key: a prompt nobody ever
    answered would otherwise keep every later plain-text turn away from the
    concierge and the diary-request handler, forever.
    """

    return _pending_record(conversation) is not None


def _pending(context: SkillContext) -> dict[str, Any] | None:
    return _pending_record(context.conversation)


def _pending_record(conversation: Any) -> dict[str, Any] | None:
    """The live «waiting for a correction value» record, or ``None``.

    Stale records are treated as absent rather than deleted here: matching must
    stay side-effect free, and the next write to the sub-key overwrites it.
    """

    if not food_memory.scanner_memory_enabled():
        # The rollback switch, applied at the ONE place that decides whether a
        # plain-text turn is ours: off → no claim, no routing change, no write.
        # Exactly the pre-DRF-1454 skill.
        return None
    pending = _state_of(conversation).get(_STATE_KEY)
    if not isinstance(pending, dict):
        return None
    if pending.get("field") not in food_memory.CORRECTION_FIELDS:
        return None
    stamped = pending.get("at")
    if not isinstance(stamped, str):
        return None
    try:
        at = datetime.fromisoformat(stamped)
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - at > timedelta(seconds=_PENDING_TTL_SECONDS):
        return None
    return pending


def _write_state(context: SkillContext, value: dict[str, Any] | None) -> None:
    """Best-effort skill-state write. A failure must never break the turn.

    ``write_skill_state`` needs a tenant in scope and a persisted Conversation;
    neither holds on every path this skill can be dispatched from (the
    tenant-less discovery bot, unit tests with a stubbed conversation). Losing
    the state costs one re-ask, which is exactly the pre-DRF-1454 behaviour —
    raising would cost the reply.
    """

    try:
        from apps.conversations.services import write_skill_state

        write_skill_state(context.conversation, _STATE_KEY, value)
    except Exception:  # noqa: BLE001 — degraded memory beats a lost reply
        logger.debug(
            "food_correction.state_write_skipped conversation=%s",
            getattr(context.conversation, "id", None),
        )


def _dish_for(context: SkillContext, scan_id: str) -> str:
    """The dish this correction is about, from the scanner's last-card stash.

    Empty when the card was rendered before this feature shipped, on another
    conversation, or when the state write did not land — in which case there is
    nothing to key memory on and the skill degrades to its P5 behaviour.
    """

    from apps.skills.food_scanner.skill import LAST_CARD_STATE_KEY

    card = _skill_state(context).get(LAST_CARD_STATE_KEY)
    if not isinstance(card, dict):
        return ""
    if scan_id and card.get("scan_id") not in (None, "", scan_id):
        return ""
    dish = card.get("dish")
    return dish if isinstance(dish, str) else ""


# ─── skill ────────────────────────────────────────────────────────────────


@register
class FoodCorrectionSkill:
    """Three correction prompts + the answer capture that feeds scanner memory."""

    name: ClassVar[str] = "food_correction"

    def matches(self, context: SkillContext) -> bool:
        text = context.message_text.strip()
        if text.startswith("cb:food:correct:"):
            parsed = parse_callback(text)
            if parsed is None or parsed["action"] != "correct":
                return False
            ref = parsed.get("ref") or ""
            # ref shape is "{field}:{scan_id}" — split on the first colon.
            field, _, _scan_id = ref.partition(":")
            return field in _PROMPTS
        # Answer path — only while a fresh prompt is pending, and only when the
        # text has the shape of an answer to it. Anything else falls through.
        if not text or text.startswith("cb:"):
            return False
        pending = _pending(context)
        if pending is None:
            return False
        field = str(pending.get("field"))
        if food_memory.classify_refusal(text):
            return True
        if field == food_memory.FIELD_NAME:
            return _looks_like_dish(text)
        shape = _SHAPES.get(field)
        return bool(shape and shape.match(text))

    def handle(self, context: SkillContext) -> SkillResult:
        text = context.message_text.strip()
        if text.startswith("cb:food:correct:"):
            return self._handle_prompt(context, text)
        return self._handle_answer(context, text)

    # ─── callback → prompt ───────────────────────────────────────────────

    def _handle_prompt(self, context: SkillContext, text: str) -> SkillResult:
        parsed = parse_callback(text)
        if parsed is None:
            return SkillResult(reply_text="", should_send=False)

        ref = parsed.get("ref") or ""
        field, _, scan_id = ref.partition(":")
        if field not in _PROMPTS:
            # matches() guards this; defensive empty.
            return SkillResult(reply_text="", should_send=False)

        dish = _dish_for(context, scan_id)
        recall = (
            food_memory.recall_corrections(context.bot_user, dish=dish)
            if dish
            else food_memory.EMPTY_RECALL
        )
        remembered = recall.has(field)
        prompt = (
            _KNOWN_PROMPTS[field].format(dish=dish, value=_recalled(recall, field))
            if remembered
            else _PROMPTS[field]
        )

        _write_state(
            context,
            {
                "field": field,
                "scan_id": scan_id,
                "dish": dish,
                "at": datetime.now(timezone.utc).isoformat(),
            },
        )

        logger.info(
            "food_correction.prompted field=%s scan=%s remembered=%s conversation=%s",
            field,
            scan_id,
            remembered,
            getattr(context.conversation, "id", None),
        )
        return SkillResult(
            reply_text=prompt,
            action_type="food_correction_prompt",
            action_data={"field": field, "scan_id": scan_id, "remembered": remembered},
            meta={
                "reply_kind": (
                    f"food_correction_{field}_remembered"
                    if remembered
                    else f"food_correction_{field}"
                )
            },
        )

    # ─── free text → memory ──────────────────────────────────────────────

    def _handle_answer(self, context: SkillContext, text: str) -> SkillResult:
        pending = _pending(context)
        if pending is None:  # matches() guards this; defensive.
            return SkillResult(reply_text="", should_send=False)

        field = str(pending.get("field"))
        dish = pending.get("dish")
        dish = dish if isinstance(dish, str) else ""

        # Perimeter first: a refusal is never a correction value, and must not
        # reach the green write path even if it happens to parse.
        if food_memory.classify_refusal(text):
            _write_state(context, None)  # answered — the question is settled
            outcome = food_memory.note_refusal(context.bot_user, text=text)
            logger.info(
                "food_correction.answer field=%s outcome=%s conversation=%s",
                field,
                outcome.value,
                getattr(context.conversation, "id", None),
            )
            return SkillResult(
                reply_text=REFUSAL_ACK,
                meta={"reply_kind": "food_correction_refusal_not_stored"},
            )

        value = food_memory.parse_correction_value(field, text)
        if value is None:
            # «0» and «99999» have the shape of an answer but no readable value.
            # The pending record is REFRESHED rather than cleared: clearing it
            # here asked a question the skill would no longer be listening to —
            # the person's «300» on the next turn fell through to the concierge
            # and the correction was lost, which is the exact bug this ticket
            # exists to fix.
            _write_state(
                context,
                {
                    "field": field,
                    "scan_id": pending.get("scan_id", ""),
                    "dish": dish,
                    "at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return SkillResult(
                reply_text=_PROMPTS[field],
                meta={"reply_kind": f"food_correction_{field}_reask"},
            )

        _write_state(context, None)  # answered — the question is settled
        outcome = (
            food_memory.remember_correction(context.bot_user, dish=dish, field=field, value=value)
            if dish
            else food_memory.Outcome.UNPARSED
        )
        logger.info(
            "food_correction.answer field=%s outcome=%s conversation=%s",
            field,
            outcome.value,
            getattr(context.conversation, "id", None),
        )

        stored = outcome in (food_memory.Outcome.WRITTEN, food_memory.Outcome.DUPLICATE)
        reply = (
            REMEMBERED_ACK[field].format(dish=dish, value=value) if stored else NOT_REMEMBERED_ACK
        )
        return SkillResult(
            reply_text=reply,
            action_type="food_correction_recorded",
            action_data={"field": field, "value": value, "stored": stored},
            meta={
                "reply_kind": (
                    f"food_correction_{field}_remembered"
                    if stored
                    else f"food_correction_{field}_not_stored"
                )
            },
        )


def _recalled(recall: food_memory.FoodRecall, field: str) -> Any:
    if field == food_memory.FIELD_GRAMS:
        return recall.portion_g
    if field == food_memory.FIELD_NAME:
        return recall.dish_name
    return recall.macros
