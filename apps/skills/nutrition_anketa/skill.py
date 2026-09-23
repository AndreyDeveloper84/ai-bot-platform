"""Nutrition anketa skill — 7-step nutrition profile FSM.

Sprint 9 / P3 (DRF-820). Largest port in Sprint 9: walks the user
through gender → age → screening → height → weight → activity → goal
(see ``fsm.py`` for why in that order), persists state in
``Conversation.skill_state['nutrition_anketa']`` (via D3), POSTs the
result to Ayla ``upsert_profile``, and renders the computed norms.

## State machine

Backed by :class:`apps.skills.nutrition_anketa.fsm.AnketaFSM`. State
serialised to JSON between turns; resumed via
:meth:`SkillFSM.deserialize`.

## Skill state contract

* Key in ``conversation.skill_state``: ``"nutrition_anketa"``
* Value: ``AnketaFSM.serialize()`` dict
* Empty / missing → fresh FSM (first turn)

The skill writes state back to the conversation after each turn. The
pipeline does NOT auto-persist (per D3 design — explicit writers).

## Entry / resume / edit

* **Entry** — match on ``cb:anketa:start`` OR the literal command
  ``/anketa`` (so a bare command can launch the flow).
* **Resume** — when an active FSM lives in skill_state, the skill
  claims plain-text + choice callbacks until COMPLETE.
* **Edit** — :func:`AnketaFSM.goto` is exposed for ``cb:anketa:edit:{step}``.
  Phase 1 wires the edit button into the summary card; Sprint 9
  delivers the callback handler only.

## Completion

On COMPLETE, the skill:

1. Calls Ayla ``upsert_profile`` with the answers; ``activity_coefficient``
   is the person's own answer (DRF-2102, the four values of §85); «Не знаю»
   sends no number, only ``_skipped_fields: ["activity"]`` (question 59).
   ``pace`` goes only with a goal that uses it (``PACE_GOALS``).
2. Reads the response's ``norms`` envelope (kcal / protein / fat /
   carbs / water_ml).
3. Renders a "норму посчитала" summary.
4. Wipes the FSM state from ``conversation.skill_state``.

## Stop scenarios (owner decision 2026-09-09, §7.1)

Ayla does not compute targets automatically for a minor, during
pregnancy or nursing, for a declared eating disorder, or for a condition
affecting nutrition or fluid balance. **The diary stays available** —
that is the point of the branch, not a consolation.

Two gates, both firing on the turn the answer arrives:

* after ``age`` — under :data:`~apps.skills.nutrition_anketa.fsm.ADULT_AGE`;
* after ``screening`` — any answer other than ``none``.

Three properties this branch must keep, each of which cost something to
learn elsewhere in this codebase:

1. **Nothing is sent to Ayla.** Not "sent and ignored" — not sent.
   ``profile_upsert_service._recompute_and_persist`` recomputes on every
   upsert unconditionally. The override ladder that once *added* kcal
   and water for pregnancy and nursing (+200/+400 kcal, +300/+700 ml) is
   gone — catalogue #372 refuses by name instead, and #403 writes the
   fluids target as a reference by sex, not a formula from weight — but
   the stop still ends before the network call: the answer to a health
   screen is not data to forward.
2. **The screening answer is not stored.** It decides, then it is
   dropped — not written to ``skill_state``, not put in ``health_flags``,
   not remembered. It is special-category data under 152-ФЗ and the
   consent that would allow keeping it is still an open question with
   the owner. Same treatment as «что не подошло» in
   :mod:`apps.skills.food_correction`.
3. **The log records the bucket, not the condition.** ``stop=screening``
   and never *which* — a log line is storage too.

## Goal hint on the last step (DRF-2124, План-B, В-4)

The person's ``ClientGoal.goal_key`` («подтянуть фигуру») and the anketa's
``goal`` (lose / maintain / gain — a parameter of the calculation) stay two
different notions. What links them is a curated table on the catalogue side
(``PlanTemplate.nutrition_goal_hint``, #515) that decision-context serves
as ``known.goal.nutrition_goal_hint``. This skill is its reader: when the
goal step is rendered and the person has an active goal with a hint, the
prompt gets a second line — «Под цель «Подтянуть фигуру» обычно выбирают
«Похудеть» или «Поддержать» — но выбирай сама» — and ``action_data``
carries the hint as data. Nothing else changes: the keyboard is the whole
table in its order, the step is not skipped, nothing is pre-selected, and
``answers["goal"]`` is written only by the person's tap (§7.1 / §5.1 —
the person's input). Any refusal of the goals read — not configured,
unavailable, malformed — renders the plain step; the hint is a courtesy,
not a dependency. The read happens only when the goal step is rendered.

## Manual target from a specialist (DRF-2138, mode 3 of §82)

The catalogue has had mode 3 for a while (``targets/manual/``, the only
writer of ``targets_source=user_entered``); the bot had no way in, and the
stop text said so. Now there are two ways in and one dialogue:

* the stop branch (§7.1) offers «Впиши ориентир от специалиста» under
  the stop text — the person who cannot be computed for CAN carry a
  number a specialist gave them;
* a deterministic phrase — a number next to a specialist word («мне врач
  назначил 1800 ккал»), or «ориентир от специалиста» without a number.
  A bare «1800», «1800 ккал» without the specialist, or a number next to a
  non-kcal unit («2000 шагов») is NOT ours — the digit could be about
  anything (:func:`_manual_target_entry`).

The dialogue: «Сколько ккал в день назначил специалист?» → number →
card «Ориентир от специалиста: N ккал. Источник — ты, не расчёт Ayla.
Подтвердить?» → POST only on the tap. The number is the person's input
and nothing else; the card prints no other number. Thresholds are the
catalogue's (§85): 422 → refusal that names the catalogue's floor, 409
``calories_deviation`` → one more question, ``calories_low`` → a warning
line. Consent M (``personal_calculation``) is NOT required — this is not
a calculation; the gate is PERSONAL_DATA (the same second gate the diary
writers use). Health is not asked and not stored. Protein is not asked:
the catalogue's contract has no protein for a manual target (DRF-2186).
State lives in ``skill_state["nutrition_manual_target"]``, separate from
the anketa FSM; entering the manual path drops an unfinished anketa.

Withdrawal (DRF-2135) touches this too: the catalogue purge clears a
manual target with everything else, and «Отключить персональный расчёт»
is offered when a ``user_entered`` target exists even without consent M
(before this ticket it said «нечего отключать»).

## «Обнови вес» — one question, not seven (DRF-2139, В3)

A recount used to need the whole anketa again. The catalogue recomputes on
every ``upsert_profile`` and keeps the inputs of the last computation in
``targets_input_snapshot``, so one new weight plus the OLD inputs is a new
proposal — and it becomes the acting target by the same confirmation tap
(§5.1). Entries: the phrase («мой вес 65», «вешу 65», «обнови вес») or the
chip «Обновить вес»; one question if the number is not in the phrase. The
body of the POST is EXACTLY the six snapshot fields with the new weight plus
the consent attestation (M) — nothing is invented by the bot: a snapshot
missing any input sends the person to the anketa instead
(:func:`_update_weight_body`). Stop states are not revisited (age and
screening are not asked again); a snapshot older than 365 days (by
``raw.targets_provenance.computed_at``) sends to the anketa — the age may
have changed; without a date the check is not made (a named limit).
Bounds 30–300 kg and «проверь число» are the ticket's (DRF-2139 п.1/п.4).
``pace`` / ``diet_preference`` are not in the body: the catalogue's upsert is
a partial patch (``_apply_patch`` touches only the keys present), so what the
person set elsewhere stays as it was — the same six-field body the anketa
sends. An anketa in flight keeps its answers: «вешу 65» on the anketa's own
weight step is the step's answer, not the short path (review #1912).

Since the catalogue's #525 (DRF-2192 / DRF-2193) the answer has a new shape,
and both old limits are gone. On ``ayla_calculated`` the upsert no longer
writes ``ayla_proposed`` over the acting target: the acting target stays and
the new one lies beside it in ``targets_provenance.pending_proposal`` until
the person confirms it — the card shows it as a proposal with the confirm
chip (:func:`pending_proposal`). On ``user_entered`` the catalogue records
the weight and leaves the person's own target alone, so the bot now writes
the weight — ``{weight_kg, consent}`` only: a manual target has no snapshot,
and the bot invents no anketa fields.

Since DRF-2279 (CD §76, №32) the catalogue marks pace and activity that it
substituted before question 59 as ``legacy_default_inputs``, and a marked
input is «not named» for the calculation. The short path used to carry the
snapshot's activity and the profile's pace forward as they were — and the
catalogue reads a value that arrives as its confirmation, so the bot would
have confirmed an old default for the person. With marks present the weight
is held and one question per marked input is asked first (activity, then
pace — pace only where the goal uses it); the POST follows with the answers
NAMED. «Не знаю» on activity is a skip, as in the anketa. The anketa itself
needs nothing extra: it asks pace and activity anew every time.

## Scope cuts vs mysite

Deferred to Phase 1 / a follow-up Sprint 9 ticket:

* Pace step (slow / balanced) — and with it the "faster than ~0.9 kg per
  week" stop, which needs a methodology that computes a rate.
* Gain-clarify branch.
* BMI-ladder override response handling ("Учла важное" override card).
* Allergies / meds (these belong with the Tier-B health screening
  port — see DRF-824 scope note).
* ~~Activity step~~ — landed with DRF-2102 (four levels + «Не знаю»).
  Until then every profile carried ``1.4``, a value outside the approved
  set; the catalogue still normalises such legacy values (#508) and its
  schema default is still 1.4 — removing that default is a separate
  ticket with a migration.
* Consent screen before the weight question. The owner requires a
  separate consent for weight; whether the same consent covers the
  screening answers is open (question 1 in
  ``docs/PLAN_NUTRITION_TARGETS.md``). Wiring one checkbox that silently
  covers both is the thing not to do, so it lands separately.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from apps.integrations.ayla import (
    NutritionAPIError,
    NutritionUnavailableError,
    external_user_id_for,
    get_nutrition_client,
)
from apps.integrations.ayla.goals_client import fetch_decision_context
from apps.integrations.ayla.nutrition_client import (
    TARGETS_PROPOSED,
    ManualTargetsConfirmationRequiredError,
    ManualTargetsRefusedError,
    NothingToConfirmError,
    health_factor_refusals,
    insufficient_inputs,
    pending_proposal,
    proposed_norms,
)
from apps.orchestrator.plan_lite_card import goal_label
from apps.orchestrator.ui.keyboards import anketa_choice_keyboard, parse_callback
from apps.skills.base import SkillContext, SkillResult
from apps.skills.fsm import Completed, NextStep
from apps.skills.nutrition_anketa.fsm import (
    ACTIVITY_CHOICES,
    ACTIVITY_COEFFICIENTS,
    PACE_GOALS,
    ACTIVITY_SKIP,
    DIET_OTHER,
    DIET_SKIP,
    DIET_SKIP_WIRE_NAME,
    ADULT_AGE,
    PACE_CHOICES,
    CHOICE_STEPS,
    GOAL_CHOICES,
    SCREENING_CLEAR,
    AnketaFSM,
    choice_keyboard_options,
)
from apps.skills.registry import register

if TYPE_CHECKING:
    from apps.consent.personal_calculation import ConsentAttestation

logger = logging.getLogger(__name__)


# Key inside Conversation.skill_state.
def _nutrition_enabled() -> bool:
    """Тот же читатель, что у меню и у хендлера (``marketplace.nutrition_enabled``).

    Один флаг, один способ чтения — ``getattr`` с ``False`` по умолчанию.
    Импорт ленивый: меню тянет клавиатуры и каталог, навыку это не нужно
    на этапе импорта.
    """
    from apps.skills.menu.marketplace import nutrition_enabled

    return nutrition_enabled()


def _nutrition_unavailable_text() -> str:
    from apps.skills.menu.marketplace import NUTRITION_UNAVAILABLE_TEXT

    return NUTRITION_UNAVAILABLE_TEXT


_STATE_KEY = "nutrition_anketa"

_AYLA_DOWN_FALLBACK = (
    "Не получилось сохранить — сервис временно недоступен. Попробуй ещё раз через минуту."
)

# ─── stop-scenario copy (§7.1) ───────────────────────────────────────────
#
# Two texts, not one. The reason differs, and a single «не считаю»
# covering both would be a refusal without a name — the person could not
# tell whether we mean their age or their answer, and neither could we.

#: Shared tail. The diary staying open IS the decision, so it is stated
#: plainly rather than tacked on as an apology.
#: DRF-2138: фраза «вписать его пока некуда — такой ручки у меня ещё нет»
#: снята — ручка есть, кнопка под стоп-текстом первой (:data:`MANUAL_TARGET_BUTTON`).
_STOP_DIARY_TAIL = (
    "Дневник остаётся: записывай еду и воду, я посчитаю и покажу, сколько "
    "вышло за день. Без дневной цели и без процентов."
)

_STOP_MINOR = (
    "Персональный ориентир по калориям и воде я считаю только совершеннолетним. "
    "Это не про тебя лично — просто такие числа несовершеннолетнему должен "
    "называть врач, а не бот.\n\n" + _STOP_DIARY_TAIL
)

#: Deliberately does not repeat back what the person just declared.
_STOP_SCREENING = (
    "Тогда персональный ориентир считать не буду — в таких случаях числа должен "
    "называть специалист, который тебя ведёт.\n\n" + _STOP_DIARY_TAIL
)

_STOP_TEXTS = {"minor": _STOP_MINOR, "screening": _STOP_SCREENING}

# ─── нет утверждения о согласии на расчёт (DRF-1658, N-a3) ───────────────
#
# Граница каталога (#324) не принимает параметры тела без утверждения
# «согласие вида personal_calculation, версия текста такая-то». Если у
# бота такого утверждения нет — POST не уходит вовсе, и человеку об этом
# говорится прямо. Тихая отправка без поля дала бы 422 из каталога и
# профиль, который ВЫГЛЯДИТ обработанным, а не является.
#
# Текст не обещает несуществующего: экрана согласия нет, «дай согласие
# вон там» отправило бы человека в место, которого нет. Собранные ответы
# стираются вместе с FSM — держать параметры тела в skill_state без
# основания нельзя.
# ЧЕРНОВИК ТЕКСТОВ. Владельцем не утверждён: это его слова к людям.
# Состав согласия взят из §92 дословно — вес, рост, возраст,
# физиологический пол, активность и цель; шесть параметров, и ни одним
# больше.
#
# Чего в тексте намеренно НЕТ:
#   * обещания, что отказ можно передумать «в настройках» — экрана
#     отзыва в боте сегодня нет, и обещать дверь, которой нет, нельзя;
#   * слова «анонимно» и «обезличенно» — параметры тела хранятся под
#     учётной записью человека, и называть это обезличиванием было бы
#     неправдой;
#   * юридических формул сверх необходимого: человек читает это в
#     мессенджере между делом.
# Тексты — дословно из пакета решений владельца 12.09 §2 (DRF-1698):
# «APPROVE WITH TEXT CHANGES». Согласие относится ТОЛЬКО к персональному
# расчёту норм; дневник работает и без него, и текст говорит это сам.
CONSENT_ASK = (
    "Хотите, чтобы Ayla рассчитывала ваши персональные нормы?\n\n"
    "Для расчёта понадобятся шесть параметров: вес, рост, возраст, пол для "
    "расчёта, уровень активности и ваша цель.\n\n"
    "Я сохраню эти данные в вашем профиле и буду использовать их только для "
    "персонального расчёта и пересчёта норм, когда данные изменятся.\n\n"
    "Это необязательно. Без персонального расчёта дневник продолжит работать: "
    "вы сможете записывать еду и воду и видеть итоги за день.\n\n"
    "Согласие можно отозвать в любой момент. После отзыва Ayla перестанет "
    "использовать эти параметры для персонального расчёта."
)

#: Свои слаги, а не свободный текст: угаданное «да» — это запись
#: согласия за человека, который его не давал.
CONSENT_GRANT_CALLBACK = "cb:pc_consent:grant"
CONSENT_DECLINE_CALLBACK = "cb:pc_consent:decline"

CONSENT_BUTTON_GRANT = "Рассчитать мои нормы"
CONSENT_BUTTON_DECLINE = "Не сейчас"

#: Фраза, которой человек включает расчёт позже (владелец §2: «если такого
#: UI-раздела ещё нет — напишите: „Рассчитать мои нормы“»). Раздела
#: «Питание» с этим действием в Mini App нет — действует вторая редакция.
ENTRY_PHRASE = "рассчитать мои нормы"

CONSENT_DECLINED = (
    "Хорошо, персональный расчёт не включаем.\n\n"
    "Дневник продолжит работать как обычно: записывайте еду и воду, а Ayla "
    "покажет итог за день.\n\n"
    "Если передумаете, напишите: «Рассчитать мои нормы»."
)

#: Fail-close ТОЛЬКО расчёта: дневник от этого не закрывается (владелец §2).
CONSENT_RECORDED_BUT_UNREADABLE = (
    "Записал согласие, но перечитать его не смог — персональный расчёт не "
    "начинаю, пока не буду уверен. Попробуйте ещё раз через пару минут. "
    "Дневник при этом работает как обычно."
)

# ─── Отзыв — детерминированная canonical action (владелец §2) ──────────────
#
# Free text может инициировать отзыв, но не единственный путь: есть кнопка
# и слаг. Перед удалением — подтверждение, потому что после него параметры
# удаляются, а нормы становятся недоступны.

WITHDRAW_ACTION_TEXT = "Отключить персональный расчёт"
WITHDRAW_CALLBACK = "cb:pc_consent:withdraw"
WITHDRAW_CONFIRM_CALLBACK = "cb:pc_consent:withdraw_confirm"
WITHDRAW_KEEP_CALLBACK = "cb:pc_consent:withdraw_keep"

# ─── ориентир от специалиста (DRF-2138, режим 3 §82) ──────────────────────

#: Кнопка под стоп-текстом — метка из листа DRF-2138.
MANUAL_TARGET_BUTTON = "Впиши ориентир от специалиста"
MANUAL_TARGET_CALLBACK = "cb:anketa:manual_target"
MANUAL_CONFIRM_CALLBACK = "cb:anketa:manual_confirm"
MANUAL_CANCEL_CALLBACK = "cb:anketa:manual_cancel"
MANUAL_CONFIRM_DEVIATION_CALLBACK = "cb:anketa:manual_confirm_deviation"
MANUAL_DEVIATION_NO_CALLBACK = "cb:anketa:manual_deviation_no"
MANUAL_CALLBACKS = frozenset(
    {
        MANUAL_TARGET_CALLBACK,
        MANUAL_CONFIRM_CALLBACK,
        MANUAL_CANCEL_CALLBACK,
        MANUAL_CONFIRM_DEVIATION_CALLBACK,
        MANUAL_DEVIATION_NO_CALLBACK,
    }
)
#: Ключ в ``Conversation.skill_state`` — отдельно от FSM анкеты.
MANUAL_STATE_KEY = "nutrition_manual_target"
#: Свежесть открытого вопроса (как у ``food_correction``): протухший ответ
#: не должен вечно держать текст человека вдали от консьержа.
MANUAL_PENDING_TTL_SECONDS = 600
#: То же окно для любого открытого вопроса навыка (ручной ориентир, вес).
PENDING_TTL_SECONDS = MANUAL_PENDING_TTL_SECONDS

#: Тексты — из листа DRF-2138 дословно. Число в карточке — ТОЛЬКО введённое;
#: порог в отказе — из ответа каталога (``floor_kcal``), не константа бота.
MANUAL_ASK_KCAL = "Сколько ккал в день назначил специалист?"
MANUAL_CARD = "Ориентир от специалиста: {kcal} ккал. Источник — ты, не расчёт Ayla. Подтвердить?"
MANUAL_BELOW_FLOOR = (
    "Такой ориентир я записать не могу — ниже {floor} ккал числа должен вести специалист напрямую."
)
#: Каталог отказал 422, но порога в ответе нет — предложение без числа.
MANUAL_BELOW_FLOOR_NO_NUMBER = (
    "Такой ориентир я записать не могу — такие числа должен вести специалист напрямую."
)
MANUAL_LOW = "Записала; это низкий ориентир — держись под наблюдением специалиста."
MANUAL_DEVIATION_ASK = "Это сильно отличается от расчётного — подтверждаешь?"
#: Заголовок сводки ``user_entered`` — та же формула источника, что в карточке.
MANUAL_SUMMARY_HEAD = "Ориентир от специалиста: {kcal} ккал. Источник — ты, не расчёт Ayla."
#: Не из листа (тексты бота, владелец решает — названы в теле PR).
MANUAL_KCAL_INVALID = "Не разобрала число. " + MANUAL_ASK_KCAL
MANUAL_CANCELLED = "Хорошо, не записываю. Дневник работает как обычно."
MANUAL_UNAVAILABLE = (
    "Не могу записать ориентир: сервис сейчас не отвечает. Нажми «Подтвердить» чуть позже."
)
MANUAL_CONSENT_REQUIRED = (
    "Чтобы записать ориентир, мне нужно согласие на обработку личных данных — "
    "без него я ничего не записываю."
)
MANUAL_BUTTON_CONFIRM = "Подтвердить"
MANUAL_BUTTON_CANCEL = "Не сейчас"
MANUAL_BUTTON_YES = "Да, подтверждаю"
MANUAL_BUTTON_NO = "Нет"
#: Происхождение согласия для кнопки «Дать согласие» (DRF-1968, welcome).
MANUAL_CONSENT_ORIGIN = "target"

# ─── «обнови вес» (DRF-2139) ──────────────────────────────────────────────

UPDATE_WEIGHT_BUTTON = "⚖️ Обновить вес"
UPDATE_WEIGHT_CALLBACK = "cb:anketa:update_weight"
UPDATE_WEIGHT_CANCEL_CALLBACK = "cb:anketa:update_weight_cancel"
UPDATE_WEIGHT_CALLBACKS = frozenset({UPDATE_WEIGHT_CALLBACK, UPDATE_WEIGHT_CANCEL_CALLBACK})
UPDATE_WEIGHT_STATE_KEY = "nutrition_update_weight"
#: Границы веса — из листа DRF-2139 (п.1: «число 30–300»; п.4: «вес 20» /
#: «вес 400» → «проверь число»). Не порог ориентира — проверка ввода.
UPDATE_WEIGHT_MIN_KG = 30
UPDATE_WEIGHT_MAX_KG = 300
#: Снимок старше этого — анкета заново (лист, п.3: возраст мог измениться).
UPDATE_WEIGHT_SNAPSHOT_MAX_AGE_DAYS = 365
#: Входы снимка, которые уезжают обратно ровно как были (+ новый вес).
_SNAPSHOT_INPUTS: tuple[str, ...] = ("gender", "age", "height_cm", "activity_coefficient", "goal")

#: Вопрос 59 (CD §72) — не из листа, в списке владельцу: отказ каталога
#: без названных входов называет, чего не хватает.
INSUFFICIENT_INPUTS_TEXT = (
    "Дневник готов — записывай еду и воду, я всё сохраню.\n"
    "Ориентиры не считаю: не хватает данных — {fields}. "
    "Пройди анкету ещё раз и ответь на {which} — тогда посчитаю."
)
_MISSING_INPUT_LABELS: dict[str, str] = {
    "gender": "пол",
    "age": "возраст",
    "height_cm": "рост",
    "weight_kg": "вес",
    "goal": "цель",
    "pace": "темп",
    "activity_coefficient": "активность",
}

#: Тексты из листа DRF-2139 — дословно.
UPDATE_WEIGHT_ASK = "Какой текущий вес в килограммах?"  # тот же вопрос, что в анкете
UPDATE_WEIGHT_NEED_ANKETA = "Сначала пройдём анкету — так я посчитаю точно."
UPDATE_WEIGHT_STALE = "Давно не обновляли анкету — пройдём заново."
UPDATE_WEIGHT_CHECK_NUMBER = "Проверь число — вес в килограммах, от {lo} до {hi}."
#: Не из листа (владелец решает; названы в теле PR).
#: DRF-2193 (каталог #525): вес пишется, ручной ориентир не трогается.
UPDATE_WEIGHT_MANUAL_SAVED = (
    "Записала вес — {kg} кг. Твой ориентир от специалиста остаётся прежним, "
    "я его не пересчитываю. Если он изменился, впиши новый: «ориентир от "
    "специалиста» и число ккал."
)
#: Перед анкетой при ручном ориентире — анкета его не заменит (каталог #525).
ANKETA_OVER_MANUAL_NOTE = "Анкета не заменит твой ориентир от специалиста — он останется."
#: DRF-2225 — таймаут пробы «стоит ли ориентир специалиста». Проба — вежливость,
#: не ворота: вход в анкету не ждёт общие 10 с клиента, а таймаут пробы не
#: кормит общий breaker питания. Инженерная константа (не продуктовый порог):
#: заметно короче хода чата, с запасом на холодный TLS до каталога.
MANUAL_TARGET_PROBE_TIMEOUT_S = 1.5
#: Ручной ориентир без согласия на расчёт: каталог принимает вес только с
#: утверждением этого согласия — сказать об этом, а не о «расчёте».
UPDATE_WEIGHT_MANUAL_NO_CONSENT = (
    "Вес записываю только с согласием на персональный расчёт — его пока нет, "
    "поэтому вес не записала. Твой ориентир от специалиста остаётся прежним."
)
#: Карточка предложения рядом с действующим (каталог DRF-2192).
PENDING_HEAD = "Предлагаю ориентиры по новым данным — посмотри и подтверди:"
PENDING_ACTING = "Пока ты не подтвердишь, действует прежний ориентир — {what}."
PENDING_UNCHANGED = "Без изменений:"

#: Строки карточки по видам (каталог DRF-1929): калории и всё, что из них
#: выведено, — и вода.
_KIND_ROWS: dict[str, tuple[str, ...]] = {
    "calories": ("daily_kcal", "protein_g", "fat_g", "carbs_g"),
    "fluids": ("water_ml",),
}
UPDATE_WEIGHT_WHOLE_NUMBER = "Напиши вес целым числом — например, 68."
UPDATE_WEIGHT_CANCELLED = "Хорошо, вес не меняю."

#: DRF-2279 (CD §76, №32) — прежние умолчания переспрашиваются, а не
#: переносятся. ЧЕРНОВИКИ текстов — владельцу на утверждение (в PR).
CB_UW_ACTIVITY = "cb:anketa:uw_activity:"
CB_UW_PACE = "cb:anketa:uw_pace:"
#: Голос бота — на «ты» (поправка главного окна к #1998).
UPDATE_WEIGHT_CONFIRM_ACTIVITY = (
    "Прежде чем пересчитать: активность в профиле — прежнее значение по "
    "умолчанию, а не твой ответ. Какая у тебя обычно активность?"
)
UPDATE_WEIGHT_CONFIRM_PACE = (
    "Прежде чем пересчитать: темп в профиле — «{current}», но {chose} — "
    "его подставляла прежняя версия анкеты. Какой темп оставить?"
)


#: DRF-2267 (§72): у аварийного хвоста «обнови вес» тоже есть следующий шаг.
#: Ярлыки — уже живущие в боте: чип «Обновить вес» (повторить, когда каталог
#: ответит) и «Меню». Новых видимых текстов здесь нет.
def _update_weight_down(reply_kind: str) -> SkillResult:
    from apps.orchestrator.next_steps import menu_button

    return SkillResult(
        reply_text=_AYLA_DOWN_FALLBACK,
        action_data={
            "buttons": [
                {"label": UPDATE_WEIGHT_BUTTON, "callback": UPDATE_WEIGHT_CALLBACK},
                menu_button(),
            ]
        },
        meta={"reply_kind": reply_kind},
    )


#: Склонение по полу из анкеты, как у подсказки цели; пола нет — без рода.
_CONFIRM_PACE_CHOSE = {"female": "выбрала его не ты", "male": "выбрал его не ты"}
_CONFIRM_PACE_CHOSE_DEFAULT = "выбран он не тобой"
#: Порядок вопросов: активность, потом темп — как в анкете.
_LEGACY_CONFIRM_ORDER: tuple[tuple[str, str], ...] = (
    ("activity_coefficient", "confirm_activity"),
    ("pace", "confirm_pace"),
)
_UPDATE_WEIGHT_STEPS = ("weight", "confirm_activity", "confirm_pace")
_LEGACY_MARKS = frozenset(name for name, _step in _LEGACY_CONFIRM_ORDER)
_LEGACY_MARK_ALIASES = {"activity": "activity_coefficient"}

#: Строка целиком (вход уже в нижнем регистре): «[я|сейчас|теперь] [мой|новый]
#: вес|вешу [сейчас|теперь] N [кг]» или «обнови(ть) вес [N]». Якоря и
#: ``\d{1,3}`` держат форму детерминированной.
_UPDATE_WEIGHT_PHRASE_RE = re.compile(
    r"^(?:(?:я|сейчас|теперь)\s+)?(?:(?:мой|новый)\s+)?(?:вес|вешу)(?:\s+(?:сейчас|теперь))?"
    r"\s*[—:-]?\s*(\d{1,3}(?:[.,]\d+)?)\s*(?:кг)?\s*$"
    r"|^обнови(?:ть)?\s+вес\s*[—:-]?\s*(\d{1,3}(?:[.,]\d+)?)?\s*(?:кг)?\s*$"
)
_WEIGHT_ANSWER_RE = re.compile(r"^\s*(\d{1,3}(?:[.,]\d+)?)\s*(?:кг)?\s*$", re.IGNORECASE)

#: Матчер входа — три условия, без эвристики по смыслу: слово специалиста
#: + число С ЕДИНИЦЕЙ ккал (или буквальная фраза «ориентир от специалиста»)
#: + рядом с числом нет единицы не-ккал / рублей / года. «врач сказал 1800»
#: без «ккал» — не наше (ревью #1907: «у специалиста был в 2019»,
#: «консультация специалиста 3000 руб» иначе читались бы как ориентир).
_MANUAL_ENTRY_PHRASE = "ориентир от специалиста"
_SPECIALIST_WORD_RE = re.compile(
    r"\b(врач|диетолог|нутрициолог|специалист|доктор|эндокринолог|терапевт)\w*",
    re.IGNORECASE,
)
_KCAL_UNIT = r"(ккал|калори\w*|kcal)"
_KCAL_NUMBER_RE = re.compile(r"(?<![\d-])(\d{3,5})(?!\d)\s*" + _KCAL_UNIT, re.IGNORECASE)
_ANY_NUMBER_RE = re.compile(r"(?<![\d-])\d{3,5}(?!\d)")
_NON_KCAL_UNIT_RE = re.compile(
    r"(?<![\d-])\d{3,5}(?!\d)\s*"
    r"(шаг\w*|мл|л\b|мг|г\b|гр\b|грамм\w*|кг|см|мин\w*|м\b|руб\w*|₽|р\b|год\w*|г\.)",
    re.IGNORECASE,
)
#: «1 800» / «1.800» — обычное русское написание тысяч; диапазон «1500-1800»
#: — два числа, не наше (человек не назвал одно).
_THOUSANDS_RE = re.compile(r"(?<!\d)(\d{1,2})[ .](\d{3})(?!\d)")
_RANGE_RE = re.compile(r"\d{3,5}\s*[-–—]\s*\d{3,5}|\bот\s+\d{3,5}\s+до\s+\d{3,5}")
_PLAIN_NUMBER_RE = re.compile(r"^\s*(\d{1,6})\s*" + _KCAL_UNIT + r"?\s*$", re.IGNORECASE)
WITHDRAW_BUTTON_CONFIRM = "Отключить и удалить"
WITHDRAW_BUTTON_KEEP = "Оставить как есть"

WITHDRAW_CONFIRM_ASK = (
    "Отключить персональный расчёт?\n\n"
    "Ayla перестанет использовать ваши вес, рост, возраст, пол для расчёта, "
    "уровень активности и цель, удалит их из профиля, а персональные нормы "
    "станут недоступны. История дневника сохранится."
)
WITHDRAW_DONE = (
    "Персональный расчёт отключён. Параметры удалены, нормы больше не "
    "показываются. Дневник продолжает работать как обычно.\n\n"
    "Если захотите вернуть расчёт, напишите: «Рассчитать мои нормы»."
)
#: DRF-2135 — те же предложения при выключенном контуре, минус два, которые
#: при OFF ложны: дневник не работает, а «Рассчитать мои нормы» получит
#: заглушку. Ничего нового не обещается — только убрана неправда.
WITHDRAW_DONE_CONTOUR_OFF = (
    "Персональный расчёт отключён. Параметры удалены, нормы больше не показываются."
)
#: Согласие снято, но каталог не подтвердил удаление: правда важнее
#: гладкости — параметры уже НЕ используются, а «удалены» сказать нельзя.
WITHDRAW_DELETE_UNCONFIRMED = (
    "Персональный расчёт отключён: параметры больше не используются и нормы "
    "не показываются. Удаление из профиля пока не подтверждено — повторите "
    "«Отключить персональный расчёт» через пару минут, я доведу его до конца."
)
WITHDRAW_KEPT = "Оставляю как есть: персональный расчёт работает."
WITHDRAW_KEPT_CONTOUR_OFF = "Оставляю как есть: согласие остаётся."
WITHDRAW_NOTHING_TO_WITHDRAW = (
    "Персональный расчёт и так не включён — отключать нечего. Дневник работает как обычно."
)
WITHDRAW_NOTHING_TO_WITHDRAW_CONTOUR_OFF = (
    "Персональный расчёт и так не включён — отключать нечего."
)


#: DRF-2124 — подсказка на шаге цели. «обычно выбирают» — про людей с такой
#: целью, не про этого человека; хвост склоняется по полу с первого шага
#: (поправка главного окна 20.09: «сама/сам» с косой чертой не звучит).
#: Без «рекомендую» и без предвыбора: выбор — вход человека (§7.1/§5.1).
_GOAL_HINT_LINE = "Под цель «{goal}» обычно выбирают {options} — но выбирай {you}."
_GOAL_HINT_YOU = {"female": "сама", "male": "сам"}
_GOAL_HINT_YOU_DEFAULT = "сама"

_CONSENT_ATTESTATION_MISSING = (
    "Персональный расчёт пока не запускаю: на обработку веса, роста, возраста "
    "и остального нужно отдельное согласие с версией текста, а у меня его нет — "
    "экран с ним ещё не готов.\n\n"
    "Дневник от этого не закрывается: записывай еду и воду, я посчитаю и покажу, "
    "сколько вышло за день. Как появится экран — предложу посчитать ориентир."
)


@register
class NutritionAnketaSkill:
    """5-step nutrition profile collection."""

    name: ClassVar[str] = "nutrition_anketa"

    # ─── match ──────────────────────────────────────────────────────────

    def matches(self, context: SkillContext) -> bool:
        text = context.message_text.strip()

        # Entry path — explicit command OR start callback OR the phrase the
        # declined text itself promises («напишите: „Рассчитать мои нормы“»).
        if text == "/anketa" or text == "cb:anketa:start" or _is_entry_phrase(text):
            return True

        # Ответ на экран согласия. Забираем оба, включая отказ: молчание
        # на «не сейчас» человек прочтёт как поломку.
        if text in (CONSENT_GRANT_CALLBACK, CONSENT_DECLINE_CALLBACK):
            return True

        # Отзыв — canonical action: кнопка/слаг и та же фраза текстом.
        if text in (WITHDRAW_CALLBACK, WITHDRAW_CONFIRM_CALLBACK, WITHDRAW_KEEP_CALLBACK):
            return True
        if _is_withdraw_phrase(text):
            return True

        # DRF-2138: ориентир от специалиста — кнопки, фраза, ход диалога.
        if text in MANUAL_CALLBACKS or _manual_target_entry(text) is not None:
            return True
        if _manual_text_is_an_answer(self._manual_state(context), text):
            return True

        # DRF-2139: «обнови вес» — кнопки, фраза, ответ на вопрос веса.
        if text in UPDATE_WEIGHT_CALLBACKS or _update_weight_entry(text) is not None:
            return True
        # DRF-2279: кнопки подтверждения — наши и после истечения вопроса: иначе
        # нажатие уходило в «не поняла», и вес пропадал молча.
        if text.startswith((CB_UW_ACTIVITY, CB_UW_PACE)):
            return True
        if update_weight_pending(context.conversation) and not text.startswith("cb:"):
            return True

        # Resume path — claim turns while an FSM is in flight.
        if self._has_active_fsm(context):
            # Anketa choice callback or plain user input.
            if text.startswith("cb:anketa:choice:") or not text.startswith("cb:"):
                return True

        # Edit callback — channel adapter routes after summary card shown.
        if text.startswith("cb:anketa:edit:"):
            return True

        # §5.1: подтверждение предложенного ориентира — кнопка под карточкой.
        if text == CB_CONFIRM_TARGETS:
            return True

        return False

    # ─── handle ──────────────────────────────────────────────────────────

    def handle(self, context: SkillContext) -> SkillResult:
        # DRF-1994 (решение U) / DRF-1295 — единый выключатель контура
        # питания, и стоит он В НАВЫКЕ, а не по хендлерам. Сюда сходятся
        # ВСЕ входы анкеты: ``/anketa``, ``cb:anketa:*``, входная фраза,
        # продолжение FSM, согласие, инструмент консьержа через
        # ``nutrition_global._run_skill``. Новый вход, доехавший до навыка,
        # упрётся в ворота, не зная о них. Единственное, что стоит выше
        # ворот, — отзыв согласия (DRF-2135, ниже).
        #
        # ``matches`` флаг НЕ читает намеренно. Верни он False, «/anketa»
        # уехал бы модели, и модель заговорила бы о питании сама — ровно
        # то, что DRF-1295 запрещает. Навык забирает ход и отвечает
        # заглушкой; стерегут это ``test_nutrition_single_switch_1994``.
        text = context.message_text.strip()

        # DRF-2135 — ОТЗЫВ согласия стоит ВЫШЕ ворот выключателя, и флаг на
        # этом пути не читается вовсе. Отзыв — не функция контура питания, а
        # право человека (§92): он обязан работать при любом флаге, как
        # ``me/*-consent/`` в Mini App. Удаление параметров тела в каталоге
        # (``purge_body_parameters``) — часть отзыва, поэтому при OFF каталог
        # по этому пути зовётся. Дать согласие (``grant``/``decline``) при OFF
        # нельзя — экран согласия при OFF и не показывается — эти ветки
        # остаются под воротами ниже. Порядок веток стережёт
        # ``test_withdraw_outside_switch_2135``.
        if text == WITHDRAW_CALLBACK or _is_withdraw_phrase(text):
            return self._on_withdraw_ask(context)
        if text == WITHDRAW_CONFIRM_CALLBACK:
            return self._on_withdraw_confirm(context)
        if text == WITHDRAW_KEEP_CALLBACK:
            return self._on_withdraw_keep(context)

        if not _nutrition_enabled():
            return SkillResult(
                reply_text=_nutrition_unavailable_text(),
                meta={"reply_kind": "nutrition_anketa_nutrition_off"},
            )

        # Ответ на экран согласия — до всего остального.
        if text == CONSENT_GRANT_CALLBACK:
            return self._on_consent_granted(context)
        if text == CONSENT_DECLINE_CALLBACK:
            return self._on_consent_declined(context)

        # DRF-2138: ориентир от специалиста — до анкеты: свой диалог, своё состояние.
        manual = self._route_manual_target(context, text)
        if manual is not None:
            return manual

        # DRF-2139: «обнови вес» — до анкеты: один вопрос, своё состояние.
        weight = self._route_update_weight(context, text)
        if weight is not None:
            return weight

        # Entry: start fresh FSM.
        if text in ("/anketa", "cb:anketa:start") or _is_entry_phrase(text):
            return self._on_enter(context)

        # Edit: jump back to a step.
        if text.startswith("cb:anketa:edit:"):
            return self._on_edit(context, text)

        # §5.1: человек подтверждает предложение.
        if text == CB_CONFIRM_TARGETS:
            return self._on_confirm_targets(context)

        # Resume — load FSM + transition.
        return self._on_transition(context, text)

    # ─── entry ──────────────────────────────────────────────────────────

    def _on_enter(self, context: SkillContext) -> SkillResult:
        # ГЕЙТ НА ВХОДЕ, А НЕ НА ШАГЕ ВЕСА И НЕ НА ЗАВЕРШЕНИИ.
        #
        # Проверка утверждения стояла в `_on_complete` — то есть ПОСЛЕ
        # того, как человек назвал пол, возраст, рост, вес и цель, а FSM
        # сохранила их в `Conversation.skill_state`. Пять параметров из
        # шести, перечисленных §92, оказывались собраны и записаны до
        # того, как появлялось основание их собирать.
        #
        # §92 требует гейт на входе в анкету прямым текстом, и довод там
        # тот же: гейт на шаге веса «исполнил бы букву правила и оставил
        # четыре параметра из шести собранными без основания».
        #
        # Прежняя проверка в `_on_complete` НЕ снимается: она вторая
        # линия. Согласие может быть отозвано между входом и завершением,
        # и тогда параметры не должны уехать в каталог.
        from apps.consent.personal_calculation import is_granted

        if not is_granted(context.bot_user):
            return self._render_consent_ask()

        fsm = AnketaFSM()
        step_result = fsm.enter()
        self._save_state(context, fsm)
        result = self._render_step(fsm.current_step, step_result.prompt, context=context, fsm=fsm)
        # Каталог (#525) ручной ориентир анкетой не заменяет — сказать это
        # ДО вопросов, а не после: человек со словами врача должен знать,
        # что анкета не отнимет его число. Проба чтения падает в «нет» —
        # тогда фразы просто нет, анкета идёт как обычно.
        if self._has_manual_target(context):
            result.reply_text = f"{ANKETA_OVER_MANUAL_NOTE}\n\n{result.reply_text}"
        return result

    # ─── edit (cb:anketa:edit:{step}) ────────────────────────────────────

    def _on_edit(self, context: SkillContext, text: str) -> SkillResult:
        # Parse cb:anketa:edit:{step}. The D2 parse_callback splits to
        # action="edit", ref="{step}".
        parsed = parse_callback(text)
        if parsed is None or not parsed.get("ref"):
            return SkillResult(reply_text="", should_send=False)
        step = parsed["ref"]
        fsm = self._load_or_new(context)
        try:
            step_result = fsm.goto(step)
        except ValueError:
            # Unknown step — defensive.
            return SkillResult(reply_text="", should_send=False)
        self._save_state(context, fsm)
        return self._render_step(fsm.current_step, step_result.prompt, context=context, fsm=fsm)

    # ─── transition ──────────────────────────────────────────────────────

    def _on_transition(self, context: SkillContext, text: str) -> SkillResult:
        fsm = self._load_or_new(context)
        if not fsm.current_step:
            # No active FSM but match() claimed the turn — defensive enter.
            return self._on_enter(context)

        # Кнопка другого шага (старая клавиатура в истории чата) — не ответ
        # на текущий вопрос. У активности и темпа общий slug «moderate»:
        # без этой проверки нажатая «Средняя активность» на вопросе о темпе
        # записалась бы темпом, которого человек не выбирал (вопрос 59).
        tapped_step = self._callback_step(text)
        if tapped_step is not None and tapped_step != fsm.current_step:
            return self._render_step(
                fsm.current_step,
                fsm.STEPS[fsm.current_step].prompt,
                context=context,
                fsm=fsm,
            )

        # Extract user input — from choice callback if present, else raw text.
        value = self._extract_value(text)

        # Which step this answer belongs to — `transition` advances
        # `current_step`, so it has to be read before the call.
        answered_step = fsm.current_step
        result = fsm.transition(value)

        # §7.1 stop-gates. Checked here, on the turn the answer arrives:
        # before the next question is asked (so nobody in a stop scenario
        # is asked their weight) and before any call to Ayla.
        stop = self._stop_reason(answered_step, fsm.answers, result)
        if stop is not None:
            self._clear_state(context)
            return self._render_stop(stop, context)

        if answered_step == "screening":
            # Decided above; not kept. Popping before `_save_state` is what
            # keeps it out of `Conversation.skill_state` — and out of the
            # `answers` copy that `Completed` hands to `_on_complete`, so
            # it cannot reach the payload either.
            fsm.answers.pop("screening", None)

        if isinstance(result, NextStep):
            self._save_state(context, fsm)
            return self._render_step(fsm.current_step, result.prompt, context=context, fsm=fsm)

        # Completed → POST to Ayla.
        assert isinstance(result, Completed)
        if "activity" not in result.answers:
            # DRF-2102: a state serialised before the activity step existed
            # (it stood on «goal» with no activity answer). The number is
            # asked, not assumed — no default is written for a person who
            # was never asked; the goal is asked again after it, that is
            # the whole cost. Guards the mechanism; whether such states
            # exist on the pilot is not claimed here.
            step_result = fsm.goto("activity")
            self._save_state(context, fsm)
            return self._render_step(fsm.current_step, step_result.prompt, context=context, fsm=fsm)
        if result.answers.get("goal") in PACE_GOALS and not result.answers.get("pace"):
            # Вопрос 59: состояние, сохранённое до шага «темп», — темп
            # спрашивается, а не подставляется за человека.
            step_result = fsm.goto("pace")
            self._save_state(context, fsm)
            return self._render_step(fsm.current_step, step_result.prompt, context=context, fsm=fsm)
        return self._on_complete(context, result.answers)

    # ─── stop scenarios (§7.1) ───────────────────────────────────────────

    def _stop_reason(
        self,
        answered_step: str,
        answers: dict,
        result: NextStep | Completed,
    ) -> str | None:
        """Name of the stop scenario this answer triggers, or ``None``.

        Returns a bucket name (``"minor"`` / ``"screening"``), never the
        declared condition: the bucket is all the caller needs, and the
        condition is special-category data we have decided not to carry.
        """
        if isinstance(result, NextStep) and result.is_validation_error:
            # Nothing was stored — the FSM re-asks the same step. Reading
            # `answers` here would test the PREVIOUS answer to this step
            # and stop on an input the person has not actually given.
            return None

        if answered_step == "age":
            age = answers.get("age")
            if isinstance(age, int) and age < ADULT_AGE:
                return "minor"

        if answered_step == "screening" and answers.get("screening") != SCREENING_CLEAR:
            return "screening"

        return None

    def _render_stop(self, reason: str, context: SkillContext) -> SkillResult:
        """State 4 of the owner's twelve: no automatic result, diary intact."""
        # Bucket only. `stop=screening` says everything the operator needs;
        # `stop=pregnancy_nursing` would put a health fact in the log file.
        logger.info(
            "anketa.stop_scenario reason=%s conv=%s",
            reason,
            getattr(context.conversation, "id", None),
        )
        return SkillResult(
            reply_text=_STOP_TEXTS[reason],
            action_type="anketa_stop",
            action_data={
                "reason": reason,
                # DRF-2138: первой — ручка для числа от специалиста (режим 3
                # §82): тот, кому не считаем, может принести своё число.
                # Дальше — те же ходы, что после законченной анкеты.
                "buttons": [
                    {"label": MANUAL_TARGET_BUTTON, "callback": MANUAL_TARGET_CALLBACK},
                    *_post_anketa_chips(),
                ],
            },
            meta={"reply_kind": f"anketa_stop_{reason}"},
        )

    def _on_complete(self, context: SkillContext, answers: dict) -> SkillResult:
        external_id = external_user_id_for(context.bot_user)

        # DRF-1658: утверждение о согласии берётся ДО сборки тела и ДО
        # сети. Без него параметры тела не уходят — ни с полем, ни без.
        # Импорт ленивый, как у остальных обращений к моделям из скиллов:
        # реестр скиллов собирается раньше, чем готовы приложения Django.
        from apps.consent.personal_calculation import (
            ConsentAttestationUnavailable,
            current_attestation,
        )

        try:
            attestation = current_attestation(context.bot_user)
        except ConsentAttestationUnavailable as exc:
            return self._render_consent_attestation_missing(context, reason=exc.reason)

        payload = self._build_ayla_payload(answers, attestation)

        try:
            profile = asyncio.run(
                get_nutrition_client().upsert_profile(
                    external_user_id=external_id,
                    data=payload,
                )
            )
        except NutritionUnavailableError:
            logger.warning("anketa.ayla_unavailable user=%s", external_id)
            return SkillResult(
                reply_text=_AYLA_DOWN_FALLBACK,
                meta={"reply_kind": "anketa_ayla_down"},
            )
        except NutritionAPIError:
            logger.exception("anketa.ayla_api_error user=%s", external_id)
            return SkillResult(
                reply_text=_AYLA_DOWN_FALLBACK,
                meta={"reply_kind": "anketa_ayla_error"},
            )

        # Wipe FSM state — anketa is one-shot per user.
        self._clear_state(context)

        return SkillResult(
            reply_text=_format_summary(profile),
            action_type="anketa_complete",
            action_data={
                "daily_kcal": profile.daily_kcal,
                "protein_g": profile.protein_g,
                "fat_g": profile.fat_g,
                "carbs_g": profile.carbs_g,
                "water_ml": profile.water_ml,
                "goal_overridden_by": profile.goal_overridden_by,
                # DRF-1302 — the anketa used to compute the norms and go
                # silent, leaving the person holding five numbers and no next
                # move. These two are the only next moves that exist as
                # working code today, and each callback is claimed by a
                # deterministic matcher (WaterSkill's beverage parser;
                # ``looks_like_diary_request``), so a tap runs rather than
                # asks a model to guess. A photo cannot be a chip -- the
                # person has to send one -- so the closing line invites it in
                # words instead of a button that could not deliver.
                "buttons": _post_anketa_chips(profile),
            },
            meta={"reply_kind": "anketa_complete"},
        )

    # ─── confirm (cb:anketa:confirm_targets) — §5.1 ───────────────────────

    def _on_confirm_targets(self, context: SkillContext) -> SkillResult:
        """Предложение → действующий ориентир. Только по нажатию человека.

        Кнопка подтверждает ТО, что предложено: тела нет, каталог
        переводит ``ayla_proposed`` в ``ayla_calculated`` и возвращает
        профиль уже с числами (инвариант DTO их больше не прячет).
        ``already_confirmed`` — повтор кнопки, отвечаем тем же, без
        второго «подтверждено». ``NOTHING_TO_CONFIRM`` — три разных
        ответа по источнику; «не вышло» без имени здесь запрещено.
        """
        external_id = external_user_id_for(context.bot_user)
        try:
            profile, outcome = asyncio.run(
                get_nutrition_client().confirm_targets(external_user_id=external_id)
            )
        except NothingToConfirmError as exc:
            logger.info("anketa.confirm_targets.nothing user=%s source=%s", external_id, exc.source)
            return SkillResult(
                reply_text=_NOTHING_TO_CONFIRM_TEXTS.get(exc.source, _NOTHING_TO_CONFIRM_TEXTS[""]),
                action_type="anketa_confirm_targets_nothing",
                action_data={"targets_source": exc.source, "buttons": _post_anketa_chips(None)},
                meta={"reply_kind": "anketa_confirm_targets_nothing"},
            )
        except NutritionUnavailableError:
            logger.warning("anketa.confirm_targets.ayla_unavailable user=%s", external_id)
            return SkillResult(
                reply_text=_AYLA_DOWN_FALLBACK, meta={"reply_kind": "anketa_ayla_down"}
            )
        except NutritionAPIError:
            logger.exception("anketa.confirm_targets.ayla_api_error user=%s", external_id)
            return SkillResult(
                reply_text=_AYLA_DOWN_FALLBACK, meta={"reply_kind": "anketa_ayla_error"}
            )

        logger.info("anketa.confirm_targets user=%s outcome=%s", external_id, outcome)
        head = (
            "Ориентиры уже были подтверждены — действуют в дневнике."
            if outcome == "already_confirmed"
            else "Ориентиры подтверждены — теперь действуют в дневнике."
        )
        return SkillResult(
            reply_text=f"{head}\n\n{_format_summary(profile)}",
            claims_done=True,
            claims_done_evidence="ayla.profile.confirm_targets:2xx",
            action_type="anketa_targets_confirmed",
            action_data={
                "outcome": outcome,
                "daily_kcal": profile.daily_kcal,
                "buttons": _post_anketa_chips(profile),
            },
            meta={
                "reply_kind": "anketa_targets_confirmed",
            },
        )

    # ─── helpers ────────────────────────────────────────────────────────

    def _has_active_fsm(self, context: SkillContext) -> bool:
        raw = getattr(context.conversation, "skill_state", None) or {}
        bucket = raw.get(_STATE_KEY) if isinstance(raw, dict) else None
        return bool(bucket and bucket.get("current_step"))

    def _load_or_new(self, context: SkillContext) -> AnketaFSM:
        raw = getattr(context.conversation, "skill_state", None) or {}
        bucket = raw.get(_STATE_KEY) if isinstance(raw, dict) else None
        return AnketaFSM.deserialize(bucket)  # type: ignore[return-value]

    def _save_state(self, context: SkillContext, fsm: AnketaFSM) -> None:
        """Persist FSM state to the conversation row.

        Per D3 contract the skill explicitly writes — pipeline doesn't
        auto-persist. We use ``update_fields`` to limit the write to
        just the JSON column.
        """
        conversation = context.conversation
        # Conversations retro B1: route through the atomic helper so
        # two concurrent skills writing different sub-keys can't
        # clobber each other. The helper handles the select_for_update
        # + read-modify-write inside a single transaction.
        if _is_real_orm_conversation(conversation):
            from apps.conversations.services import write_skill_state

            write_skill_state(conversation, _STATE_KEY, fsm.serialize())
            return

        # Mock / synthetic conversations (used by isolated unit tests that
        # don't spin up the ORM): fall back to in-memory mutation.
        raw = getattr(conversation, "skill_state", None) or {}
        if not isinstance(raw, dict):
            raw = {}
        raw[_STATE_KEY] = fsm.serialize()
        conversation.skill_state = raw
        save = getattr(conversation, "save", None)
        if callable(save):
            save(update_fields=["skill_state"])

    def _clear_state(self, context: SkillContext) -> None:
        conversation = context.conversation
        if _is_real_orm_conversation(conversation):
            from apps.conversations.services import write_skill_state

            write_skill_state(conversation, _STATE_KEY, None)
            return

        raw = getattr(conversation, "skill_state", None) or {}
        if isinstance(raw, dict) and _STATE_KEY in raw:
            conversation.skill_state = {k: v for k, v in raw.items() if k != _STATE_KEY}
            save = getattr(conversation, "save", None)
            if callable(save):
                save(update_fields=["skill_state"])

    @staticmethod
    def _callback_step(text: str) -> str | None:
        """Шаг из ``cb:anketa:choice:{step}:{value}`` — или ``None`` для текста."""
        if not text.startswith("cb:anketa:choice:"):
            return None
        parsed = parse_callback(text)
        if parsed is None:
            return None
        step, _, _value = (parsed.get("ref") or "").partition(":")
        return step or None

    def _extract_value(self, text: str) -> str:
        """Pull the value from a ``cb:anketa:choice:{step}:{value}`` callback
        OR return the raw text. Plain numbers and free-text pass through."""
        if not text.startswith("cb:anketa:choice:"):
            return text
        parsed = parse_callback(text)
        if parsed is None:
            return text
        ref = parsed.get("ref") or ""
        # ref shape is "{step}:{value}" — return just the value half.
        _step, _, value = ref.partition(":")
        return value or text

    def _render_consent_ask(self) -> SkillResult:
        """Экран согласия: текст с версией и два действия.

        Версия не показывается человеку — она записывается вместе с
        согласием. Показывать её значило бы требовать от человека помнить
        номер; доказывать, на что он согласился, — наша работа, а не его.
        """
        from apps.consent.personal_calculation import (
            PERSONAL_CALCULATION_DOCUMENT_VERSION,
        )

        return SkillResult(
            reply_text=CONSENT_ASK,
            action_type="anketa_consent_ask",
            action_data={
                "document_version": PERSONAL_CALCULATION_DOCUMENT_VERSION,
                "buttons": [
                    {"label": CONSENT_BUTTON_GRANT, "callback": CONSENT_GRANT_CALLBACK},
                    {
                        "label": CONSENT_BUTTON_DECLINE,
                        "callback": CONSENT_DECLINE_CALLBACK,
                    },
                ],
            },
            meta={"reply_kind": "anketa_consent_ask"},
        )

    def _on_consent_granted(self, context: SkillContext) -> SkillResult:
        """Записать согласие и начать анкету — но только если записалось.

        `grant` возвращает результат ЧТЕНИЯ, а не факт вызова. Если
        реестр не отдал запись обратно, анкета не начинается: собрать
        параметры и потом обнаружить, что основания нет, хуже, чем
        попросить человека повторить.
        """
        from apps.consent.personal_calculation import (
            PERSONAL_CALCULATION_DOCUMENT_VERSION,
            grant,
        )

        recorded = grant(
            context.bot_user,
            document_version=PERSONAL_CALCULATION_DOCUMENT_VERSION,
        )
        logger.info(
            "anketa.consent_granted conv=%s recorded=%s version=%s",
            getattr(context, "conversation_id", None),
            recorded,
            PERSONAL_CALCULATION_DOCUMENT_VERSION,
        )
        if not recorded:
            return SkillResult(
                reply_text=CONSENT_RECORDED_BUT_UNREADABLE,
                meta={"reply_kind": "anketa_consent_unreadable"},
            )
        return self._on_enter(context)

    def _on_consent_declined(self, context: SkillContext) -> SkillResult:
        """Отказ — не ошибка и не тупик.

        §92: «отказ не закрывает дневник» — он продолжает работать в
        своём объёме, и текст говорит об этом первым делом, а не
        последним.
        """
        logger.info(
            "anketa.consent_declined conv=%s",
            getattr(context, "conversation_id", None),
        )
        return SkillResult(
            reply_text=CONSENT_DECLINED,
            meta={"reply_kind": "anketa_consent_declined"},
        )

    # ─── отзыв согласия (владелец 12.09 §2) ───────────────────────────────

    def _on_withdraw_ask(self, context: SkillContext) -> SkillResult:
        """Canonical action «Отключить персональный расчёт» → подтверждение.

        Ничего не удаляется до нажатия «Отключить и удалить»: после него
        параметры уходят из профиля, и вернуть их можно только заново
        рассказав. Нечего отключать — говорим и это, а не «готово».
        """
        from apps.consent.personal_calculation import is_granted

        # DRF-2138 (касание отзыва): ручной ориентир ставится без согласия M,
        # и «нечего отключать» при стоящем user_entered было бы неправдой —
        # purge каталога снимает и его. Профиль читается только когда M нет.
        # DRF-2225: проба теперь короткая и мимо breaker'а; «не ответил» (None)
        # здесь читается как прежде — «нет» (поведение отзыва этим листом не
        # меняется, предел назван в PR).
        if not is_granted(context.bot_user) and self._manual_target_probe(context) is not True:
            return SkillResult(
                reply_text=self._contour_copy(
                    WITHDRAW_NOTHING_TO_WITHDRAW, WITHDRAW_NOTHING_TO_WITHDRAW_CONTOUR_OFF
                ),
                meta={"reply_kind": "anketa_withdraw_nothing"},
            )
        return SkillResult(
            reply_text=WITHDRAW_CONFIRM_ASK,
            action_type="anketa_withdraw_ask",
            action_data={
                "buttons": [
                    {"label": WITHDRAW_BUTTON_CONFIRM, "callback": WITHDRAW_CONFIRM_CALLBACK},
                    {"label": WITHDRAW_BUTTON_KEEP, "callback": WITHDRAW_KEEP_CALLBACK},
                ]
            },
            meta={"reply_kind": "anketa_withdraw_ask"},
        )

    def _on_withdraw_confirm(self, context: SkillContext) -> SkillResult:
        """Снять согласие, удалить параметры в каталоге, закрыть нормы.

        Порядок — сначала согласие: с этого мига параметры НЕ используются
        (гейт анкеты закрыт, утверждения для POST нет), что бы ни случилось
        дальше. Затем удаление в каталоге — оно инвалидирует нормы там же.
        Если каталог не подтвердил, сказать «удалены» нельзя: человек
        получает честное «отключено, удаление не подтверждено» и путь
        повторить тем же действием.
        """
        from apps.consent.personal_calculation import withdraw

        withdrawn = withdraw(context.bot_user)
        self._clear_state(context)
        logger.info(
            "anketa.consent_withdrawn conv=%s rows=%s",
            getattr(context, "conversation_id", None),
            withdrawn,
        )

        external_id = external_user_id_for(context.bot_user)
        try:
            deleted = asyncio.run(
                get_nutrition_client().purge_body_parameters(external_user_id=external_id)
            )
        except (NutritionUnavailableError, NutritionAPIError) as exc:
            logger.warning(
                "anketa.withdraw_purge_unconfirmed user=%s error=%s",
                external_id,
                exc.__class__.__name__,
            )
            deleted = False
        if not deleted:
            return SkillResult(
                reply_text=WITHDRAW_DELETE_UNCONFIRMED,
                meta={"reply_kind": "anketa_withdraw_unconfirmed"},
            )
        return SkillResult(
            reply_text=self._contour_copy(WITHDRAW_DONE, WITHDRAW_DONE_CONTOUR_OFF),
            # ``deleted`` — прочитанный результат, а не факт вызова: при
            # ``False`` ветка выше отвечает «не подтверждено».
            claims_done=True,
            claims_done_evidence="ayla.profile.purge:deleted",
            meta={"reply_kind": "anketa_withdraw_done"},
        )

    def _on_withdraw_keep(self, context: SkillContext) -> SkillResult:
        return SkillResult(
            reply_text=self._contour_copy(WITHDRAW_KEPT, WITHDRAW_KEPT_CONTOUR_OFF),
            meta={"reply_kind": "anketa_withdraw_kept"},
        )

    # ─── «обнови вес» (DRF-2139) ────────────────────────────────────────

    def _save_update_weight_state(self, context: SkillContext, bucket: dict | None) -> None:
        _write_bucket(context.conversation, UPDATE_WEIGHT_STATE_KEY, bucket)

    def _route_update_weight(self, context: SkillContext, text: str) -> SkillResult | None:
        """Вход / ход «обнови вес» — или ``None`` («не наше»)."""
        pending = update_weight_pending(context.conversation)

        if pending and (text in ("/anketa", "cb:anketa:start") or _is_entry_phrase(text)):
            self._save_update_weight_state(context, None)
            return None
        if text == UPDATE_WEIGHT_CANCEL_CALLBACK:
            self._save_update_weight_state(context, None)
            return SkillResult(
                reply_text=UPDATE_WEIGHT_CANCELLED,
                meta={"reply_kind": "anketa_update_weight_cancelled"},
            )
        if text == UPDATE_WEIGHT_CALLBACK:
            return self._update_weight_ask(context)

        entry = _update_weight_entry(text)
        if entry is not None:
            if self._has_active_fsm(context):
                # Ревью #1912: «вешу 65» на шаге веса анкеты — ответ шагу, не
                # короткий путь: иначе предложение считалось бы от СТАРОГО
                # снимка, пока человек прямо сейчас называет новые входы.
                return None
            self._clear_open_questions(context)
            if entry == "":
                return self._update_weight_ask(context)
            return self._update_weight_with(context, entry)

        if text.startswith((CB_UW_ACTIVITY, CB_UW_PACE)):
            return self._update_weight_confirm(context, text)
        if pending and not text.startswith("cb:"):
            bucket = self._update_weight_bucket(context)
            if bucket and str(bucket.get("step", "")).startswith("confirm_"):
                # DRF-2279: вопрос задан кнопками — текст ответом не
                # считается, вопрос повторяется; веса он не меняет.
                return self._update_weight_confirm_ask(context, bucket)
            return self._update_weight_with(context, text)
        return None

    def _update_weight_bucket(self, context: SkillContext) -> dict | None:
        return _fresh_bucket(context.conversation, UPDATE_WEIGHT_STATE_KEY, _UPDATE_WEIGHT_STEPS)

    def _clear_open_questions(self, context: SkillContext) -> None:
        """Смена курса: незаконченная анкета и ЧУЖОЙ открытый вопрос (ручной
        ориентир ↔ вес) снимаются — два открытых вопроса разом читали бы одно
        число двумя способами (ревью #1912)."""
        self._clear_state(context)
        state = getattr(context.conversation, "skill_state", None)
        present = set(state) if isinstance(state, dict) else set()
        # Пишется только то, что стоит: лишний write на ORM-беседе — лишняя
        # транзакция (сторож r4 DRF-2138 считает записи).
        if MANUAL_STATE_KEY in present:
            self._save_manual_state(context, None)
        if UPDATE_WEIGHT_STATE_KEY in present:
            self._save_update_weight_state(context, None)

    def _update_weight_ask(self, context: SkillContext) -> SkillResult:
        self._clear_open_questions(context)
        self._save_update_weight_state(context, {"step": "weight"})
        return SkillResult(
            reply_text=UPDATE_WEIGHT_ASK,
            action_type="anketa_update_weight_ask",
            action_data={
                "buttons": [
                    {"label": MANUAL_BUTTON_CANCEL, "callback": UPDATE_WEIGHT_CANCEL_CALLBACK}
                ]
            },
            meta={"reply_kind": "anketa_update_weight_ask"},
        )

    def _update_weight_with(self, context: SkillContext, raw: str) -> SkillResult:
        """Число есть — проверить ввод (границы листа), потом профиль, потом POST."""
        parsed = _parse_weight(raw)
        if isinstance(parsed, str):
            # «68,5» — дробь: переспрос целым, число за человека не округляется.
            self._save_update_weight_state(context, {"step": "weight"})
            return SkillResult(
                reply_text=UPDATE_WEIGHT_WHOLE_NUMBER,
                meta={"reply_kind": "anketa_update_weight_invalid"},
            )
        if parsed is None:
            # Не число вовсе («что я ел сегодня») — не «проверь число».
            self._save_update_weight_state(context, {"step": "weight"})
            return SkillResult(
                reply_text=UPDATE_WEIGHT_WHOLE_NUMBER,
                meta={"reply_kind": "anketa_update_weight_invalid"},
            )
        if not (UPDATE_WEIGHT_MIN_KG <= parsed <= UPDATE_WEIGHT_MAX_KG):
            self._save_update_weight_state(context, {"step": "weight"})
            return SkillResult(
                reply_text=UPDATE_WEIGHT_CHECK_NUMBER.format(
                    lo=UPDATE_WEIGHT_MIN_KG, hi=UPDATE_WEIGHT_MAX_KG
                ),
                meta={"reply_kind": "anketa_update_weight_check_number"},
            )
        weight = int(parsed)
        self._save_update_weight_state(context, None)

        external_id = external_user_id_for(context.bot_user)
        try:
            profile = asyncio.run(get_nutrition_client().get_profile(external_user_id=external_id))
        except NutritionUnavailableError:
            logger.warning("anketa.update_weight_ayla_unavailable step=read")
            return SkillResult(
                reply_text=_AYLA_DOWN_FALLBACK, meta={"reply_kind": "anketa_ayla_down"}
            )
        except NutritionAPIError:
            logger.exception("anketa.update_weight_ayla_error step=read")
            return SkillResult(
                reply_text=_AYLA_DOWN_FALLBACK, meta={"reply_kind": "anketa_ayla_error"}
            )

        source = getattr(profile, "targets_source", "") if profile is not None else ""
        if source == "user_entered":
            # DRF-2193 (каталог #525): вес пишется, ручной ориентир каталог не
            # трогает и предложения рядом не кладёт. Тело — только вес:
            # снимка у ручного ориентира нет, полей анкеты бот не выдумывает.
            return self._update_weight_over_manual(context, external_id, weight)
        body = _update_weight_body(profile, weight)
        if body is None:
            return self._update_weight_go_to_anketa(UPDATE_WEIGHT_NEED_ANKETA, "need_anketa")
        if _snapshot_is_stale(profile):
            return self._update_weight_go_to_anketa(UPDATE_WEIGHT_STALE, "stale")

        # DRF-2279: помеченное прежнее умолчание не переносится — у каталога
        # присланное значение и есть подтверждение, и бот подтвердил бы его
        # за человека. Сначала вопрос; вес держится в состоянии.
        to_ask = _legacy_steps(profile)
        if to_ask:
            bucket = {
                "step": to_ask[0],
                "weight": weight,
                "answers": {},
                # Темп — каким он был, когда вопрос задан: одна запись на ход.
                "current_pace": str(getattr(profile, "goal_pace", "") or ""),
                "gender": str(getattr(profile, "gender", "") or ""),
            }
            self._save_update_weight_state(context, bucket)
            return self._update_weight_confirm_ask(context, bucket, profile=profile)

        return self._update_weight_send(context, external_id, body)

    def _update_weight_send(
        self, context: SkillContext, external_id: str, body: dict[str, Any]
    ) -> SkillResult:
        """Согласие M → POST → карточка предложения (общий хвост «обнови вес»)."""
        from apps.consent.personal_calculation import (
            ConsentAttestationUnavailable,
            attach as attach_consent,
            current_attestation,
        )

        try:
            attestation = current_attestation(context.bot_user)
        except ConsentAttestationUnavailable as exc:
            return self._render_consent_attestation_missing(context, reason=exc.reason)

        try:
            proposed = asyncio.run(
                get_nutrition_client().upsert_profile(
                    external_user_id=external_id, data=attach_consent(body, attestation)
                )
            )
        except NutritionUnavailableError:
            logger.warning("anketa.update_weight_ayla_unavailable step=upsert")
            return _update_weight_down("anketa_ayla_down")
        except NutritionAPIError:
            logger.exception("anketa.update_weight_ayla_error step=upsert")
            return _update_weight_down("anketa_ayla_error")

        logger.info(
            "anketa.update_weight_proposed conv=%s source=%s",
            getattr(context.conversation, "id", None),
            getattr(proposed, "targets_source", ""),
        )
        return SkillResult(
            reply_text=_format_summary(proposed),
            action_type="anketa_update_weight_proposed",
            action_data={"buttons": _post_anketa_chips(proposed)},
            meta={"reply_kind": "anketa_update_weight_proposed"},
        )

    def _update_weight_over_manual(
        self, context: SkillContext, external_id: str, weight: int
    ) -> SkillResult:
        from apps.consent.personal_calculation import (
            ConsentAttestationUnavailable,
            attach as attach_consent,
            current_attestation,
        )

        try:
            attestation = current_attestation(context.bot_user)
        except ConsentAttestationUnavailable as exc:
            # Путь ручного ориентира согласия на расчёт не спрашивает, и у
            # многих его нет. Общий текст «расчёт пока не запускаю…»
            # сказал бы про расчёт, которого здесь нет, — говорим про вес.
            logger.info("anketa.update_weight_manual_no_consent reason=%s", exc.reason)
            return SkillResult(
                reply_text=UPDATE_WEIGHT_MANUAL_NO_CONSENT,
                meta={"reply_kind": "anketa_update_weight_manual_no_consent"},
            )
        try:
            saved = asyncio.run(
                get_nutrition_client().upsert_profile(
                    external_user_id=external_id,
                    data=attach_consent({"weight_kg": weight}, attestation),
                )
            )
        except NutritionUnavailableError:
            logger.warning("anketa.update_weight_ayla_unavailable step=upsert_manual")
            return SkillResult(
                reply_text=_AYLA_DOWN_FALLBACK, meta={"reply_kind": "anketa_ayla_down"}
            )
        except NutritionAPIError:
            logger.exception("anketa.update_weight_ayla_error step=upsert_manual")
            return SkillResult(
                reply_text=_AYLA_DOWN_FALLBACK, meta={"reply_kind": "anketa_ayla_error"}
            )
        saved_kg = getattr(saved, "weight_kg", None)
        if saved_kg is not None and round(float(saved_kg)) != weight:
            # Каталог ответил, но другим весом — не говорим «записала».
            logger.warning("anketa.update_weight_manual_mismatch")
            return SkillResult(
                reply_text=_AYLA_DOWN_FALLBACK, meta={"reply_kind": "anketa_ayla_error"}
            )
        return SkillResult(
            reply_text=UPDATE_WEIGHT_MANUAL_SAVED.format(kg=weight),
            action_data={"buttons": _post_anketa_chips(saved)},
            # Подтверждение сверено: вес, вернувшийся от каталога, равен
            # тому, что просили (иначе ветка выше отвечает аварийным текстом).
            claims_done=True,
            claims_done_evidence="ayla.profile.upsert:weight_kg",
            meta={"reply_kind": "anketa_update_weight_manual_saved"},
        )

    # ─── DRF-2279: прежние умолчания — вопрос, а не перенос ─────────────

    def _update_weight_confirm_ask(
        self, context: SkillContext, bucket: dict, *, profile: Any = None
    ) -> SkillResult:
        """Вопрос текущего шага подтверждения; вес ждёт в ``bucket``."""
        cancel = {"label": MANUAL_BUTTON_CANCEL, "callback": UPDATE_WEIGHT_CANCEL_CALLBACK}
        if bucket.get("step") == "confirm_activity":
            buttons = [
                {"label": label, "callback": f"{CB_UW_ACTIVITY}{slug}"}
                for slug, label in ACTIVITY_CHOICES.items()
            ]
            return SkillResult(
                reply_text=UPDATE_WEIGHT_CONFIRM_ACTIVITY,
                action_type="anketa_update_weight_confirm",
                action_data={"buttons": [*buttons, cancel]},
                meta={"reply_kind": "anketa_update_weight_confirm_activity"},
            )
        current = str(bucket.get("current_pace") or getattr(profile, "goal_pace", "") or "")
        if current and "current_pace" not in bucket:
            bucket = {**bucket, "current_pace": current}
            self._save_update_weight_state(context, bucket)
        # Нынешний темп — первым: это вопрос «оставить?», а не выбор с нуля.
        order = [current] if current in PACE_CHOICES else []
        order += [slug for slug in PACE_CHOICES if slug not in order]
        buttons = [
            {
                "label": f"{PACE_CHOICES[slug]} — оставить"
                if slug == current
                else PACE_CHOICES[slug],
                "callback": f"{CB_UW_PACE}{slug}",
            }
            for slug in order
        ]
        return SkillResult(
            reply_text=UPDATE_WEIGHT_CONFIRM_PACE.format(
                current=PACE_CHOICES.get(current, current or "—"),
                chose=_CONFIRM_PACE_CHOSE.get(
                    str(bucket.get("gender") or getattr(profile, "gender", "") or ""),
                    _CONFIRM_PACE_CHOSE_DEFAULT,
                ),
            ),
            action_type="anketa_update_weight_confirm",
            action_data={"buttons": [*buttons, cancel]},
            meta={"reply_kind": "anketa_update_weight_confirm_pace"},
        )

    def _update_weight_confirm(self, context: SkillContext, text: str) -> SkillResult:
        """Ответ кнопкой: записать, следующий вопрос или — все названы — POST."""
        bucket = self._update_weight_bucket(context)
        if bucket is None or not str(bucket.get("step", "")).startswith("confirm_"):
            # Вопрос истёк или не задавался — вес не угадывается, спрашиваем заново.
            return self._update_weight_ask(context)
        step = bucket["step"]
        answers = dict(bucket.get("answers") or {})
        if step == "confirm_activity" and text.startswith(CB_UW_ACTIVITY):
            slug = text[len(CB_UW_ACTIVITY) :]
            if slug not in ACTIVITY_CHOICES:
                return self._update_weight_confirm_ask(context, bucket)
            answers["activity"] = slug
        elif step == "confirm_pace" and text.startswith(CB_UW_PACE):
            slug = text[len(CB_UW_PACE) :]
            if slug not in PACE_CHOICES:
                return self._update_weight_confirm_ask(context, bucket)
            answers["pace"] = slug
        else:
            # Кнопка другого шага (старое сообщение) — текущий вопрос снова.
            return self._update_weight_confirm_ask(context, bucket)

        external_id = external_user_id_for(context.bot_user)
        try:
            profile = asyncio.run(get_nutrition_client().get_profile(external_user_id=external_id))
        except NutritionUnavailableError:
            self._save_update_weight_state(context, None)
            logger.warning("anketa.update_weight_ayla_unavailable step=confirm_read")
            return _update_weight_down("anketa_ayla_down")
        except NutritionAPIError:
            self._save_update_weight_state(context, None)
            logger.exception("anketa.update_weight_ayla_error step=confirm_read")
            return _update_weight_down("anketa_ayla_error")

        if getattr(profile, "targets_source", "") == "user_entered":
            # Пока вопрос висел, человек поставил ориентир специалиста: вес
            # пишется поверх ручного ориентира (DRF-2193), а не теряется.
            self._save_update_weight_state(context, None)
            return self._update_weight_over_manual(context, external_id, int(bucket["weight"]))

        answered = {"confirm_activity": "activity", "confirm_pace": "pace"}
        remaining = [s for s in _legacy_steps(profile) if answered[s] not in answers]
        if remaining:
            bucket = {**bucket, "step": remaining[0], "answers": answers}
            self._save_update_weight_state(context, bucket)
            return self._update_weight_confirm_ask(context, bucket, profile=profile)

        self._save_update_weight_state(context, None)
        body = _update_weight_body(profile, int(bucket["weight"]))
        if body is None:
            return self._update_weight_go_to_anketa(UPDATE_WEIGHT_NEED_ANKETA, "need_anketa")
        if _snapshot_is_stale(profile):
            return self._update_weight_go_to_anketa(UPDATE_WEIGHT_STALE, "stale")
        # Ответы — НАЗВАННЫЕ человеком: у каталога они снимают пометку.
        if "activity" in answers:
            if answers["activity"] == ACTIVITY_SKIP:
                # «Не знаю» — пропуск, не число (вопрос 59): каталог ответит
                # «не хватает данных: активность».
                body.pop("activity_coefficient", None)
                body["_skipped_fields"] = ["activity"]
            else:
                body["activity_coefficient"] = ACTIVITY_COEFFICIENTS[answers["activity"]]
        if "pace" in answers and body.get("goal") in PACE_GOALS:
            body["pace"] = answers["pace"]
        return self._update_weight_send(context, external_id, body)

    @staticmethod
    def _update_weight_go_to_anketa(text: str, why: str) -> SkillResult:
        from apps.orchestrator.personal_surface import CHIP_ANKETA

        return SkillResult(
            reply_text=text,
            action_data={"buttons": [dict(CHIP_ANKETA)]},
            meta={"reply_kind": f"anketa_update_weight_{why}"},
        )

    # ─── ориентир от специалиста (DRF-2138) ─────────────────────────────

    def _manual_state(self, context: SkillContext) -> dict | None:
        return manual_target_state(context.conversation)

    def _save_manual_state(self, context: SkillContext, bucket: dict | None) -> None:
        """Тот же атомарный писатель, что у FSM (retro B1): подключ, не весь
        ``skill_state`` — соседние навыки пишут свои ключи параллельно."""
        _write_bucket(context.conversation, MANUAL_STATE_KEY, bucket)

    def _manual_target_probe(self, context: SkillContext) -> bool | None:
        """Стоит ли у человека ``user_entered`` — ``True`` / ``False``, или
        ``None`` — каталог не ответил за :data:`MANUAL_TARGET_PROBE_TIMEOUT_S`
        (или отказал). Таймаут пробы общий breaker не кормит (DRF-2225).
        Что делать с ``None`` — решает каждый вызывающий, и решение названо.
        """
        try:
            profile = asyncio.run(
                get_nutrition_client().get_profile(
                    external_user_id=external_user_id_for(context.bot_user),
                    timeout_s=MANUAL_TARGET_PROBE_TIMEOUT_S,
                    feeds_circuit=False,
                )
            )
        except Exception as exc:  # noqa: BLE001 — чтение ради вежливости, не ворота
            logger.info("anketa.manual_target_probe_failed class=%s", type(exc).__name__)
            return None
        return getattr(profile, "targets_source", "") == "user_entered"

    def _has_manual_target(self, context: SkillContext) -> bool:
        """Вход в анкету: фраза «анкета не заменит ориентир специалиста» — только
        при ПОДТВЕРЖДЁННОМ ``user_entered``. Каталог не ответил → фразы нет: без
        ответа бот не утверждает, что у человека есть ориентир (DRF-2225)."""
        return self._manual_target_probe(context) is True

    def _manual_consent_refusal(self, context: SkillContext) -> SkillResult | None:
        """PERSONAL_DATA — то же второе ворото, что у писателей дневника
        (``diary_write_refusal``); согласие M здесь не спрашивается."""
        from apps.orchestrator import personal_surface

        if personal_surface.personal_records_consent_open(context.bot_user):
            return None
        from apps.skills.welcome.skill import consent_offer_action_data

        self._save_manual_state(context, None)
        return SkillResult(
            reply_text=MANUAL_CONSENT_REQUIRED,
            action_data=consent_offer_action_data(MANUAL_CONSENT_ORIGIN),
            meta={"reply_kind": "anketa_manual_target_consent_required"},
        )

    def _route_manual_target(self, context: SkillContext, text: str) -> SkillResult | None:
        """Вход / ход диалога ручного ориентира — или ``None`` («не наше»)."""
        state = self._manual_state(context)

        # Выходы: «/anketa», кнопка старта и входная фраза анкеты снимают
        # открытый вопрос и уходят в анкету (ревью #1907: иначе «/anketa» на
        # шаге числа отвечал бы «Не разобрала число» без конца).
        if state is not None and (text in ("/anketa", "cb:anketa:start") or _is_entry_phrase(text)):
            self._save_manual_state(context, None)
            return None

        if text == MANUAL_TARGET_CALLBACK:
            return self._manual_start(context, kcal=None)
        entry = _manual_target_entry(text)
        if entry is not None:
            return self._manual_start(context, kcal=entry or None)

        if text == MANUAL_CANCEL_CALLBACK or text == MANUAL_DEVIATION_NO_CALLBACK:
            self._save_manual_state(context, None)
            return SkillResult(
                reply_text=MANUAL_CANCELLED,
                action_data={"buttons": _post_anketa_chips()},
                meta={"reply_kind": "anketa_manual_target_cancelled"},
            )
        if text == MANUAL_CONFIRM_CALLBACK:
            if state is None or state.get("step") not in ("confirm", "deviation"):
                return self._manual_stale()
            return self._manual_post(context, int(state["kcal"]), confirm_deviation=False)
        if text == MANUAL_CONFIRM_DEVIATION_CALLBACK:
            if state is None or state.get("step") != "deviation":
                return self._manual_stale()
            return self._manual_post(context, int(state["kcal"]), confirm_deviation=True)

        if _manual_text_is_an_answer(state, text):
            kcal = _parse_kcal(text)
            if kcal is None:
                # Только на шаге числа: на карточке чужой текст не наш.
                return SkillResult(
                    reply_text=MANUAL_KCAL_INVALID,
                    action_data={"buttons": _manual_cancel_button()},
                    meta={"reply_kind": "anketa_manual_target_kcal_invalid"},
                )
            # На карточке новое число — поправка: карточка перерисовывается.
            return self._manual_card(context, kcal)
        return None

    def _manual_start(self, context: SkillContext, *, kcal: int | None) -> SkillResult:
        refusal = self._manual_consent_refusal(context)
        if refusal is not None:
            return refusal
        # Незаконченная анкета и открытый вопрос веса не переживают смену
        # курса: их состояние стояло бы на шаге, к которому человек не вернётся.
        self._clear_open_questions(context)
        if kcal is None:
            self._save_manual_state(context, {"step": "kcal"})
            return SkillResult(
                reply_text=MANUAL_ASK_KCAL,
                action_type="anketa_manual_target_ask",
                action_data={"buttons": _manual_cancel_button()},
                meta={"reply_kind": "anketa_manual_target_ask"},
            )
        return self._manual_card(context, kcal)

    def _manual_card(self, context: SkillContext, kcal: int) -> SkillResult:
        self._save_manual_state(context, {"step": "confirm", "kcal": kcal})
        return SkillResult(
            reply_text=MANUAL_CARD.format(kcal=kcal),
            action_type="anketa_manual_target_card",
            action_data={
                "buttons": [
                    {"label": MANUAL_BUTTON_CONFIRM, "callback": MANUAL_CONFIRM_CALLBACK},
                    {"label": MANUAL_BUTTON_CANCEL, "callback": MANUAL_CANCEL_CALLBACK},
                ]
            },
            meta={"reply_kind": "anketa_manual_target_card"},
        )

    @staticmethod
    def _manual_stale() -> SkillResult:
        return SkillResult(
            reply_text=MANUAL_CANCELLED,
            meta={"reply_kind": "anketa_manual_target_stale"},
        )

    def _manual_post(
        self, context: SkillContext, kcal: int, *, confirm_deviation: bool
    ) -> SkillResult:
        """POST в каталог — только отсюда, только по тапу подтверждения."""
        refusal = self._manual_consent_refusal(context)
        if refusal is not None:
            return refusal
        external_id = external_user_id_for(context.bot_user)
        # Флаг подтверждения — только когда человек его дал: тело без ключа,
        # не ``false`` (тот же контракт, что на проводе).
        kwargs: dict = {"external_user_id": external_id, "calories_kcal": kcal}
        if confirm_deviation:
            kwargs["confirm_deviation"] = True
        try:
            profile, report = asyncio.run(get_nutrition_client().set_manual_targets(**kwargs))
        except ManualTargetsRefusedError as exc:
            # 422: не сохранено; порог называет каталог.
            self._save_manual_state(context, None)
            logger.info("anketa.manual_target_refused code=%s", exc.code)
            floor = exc.details.get("floor_kcal")
            return SkillResult(
                reply_text=(
                    MANUAL_BELOW_FLOOR.format(floor=floor)
                    if isinstance(floor, int) and not isinstance(floor, bool)
                    else MANUAL_BELOW_FLOOR_NO_NUMBER
                ),
                action_data={"buttons": _post_anketa_chips()},
                meta={"reply_kind": "anketa_manual_target_refused"},
            )
        except ManualTargetsConfirmationRequiredError as exc:
            self._save_manual_state(context, {"step": "deviation", "kcal": kcal})
            logger.info("anketa.manual_target_confirmation_required kind=%s", exc.kind)
            return SkillResult(
                reply_text=MANUAL_DEVIATION_ASK,
                action_type="anketa_manual_target_deviation",
                action_data={
                    "buttons": [
                        {"label": MANUAL_BUTTON_YES, "callback": MANUAL_CONFIRM_DEVIATION_CALLBACK},
                        {"label": MANUAL_BUTTON_NO, "callback": MANUAL_DEVIATION_NO_CALLBACK},
                    ]
                },
                meta={"reply_kind": "anketa_manual_target_deviation"},
            )
        except (NutritionUnavailableError, NutritionAPIError) as exc:
            # Число не теряется: карточка стоит, кнопку можно нажать ещё раз.
            logger.warning("anketa.manual_target_unavailable class=%s", type(exc).__name__)
            return SkillResult(
                reply_text=MANUAL_UNAVAILABLE,
                action_data={
                    "buttons": [
                        {"label": MANUAL_BUTTON_CONFIRM, "callback": MANUAL_CONFIRM_CALLBACK},
                        {"label": MANUAL_BUTTON_CANCEL, "callback": MANUAL_CANCEL_CALLBACK},
                    ]
                },
                meta={"reply_kind": "anketa_manual_target_unavailable"},
            )

        self._save_manual_state(context, None)
        logger.info(
            "anketa.manual_target_set warnings=%s conv=%s",
            list(report.get("warnings") or []),
            getattr(context.conversation, "id", None),
        )
        parts = [_format_summary(profile)]
        if "calories_low" in (report.get("warnings") or []):
            parts.append(MANUAL_LOW)
        return SkillResult(
            reply_text="\n\n".join(parts),
            action_type="anketa_manual_target_done",
            action_data={"buttons": _post_anketa_chips(profile)},
            meta={"reply_kind": "anketa_manual_target_done"},
        )

    @staticmethod
    def _contour_copy(when_on: str, when_off: str) -> str:
        """Копия отзыва по состоянию контура. Флаг здесь ВЫБИРАЕТ ФРАЗУ, не
        решает исход: отзыв уже произошёл (или не понадобился) до этой строки.
        При OFF убраны предложения, которые при OFF ложны («дневник работает»,
        «напишите „Рассчитать мои нормы“»); новых обещаний нет."""
        return when_on if _nutrition_enabled() else when_off

    def _render_step(
        self,
        step: str,
        prompt: str,
        *,
        context: SkillContext | None = None,
        fsm: AnketaFSM | None = None,
    ) -> SkillResult:
        action_data: dict = {"step": step}
        if step in CHOICE_STEPS:
            # Клавиатура — вся таблица в её порядке, метки как в таблице
            # (история хода читает их той же таблицей — стража a5
            # DRF-2102). Подсказка цели (ниже) кнопок не касается.
            action_data["buttons"] = anketa_choice_keyboard(step, choice_keyboard_options(step))
        if step == "goal" and context is not None:
            # DRF-2124: подсказка — строкой под вопросом и данными в
            # action_data; в answers она не пишет ничего. Без context
            # (рендер шага вне хода — тесты провода MAX) подсказки нет.
            hint = _goal_hint(context)
            if hint is not None:
                goal_key, label, options = hint
                answers = fsm.answers if fsm is not None else {}
                prompt = f"{prompt}\n\n" + _goal_hint_line(label, options, answers)
                action_data["goal_hint"] = {"goal_key": goal_key, "options": options}
        return SkillResult(
            reply_text=prompt,
            action_type=f"anketa_step_{step}",
            action_data=action_data,
            meta={"reply_kind": f"anketa_{step}"},
        )

    def _render_consent_attestation_missing(
        self, context: SkillContext, *, reason: str
    ) -> SkillResult:
        """DRF-1658: утверждения нет — отказ по названной причине, POST не ушёл.

        ``reason`` — одна из трёх констант
        :mod:`apps.consent.personal_calculation`; в лог идёт она, а не
        общее «нет согласия»: у трёх причин три разных адреса починки.
        """
        # Незавершённая анкета не переживает отказ — иначе следующий ход
        # человека попал бы в FSM, стоящую на «готово», а параметры тела
        # лежали бы в skill_state без основания.
        self._clear_state(context)
        logger.info(
            "anketa.consent_attestation_missing reason=%s conv=%s",
            reason,
            getattr(context.conversation, "id", None),
        )
        return SkillResult(
            reply_text=_CONSENT_ATTESTATION_MISSING,
            action_type="anketa_consent_required",
            action_data={"reason": reason, "buttons": _post_anketa_chips()},
            meta={"reply_kind": "anketa_consent_required"},
        )

    def _build_ayla_payload(self, answers: dict, attestation: "ConsentAttestation") -> dict:
        """Map FSM answers to the Ayla profile schema.

        Активность — ответ человека (DRF-2102): один из четырёх
        коэффициентов §85 по slug. «Не знаю» — пропуск: числа НЕТ (CD §72,
        вопрос 59), уходит только ``_skipped_fields: ["activity"]``. С
        каталожной половиной вопроса 59 каталог по пропуску снимает прежнюю
        активность и отвечает «не хватает данных: активность»; до неё — ещё
        считает от своего умолчания или от активности, названной раньше. До
        вопроса 59 здесь уходило 1.375 — число, выбранное за человека.

        Темп — только при цели, которая его использует (:data:`PACE_GOALS`).
        Отсутствие ответа — ``KeyError``, как у остальных полей: тихого
        умолчания за человека здесь нет (см. ``_on_transition``).

        Утверждение о согласии — обязательный аргумент, не флаг и не
        ``None`` по умолчанию: все шесть полей ниже закрыты границей
        каталога (#324), и тело без утверждения собрать здесь нельзя
        даже по ошибке.
        """
        from apps.consent.personal_calculation import attach as attach_consent

        activity = answers["activity"]
        body = {
            "gender": answers["gender"],
            "age": int(answers["age"]),
            "height_cm": int(answers["height"]),
            "weight_kg": int(answers["weight"]),
            "goal": answers["goal"],
        }
        if activity != ACTIVITY_SKIP:
            body["activity_coefficient"] = ACTIVITY_COEFFICIENTS[activity]
        if answers["goal"] in PACE_GOALS:
            body["pace"] = answers["pace"]
        skipped: list[str] = []
        if activity == ACTIVITY_SKIP:
            skipped.append("activity")

        # DRF-2310. Тип питания: значение из словаря каталога, слова — только
        # у «другого». «Пропустить» значения НЕ подставляет: молчание ответом
        # не становится, уходит пометка с коротким именем вопроса.
        diet = answers.get("diet")
        if diet == DIET_SKIP:
            skipped.append(DIET_SKIP_WIRE_NAME)
        elif diet:
            body["diet_preference"] = diet
            if diet == DIET_OTHER:
                body["diet_note"] = answers["diet_note"]

        if skipped:
            body["_skipped_fields"] = skipped
        return attach_consent(body, attestation)


# ─── helpers ──────────────────────────────────────────────────────────────


def _goal_hint(context: SkillContext) -> tuple[str, str, list[str]] | None:
    """DRF-2124: ``(goal_key, метка, [slug, …])`` подсказки из decision-context или ``None``.

    Читает ``known.goal.nutrition_goal_hint`` активной цели; оставляет только
    значения из таблицы анкеты (:data:`GOAL_CHOICES`) в её порядке — чужое
    (``slim``) не печатается. ``None`` — цели нет (в т.ч. ``is_active: false``,
    как у :mod:`apps.nutrition_coach.goals`), подсказки нет (``null``, §103),
    список без известных значений, документ не той формы, у зеркала нет
    метки для ключа (в текст человеку не печатается сырой слаг — только
    курируемое слово) или Ayla не ответила: подсказка — вежливость, шаг без
    неё полноценен. В лог — класс отказа, без идентификатора канала (DRF-2009).
    """
    try:
        document = fetch_decision_context(external_user_id=external_user_id_for(context.bot_user))
    except Exception as exc:  # noqa: BLE001 — любой отказ чтения = «подсказки нет»
        logger.info("anketa.goal_hint_unavailable class=%s", type(exc).__name__)
        return None
    known = document.get("known") if isinstance(document, dict) else None
    goal = known.get("goal") if isinstance(known, dict) else None
    if not isinstance(goal, dict) or goal.get("is_active") is False:
        return None
    goal_key = goal.get("goal_key")
    raw = goal.get("nutrition_goal_hint")
    if not isinstance(goal_key, str) or not goal_key or not isinstance(raw, list):
        return None
    options = [slug for slug in GOAL_CHOICES if slug in raw]
    if not options:
        return None
    label = goal_label(goal_key)
    if not label or label == goal_key:
        # Зеркало знает только цели с живой услугой; без метки — без подсказки.
        return None
    return goal_key, label, options


def _goal_hint_line(label: str, options: list[str], answers: dict) -> str:
    """Строка подсказки: метка цели из зеркала (не свободный текст человека),
    метки вариантов — из таблицы анкеты, хвост — по полу с первого шага."""
    labels = [f"«{GOAL_CHOICES[slug]}»" for slug in options]
    joined = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " или " + labels[-1]
    you = _GOAL_HINT_YOU.get(str(answers.get("gender")), _GOAL_HINT_YOU_DEFAULT)
    return _GOAL_HINT_LINE.format(goal=label, options=joined, you=you)


def _is_real_orm_conversation(conversation: object) -> bool:
    """True iff ``conversation`` is a real :class:`apps.conversations.models.Conversation` instance.

    Skills test with synthetic ``_StatefulConversation`` classes that do
    NOT subclass ``Conversation`` — the atomic
    :func:`apps.conversations.services.write_skill_state` helper would
    raise ``ValueError`` on those (no tenant_id, no ORM row). Use
    ``isinstance`` so a bare ``MagicMock()`` (whose attribute access
    returns truthy MagicMocks) doesn't get mis-routed into the ORM
    path — closes reviewer Y-1.
    """

    from apps.conversations.models import Conversation

    return isinstance(conversation, Conversation)


# ─── summary rendering ────────────────────────────────────────────────────


#: Кнопка подтверждения предложенного ориентира (§5.1). Callback
#: заявляется этим скиллом детерминированно (``matches``), как и прочие
#: ``cb:anketa:*``.
CB_CONFIRM_TARGETS = "cb:anketa:confirm_targets"
CHIP_CONFIRM_TARGETS = {"label": "✅ Подтвердить ориентиры", "callback": CB_CONFIRM_TARGETS}

#: Ответы на ``NOTHING_TO_CONFIRM`` — по источнику, а не одно «не вышло».
_NOTHING_TO_CONFIRM_TEXTS: dict[str, str] = {
    "none": "Подтверждать пока нечего — ориентиров сейчас нет. Пройди анкету: /anketa.",
    "ayla_calculated": "Ориентиры уже подтверждены — действуют в дневнике.",
    "user_entered": "Ориентиры заданы тобой вручную — подтверждать их не нужно.",
    "unknown_legacy": "Эти ориентиры посчитаны давно, без сохранённого основания — "
    "подтвердить их нельзя. Пройди анкету заново: /anketa.",
    "": "Подтверждать пока нечего.",
}


def _normalise_phrase(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").strip(" .!?«»\"'").split())


def _is_entry_phrase(text: str) -> bool:
    return _normalise_phrase(text) == _normalise_phrase(ENTRY_PHRASE)


def _manual_target_entry(text: str) -> int | None:
    """Детерминированный вход ручного ориентира (DRF-2138) — или ``None``.

    Возвращает число из фразы («мне врач назначил 1800 ккал» → 1800) или
    ``0`` для фразы без числа («ориентир от специалиста» → спросить).
    Не наше: голое число, «1800 ккал» без слова специалиста, число рядом с
    единицей не-ккал («2000 шагов», «2000 мл», «100 г белка») — цифра могла
    быть про что угодно. Не LLM, не эвристика по смыслу — три условия.
    """
    stripped = (text or "").strip()
    if not stripped or stripped.startswith("cb:"):
        return None
    normalised = _join_thousands(_normalise_phrase(stripped))
    literal = _MANUAL_ENTRY_PHRASE in normalised
    if not literal and not _SPECIALIST_WORD_RE.search(normalised):
        return None
    if _NON_KCAL_UNIT_RE.search(normalised) or _RANGE_RE.search(normalised):
        return None
    all_numbers = _ANY_NUMBER_RE.findall(normalised)
    if not all_numbers:
        return 0 if literal else None
    if len(all_numbers) != 1:
        return None
    with_unit = _KCAL_NUMBER_RE.findall(normalised)
    if with_unit:
        return int(with_unit[0][0])
    # Число без «ккал»: только при буквальной фразе — иначе цифра про что угодно.
    return int(all_numbers[0]) if literal else None


def _join_thousands(text: str) -> str:
    """«1 800» / «1.800» → «1800» — обычное написание тысяч."""
    return _THOUSANDS_RE.sub(lambda m: m.group(1) + m.group(2), text)


def _parse_kcal(text: str) -> int | None:
    """Ответ на «Сколько ккал…»: одно число, необязательно с «ккал».
    Диапазон не проверяется здесь — пороги у каталога (§85); отсекается
    только то, что числом не является, и ноль."""
    match = _PLAIN_NUMBER_RE.match(_join_thousands(text or ""))
    if match is None:
        return None
    value = int(match.group(1))
    return value if value > 0 else None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def manual_target_state(conversation: Any) -> dict | None:
    """Свежий открытый вопрос ручного ориентира — или ``None``.

    Протухший (старше :data:`MANUAL_PENDING_TTL_SECONDS`) читается как
    отсутствующий: иначе одно нажатие кнопки навсегда забирало бы у консьержа
    любой текст человека. Читается и оркестратором (``nutrition_global``) —
    на глобальном пути свободный текст доходит до навыка только когда бот сам
    задал вопрос.
    """
    return _fresh_bucket(conversation, MANUAL_STATE_KEY, ("kcal", "confirm", "deviation"))


def manual_target_pending(conversation: Any) -> bool:
    """Для ``is_structured_nutrition_turn``: бот ждёт число или подтверждение."""
    return manual_target_state(conversation) is not None


def _write_bucket(conversation: Any, key: str, bucket: dict | None) -> None:
    """Подключ ``skill_state`` со штампом ``asked_at`` — атомарным писателем
    (retro B1) у ORM-беседы, в памяти — у синтетической."""
    value = None if bucket is None else {**bucket, "asked_at": _now_iso()}
    if _is_real_orm_conversation(conversation):
        from apps.conversations.services import write_skill_state

        write_skill_state(conversation, key, value)
        return
    raw = getattr(conversation, "skill_state", None) or {}
    if not isinstance(raw, dict):
        raw = {}
    raw = {k: v for k, v in raw.items() if k != key} if value is None else {**raw, key: value}
    conversation.skill_state = raw
    save = getattr(conversation, "save", None)
    if callable(save):
        save(update_fields=["skill_state"])


def _fresh_bucket(conversation: Any, key: str, steps: tuple[str, ...]) -> dict | None:
    state = getattr(conversation, "skill_state", None)
    bucket = state.get(key) if isinstance(state, dict) else None
    if not isinstance(bucket, dict) or bucket.get("step") not in steps:
        return None
    stamped = bucket.get("asked_at")
    if isinstance(stamped, str):
        try:
            at = datetime.fromisoformat(stamped)
        except ValueError:
            return None
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        if datetime.now(UTC) - at > timedelta(seconds=PENDING_TTL_SECONDS):
            return None
    return dict(bucket)


def update_weight_pending(conversation: Any) -> bool:
    """DRF-2139, для ``is_structured_nutrition_turn``: бот ждёт вес."""
    return _fresh_bucket(conversation, UPDATE_WEIGHT_STATE_KEY, _UPDATE_WEIGHT_STEPS) is not None


def update_weight_phrase(text: str) -> bool:
    """Для оркестратора: детерминированная фраза «мой вес 65» / «обнови вес»."""
    return _update_weight_entry(text) is not None


def manual_target_phrase(text: str) -> bool:
    """Для оркестратора: детерминированная фраза «мне врач назначил 1800 ккал»."""
    return _manual_target_entry(text) is not None


def _update_weight_entry(text: str) -> str | None:
    """«мой вес 65» → «65»; «обнови вес» → ``""`` (спросить); иначе ``None``.

    Только форма фразы, без эвристики: строка целиком — «[мой] вес|вешу N [кг]»
    или «обнови(ть) вес [N]». «вес ребёнка 20 кг», «перевес багажа 65», «мой
    вес был 90 в прошлом году», «вес 65 и 70» — не наше.
    """
    stripped = (text or "").strip()
    if not stripped or stripped.startswith("cb:"):
        return None
    normalised = _normalise_phrase(stripped)
    match = _UPDATE_WEIGHT_PHRASE_RE.match(normalised)
    if match is None:
        return None
    return match.group(1) or match.group(2) or ""


def _parse_weight(text: str) -> int | str | None:
    """Ответ на вопрос веса: целое → число; дробное → ``"decimal"`` (переспрос
    целым — число за человека не округляется); прочее → ``None``."""
    match = _WEIGHT_ANSWER_RE.match((text or "").strip())
    if match is None:
        return None
    raw = match.group(1)
    if "." in raw or "," in raw:
        return "decimal"
    return int(raw)


def _update_weight_body(profile: Any, weight: int) -> dict[str, Any] | None:
    """Тело upsert — РОВНО входы снимка с новым весом; любого нет → ``None``
    (в анкету: число за человека не подставляется, §103)."""
    if profile is None:
        return None
    if getattr(profile, "targets_source", "") not in ("ayla_calculated", "ayla_proposed"):
        return None
    snapshot = dict(getattr(profile, "targets_input_snapshot", None) or {})
    if any(snapshot.get(name) in (None, "") for name in _SNAPSHOT_INPUTS):
        return None
    # Цель и темп — НАЗВАННЫЕ человеком (поля профиля), а не расчётные из
    # снимка. Снимок хранит результат ступени пола BMR («поддержание»,
    # «мягкий» темп), и отправить его как вход значило бы записать за
    # человека цель и темп, которых он не выбирал (DRF-2241 для цели,
    # вопрос 59 для темпа).
    #
    # Вопрос 59: при цели, которая использует темп, он обязателен; нет его —
    # в анкету, темп бот не подставляет. Предел (назван в PR, решение (а1)):
    # у профилей, посчитанных ДО вопроса 59, темп и активность подставлял
    # каталог — «moderate», 1.4 → 1.375 — и они неотличимы от названных; бот
    # их переносит, как любой вход.
    goal = str(getattr(profile, "goal", "") or "")
    pace = str(getattr(profile, "goal_pace", "") or "")
    if not goal:
        return None
    needs_pace = goal in PACE_GOALS
    if needs_pace and not pace:
        return None
    try:
        # Типы — как шлёт анкета: целые и float; снимок мог прийти с «28.0»
        # или строкой — непригодный снимок = в анкету, не исключение в ходе.
        body: dict[str, Any] = {
            "gender": str(snapshot["gender"]),
            "age": int(float(snapshot["age"])),
            "height_cm": int(float(snapshot["height_cm"])),
            "weight_kg": weight,
            "goal": goal,
            "activity_coefficient": float(snapshot["activity_coefficient"]),
        }
    except (TypeError, ValueError):
        return None
    if needs_pace:
        body["pace"] = pace
    return body


def _legacy_steps(profile: Any) -> list[str]:
    """Шаги подтверждения по пометкам каталога (DRF-2279), в порядке анкеты.

    Темп спрашивается только там, где он меняет число (цель с темпом, вопрос
    59): у «поддерживать» его в теле нет, и подтверждать нечего.
    """
    marks: set[str] = set()
    for name in getattr(profile, "legacy_default_inputs", ()) or ():
        # Имена — каталога (``activity_coefficient``, ``pace``). Тот же API
        # зовёт активность «activity» в ``_skipped_fields``: пометка под этим
        # именем тоже пометка — иначе старое умолчание ушло бы молча.
        name = _LEGACY_MARK_ALIASES.get(name, name)
        if name in _LEGACY_MARKS:
            marks.add(name)
        else:
            logger.warning("anketa.legacy_default_unknown_mark name=%s", name)
    goal = str(getattr(profile, "goal", "") or "")
    steps: list[str] = []
    for name, step in _LEGACY_CONFIRM_ORDER:
        if name not in marks:
            continue
        if name == "pace" and goal not in PACE_GOALS:
            continue
        steps.append(step)
    return steps


def _snapshot_is_stale(profile: Any) -> bool:
    """``raw.targets_provenance.computed_at`` старше 365 дней. Даты нет — не
    проверяется (предел: в DTO поля нет, каталог может не прислать)."""
    raw = getattr(profile, "raw", None) or {}
    provenance = raw.get("targets_provenance") if isinstance(raw, dict) else None
    stamped = provenance.get("computed_at") if isinstance(provenance, dict) else None
    if not isinstance(stamped, str) or not stamped:
        return False
    try:
        at = datetime.fromisoformat(stamped.replace("Z", "+00:00"))
    except ValueError:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return datetime.now(UTC) - at > timedelta(days=UPDATE_WEIGHT_SNAPSHOT_MAX_AGE_DAYS)


def _manual_text_is_an_answer(state: dict | None, text: str) -> bool:
    """Свободный текст — наш ответ? На шаге числа — любой (переспросим);
    на карточке — только новое число (поправка), остальное не наше."""
    if state is None or text.startswith("cb:"):
        return False
    if state.get("step") == "kcal":
        return True
    return _parse_kcal(text) is not None


def _manual_cancel_button() -> list[dict[str, str]]:
    return [{"label": MANUAL_BUTTON_CANCEL, "callback": MANUAL_CANCEL_CALLBACK}]


def _is_withdraw_phrase(text: str) -> bool:
    return _normalise_phrase(text) == _normalise_phrase(WITHDRAW_ACTION_TEXT)


def _post_anketa_chips(profile=None) -> list[dict[str, str]]:
    """The next steps that really execute after the norms land.

    Kept next to the summary it ships with, and built from the
    ``personal_surface`` constants so the labels and callbacks cannot drift
    apart from the matchers that claim them.

    §5.1: при предложении (``ayla_proposed``) первой стоит кнопка
    подтверждения — единственный шаг, который переводит число из
    «предлагаю» в «действует». Без предложения кнопки нет: она обещала бы
    действие, у которого нет предмета.
    """

    from apps.orchestrator.personal_surface import CHIP_DIARY, CHIP_WATER, diary_is_reachable

    chips: list[dict[str, str]] = []
    if profile is not None and (
        profile.targets_source == TARGETS_PROPOSED or pending_proposal(profile)
    ):
        # DRF-2192: предложение рядом с действующим подтверждается той же
        # кнопкой — каталог ``confirm_targets`` забирает его.
        chips.append(dict(CHIP_CONFIRM_TARGETS))
    if profile is not None and profile.targets_source in ("ayla_calculated", "ayla_proposed"):
        # DRF-2139: пересчёт одним вопросом — только там, где есть расчёт,
        # входы которого можно взять из снимка.
        chips.append({"label": UPDATE_WEIGHT_BUTTON, "callback": UPDATE_WEIGHT_CALLBACK})
    chips.append(dict(CHIP_WATER))
    if diary_is_reachable():
        # Only the global path claims «что я ел сегодня» deterministically.
        # On a salon bot the same tap would fall through the skill ladder to a
        # generic reply -- a button that answers «я вас не понял», which is
        # worse than no button.
        chips.append(dict(CHIP_DIARY))
    # Владелец 12.09 §2: отзыв — детерминированная canonical action, не
    # только свободный текст. Кнопка живёт там, где нормы только что
    # появились: человек видит, что их можно и выключить.
    chips.append({"label": WITHDRAW_ACTION_TEXT, "callback": WITHDRAW_CALLBACK})
    return chips


#: Строки карточки: подпись, поле профиля, единица. Порядок значим —
#: он же порядок на экране.
_SUMMARY_ROWS: tuple[tuple[str, str, str], ...] = (
    ("🔥 Калории", "daily_kcal", "ккал/день"),
    ("🍗 Белок", "protein_g", "г"),
    ("🥑 Жиры", "fat_g", "г"),
    ("🍚 Углеводы", "carbs_g", "г"),
    ("💧 Вода", "water_ml", "мл"),
)


#: Подписи методик по версии из каталога. Неизвестная версия печатается
#: как есть — это лучше, чем подпись, которой каталог не давал.
_METHOD_LABELS: dict[str, str] = {
    "mifflin_st_jeor_v1": "Миффлин — Сан Жеор, версия 1",
}

#: Подписи методик ориентира по жидкости (ключ ``fluids`` в
#: ``targets_method_versions``, каталог #403). Раздел 4 решения 09.09:
#: «справочный ориентир по напиткам: женщина 2200 мл/сутки, мужчина
#: 3000 мл/сутки… активность, жара, беременность, кормление и заболевания
#: автоматически не прибавляются». Подпись обязана сказать человеку, что
#: это НЕ расчёт от его веса — иначе цифра рядом с «от твоих данных: вес —
#: 62 кг» читается как посчитанная от веса, а это как раз то, что владелец
#: снял (§82).
_FLUIDS_METHOD_LABELS: dict[str, str] = {
    "adult_beverages_reference_v1": (
        "справочный ориентир по выпитой жидкости для взрослых по полу "
        "(2200 мл женщине, 3000 мл мужчине), не расчёт от веса и активности"
    ),
}

_GENDER_LABELS: dict[str, str] = {"female": "женский", "male": "мужской"}
_GOAL_LABELS: dict[str, str] = {
    "lose": "похудеть",
    "maintain": "поддерживать",
    "gain": "набрать",
    "tone": "подтянуть",
}
_PACE_LABELS: dict[str, str] = {"gentle": "мягкий", "moderate": "средний"}
#: Ступень ``bmr_floor``: каталог перевёл цель «похудеть» в «поддерживать»,
#: потому что дефицит опустил бы ориентир ниже BMR (DRF-2222). Тексты
#: утверждены владельцем дословно (CD §72). Ступень бывает только при
#: названной цели «похудеть» — поэтому цель в тексте литеральная.
_BMR_FLOOR = "bmr_floor"
_BMR_FLOOR_GOAL_TEXT = (
    "Твоя цель — снизить вес. Сейчас ориентир на поддержание: ниже безопасного минимума не опускаю."
)
_BMR_FLOOR_REMARK = "Ориентир не ниже безопасного минимума."
#: Число из снимка → слово, которое человек выбирал (DRF-2102). Значение
#: вне таблицы (1.4 у профилей, посчитанных до шага) печатается числом.
_ACTIVITY_LABELS: dict[float, str] = {
    coefficient: ACTIVITY_CHOICES[slug].lower()
    for slug, coefficient in ACTIVITY_COEFFICIENTS.items()
}


def _method_and_inputs_line(profile) -> str:
    """«Считала по … от твоих данных: …» — или пустая строка.

    Решение владельца 11.09.2026 §5.1: «методика и использованные данные
    показываются человеку». Ориентир без слов «от чего посчитан» — число,
    которому человек должен верить на слово; с этой строкой он видит и
    формулу, и свои же ответы, из которых она посчитана, — и может
    заметить, если что-то ввёл не так.

    Печатается только то, что каталог прислал: пустой снимок — строки
    нет, а не «от твоих данных» без данных; неизвестная версия методики —
    как есть, без подписи. Значения из снимка — ТЕ ЖЕ входы, что и в
    расчёте (каталог собирает снимок по ``SNAPSHOT_INPUTS`` в момент
    расчёта), а не текущие поля профиля.
    """
    snapshot: dict = dict(getattr(profile, "targets_input_snapshot", None) or {})
    if not snapshot:
        return ""
    versions: dict = dict(getattr(profile, "targets_method_versions", None) or {})
    version = versions.get("calories")

    facts: list[str] = []
    gender = snapshot.get("gender")
    if gender:
        facts.append(f"пол — {_GENDER_LABELS.get(str(gender), str(gender))}")
    if snapshot.get("age") is not None:
        facts.append(f"возраст — {snapshot['age']}")
    if snapshot.get("height_cm") is not None:
        facts.append(f"рост — {snapshot['height_cm']} см")
    if snapshot.get("weight_kg") is not None:
        weight = snapshot["weight_kg"]
        weight_text = f"{weight:g}" if isinstance(weight, (int, float)) else str(weight)
        facts.append(f"вес — {weight_text} кг")
    if snapshot.get("activity_coefficient") is not None:
        activity = snapshot["activity_coefficient"]
        label = _ACTIVITY_LABELS.get(activity) if isinstance(activity, (int, float)) else None
        facts.append(f"активность — {label} ({activity})" if label else f"активность — {activity}")
    floored = getattr(profile, "goal_overridden_by", None) == _BMR_FLOOR
    goal = snapshot.get("goal")
    # При ступени в снимке — расчётная цель «поддерживать», а не названная
    # человеком: «цель — поддерживать» выдало бы её за его выбор.
    if goal and not floored:
        facts.append(f"цель — {_GOAL_LABELS.get(str(goal), str(goal))}")
    pace = snapshot.get("pace")
    if pace:
        facts.append(f"темп — {_PACE_LABELS.get(str(pace), str(pace))}")

    head = (
        f"Считала по методике {_METHOD_LABELS.get(str(version), str(version))}"
        if version
        else "Считала"
    )
    line = f"{head} от твоих данных: {', '.join(facts)}."
    return f"{line} {_BMR_FLOOR_GOAL_TEXT}" if floored else line


def _fluids_reference_line(profile) -> str:
    """«Вода — справочный ориентир …» — или пустая строка.

    Печатается только когда каталог назвал методику жидкости (ключ
    ``fluids`` в ``targets_method_versions``): без версии строки нет —
    не «вода по справочнику» на слово, а подпись под тем, что каталог
    подписал. Неизвестная версия печатается как есть, как и у калорий.

    Строка отдельная от ``_method_and_inputs_line`` намеренно: та
    говорит «от твоих данных: вес — 62 кг», и вода в той же фразе
    читалась бы как посчитанная от веса.
    """
    versions: dict = dict(getattr(profile, "targets_method_versions", None) or {})
    version = versions.get("fluids")
    if not version:
        return ""
    return f"Вода — {_FLUIDS_METHOD_LABELS.get(str(version), str(version))}."


#: Подписи health-факторов для человека (§5.1, каталог #372). Имя из
#: ``overrides_applied`` — часть контракта; неизвестное печатается как есть.
_HEALTH_FACTOR_LABELS: dict[str, str] = {
    "pregnant": "беременности",
    "breastfeeding": "грудном вскармливании",
    "eating_disorder": "расстройстве пищевого поведения",
    "minor": "возрасте до 18 лет",
}


def _format_health_factor_refusal(names: list[str]) -> str:
    """«Ориентиры не считаю: при … Ayla их не рассчитывает».

    Решение владельца 11.09.2026 §5.1: «При health-факторах Ayla не
    рассчитывает индивидуальную норму». Отказ назван по факту, без
    чисел и без обещания «сделаю позже»; дневник при этом открыт (§92:
    отказ не закрывает дневник).
    """
    labels = [_HEALTH_FACTOR_LABELS.get(n, n) for n in names]
    joined = ", ".join(labels)
    return (
        f"Ориентиры не считаю: при {joined} Ayla их не рассчитывает.\n"
        "Дневник и вода работают как раньше — записывай, я всё сохраню."
    )


def _format_summary(profile) -> str:
    """Карточка после анкеты — только те ориентиры, которые ЕСТЬ.

    Здесь стояли пять строк подряд, безусловно. Каждая печатала
    `{profile.<поле>}`, и ноль печатался как значение: «💧 Вода: 0 мл»,
    а у человека, не назвавшего вес, — ещё и «🔥 Калории: 0 ккал/день».
    Ноль ккал в сутки не бывает; такая карточка не «пустая», она ЛЖЁТ, и
    лжёт в самом громком месте — сразу после «Готово, посчитала твои
    ориентиры».

    С 09.09.2026 нолей в этих полях штатно много: владелец снял формулу
    воды `30 мл × вес` (§82) и подстановку медианы за пропущенные
    рост/вес/возраст/пол (§85, раздел 3.2 — входы обязательны). Строка
    без значения теперь не печатается вовсе, а если не посчиталось
    НИЧЕГО — карточка не притворяется расчётом.

    Ноль читается как отсутствие, а не как «ориентир ноль»: ни одна из
    пяти величин не может быть нулём у живого человека.
    """
    # §5.1: отказ по health-фактору — сказать ПОЧЕМУ, а не «не считаю».
    refused_for = health_factor_refusals(profile)
    if refused_for:
        return _format_health_factor_refusal(refused_for)

    # Вопрос 59 / #527: каталог не считает без названных входов — назвать
    # их, а не сказать общее «пока не считаю».
    missing = insufficient_inputs(profile)
    has_any_row = any(
        int(getattr(profile, field, 0) or 0) > 0 for _label, field, _unit in _SUMMARY_ROWS
    )
    if missing and not has_any_row and not pending_proposal(profile):
        named = ", ".join(_MISSING_INPUT_LABELS.get(name, name) for name in missing)
        which = "этот вопрос" if len(missing) == 1 else "эти вопросы"
        return INSUFFICIENT_INPUTS_TEXT.format(fields=named, which=which)

    # DRF-2138: ручной ориентир — «от специалиста», не «считала по методике».
    # Число — то, что человек назвал; строки методики и входов нет по
    # построению (снимок пуст), вода справочная остаётся своей строкой.
    if getattr(profile, "targets_source", "") == "user_entered":
        kcal = int(getattr(profile, "daily_kcal", 0) or 0)
        manual_parts = [MANUAL_SUMMARY_HEAD.format(kcal=kcal)] if kcal > 0 else []
        water = int(getattr(profile, "water_ml", 0) or 0)
        if water > 0:
            manual_parts.append(f"💧 Вода: {water} мл")
            manual_fluids = _fluids_reference_line(profile)
            if manual_fluids:
                manual_parts.append(manual_fluids)
        if not manual_parts:
            return (
                "Дневник готов — записывай еду и воду, я всё сохраню.\n"
                "Дневных ориентиров пока не считаю."
            )
        return "\n\n".join(manual_parts)

    # DRF-2192 (каталог #525): новое предложение лежит РЯДОМ с действующим.
    # Показать его как предложение и назвать, что до подтверждения действует
    # прежний ориентир, — иначе карточка говорила бы «Готово, посчитала»
    # прежними числами, а новое было бы невидимо и неподтверждаемо.
    beside = pending_proposal(profile)
    if beside:
        # Показываются ТОЛЬКО пересчитанные виды: вода-предложение не
        # сравнивается с калориями, и действующая вода не пропадает с
        # карточки, если пересчитаны только калории.
        pending_fields = {f for k in beside["kinds"] for f in _KIND_ROWS.get(k, ())}
        pending_rows = [
            f"{label}: {value} {unit}"
            for label, field, unit in _SUMMARY_ROWS
            if field in pending_fields and (value := int(beside["norms"].get(field) or 0)) > 0
        ]
        if pending_rows:
            from types import SimpleNamespace

            pending_parts = [PENDING_HEAD, "\n".join(pending_rows)]
            method_line = _method_and_inputs_line(
                SimpleNamespace(
                    targets_input_snapshot=beside["input_snapshot"],
                    targets_method_versions=beside["method_versions"],
                )
            )
            if method_line:
                pending_parts.append(method_line)
            acting: list[str] = []
            acting_kcal = int(getattr(profile, "daily_kcal", 0) or 0)
            if "calories" in beside["kinds"] and acting_kcal > 0:
                acting.append(f"{acting_kcal} ккал в день")
            acting_water = int(getattr(profile, "water_ml", 0) or 0)
            if "fluids" in beside["kinds"] and acting_water > 0:
                acting.append(f"вода {acting_water} мл")
            if acting:
                pending_parts.append(PENDING_ACTING.format(what=", ".join(acting)))
            unchanged = [
                f"{label}: {value} {unit}"
                for label, field, unit in _SUMMARY_ROWS
                if field not in pending_fields
                and (value := int(getattr(profile, field, 0) or 0)) > 0
            ]
            if unchanged:
                pending_parts.append(f"{PENDING_UNCHANGED}\n" + "\n".join(unchanged))
            return "\n\n".join(pending_parts)

    # §5.1: предложение показывается как предложение. Числа — из ``raw``:
    # инвариант DTO обнуляет поля у не настроенного источника, и это
    # верно для всех читателей, кроме этого экрана.
    proposal = proposed_norms(profile)
    if proposal:
        rows = [
            f"{label}: {value} {unit}"
            for label, field, unit in _SUMMARY_ROWS
            if (value := int(proposal.get(field) or 0)) > 0
        ]
        if rows:
            proposal_parts = ["Предлагаю ориентиры — посмотри и подтверди:", "\n".join(rows)]
            method_line = _method_and_inputs_line(profile)
            if method_line:
                proposal_parts.append(method_line)
            fluids_line = _fluids_reference_line(profile)
            if fluids_line:
                proposal_parts.append(fluids_line)
            proposal_parts.append(
                "Это предложение: пока ты его не подтвердишь, в дневнике оно не "
                "действует — ни «осталось на сегодня», ни оценок по нему не будет."
            )
            return "\n\n".join(proposal_parts)

    rows = [
        f"{label}: {value} {unit}"
        for label, field, unit in _SUMMARY_ROWS
        if (value := int(getattr(profile, field, 0) or 0)) > 0
    ]

    if not rows:
        # Ни одного ориентира — говорим об этом прямо и не называем
        # причину чужими словами: у отсутствия должно быть имя, но имя
        # это «мы не считали», а не «ты чего-то не заполнил».
        return (
            "Дневник готов — записывай еду и воду, я всё сохраню.\n"
            "Дневных ориентиров пока не считаю."
        )

    parts: list[str] = ["Готово, посчитала твои ориентиры:"]
    parts.append("\n".join(rows))
    method_line = _method_and_inputs_line(profile)
    if method_line:
        parts.append(method_line)
    fluids_line = _fluids_reference_line(profile)
    if fluids_line:
        parts.append(fluids_line)
    if profile.goal_overridden_by == _BMR_FLOOR:
        # Ступень по BMR — граница расчёта, не анамнез.
        parts.append(_BMR_FLOOR_REMARK)
    elif profile.goal_overridden_by:
        # Ayla applied a safety override (pregnancy / eating-disorder /
        # BMI floor). Mention it gently — the override is the right call,
        # not a downgrade.
        parts.append("Учла важное в анамнезе — ориентиры подобрала с поправкой на это.")
    parts.append("Теперь могу считать калории и БЖУ из фото блюд.")
    return "\n\n".join(parts)
