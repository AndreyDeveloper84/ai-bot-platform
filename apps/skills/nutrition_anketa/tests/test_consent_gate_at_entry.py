"""Гейт согласия стоит НА ВХОДЕ в анкету, а не на завершении (§92).

До этой правки утверждение проверялось в `_on_complete` — после того как
человек назвал пол, возраст, рост, вес и цель, а FSM записала их в
`Conversation.skill_state`. Пять параметров из шести, перечисленных §92,
оказывались собраны и сохранены до появления основания их собирать.

§92 требует гейт на входе прямым текстом: «до второго согласия вопрос о
весе не задаётся». И довод оттуда же: гейт на шаге веса «исполнил бы
букву правила и оставил четыре параметра из шести собранными без
основания».

Прежняя проверка в `_on_complete` не снята — вторая линия: согласие может
быть отозвано между входом и завершением.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

from apps.skills.base import SkillContext
from apps.skills.nutrition_anketa import skill as anketa
from apps.skills.nutrition_anketa.skill import NutritionAnketaSkill

_IS_GRANTED = "apps.consent.personal_calculation.is_granted"
_GRANT = "apps.consent.personal_calculation.grant"


class _Conversation:
    def __init__(self) -> None:
        self.id = "conv-gate"
        self.skill_state: dict = {}

    def save(self, update_fields=None) -> None:
        return


def _ctx(text: str) -> tuple[SkillContext, _Conversation]:
    conversation = _Conversation()
    ctx = SkillContext(
        conversation=conversation,  # type: ignore[arg-type]
        bot_user=Mock(channel="max", channel_user_id="gate-1"),
        message_text=text,
    )
    return ctx, conversation


class TestTheEntranceIsGated:
    def test_without_consent_the_first_question_is_not_asked(self):
        """Главное утверждение среза: без согласия FSM НЕ стартует.

        Проверяется не текстом ответа, а тем, что в `skill_state` не
        появилось анкеты. Текст можно сочинить любой; отсутствие FSM —
        это отсутствие сбора параметров.
        """
        ctx, conversation = _ctx("cb:anketa:start")

        with patch(_IS_GRANTED, return_value=False):
            result = NutritionAnketaSkill().handle(ctx)

        assert result.reply_text == anketa.CONSENT_ASK
        assert "nutrition_anketa" not in conversation.skill_state
        assert result.action_type == "anketa_consent_ask"

    def test_the_ask_carries_the_version_and_two_actions(self):
        """Версия — в данных, а не в тексте: доказывать, на что человек
        соглашался, — наша работа, а не его память."""
        from apps.consent.personal_calculation import (
            PERSONAL_CALCULATION_DOCUMENT_VERSION,
        )

        ctx, _ = _ctx("/anketa")

        with patch(_IS_GRANTED, return_value=False):
            result = NutritionAnketaSkill().handle(ctx)

        assert result.action_data["document_version"] == PERSONAL_CALCULATION_DOCUMENT_VERSION
        callbacks = {b["callback"] for b in result.action_data["buttons"]}
        assert callbacks == {anketa.CONSENT_GRANT_CALLBACK, anketa.CONSENT_DECLINE_CALLBACK}

    def test_with_consent_the_first_question_is_asked(self):
        """Положительная стража: гейт закрывает НЕ всё.

        Без неё тест выше зеленел бы и на анкете, которая не стартует
        никогда, — то есть на закрытом расчёте вместо гейта.
        """
        ctx, conversation = _ctx("cb:anketa:start")

        with patch(_IS_GRANTED, return_value=True):
            result = NutritionAnketaSkill().handle(ctx)

        assert result.reply_text != anketa.CONSENT_ASK
        assert "nutrition_anketa" in conversation.skill_state
        assert result.action_type.startswith("anketa_step_")


class TestTheTwoActions:
    def test_grant_records_and_starts_the_questionnaire(self):
        """Согласие записано → анкета начинается тем же ходом."""
        ctx, conversation = _ctx(anketa.CONSENT_GRANT_CALLBACK)

        with patch(_GRANT, return_value=True) as grant, patch(_IS_GRANTED, return_value=True):
            result = NutritionAnketaSkill().handle(ctx)

        from apps.consent.personal_calculation import (
            PERSONAL_CALCULATION_DOCUMENT_VERSION,
        )

        grant.assert_called_once()
        assert grant.call_args.kwargs["document_version"] == PERSONAL_CALCULATION_DOCUMENT_VERSION
        assert "nutrition_anketa" in conversation.skill_state
        assert result.action_type.startswith("anketa_step_")

    def test_grant_that_cannot_be_read_back_does_not_start(self):
        """`grant` вернул False — записали, но перечитать не смогли.

        Анкета не начинается: собрать параметры и потом обнаружить, что
        основания нет, хуже, чем попросить повторить.
        """
        ctx, conversation = _ctx(anketa.CONSENT_GRANT_CALLBACK)

        with patch(_GRANT, return_value=False):
            result = NutritionAnketaSkill().handle(ctx)

        assert result.reply_text == anketa.CONSENT_RECORDED_BUT_UNREADABLE
        assert "nutrition_anketa" not in conversation.skill_state

    def test_decline_keeps_the_diary_and_starts_nothing(self):
        """§92: «отказ не закрывает дневник»."""
        ctx, conversation = _ctx(anketa.CONSENT_DECLINE_CALLBACK)

        with patch(_GRANT) as grant:
            result = NutritionAnketaSkill().handle(ctx)

        grant.assert_not_called()
        assert result.reply_text == anketa.CONSENT_DECLINED
        assert "nutrition_anketa" not in conversation.skill_state
        assert "дневник" in result.reply_text.lower()

    def test_both_callbacks_are_claimed_by_this_skill(self):
        """Иначе «не сейчас» уйдёт в общий конвейер и человек получит
        молчание или чужой ответ на своё «нет»."""
        skill = NutritionAnketaSkill()
        for cb in (anketa.CONSENT_GRANT_CALLBACK, anketa.CONSENT_DECLINE_CALLBACK):
            ctx, _ = _ctx(cb)
            assert skill.matches(ctx), cb


class TestTheTextsPromiseNothingThatDoesNotExist:
    def test_no_promise_of_a_settings_screen(self):
        """Экрана отзыва в боте нет — обещать дверь, которой нет, нельзя.

        Дверь, которая ЕСТЬ, названа впереди: отзыв — словами (§2), а
        не экраном настроек.
        """
        for text in (anketa.CONSENT_ASK, anketa.CONSENT_DECLINED):
            low = text.lower()
            assert "дневник" in low
            assert "настройк" not in low
        assert "отозвать" in anketa.CONSENT_ASK.lower()

    def test_no_claim_of_anonymity(self):
        """Параметры тела хранятся под учётной записью — называть это
        обезличиванием было бы неправдой. Где хранятся — сказано впереди."""
        assert "в вашем профиле" in anketa.CONSENT_ASK.lower()
        for text in (anketa.CONSENT_ASK, anketa.CONSENT_DECLINED):
            low = text.lower()
            assert "дневник" in low
            assert "аноним" not in low
            assert "обезлич" not in low

    def test_the_six_parameters_are_named(self):
        """Состав согласия — из §92 дословно: шесть, и ни одним больше."""
        low = anketa.CONSENT_ASK.lower()
        for word in ("вес", "рост", "возраст", "пол", "активност", "цел"):
            assert word in low, word


# ---------------------------------------------------------------------------
# Пакет решений владельца 12.09 §2 (DRF-1698): тексты дословно, вход фразой,
# отзыв — canonical action с подтверждением, fail-close только расчёта.
# ---------------------------------------------------------------------------


class TestTheOwnersTextsAreVerbatim:
    def test_ask_is_the_owners_text_and_buttons(self):
        assert anketa.CONSENT_ASK.startswith(
            "Хотите, чтобы Ayla рассчитывала ваши персональные нормы?"
        )
        assert "пол для расчёта" in anketa.CONSENT_ASK
        assert "Это необязательно." in anketa.CONSENT_ASK
        assert anketa.CONSENT_BUTTON_GRANT == "Рассчитать мои нормы"
        assert anketa.CONSENT_BUTTON_DECLINE == "Не сейчас"

    def test_declined_names_the_phrase_that_really_reenters(self):
        """Вариант без UI-раздела «Питание» (его нет): обещанная фраза
        обязана быть настоящим входом, иначе текст врёт."""
        assert "напишите: «Рассчитать мои нормы»" in anketa.CONSENT_DECLINED
        assert "раздел" not in anketa.CONSENT_DECLINED.lower()
        skill = NutritionAnketaSkill()
        for spelled in (
            "Рассчитать мои нормы",
            "рассчитать мои нормы!",
            "  Рассчитать   мои нормы. ",
        ):
            ctx, _ = _ctx(spelled)
            assert skill.matches(ctx), spelled

    def test_the_entry_phrase_is_gated_like_any_entry(self):
        ctx, conversation = _ctx("Рассчитать мои нормы")
        with patch(_IS_GRANTED, return_value=False):
            result = NutritionAnketaSkill().handle(ctx)
        assert result.reply_text == anketa.CONSENT_ASK
        assert "nutrition_anketa" not in conversation.skill_state

    def test_unreadable_grant_closes_only_the_calculation(self):
        assert "Дневник при этом работает" in anketa.CONSENT_RECORDED_BUT_UNREADABLE

    def test_purpose_is_specific_not_general_health(self):
        from apps.consent.personal_calculation import PURPOSE

        assert PURPOSE == "NUTRITION_PERSONAL_NORMS"
        assert "HEALTH" not in PURPOSE


_WITHDRAW = "apps.consent.personal_calculation.withdraw"
_CLIENT = "apps.skills.nutrition_anketa.skill.get_nutrition_client"


class _FakeClient:
    def __init__(self, purge=True, raise_exc=None, order=None):
        self._purge = purge
        self._raise = raise_exc
        self.calls = 0
        self.order = order if order is not None else []

    async def purge_body_parameters(self, *, external_user_id):
        self.calls += 1
        self.order.append("purge")
        if self._raise is not None:
            raise self._raise
        return self._purge


class TestWithdrawalIsACanonicalActionWithConfirmation:
    def test_the_action_is_claimed_by_button_and_by_text(self):
        skill = NutritionAnketaSkill()
        for t in (
            anketa.WITHDRAW_CALLBACK,
            anketa.WITHDRAW_ACTION_TEXT,
            "отключить персональный расчет",
        ):
            ctx, _ = _ctx(t)
            assert skill.matches(ctx), t

    def test_asking_deletes_nothing_and_offers_two_buttons(self):
        ctx, _ = _ctx(anketa.WITHDRAW_CALLBACK)
        with patch(_IS_GRANTED, return_value=True), patch(_WITHDRAW) as withdraw:
            result = NutritionAnketaSkill().handle(ctx)

        withdraw.assert_not_called()
        assert result.reply_text == anketa.WITHDRAW_CONFIRM_ASK
        labels = [b["label"] for b in result.action_data["buttons"]]
        assert labels == ["Отключить и удалить", "Оставить как есть"]
        assert "История дневника сохранится" in result.reply_text

    def test_keep_changes_nothing(self):
        ctx, _ = _ctx(anketa.WITHDRAW_KEEP_CALLBACK)
        with patch(_WITHDRAW) as withdraw:
            result = NutritionAnketaSkill().handle(ctx)
        withdraw.assert_not_called()
        assert result.reply_text == anketa.WITHDRAW_KEPT

    def test_confirm_withdraws_first_then_purges_and_says_deleted(self):
        ctx, _ = _ctx(anketa.WITHDRAW_CONFIRM_CALLBACK)
        order: list[str] = []
        fake = _FakeClient(purge=True, order=order)
        with (
            patch(_WITHDRAW, side_effect=lambda u: order.append("withdraw") or 1) as withdraw,
            patch(_CLIENT, return_value=fake),
        ):
            result = NutritionAnketaSkill().handle(ctx)

        withdraw.assert_called_once()
        assert fake.calls == 1
        # Сначала согласие (использование прекращено), потом удаление.
        assert order == ["withdraw", "purge"]
        assert result.reply_text == anketa.WITHDRAW_DONE
        assert "Параметры удалены" in result.reply_text

    def test_confirm_without_catalog_confirmation_says_unconfirmed_not_deleted(self):
        """Правда важнее гладкости: согласие снято (использование прекращено),
        но «удалены» — только когда каталог подтвердил."""
        from apps.integrations.ayla import NutritionUnavailableError

        for fake in (
            _FakeClient(purge=False),
            _FakeClient(raise_exc=NutritionUnavailableError("down")),
        ):
            ctx, _ = _ctx(anketa.WITHDRAW_CONFIRM_CALLBACK)
            with patch(_WITHDRAW, return_value=1) as withdraw, patch(_CLIENT, return_value=fake):
                result = NutritionAnketaSkill().handle(ctx)
            withdraw.assert_called_once()
            assert result.reply_text == anketa.WITHDRAW_DELETE_UNCONFIRMED
            assert "не подтверждено" in result.reply_text
            assert result.reply_text != anketa.WITHDRAW_DONE

    def test_nothing_to_withdraw_is_said_not_pretended(self):
        ctx, _ = _ctx(anketa.WITHDRAW_ACTION_TEXT)
        with patch(_IS_GRANTED, return_value=False), patch(_WITHDRAW) as withdraw:
            result = NutritionAnketaSkill().handle(ctx)
        withdraw.assert_not_called()
        assert result.reply_text == anketa.WITHDRAW_NOTHING_TO_WITHDRAW

    def test_the_action_is_offered_where_norms_appear(self):
        chips = anketa._post_anketa_chips()
        assert {"label": anketa.WITHDRAW_ACTION_TEXT, "callback": anketa.WITHDRAW_CALLBACK} in chips
