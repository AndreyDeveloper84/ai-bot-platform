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

## History (DRF-1467) — read at Ayla, not kept here

``note_meal`` above still refuses to store the meal, and that is not the gap.
The gap was that nothing ASKED Ayla what is already in the diary, so a second
photograph of the same lunch was offered «Записать в дневник?» as if the day
were empty. :func:`apps.orchestrator.food_history.read_today` is that ask: one
GET of ``summary``, gated on PERSONAL_DATA + HEALTH, stored nowhere and cached
nowhere — a cache would be the same copy under another name. When Ayla does
not answer, the card simply omits the line: the person asked what is on the
plate in front of them, not what is in the diary, so inventing «уже записано»
would be a lie and announcing «дневник недоступен» would be an answer to a
question nobody asked. On this path Ayla being down is already said out loud
anyway — she is the recogniser too, so the scan fails first and
:data:`AYLA_DOWN_FALLBACK` is what the person sees.

The dish behind a card is stashed in ``Conversation.skill_state`` under
:data:`LAST_CARD_STATE_KEY` so the correction callback — which carries only a
``scan_id`` — can key memory on it.

DRF-1579: a weight named on the card before «✅ В дневник» lives in
:data:`GRAMS_STATE_KEY` (written only by ``food_correction``), and which scans
are already logged lives in :data:`LOGGED_STATE_KEY` (written only here, and
marked in flight BEFORE ``log_meal`` — a weight named while the entry is on its
way, or after a timeout with an unknown outcome, is not promised). Each
subkey has one writer, so neither clobbers the other from a stale read, and a
newer photo replacing the card does not lose a promised weight.

## Scope cut (vs mysite source)

Skipped for Sprint 9 — folded into P5 or Phase 1:

* Consent flow in chat. The mysite version asked first-time scanners for
  opt-in; here the consent is given on the Mini App screen and lives in the
  consent registry (``food_diary_processing``, DRF-1963) — see
  :func:`_gate`.
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
from typing import Any, ClassVar

from apps.integrations.ayla import (
    FoodNotRecognizedError,
    NutritionAPIError,
    NutritionUnavailableError,
    ScanBudgetExhaustedError,
    ScanProviderDownError,
    ScanDailyLimitError,
    external_user_id_for,
    get_nutrition_client,
)
from apps.integrations.ayla.portion_provenance import (
    portion_numbers_are_named,
    portion_provenance_of,
)
from apps.orchestrator import food_history
from apps.orchestrator.memory import food as food_memory
from apps.orchestrator.ui.keyboards import (
    Button,
    food_recognition_keyboard,
    parse_callback,
)
from apps.skills.base import SkillContext, SkillResult
from apps.skills.food_clarify.text_entry import DIARY_CONSENT_REQUIRED_TEXT
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

# DRF-1467 — read from Ayla's diary on this turn, never from a store of ours.
ALREADY_LOGGED_LINE = "Сегодня это блюдо уже есть в дневнике."

AYLA_DOWN_FALLBACK = "Сервис распознавания временно недоступен — попробуй через минуту."

# DRF-2195 — штатные отказы каталога по бюджету распознавания. Ни один из них
# не «временно недоступен»: каталог работает и отвечает осознанно, а счёт
# снимется в полночь, не «через минуту». Поэтому тексты называют срок и зовут
# туда, где дорога открыта прямо сейчас, — записать еду словами.
#
# `retry_after` у суточного потолка есть, но в текст не идёт: «попробуй через
# 7 часов» — обещание часа, который человеку ничего не даёт, когда соседняя
# дорога работает сию секунду.
SCAN_DAILY_LIMIT_FALLBACK = "Сегодня фото больше не распознаю — напиши словами, что было."

SCAN_BUDGET_EXHAUSTED_FALLBACK = "Распознавание фото сейчас недоступно — напиши словами."

# DRF-2318 — стойкий отказ распознавателя в каталоге (счёт, ключ, квота).
# Через минуту ничего не изменится; дорога рядом — записать словами. ЧЕРНОВИК
# владельцу: текст не из листа, утверждает владелец.
SCAN_PROVIDER_DOWN_FALLBACK = (
    "Распознавание фото сейчас не работает — мы уже чиним. "
    "А пока напиши словами, что было, — посчитаю и покажу."
)
#: Кнопка «Записать словами» — тот же тап, что «📔 В дневник» без фразы:
#: ведёт в запись текстом (``food_clarify`` → ``ASK_WHAT_TEXT``).
#: Контракт кнопки — ``Button`` (ключ ``label``): композер пропускает словари
#: без ``label`` молча (ревью #1997).
WRITE_IN_WORDS_BUTTON = Button(label="Записать словами", callback="cb:food:diary")

NOT_RECOGNIZED_FALLBACK = (
    "Фото немного сложное — не разобралась. Можешь переснять поближе или просто написать, что было?"
)

# Веха 1 (founder verdict 2026-06-02) — two-gate fallback copy.
NUTRITION_OFF_FALLBACK = "Дневник еды пока недоступен — функция готовится. Могу помочь с записью?"

PHOTO_SCAN_OFF_FALLBACK = (
    # DRF-1837. Раньше текст отсылал в Mini App «записать вручную»: такого
    # пути там нет (guardProd), а совет написать в чат давал цикл —
    # food_clarify отвечал «Скинь фото». Замер 10.09 назвал это кольцом:
    # записать еду было нечем. Теперь текстовый ввод есть
    # (apps.skills.food_clarify.text_entry) — зовём туда, где работает.
    "Фото-распознавание пока недоступно. Напиши, что было и сколько граммов, "
    "— например «гречка 200 г»: посчитаю и покажу, прежде чем записать."
)

#: DRF-2093 — один текст отказа реестра на воду, текст и фото; живёт в
#: ``text_entry`` рядом с CONSENT_TEXT, здесь — прежнее имя для читателей.
CONSENT_REQUIRED_FALLBACK = DIARY_CONSENT_REQUIRED_TEXT


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
                action_data=_log_it_another_way(),
                meta={"reply_kind": "food_scanner_no_bytes"},
            )

        external_id = external_user_id_for(context.bot_user)
        try:
            scan, diary = _scan_and_read_diary(context, external_id, photo_bytes)
        except FoodNotRecognizedError:
            return SkillResult(
                reply_text=NOT_RECOGNIZED_FALLBACK,
                action_data=_log_it_another_way(),
                meta={"reply_kind": "food_scanner_not_recognized"},
            )
        except ScanDailyLimitError as exc:
            # Порядок ветвей: оба класса бюджета — наследники
            # `NutritionAPIError`, значит стоят ВЫШЕ общего хвоста, иначе
            # ветка недостижима. И НЕ наследники `NutritionUnavailableError`,
            # значит ветка ниже их не перехватит.
            logger.info(
                "food_scanner.daily_limit user=%s retry_after=%s",
                external_id,
                exc.retry_after,
            )
            return SkillResult(
                reply_text=SCAN_DAILY_LIMIT_FALLBACK,
                action_data=_log_it_another_way(),
                meta={"reply_kind": "food_scanner_daily_limit"},
            )
        except ScanBudgetExhaustedError:
            logger.info("food_scanner.budget_exhausted user=%s", external_id)
            return SkillResult(
                reply_text=SCAN_BUDGET_EXHAUSTED_FALLBACK,
                action_data=_log_it_another_way(),
                meta={"reply_kind": "food_scanner_budget_exhausted"},
            )
        except ScanProviderDownError as exc:
            # DRF-2318: стойкий отказ — честно и с дорогой. Старая фраза из
            # состояния записи текстом забывается: иначе тап «Записать
            # словами» оценил бы её, а не спросил, что было.
            logger.warning("food_scanner.provider_down user=%s reason=%s", external_id, exc.reason)
            from apps.skills.food_clarify import text_entry

            text_entry.forget(context)
            return SkillResult(
                reply_text=SCAN_PROVIDER_DOWN_FALLBACK,
                action_data={"buttons": [WRITE_IN_WORDS_BUTTON.as_dict()]},
                meta={"reply_kind": "food_scanner_provider_down"},
            )
        except NutritionUnavailableError:
            logger.warning("food_scanner.unavailable user=%s", external_id)
            return SkillResult(
                reply_text=AYLA_DOWN_FALLBACK,
                action_data=_log_it_another_way(),
                meta={"reply_kind": "food_scanner_unavailable"},
            )
        except NutritionAPIError:
            logger.exception("food_scanner.api_error user=%s", external_id)
            return SkillResult(
                reply_text=AYLA_DOWN_FALLBACK,
                action_data=_log_it_another_way(),
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

        reply = _format_scan_card(scan, recall, diary)
        return SkillResult(
            reply_text=reply,
            action_type="food_scan_card",
            action_data={
                "scan_id": scan.scan_id,
                "dish_name": scan.dish_name,
                "remembered": not recall.is_empty(),
                "already_logged_today": diary.has_dish(dish),
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
            # DRF-2267 (CD §72): текст зовёт прислать ещё фото — кнопка
            # «Записать еду» зовёт туда же (фото или название), плюс «Меню».
            from apps.orchestrator.next_steps import (
                log_food_button,
                menu_button,
                next_step_action_data,
            )

            return SkillResult(
                reply_text=REJECTED_ACK,
                action_data=next_step_action_data(log_food_button(), menu_button()),
                meta={"reply_kind": "food_scanner_rejected"},
            )

        if action == "clarify":
            return SkillResult(
                reply_text=CLARIFY_PROMPT,
                meta={"reply_kind": "food_scanner_clarify_prompt"},
            )

        # action == "to_diary"
        external_id = external_user_id_for(context.bot_user)
        correction = _correction_for(context, scan_id)
        grams = correction.get("grams") if correction else None
        corrected = corrected_multiplier(grams, correction.get("portion_g")) if correction else None
        if corrected is OUT_OF_RANGE:
            return SkillResult(
                reply_text=GRAMS_OUT_OF_RANGE_TEXT.format(grams=grams),
                meta={"reply_kind": "food_scanner_log_grams_out_of_range"},
            )
        from apps.skills.food_clarify.text_entry import (
            PHOTO_ORIGIN_ESTIMATED_CONFIRMED,
            PHOTO_ORIGIN_USER_CORRECTED,
        )

        extra: dict = {}
        # §136 / DRF-2110: происхождение решается на карточке и едет ВСЕГДА —
        # подтверждённая как есть фото-запись тоже названа своим кодом, а не
        # NULL, который читается как «до §136».
        entry_origin = PHOTO_ORIGIN_ESTIMATED_CONFIRMED
        if isinstance(corrected, float):
            # DRF-1579: вес, названный на карточке, — множитель от распознанной
            # порции; число исправлено человеком (§136).
            extra = {"portion_multiplier": corrected}
            entry_origin = PHOTO_ORIGIN_USER_CORRECTED
        # «В полёте» ДО сетевого вызова: ответ про граммы, пришедший, пока запись
        # летит, не пообещает вес, который в неё уже не попадёт.
        # Уже записанный скан не понижается: повтор ключа вернёт ту же запись.
        if _logged_id(context, scan_id) is None:
            _mark_logged(context, scan_id, None)
        try:
            log = asyncio.run(
                get_nutrition_client().log_meal(
                    external_user_id=external_id,
                    scan_id=scan_id,
                    meal_type="other",  # P1 doesn't show meal-type buttons
                    idempotency_key=f"diary:{external_id}:{scan_id}",
                    entry_origin=entry_origin,
                    **extra,
                )
            )
        except FoodNotRecognizedError:
            _clear_in_flight(context, scan_id)
            return SkillResult(
                reply_text=NOT_RECOGNIZED_FALLBACK,
                meta={"reply_kind": "food_scanner_log_not_recognized"},
            )
        except NutritionUnavailableError:
            # Таймаут / 5xx / неизвестный исход: запись могла лечь — отметка
            # «в полёте» остаётся.
            logger.warning("food_scanner.log.unavailable user=%s", external_id)
            return SkillResult(
                reply_text=AYLA_DOWN_FALLBACK,
                meta={"reply_kind": "food_scanner_log_unavailable"},
            )
        except NutritionAPIError:
            _clear_in_flight(context, scan_id)
            logger.exception("food_scanner.log.error user=%s", external_id)
            return SkillResult(
                reply_text=AYLA_DOWN_FALLBACK,
                meta={"reply_kind": "food_scanner_log_error"},
            )

        _mark_logged(context, scan_id, log.log_id)
        # DRF-2371 — число называем, только когда вес кто-то назвал. Иначе
        # правило держалось бы один ход: карточка о числе молчит, а ответ
        # после тапа говорит «250 ккал», посчитанные по константе каталога.
        # Ход назвать вес на карточке уже есть — кнопка «✏️ Уточнить»
        # (``food_recognition_keyboard``) ведёт в вопрос «Сколько граммов?».
        log_provenance = portion_provenance_of(
            ((log.raw or {}).get("nutrition") or {}).get("portion_source")
        )
        if log.calories is None or not portion_numbers_are_named(log_provenance):
            # DRF-2371 — каталог сохранил запись, а числа в ней нет: порция
            # неизвестна или блюда нет в справочнике. Раньше здесь падал
            # ``int(None)`` — человек не видел ничего, хотя запись легла.
            # Число не выдумываем и ноль не подставляем: ноль читался бы как
            # «посчитано, и вышло почти ничего». Текст карточки для случая
            # «блюда нет в справочнике» ждёт слова владельца
            # (OWNER_QUESTIONS) — до ответа говорим то же, без числа.
            reply = f"Записала: {log.dish_name}."
        else:
            reply = f"Записала: {log.dish_name} — {int(log.calories)} ккал."
        if extra and (log.raw or {}).get("entry_origin") != PHOTO_ORIGIN_USER_CORRECTED:
            # Ключ повтора вернул ПРЕЖНЮЮ запись (первый тап дошёл, ответ — нет):
            # вес в неё не лёг, и сказать «записала» без оговорки было бы ложью.
            reply = f"{reply} {GRAMS_NOT_APPLIED_TEXT.format(grams=grams)}"
        # DRF-2108 — §109 шаг 7 и под фото-записью: только «Удалить».
        # «Исправить граммы» (÷100) верно лишь для записи текстом; обработчик
        # чипов общий (``food_clarify.text_entry.on_entry_callback``).
        from apps.orchestrator.next_steps import after_entry_buttons
        from apps.orchestrator.ui.keyboards import ENTRY_ID_RE, food_entry_keyboard

        action_data: dict[str, Any] = {
            "log_id": log.log_id,
            "dish_name": log.dish_name,
            "calories": log.calories,
        }
        entry_chips: list[dict[str, str]] = []
        if log.log_id and ENTRY_ID_RE.match(log.log_id):
            entry_chips = food_entry_keyboard(log.log_id, fixable=False)
        # DRF-2267 (CD §72): и следующий шаг — «Мой дневник», «Меню».
        action_data["buttons"] = [*entry_chips, *after_entry_buttons()]
        return SkillResult(
            reply_text=reply,
            claims_done=True,
            claims_done_evidence="ayla.meals.log:log_id",
            action_type="food_logged",
            action_data=action_data,
            meta={
                "reply_kind": "food_scanner_logged",
            },
        )


# ─── helpers ──────────────────────────────────────────────────────────────


def _log_it_another_way() -> dict:
    """DRF-2267 (CD §72) — под отказом по фото: «Записать еду» и «Меню».

    Все эти тексты зовут в одно и то же — написать словами или прислать
    ещё фото; ``cb:welcome:food`` отвечает ровно этим приглашением (и с
    воротами дневника), то есть кнопка делает то, что обещает текст.
    """
    from apps.orchestrator.next_steps import log_food_button, menu_button, next_step_action_data

    return next_step_action_data(log_food_button(), menu_button())


def _extract_photo_bytes(context: SkillContext) -> bytes | None:
    """Read photo bytes from the channel-adapter's conversation stash.

    Phase 1 channel adapters set ``conversation.last_photo_bytes`` before
    dispatch. In Sprint 9 the bytes path is not yet wired; tests inject
    them via Mock. Returns ``None`` when no bytes available — caller
    emits ``PHOTO_NO_BYTES``.
    """
    return getattr(context.conversation, "last_photo_bytes", None)


def _scan_and_read_diary(context: SkillContext, external_id: str, photo_bytes):
    """Recognise the photo and read today's diary — in one round trip.

    Two independent GETs to the same service, so they go together rather
    than one after the other (DRF-1467). Sequential would have added a
    second full ``DEFAULT_TIMEOUT_S`` to the most visible reply the bot
    sends: recognition already costs seconds, and «Ayla alive but slow» —
    the state the circuit breaker deliberately does NOT trip on, since it
    counts failures rather than latency — would have doubled the wait for a
    single optional line. Concurrently the line is free.

    The diary half is skipped outright when the consent gate is shut, so an
    unconsented person pays nothing and Ayla is not asked. That check is
    :func:`apps.orchestrator.food_history.read_consent_open` — the same one
    ``read_today`` runs; it is lifted out only because the fetch it guards
    has to be scheduled next to the scan rather than after it.

    Returns ``(scan, diary)``. The scan's exceptions are re-raised for the
    caller's existing ladder to answer; the diary's are swallowed into
    :data:`apps.orchestrator.food_history.UNKNOWN`, because a diary we could
    not read costs one line and a scan we could not run costs the turn.
    """
    client = get_nutrition_client()
    want_history = food_history.read_consent_open(context.bot_user)

    async def _gather():
        calls = [client.scan_photo(external_user_id=external_id, image_bytes=photo_bytes)]
        if want_history:
            calls.append(client.daily_summary(external_user_id=external_id))
        return await asyncio.gather(*calls, return_exceptions=True)

    results = asyncio.run(_gather())

    scan = results[0]
    if isinstance(scan, BaseException):
        raise scan

    diary = food_history.UNKNOWN
    if len(results) > 1:
        summary = results[1]
        if isinstance(summary, BaseException):
            logger.info(
                "food_scanner.diary_unavailable user=%s err=%s",
                external_id,
                type(summary).__name__,
            )
        else:
            diary = food_history.TodayDiary(
                food_history.Status.OK, food_history.meals_from_summary(summary)
            )
    return scan, diary


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
    2. ``FOOD_PHOTO_SCAN_ENABLED`` — cross-border gate, asked through
       :func:`apps.consent.photo_gate.photo_scan_refusal` (DRF-2109: the same
       predicate the Mini App proxy asks). Only consulted when
       ``require_photo_scan=True`` (new scans).
       False → manual-entry hint.
    3. PERSONAL_DATA (DRF-1948) — ``personal_records_consent_open``, the same
       rule every other diary write already follows (text entry, Mini App
       edit/restore): no PERSONAL_DATA, no diary. Refused with the text entry's
       own ``CONSENT_TEXT`` so the two ways into the diary say the same thing.
    4. ``food_diary_processing`` (DRF-1963, M1) — the diary/scanner consent,
       ON TOP of PERSONAL_DATA, not instead of it. Read from the consent
       registry via :func:`apps.consent.nutrition.diary_is_granted` — the
       same predicate the Mini App screen and the proactive layer ask, so a
       withdrawn row closes all three at once. Missing → redirect-to-Mini-App
       reply (``reply_kind`` unchanged: M2+ recovery keys on it).

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

    # DRF-2109 — cross-border флаг фото через один предикат на чат и прокси
    # Mini App (``apps.consent.photo_gate``); текст и reply_kind прежние.
    from apps.consent.photo_gate import photo_scan_refusal

    if require_photo_scan and photo_scan_refusal() is not None:
        logger.info(
            "food_scanner.gate.photo_scan_off kind=%s conv=%s",
            kind,
            getattr(context.conversation, "id", None),
        )
        return SkillResult(
            reply_text=PHOTO_SCAN_OFF_FALLBACK,
            meta={"reply_kind": "food_scanner_photo_scan_off"},
        )

    # DRF-1948 / DRF-1963 / DRF-2093 — PERSONAL_DATA и согласие дневника из
    # реестра спрашиваются ОДНИМ предикатом на всех писателей
    # (``apps.consent.diary_gate.diary_write_refusal``, fail-closed на сбое
    # реестра). Тексты и ``reply_kind`` — прежние: M2+ recovery ключуется на них.
    from apps.consent.diary_gate import (
        CONSENT_REQUIRED,
        FOOD_DIARY_CONSENT_REQUIRED,
        diary_write_refusal,
    )

    reason = diary_write_refusal(context.bot_user)
    if reason == CONSENT_REQUIRED:
        from apps.skills.food_clarify.text_entry import CONSENT_TEXT

        logger.info(
            "food_scanner.gate.personal_data_missing kind=%s conv=%s",
            kind,
            getattr(context.conversation, "id", None),
        )
        from apps.skills.welcome.skill import consent_offer_action_data

        return SkillResult(
            reply_text=CONSENT_TEXT,
            action_data=consent_offer_action_data("photo"),
            meta={"reply_kind": "food_scanner_personal_data_required"},
        )
    if reason == FOOD_DIARY_CONSENT_REQUIRED:
        from apps.skills.food_clarify.text_entry import diary_consent_required_result

        logger.info(
            "food_scanner.gate.consent_missing kind=%s conv=%s",
            kind,
            getattr(context.conversation, "id", None),
        )
        # DRF-2096 — тот же отказ и та же кнопка, что у воды и текста.
        return diary_consent_required_result("food_scanner_consent_required")

    return None


#: DRF-1579 — поправки веса по ``scan_id``: ``{scan_id: {"grams", "portion_g"}}``.
#: Пишет ТОЛЬКО ``food_correction``; этот скилл лишь читает. Отдельно от
#: карточки последнего фото — новое фото не теряет обещанный вес, а два
#: писателя одного подключа затирали бы друг друга по старому чтению.
GRAMS_STATE_KEY = "food_scan_grams"
#: DRF-1579 — какие сканы уже записаны: ``{scan_id: log_id}``; ``None`` — запись
#: отправлена, исход неизвестен. Пишет ТОЛЬКО этот скилл: «в полёте» до
#: ``log_meal``, ``log_id`` после успеха; снимает, только когда каталог отказал.
LOGGED_STATE_KEY = "food_scan_logged"
_MAX_LOGGED = 10

#: Границы множителя, которые каталог принимает в запись (FoodLogCreateSerializer).
_MIN_PORTION_MULTIPLIER = 0.1
_MAX_PORTION_MULTIPLIER = 20.0
OUT_OF_RANGE = object()
GRAMS_OUT_OF_RANGE_TEXT = (
    "Вес {grams} г слишком далёк от распознанной порции — пересчитать не могу. "
    "Нажми «✏️ Уточнить» → «⚖️ Грамм» и укажи вес ещё раз."
)
GRAMS_NOT_APPLIED_TEXT = (
    "Вес {grams} г не применился: это блюдо уже было в дневнике. "
    "Чтобы поменять вес, удали запись и запиши заново текстом."
)


def _state(context: SkillContext) -> dict:
    raw = getattr(context.conversation, "skill_state", None)
    return raw if isinstance(raw, dict) else {}


def corrected_multiplier(grams, portion):
    """Множитель от поправленного веса (DRF-1579).

    ``float`` — множитель от распознанной порции; ``None`` — считать не из чего
    (нет веса или порции); :data:`OUT_OF_RANGE` — каталог такой не примет.
    Одна функция на ответ про граммы и на «В дневник»: обещание и запись не
    должны расходиться в том, что считается допустимым.
    """
    if isinstance(grams, bool) or not isinstance(grams, int):
        return None
    if isinstance(portion, bool) or not isinstance(portion, (int, float)) or portion <= 0:
        return None
    multiplier = round(grams / float(portion), 3)
    if not _MIN_PORTION_MULTIPLIER <= multiplier <= _MAX_PORTION_MULTIPLIER:
        return OUT_OF_RANGE
    return multiplier


def _correction_for(context: SkillContext, scan_id: str) -> dict | None:
    """Поправка веса ЭТОГО скана из ``food_scan_grams``, или ``None``."""
    entries = _state(context).get(GRAMS_STATE_KEY)
    entry = entries.get(scan_id) if isinstance(entries, dict) else None
    return entry if isinstance(entry, dict) else None


def _logged_id(context: SkillContext, scan_id: str) -> str | None:
    """``log_id`` уже записанного скана, или ``None`` (не записан / «в полёте»)."""
    logged = _state(context).get(LOGGED_STATE_KEY)
    value = logged.get(scan_id) if isinstance(logged, dict) else None
    return value if isinstance(value, str) and value else None


def _mark_logged(context: SkillContext, scan_id: str, log_id: str | None) -> None:
    """Скан «записан» (``log_id``) или «в полёте» (``None``).

    Поправка веса после этого больше не делает вид, что применится.
    """
    current = _state(context).get(LOGGED_STATE_KEY)
    logged = dict(current) if isinstance(current, dict) else {}
    logged.pop(scan_id, None)
    logged[scan_id] = log_id
    _write_logged(context, dict(list(logged.items())[-_MAX_LOGGED:]))


def _clear_in_flight(context: SkillContext, scan_id: str) -> None:
    """Каталог отказал — записи нет: снять «в полёте», вес снова можно назвать."""
    current = _state(context).get(LOGGED_STATE_KEY)
    logged = dict(current) if isinstance(current, dict) else {}
    if logged.get(scan_id) is None:
        logged.pop(scan_id, None)
    _write_logged(context, logged)


def _write_logged(context: SkillContext, logged: dict) -> None:
    try:
        from apps.conversations.services import write_skill_state

        write_skill_state(context.conversation, LOGGED_STATE_KEY, logged)
    except Exception:  # noqa: BLE001 — the entry is written; a lost mark costs one wrong ack
        logger.debug(
            "food_scanner.logged_mark_skipped conversation=%s",
            getattr(context.conversation, "id", None),
        )


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
            # DRF-1579: порция, которую распознал скан, — от неё считается
            # множитель, если человек поправит вес до «В дневник».
            {
                "scan_id": scan.scan_id,
                "dish": scan.dish_name or "",
                "portion_g": getattr(scan, "portion_g", None),
            },
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


def _already_logged_line(diary, dish: str) -> str:
    """One line when Ayla already holds this dish for today, else ``""``.

    Deliberately silent on every other outcome. ``has_dish`` answers False for
    «Ayla did not reply» and «no HEALTH consent» alike, and that is the right
    shape here: the card is an answer about the photograph, and a person who
    sent a plate did not ask about the state of the diary. Saying nothing
    invents nothing — which is the whole rule (DRF-1467).
    """
    return ALREADY_LOGGED_LINE if diary is not None and diary.has_dish(dish) else ""


def _format_scan_card(
    scan,
    recall: food_memory.FoodRecall | None = None,
    diary=None,
) -> str:
    """User-facing recognition card text.

    Voice mirrors D1 ``FOOD_RECOGNITION_EXAMPLES`` — terse, friendly,
    ends with the implicit question (the buttons answer it). When
    confidence is low (<0.6) we lead with a hedge so the user is
    primed to use the ✏️ Уточнить button.

    ``recall`` (DRF-1454) adds at most one line: what this person already
    corrected for this dish. Defaulting it to ``None`` keeps the pre-memory
    output byte-identical for every caller that does not pass it.

    ``diary`` (DRF-1467) adds at most one more: whether Ayla already has this
    dish logged today, read from her on this turn and kept nowhere. Same
    defaulting rule — ``None`` renders the card exactly as before.
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
    # DRF-2371 — число называем, только когда вес кто-то назвал. Признак
    # берём из тела ответа каталога через единственный вход перевода:
    # отсутствие поля и незнакомое значение оба читаются как «не названо».
    # Число, посчитанное по константе каталога, существует — но выдавать
    # его за названное нельзя.
    # Каталог кладёт признак ВНУТРЬ ``nutrition``, рядом с числами
    # (``FoodScanResponseSerializer``), а не на верхний уровень ответа.
    provenance = portion_provenance_of(nutrition.get("portion_source"))
    if kcal is not None and portion_numbers_are_named(provenance):
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
    # ``scan.dish_name``, not the ``dish`` above: that one falls back to the
    # placeholder «блюдо», and matching a placeholder against the diary could
    # claim a dish the recogniser never named.
    logged_line = _already_logged_line(diary, scan.dish_name or "")
    if logged_line:
        parts.append(logged_line)
    parts.append("Записать в дневник?")
    return "\n".join(parts)
