"""Nutrition anketa skill — 5-step nutrition profile FSM.

Sprint 9 / P3 (DRF-820). Largest port in Sprint 9: walks the user
through gender → age → height → weight → goal, persists state in
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

1. Calls Ayla ``upsert_profile`` with the answers + ``activity_coefficient=1.4``
   (sedentary default; Phase 1 will collect activity as a separate step).
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

## Scope cuts vs mysite

Deferred to Phase 1 / a follow-up Sprint 9 ticket:

* Pace step (slow / balanced) — and with it the "faster than ~0.9 kg per
  week" stop, which needs a methodology that computes a rate.
* Gain-clarify branch.
* BMI-ladder override response handling ("Учла важное" override card).
* Allergies / meds (these belong with the Tier-B health screening
  port — see DRF-824 scope note).
* Activity step — defaulted to ``1.4`` (sedentary). Phase 1 makes it a
  step with 5 levels. Note ``1.4`` is not one of the four coefficients
  the owner approved (1.2 / 1.375 / 1.55 / 1.725); the calculation
  service change carries that.
* Consent screen before the weight question. The owner requires a
  separate consent for weight; whether the same consent covers the
  screening answers is open (question 1 in
  ``docs/PLAN_NUTRITION_TARGETS.md``). Wiring one checkbox that silently
  covers both is the thing not to do, so it lands separately.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, ClassVar

from apps.integrations.ayla import (
    NutritionAPIError,
    NutritionUnavailableError,
    external_user_id_for,
    get_nutrition_client,
)
from apps.integrations.ayla.nutrition_client import (
    TARGETS_PROPOSED,
    NothingToConfirmError,
    health_factor_refusals,
    proposed_norms,
)
from apps.orchestrator.ui.keyboards import anketa_choice_keyboard, parse_callback
from apps.skills.base import SkillContext, SkillResult
from apps.skills.fsm import Completed, NextStep
from apps.skills.nutrition_anketa.fsm import (
    ADULT_AGE,
    CHOICE_STEPS,
    SCREENING_CLEAR,
    AnketaFSM,
    choice_keyboard_options,
)
from apps.skills.registry import register

if TYPE_CHECKING:
    from apps.consent.personal_calculation import ConsentAttestation

logger = logging.getLogger(__name__)

# Key inside Conversation.skill_state.
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
_STOP_DIARY_TAIL = (
    "Дневник остаётся: записывай еду и воду, я посчитаю и покажу, сколько "
    "вышло за день. Без дневной цели и без процентов.\n\n"
    "Если ориентир тебе назначил специалист, вписать его пока некуда — "
    "такой ручки у меня ещё нет."
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
#: Согласие снято, но каталог не подтвердил удаление: правда важнее
#: гладкости — параметры уже НЕ используются, а «удалены» сказать нельзя.
WITHDRAW_DELETE_UNCONFIRMED = (
    "Персональный расчёт отключён: параметры больше не используются и нормы "
    "не показываются. Удаление из профиля пока не подтверждено — повторите "
    "«Отключить персональный расчёт» через пару минут, я доведу его до конца."
)
WITHDRAW_KEPT = "Оставляю как есть: персональный расчёт работает."
WITHDRAW_NOTHING_TO_WITHDRAW = (
    "Персональный расчёт и так не включён — отключать нечего. Дневник работает как обычно."
)


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
        text = context.message_text.strip()

        # Ответ на экран согласия — до всего остального.
        if text == CONSENT_GRANT_CALLBACK:
            return self._on_consent_granted(context)
        if text == CONSENT_DECLINE_CALLBACK:
            return self._on_consent_declined(context)

        # Отзыв: спросить → подтвердить / оставить.
        if text == WITHDRAW_CALLBACK or _is_withdraw_phrase(text):
            return self._on_withdraw_ask(context)
        if text == WITHDRAW_CONFIRM_CALLBACK:
            return self._on_withdraw_confirm(context)
        if text == WITHDRAW_KEEP_CALLBACK:
            return self._on_withdraw_keep(context)

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
        return self._render_step(fsm.current_step, step_result.prompt)

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
        return self._render_step(fsm.current_step, step_result.prompt)

    # ─── transition ──────────────────────────────────────────────────────

    def _on_transition(self, context: SkillContext, text: str) -> SkillResult:
        fsm = self._load_or_new(context)
        if not fsm.current_step:
            # No active FSM but match() claimed the turn — defensive enter.
            return self._on_enter(context)

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
            return self._render_step(fsm.current_step, result.prompt)

        # Completed → POST to Ayla.
        assert isinstance(result, Completed)
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
                # The same two next moves the completed anketa offers. The
                # branch says the diary stays open, so it has to open it.
                "buttons": _post_anketa_chips(),
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
            action_type="anketa_targets_confirmed",
            action_data={
                "outcome": outcome,
                "daily_kcal": profile.daily_kcal,
                "buttons": _post_anketa_chips(profile),
            },
            meta={"reply_kind": "anketa_targets_confirmed"},
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

        if not is_granted(context.bot_user):
            return SkillResult(
                reply_text=WITHDRAW_NOTHING_TO_WITHDRAW,
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
            reply_text=WITHDRAW_DONE,
            meta={"reply_kind": "anketa_withdraw_done"},
        )

    def _on_withdraw_keep(self, context: SkillContext) -> SkillResult:
        return SkillResult(
            reply_text=WITHDRAW_KEPT,
            meta={"reply_kind": "anketa_withdraw_kept"},
        )

    def _render_step(self, step: str, prompt: str) -> SkillResult:
        action_data: dict = {"step": step}
        if step in CHOICE_STEPS:
            action_data["buttons"] = anketa_choice_keyboard(step, choice_keyboard_options(step))
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

        Activity is hardcoded to ``1.4`` (sedentary) for Sprint 9.
        Phase 1 collects activity as a step.

        Утверждение о согласии — обязательный аргумент, не флаг и не
        ``None`` по умолчанию: все шесть полей ниже закрыты границей
        каталога (#324), и тело без утверждения собрать здесь нельзя
        даже по ошибке.
        """
        from apps.consent.personal_calculation import attach as attach_consent

        body = {
            "gender": answers["gender"],
            "age": int(answers["age"]),
            "height_cm": int(answers["height"]),
            "weight_kg": int(answers["weight"]),
            "goal": answers["goal"],
            "activity_coefficient": 1.4,
        }
        return attach_consent(body, attestation)


# ─── helpers ──────────────────────────────────────────────────────────────


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
    if profile is not None and profile.targets_source == TARGETS_PROPOSED:
        chips.append(dict(CHIP_CONFIRM_TARGETS))
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
        facts.append(f"активность — {snapshot['activity_coefficient']}")
    goal = snapshot.get("goal")
    if goal:
        facts.append(f"цель — {_GOAL_LABELS.get(str(goal), str(goal))}")
    pace = snapshot.get("pace")
    if pace:
        facts.append(f"темп — {_PACE_LABELS.get(str(pace), str(pace))}")

    head = (
        f"Считала по методике {_METHOD_LABELS.get(str(version), str(version))}"
        if version
        else "Считала"
    )
    return f"{head} от твоих данных: {', '.join(facts)}."


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
    """«Норму не считаю: при … Ayla индивидуальные ориентиры не рассчитывает».

    Решение владельца 11.09.2026 §5.1: «При health-факторах Ayla не
    рассчитывает индивидуальную норму». Отказ назван по факту, без
    чисел и без обещания «сделаю позже»; дневник при этом открыт (§92:
    отказ не закрывает дневник).
    """
    labels = [_HEALTH_FACTOR_LABELS.get(n, n) for n in names]
    joined = ", ".join(labels)
    return (
        f"Норму не считаю: при {joined} Ayla индивидуальные ориентиры не рассчитывает.\n"
        "Дневник и вода работают как раньше — записывай, я всё сохраню."
    )


def _format_summary(profile) -> str:
    """Карточка после анкеты — только те ориентиры, которые ЕСТЬ.

    Здесь стояли пять строк подряд, безусловно. Каждая печатала
    `{profile.<поле>}`, и ноль печатался как значение: «💧 Вода: 0 мл»,
    а у человека, не назвавшего вес, — ещё и «🔥 Калории: 0 ккал/день».
    Ноль ккал в сутки не бывает; такая карточка не «пустая», она ЛЖЁТ, и
    лжёт в самом громком месте — сразу после «Готово, рассчитала твои
    нормы».

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

    parts: list[str] = ["Готово, рассчитала твои нормы:"]
    parts.append("\n".join(rows))
    method_line = _method_and_inputs_line(profile)
    if method_line:
        parts.append(method_line)
    fluids_line = _fluids_reference_line(profile)
    if fluids_line:
        parts.append(fluids_line)
    if profile.goal_overridden_by:
        # Ayla applied a safety override (pregnancy / eating-disorder /
        # BMI floor). Mention it gently — the override is the right call,
        # not a downgrade.
        parts.append("Учла важное в анамнезе — нормы подобрала с поправкой на это.")
    parts.append("Теперь могу считать калории и БЖУ из фото блюд.")
    return "\n\n".join(parts)
