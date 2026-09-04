"""Food-scanner skill — photo → Ayla recognize → diary log.

Sprint 9 / P1 (DRF-818). Ports the **skill-level** path from
``legacy_maxbot/handlers/food_scanner.py`` (554 LOC); channel-specific
photo-byte download is not yet plumbed through the platform's skill
contract, so the Sprint 9 cut ships the callback half of the flow plus
a scan entry-point that takes bytes via a context-meta passthrough.

## Platform-side flow

1. **Photo turn** — channel adapter delivers an attachment-only turn
   (``context.has_attachments=True``, ``context.message_text==""``). The
   adapter stashes the raw bytes on the conversation as
   ``conversation.last_photo_bytes`` (channel-adapter convention; the
   web channel adapter introduced this Phase 1 / DRF-850). When bytes
   are absent the skill returns a graceful "не получилось скачать фото"
   message rather than crashing.

2. **Scan call** — :func:`apps.integrations.ayla.NutritionClient.scan_photo`
   with the bytes. Result includes ``scan_id`` (used by the callbacks
   below to reference this dish).

3. **Recognition card** — text body with dish + macros + a
   :func:`apps.orchestrator.ui.keyboards.food_recognition_keyboard`
   3-button row (✅ В дневник / ✏️ Уточнить / ❌ Не то).

4. **Callback turn** — channel adapter routes ``cb:food:to_diary:{id}``
   / ``cb:food:clarify:{id}`` / ``cb:food:reject:{id}`` to the dispatcher,
   which lands them here. Each callback writes-or-skips through Ayla:

   * ``to_diary`` → :func:`NutritionClient.log_meal` (idempotent on
     ``scan_id``) + confirmation reply.
   * ``clarify`` → reply prompts the user to type the correction; the
     P5 food_correction skill takes over on the next turn.
   * ``reject`` → silent ack.

## Memory (DRF-1454)

Until this ticket the scanner had none: every photo started from a blank slate,
so it re-asked what the person had already corrected. Three hooks now run on the
photo/callback paths, all best-effort and all through
:mod:`apps.orchestrator.memory.food`, which owns the zone decision:

* before rendering a card — ``recall_corrections`` adds at most one line with
  the name this person gave this dish (🟢 green, ``explicit`` only). Only the
  name: weight and macros belong to Ayla's diary and are not kept by the bot
  (owner decision 2026-09-04, variant А — see ``food_memory.REMEMBERED_FIELDS``);
* on every recognised dish — ``note_meal`` declares meal history 🟡 yellow and
  refuses to store it: the diary belongs to Ayla behind the HEALTH consent, and
  a second copy here would be the same profile on a weaker basis;
* on ``reject`` — ``note_recognition_rejected`` records «мы распознали не то» as
  a quality signal, never as «он это не ест».

The dish behind a card is stashed in ``Conversation.skill_state`` under
:data:`LAST_CARD_STATE_KEY` so the correction callback — which carries only a
``scan_id`` — can key memory on it.

## Scope cut (vs mysite source)

Skipped for Sprint 9 — folded into P5 or Phase 1:

* Consent flow (``food_scanner_consent_at`` BotUser field). The mysite
  version asked first-time scanners for opt-in; we assume the channel
  adapter handles consent uplink in Phase 1.
* Meal-type buttons (``Завтрак|Обед|Ужин|Перекус``). The mysite
  ``on_log_meal`` callback took ``cb:nutrition:log:{scan_id}:{meal_type}``;
  here we log without meal type (Ayla defaults to "other"). Adding
  meal-type buttons is a 1-line keyboard extension.
* Evening-inline daily report trigger. Belongs in P3 nutrition_anketa
  context or a separate notification job.
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

from apps.integrations.ayla import (
    FoodNotRecognizedError,
    NutritionAPIError,
    NutritionUnavailableError,
    external_user_id_for,
    get_nutrition_client,
)
from apps.orchestrator.memory import food as food_memory
from apps.orchestrator.ui.keyboards import (
    food_recognition_keyboard,
    parse_callback,
)
from apps.skills.base import SkillContext, SkillResult
from apps.skills.registry import register

logger = logging.getLogger(__name__)

# ``Conversation.skill_state`` sub-key holding the card we last rendered
# (DRF-1454). Dialogue state, not memory: the callbacks that follow a card
# carry only a ``scan_id``, and memory is keyed on the dish — this is the only
# place the two are tied together. Read by the food_correction skill.
LAST_CARD_STATE_KEY = "food_scan"


# ─── reply templates ──────────────────────────────────────────────────────


PHOTO_NO_BYTES = "Фото пришло, но скачать не получилось — пришли ещё раз, пожалуйста."

REJECTED_ACK = "Поняла, не записываю. Если хочешь — пришли ещё фото."

CLARIFY_PROMPT = "Что не так? Напиши коротко — поправлю граммы, название или БЖУ."

AYLA_DOWN_FALLBACK = "Сервис распознавания временно недоступен — попробуй через минуту."

NOT_RECOGNIZED_FALLBACK = (
    "Фото немного сложное — не разобралась. Можешь переснять поближе или просто написать, что было?"
)

# Веха 1 (founder verdict 2026-06-02) — two-gate fallback copy.
NUTRITION_OFF_FALLBACK = "Дневник еды пока недоступен — функция готовится. Могу помочь с записью?"

PHOTO_SCAN_OFF_FALLBACK = (
    # Адверсариальный обзор #7 — текст НЕ должен советовать ввести блюдо
    # в чате: food_clarify перехватывает короткий текст и отвечает
    # «Скинь фото» → цикл. Mini App «manual entry» — единственный
    # реально работающий путь при выключенном photo gate.
    "Фото-распознавание пока недоступно. Открой Mini App — там можно записать блюдо вручную."
)

CONSENT_REQUIRED_FALLBACK = (
    "Чтобы записать еду, нужно открыть Mini App и подтвердить согласие "
    "на обработку данных (152-ФЗ). После этого вернись — и пришли фото."
)


# ─── skill ────────────────────────────────────────────────────────────────


@register
class FoodScannerSkill:
    """Photo recognition + diary log. Sprint 9 / P1."""

    name: ClassVar[str] = "food_scanner"

    def matches(self, context: SkillContext) -> bool:
        # Photo-only turn → scan path.
        if context.has_attachments and not context.message_text.strip():
            return True
        # Callback path — channel adapter delivers cb:food:* strings as
        # message_text per the D2 contract.
        text = context.message_text.strip()
        if not text.startswith("cb:food:"):
            return False
        parsed = parse_callback(text)
        if parsed is None:
            return False
        # Owned actions. The simpler "cb:food:diary" / "cb:food:typo"
        # variants belong to P4 food_clarify (no scan_id).
        return parsed["action"] in {"to_diary", "clarify", "reject"} and bool(parsed.get("ref"))

    def handle(self, context: SkillContext) -> SkillResult:
        text = context.message_text.strip()
        if text.startswith("cb:food:"):
            return self._handle_callback(context, text)
        return self._handle_photo(context)

    # ─── photo flow ──────────────────────────────────────────────────────

    def _handle_photo(self, context: SkillContext) -> SkillResult:
        # Веха 1 gates: master switch → photo gate → consent. Order
        # matters — we surface the most informative refusal first
        # («feature off» beats «consent missing» when both are true).
        gate = _check_gates(
            context,
            require_photo_scan=True,
            kind="photo",
        )
        if gate is not None:
            return gate

        photo_bytes = _extract_photo_bytes(context)
        if not photo_bytes:
            logger.info(
                "food_scanner.no_bytes conversation=%s",
                getattr(context.conversation, "id", None),
            )
            return SkillResult(
                reply_text=PHOTO_NO_BYTES,
                meta={"reply_kind": "food_scanner_no_bytes"},
            )

        external_id = external_user_id_for(context.bot_user)
        try:
            scan = asyncio.run(
                get_nutrition_client().scan_photo(
                    external_user_id=external_id,
                    image_bytes=photo_bytes,
                )
            )
        except FoodNotRecognizedError:
            return SkillResult(
                reply_text=NOT_RECOGNIZED_FALLBACK,
                meta={"reply_kind": "food_scanner_not_recognized"},
            )
        except NutritionUnavailableError:
            logger.warning("food_scanner.unavailable user=%s", external_id)
            return SkillResult(
                reply_text=AYLA_DOWN_FALLBACK,
                meta={"reply_kind": "food_scanner_unavailable"},
            )
        except NutritionAPIError:
            logger.exception("food_scanner.api_error user=%s", external_id)
            return SkillResult(
                reply_text=AYLA_DOWN_FALLBACK,
                meta={"reply_kind": "food_scanner_error"},
            )

        # DRF-1454 — memory, in the order that keeps the turn cheap and honest:
        # read what this person already told us about this dish, declare the
        # meal-history zone (and refuse to store it), then tie scan_id → dish so
        # the correction callback that may follow knows what it is about.
        dish = scan.dish_name or ""
        recall = food_memory.recall_corrections(context.bot_user, dish=dish)
        food_memory.note_meal(context.bot_user, dish=dish)
        _stash_last_card(context, scan)

        reply = _format_scan_card(scan, recall)
        return SkillResult(
            reply_text=reply,
            action_type="food_scan_card",
            action_data={
                "scan_id": scan.scan_id,
                "dish_name": scan.dish_name,
                "remembered": not recall.is_empty(),
                "buttons": food_recognition_keyboard(scan.scan_id),
            },
            meta={"reply_kind": "food_scanner_card"},
        )

    # ─── callback flow ───────────────────────────────────────────────────

    def _handle_callback(self, context: SkillContext, text: str) -> SkillResult:
        parsed = parse_callback(text)
        # matches() already validated — but be defensive.
        if parsed is None or not parsed.get("ref"):
            return SkillResult(reply_text="", should_send=False)

        action = parsed["action"]
        scan_id = parsed["ref"]

        # Веха 1 gate. Callbacks reference a prior scan, so if the
        # nutrition surface was turned off / consent revoked between the
        # scan and the tap, we refuse here rather than committing the
        # log to Ayla. ``require_photo_scan=False`` — the photo gate is
        # only needed for new scans, not the buttons on an already-
        # rendered card. ``reject`` is harmless; we don't gate it (silent
        # ack costs nothing and a refusal popup would be confusing).
        if action != "reject":
            gate = _check_gates(
                context,
                require_photo_scan=False,
                kind="callback",
            )
            if gate is not None:
                return gate

        if action == "reject":
            # «Не то» is a verdict on the recogniser, not on the person's diet —
            # see food_memory.note_recognition_rejected for why it is a quality
            # signal and never a stored fact.
            food_memory.note_recognition_rejected(context.bot_user, scan_id=scan_id)
            return SkillResult(
                reply_text=REJECTED_ACK,
                meta={"reply_kind": "food_scanner_rejected"},
            )

        if action == "clarify":
            return SkillResult(
                reply_text=CLARIFY_PROMPT,
                meta={"reply_kind": "food_scanner_clarify_prompt"},
            )

        # action == "to_diary"
        external_id = external_user_id_for(context.bot_user)
        try:
            log = asyncio.run(
                get_nutrition_client().log_meal(
                    external_user_id=external_id,
                    scan_id=scan_id,
                    meal_type="other",  # P1 doesn't show meal-type buttons
                    idempotency_key=f"diary:{external_id}:{scan_id}",
                )
            )
        except FoodNotRecognizedError:
            return SkillResult(
                reply_text=NOT_RECOGNIZED_FALLBACK,
                meta={"reply_kind": "food_scanner_log_not_recognized"},
            )
        except NutritionUnavailableError:
            logger.warning("food_scanner.log.unavailable user=%s", external_id)
            return SkillResult(
                reply_text=AYLA_DOWN_FALLBACK,
                meta={"reply_kind": "food_scanner_log_unavailable"},
            )
        except NutritionAPIError:
            logger.exception("food_scanner.log.error user=%s", external_id)
            return SkillResult(
                reply_text=AYLA_DOWN_FALLBACK,
                meta={"reply_kind": "food_scanner_log_error"},
            )

        return SkillResult(
            reply_text=f"Записала: {log.dish_name} — {int(log.calories)} ккал.",
            action_type="food_logged",
            action_data={
                "log_id": log.log_id,
                "dish_name": log.dish_name,
                "calories": log.calories,
            },
            meta={"reply_kind": "food_scanner_logged"},
        )


# ─── helpers ──────────────────────────────────────────────────────────────


def _extract_photo_bytes(context: SkillContext) -> bytes | None:
    """Read photo bytes from the channel-adapter's conversation stash.

    Phase 1 channel adapters set ``conversation.last_photo_bytes`` before
    dispatch. In Sprint 9 the bytes path is not yet wired; tests inject
    them via Mock. Returns ``None`` when no bytes available — caller
    emits ``PHOTO_NO_BYTES``.
    """
    return getattr(context.conversation, "last_photo_bytes", None)


def _check_gates(
    context: SkillContext,
    *,
    require_photo_scan: bool,
    kind: str,
) -> SkillResult | None:
    """Veха 1 two-flag + consent gate. Returns a refusal SkillResult or
    ``None`` to proceed.

    Order:

    1. ``settings.NUTRITION_ENABLED`` — master switch. False → «feature
       off» reply. Covers the whole RU-side nutrition surface.
    2. ``settings.FOOD_PHOTO_SCAN_ENABLED`` — cross-border gate.
       Only consulted when ``require_photo_scan=True`` (new scans).
       False → manual-entry hint.
    3. ``BotUser.food_scanner_consent_at`` — feature-specific 152-ФЗ
       acknowledgement. NULL → redirect-to-Mini-App reply.

    ``kind`` is a label («photo» / «callback») used in the meta so
    observability can distinguish refusal sites.

    The skill matches() still owns turn capture; the gate refuses
    here so other skills (e.g. echo) don't accidentally pick up
    the photo turn.
    """
    from django.conf import settings

    if not getattr(settings, "NUTRITION_ENABLED", False):
        logger.info(
            "food_scanner.gate.nutrition_off kind=%s conv=%s",
            kind,
            getattr(context.conversation, "id", None),
        )
        return SkillResult(
            reply_text=NUTRITION_OFF_FALLBACK,
            meta={"reply_kind": "food_scanner_nutrition_off"},
        )

    if require_photo_scan and not getattr(settings, "FOOD_PHOTO_SCAN_ENABLED", False):
        logger.info(
            "food_scanner.gate.photo_scan_off kind=%s conv=%s",
            kind,
            getattr(context.conversation, "id", None),
        )
        return SkillResult(
            reply_text=PHOTO_SCAN_OFF_FALLBACK,
            meta={"reply_kind": "food_scanner_photo_scan_off"},
        )

    # Адверсариальный обзор #2 — Mock(spec=None).food_scanner_consent_at
    # авто-генерирует truthy Mock-объект вместо None, и тест без явной
    # установки атрибута молча проходит гейт. Защита: требуем datetime
    # (production-shape — Django возвращает aware datetime либо None).
    # Это покрывает replay fixtures, integration stubs и любые будущие
    # тесты, которые забыли inscribe ``food_scanner_consent_at = …``.
    from datetime import datetime as _datetime

    consent_at = getattr(context.bot_user, "food_scanner_consent_at", None)
    if not isinstance(consent_at, _datetime):
        logger.info(
            "food_scanner.gate.consent_missing kind=%s conv=%s",
            kind,
            getattr(context.conversation, "id", None),
        )
        return SkillResult(
            reply_text=CONSENT_REQUIRED_FALLBACK,
            meta={"reply_kind": "food_scanner_consent_required"},
        )

    return None


def _stash_last_card(context: SkillContext, scan) -> None:
    """Tie ``scan_id`` → dish in ``Conversation.skill_state``. Best-effort.

    The ``cb:food:correct:{field}:{scan_id}`` callback carries no dish name, and
    memory is keyed on the dish — without this stash a correction has nothing to
    attach to. ``write_skill_state`` needs a tenant in scope and a persisted
    Conversation, neither of which holds on every dispatch path, so a failure
    degrades to the pre-DRF-1454 behaviour (one re-ask) rather than costing the
    reply: by this point the turn's idempotency key is already claimed.
    """

    try:
        from apps.conversations.services import write_skill_state

        write_skill_state(
            context.conversation,
            LAST_CARD_STATE_KEY,
            {"scan_id": scan.scan_id, "dish": scan.dish_name or ""},
        )
    except Exception:  # noqa: BLE001 — degraded memory beats a lost reply
        logger.debug(
            "food_scanner.card_stash_skipped conversation=%s",
            getattr(context.conversation, "id", None),
        )


def _memory_line(recall: food_memory.FoodRecall) -> str:
    """One line for the name this person gave this dish, or ``""``.

    One line, never more: the card's job is still «записать в дневник?», and a
    memory that pushes the question off the screen has stopped helping.

    Ayla's own numbers above it are printed unchanged, and there is nothing here
    that could contradict them: the bot keeps no portion and no macros of its
    own (``food_memory.REMEMBERED_FIELDS``). Two figures for one meal — one on
    the card, another in the diary — is exactly what variant А removed.
    """

    if not recall.dish_name:
        return ""
    return f"Помню с прошлого раза: «{recall.dish_name}»."


def _format_scan_card(scan, recall: food_memory.FoodRecall | None = None) -> str:
    """User-facing recognition card text.

    Voice mirrors D1 ``FOOD_RECOGNITION_EXAMPLES`` — terse, friendly,
    ends with the implicit question (the buttons answer it). When
    confidence is low (<0.6) we lead with a hedge so the user is
    primed to use the ✏️ Уточнить button.

    ``recall`` (DRF-1454) adds at most one line: what this person already
    corrected for this dish. Defaulting it to ``None`` keeps the pre-memory
    output byte-identical for every caller that does not pass it.
    """
    recall = recall or food_memory.EMPTY_RECALL
    dish = scan.dish_name or "блюдо"
    portion = scan.portion_g or 0
    nutrition = scan.nutrition or {}
    kcal = nutrition.get("calories")
    protein = nutrition.get("protein_g")
    fat = nutrition.get("fat_g")
    carbs = nutrition.get("carbs_g")

    parts: list[str] = []
    hedge = "Похоже на" if scan.confidence < 0.6 else "Узнала:"
    parts.append(f"{hedge} {dish}.")
    if portion:
        parts.append(f"Примерно {int(portion)} г.")
    if kcal is not None:
        macros_line = f"{int(kcal)} ккал"
        if protein is not None:
            macros_line += f" · Б {int(protein)}"
        if fat is not None:
            macros_line += f" · Ж {int(fat)}"
        if carbs is not None:
            macros_line += f" · У {int(carbs)}"
        parts.append(macros_line)
    memory_line = _memory_line(recall)
    if memory_line:
        parts.append(memory_line)
    parts.append("Записать в дневник?")
    return "\n".join(parts)
