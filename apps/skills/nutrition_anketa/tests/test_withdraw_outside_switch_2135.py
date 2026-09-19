"""DRF-2135 — отзыв согласия ПДн из анкеты работает при любом значении выключателя.

Ворота ``NUTRITION_ENABLED`` (PR #1800) стояли в ``handle`` первыми и закрывали
при OFF весь вход анкеты — включая ``cb:pc_consent:withdraw*`` и фразу
«Отключить персональный расчёт». Человек при выключенном контуре не мог
отозвать согласие через чат: на «отозвать» бот отвечал «Функция пока
недоступна». Отзыв согласия — не функция контура питания, а право человека
(§92): он обязан работать при любом флаге, как ``me/*-consent/`` в Mini App
(положительный страж в переписи DRF-2071).

Здесь: при OFF три шага отзыва (спросить → подтвердить / оставить) и текстовая
форма делают то же, что при ON: ``withdraw`` пишется, параметры в каталоге
удаляются (``purge_body_parameters`` — часть отзыва, не функция питания).
Копия при OFF — те же утверждённые предложения минус те, что при OFF ложны
(«дневник работает как обычно», «напишите „Рассчитать мои нормы“» — второе
при OFF получило бы заглушку); флаг ВЫБИРАЕТ фразу, но не решает исход.
Остальные входы анкеты при OFF — заглушка, как и были (стража: ворота
сдвигаются, не исчезают). Положительная пара при ON на тех же входах.

Предел, названный, а не спрятанный: всё здесь — уровень НАВЫКА. Кнопки
``cb:pc_consent:withdraw*`` доезжают до навыка на любом боте (структурный
префикс, ``test_pc_consent_callbacks_structured_2074``). Текстовая форма
«Отключить персональный расчёт» доезжает по лестнице тенантного бота и при
активной анкете; на глобальном боте без анкеты в полёте свободный текст уходит
консьержу и до навыка не доходит — это устройство маршрута (``nutrition_global.
is_structured_nutrition_turn``), а не ворот, и оно было таким до листа.
"""

from __future__ import annotations

import inspect
from unittest.mock import Mock, patch

import pytest

from apps.skills.base import SkillContext
from apps.skills.nutrition_anketa import skill as anketa
from apps.skills.nutrition_anketa.skill import NutritionAnketaSkill
from apps.skills.nutrition_anketa.tests.test_consent_gate_at_entry import (
    _CLIENT,
    _IS_GRANTED,
    _WITHDRAW,
    _FakeClient,
)

STUB = anketa._nutrition_unavailable_text()
_FLAG = "apps.skills.nutrition_anketa.skill._nutrition_enabled"


@pytest.fixture
def nutrition_off(settings):
    # Перекрывает autouse ``_nutrition_contour_on`` из conftest пакета.
    settings.NUTRITION_ENABLED = False


def _ctx(text: str) -> SkillContext:
    conversation = Mock(id="conv-2135", skill_state={})
    conversation.save = Mock()
    return SkillContext(
        conversation=conversation,
        bot_user=Mock(channel="max", channel_user_id="2135"),
        message_text=text,
    )


WITHDRAW_ASKS = [
    pytest.param(anketa.WITHDRAW_CALLBACK, id="кнопка"),
    pytest.param(anketa.WITHDRAW_ACTION_TEXT, id="фраза"),
    pytest.param("отключить персональный расчет", id="фраза-без-ё"),
]


class TestWithdrawalWorksWhenTheContourIsOff:
    @pytest.mark.parametrize("text", WITHDRAW_ASKS)
    def test_asking_to_withdraw_gets_the_confirmation_not_the_stub(self, nutrition_off, text):
        ctx = _ctx(text)
        skill = NutritionAnketaSkill()
        assert skill.matches(ctx), text

        with patch(_IS_GRANTED, return_value=True), patch(_WITHDRAW) as withdraw:
            result = skill.handle(ctx)

        assert result.reply_text == anketa.WITHDRAW_CONFIRM_ASK
        assert result.meta["reply_kind"] == "anketa_withdraw_ask"
        withdraw.assert_not_called()  # до подтверждения ничего не снимается

    def test_nothing_to_withdraw_is_said_too(self, nutrition_off):
        ctx = _ctx(anketa.WITHDRAW_CALLBACK)
        with patch(_IS_GRANTED, return_value=False):
            result = NutritionAnketaSkill().handle(ctx)

        assert result.reply_text == anketa.WITHDRAW_NOTHING_TO_WITHDRAW_CONTOUR_OFF
        # Про дневник при OFF — ни слова: он выключен.
        assert "Дневник" not in result.reply_text

    def test_confirming_withdraws_and_purges_and_says_done(self, nutrition_off):
        ctx = _ctx(anketa.WITHDRAW_CONFIRM_CALLBACK)
        client = _FakeClient(purge=True)
        with (
            patch(_WITHDRAW, return_value=1) as withdraw,
            patch(_CLIENT, return_value=client),
        ):
            result = NutritionAnketaSkill().handle(ctx)

        withdraw.assert_called_once_with(ctx.bot_user)
        assert client.calls == 1  # удаление параметров — часть отзыва, не функция питания
        assert result.reply_text == anketa.WITHDRAW_DONE_CONTOUR_OFF
        assert result.meta["reply_kind"] == "anketa_withdraw_done"
        # Наличие раньше отсутствия: суть отзыва сказана, ложных при OFF обещаний нет.
        assert "Параметры удалены" in result.reply_text
        assert "Дневник" not in result.reply_text
        assert anketa.ENTRY_PHRASE.capitalize() not in result.reply_text

    def test_confirming_when_the_catalog_is_down_still_withdraws(self, nutrition_off):
        from apps.integrations.ayla.nutrition_client import NutritionUnavailableError

        ctx = _ctx(anketa.WITHDRAW_CONFIRM_CALLBACK)
        client = _FakeClient(raise_exc=NutritionUnavailableError("down"))
        with (
            patch(_WITHDRAW, return_value=1) as withdraw,
            patch(_CLIENT, return_value=client),
        ):
            result = NutritionAnketaSkill().handle(ctx)

        withdraw.assert_called_once()
        assert result.reply_text == anketa.WITHDRAW_DELETE_UNCONFIRMED

    def test_keeping_is_acknowledged(self, nutrition_off):
        result = NutritionAnketaSkill().handle(_ctx(anketa.WITHDRAW_KEEP_CALLBACK))
        # «Персональный расчёт работает» при OFF — неправда; остаётся согласие.
        assert result.reply_text == anketa.WITHDRAW_KEPT_CONTOUR_OFF


class TestTheGateMovedButDidNotVanish:
    """Остальные входы анкеты при OFF — заглушка, как до листа."""

    @pytest.mark.parametrize(
        "text",
        [
            "/anketa",
            "cb:anketa:start",
            anketa.ENTRY_PHRASE,
            anketa.CONSENT_GRANT_CALLBACK,
            anketa.CONSENT_DECLINE_CALLBACK,
            anketa.CB_CONFIRM_TARGETS,
            "cb:anketa:edit:weight",
        ],
    )
    def test_other_entries_still_answer_the_stub(self, nutrition_off, text):
        ctx = _ctx(text)
        skill = NutritionAnketaSkill()
        assert skill.matches(ctx), text
        result = skill.handle(ctx)
        assert result.reply_text == STUB
        assert result.meta["reply_kind"] == "nutrition_anketa_nutrition_off"


class TestPositiveControlWhenOn:
    def test_asking_to_withdraw_is_the_same_when_on(self):
        ctx = _ctx(anketa.WITHDRAW_CALLBACK)
        with patch(_IS_GRANTED, return_value=True), patch(_WITHDRAW):
            result = NutritionAnketaSkill().handle(ctx)
        assert result.reply_text == anketa.WITHDRAW_CONFIRM_ASK

    def test_confirming_is_the_same_when_on(self):
        ctx = _ctx(anketa.WITHDRAW_CONFIRM_CALLBACK)
        with patch(_WITHDRAW, return_value=1), patch(_CLIENT, return_value=_FakeClient()):
            result = NutritionAnketaSkill().handle(ctx)
        assert result.reply_text == anketa.WITHDRAW_DONE  # прежний текст, с «Рассчитать мои нормы»

    def test_keeping_and_nothing_are_the_same_when_on(self):
        assert (
            NutritionAnketaSkill().handle(_ctx(anketa.WITHDRAW_KEEP_CALLBACK)).reply_text
            == anketa.WITHDRAW_KEPT
        )
        with patch(_IS_GRANTED, return_value=False):
            result = NutritionAnketaSkill().handle(_ctx(anketa.WITHDRAW_CALLBACK))
        assert result.reply_text == anketa.WITHDRAW_NOTHING_TO_WITHDRAW


class TestTheGuardAgainstAQuietRegression:
    """Ложный вход: рефактор, который вернёт отзыв ПОД ворота (или добавит
    чтение флага внутри самого отзыва), обязан дать красный, а не тихую
    заглушку. Два независимых сторожа: поведение и порядок веток."""

    @pytest.mark.parametrize(
        "text",
        [
            anketa.WITHDRAW_CALLBACK,
            anketa.WITHDRAW_ACTION_TEXT,
            anketa.WITHDRAW_CONFIRM_CALLBACK,
            anketa.WITHDRAW_KEEP_CALLBACK,
        ],
    )
    def test_the_flag_does_not_decide_the_outcome_on_the_withdrawal_path(self, text):
        """Флаг на пути отзыва может выбрать фразу, но не исход: при обоих
        значениях читателя ``reply_kind`` один и тот же ``anketa_withdraw_*``,
        и это не заглушка. Ветка отзыва ниже ворот или «if not
        _nutrition_enabled(): return stub» внутри ``_on_withdraw_*`` → красный."""
        kinds: set[str] = set()
        for flag in (True, False):
            ctx = _ctx(text)
            with (
                patch(_FLAG, return_value=flag),
                patch(_IS_GRANTED, return_value=True),
                patch(_WITHDRAW, return_value=1),
                patch(_CLIENT, return_value=_FakeClient()),
            ):
                result = NutritionAnketaSkill().handle(ctx)
            assert result.reply_text != STUB, (text, flag)
            kinds.add(result.meta["reply_kind"])
        assert len(kinds) == 1, kinds
        assert kinds.pop().startswith("anketa_withdraw_")

    def test_the_flag_reader_is_still_called_for_everything_else(self):
        """Положительная стража сторожа выше: на не-отзыве читатель зовётся —
        иначе первый тест зеленел бы и на навыке, где ворот нет вовсе."""
        with patch(_FLAG, return_value=False) as flag:
            result = NutritionAnketaSkill().handle(_ctx("/anketa"))
        flag.assert_called_once()
        assert result.reply_text == STUB

    def test_in_the_source_the_withdrawal_branches_precede_the_gate(self):
        """Порядок веток в ``handle`` — сначала отзыв, потом ворота. Поменяй
        местами — красный здесь, ещё до поведения."""
        src = inspect.getsource(NutritionAnketaSkill.handle)
        gate = src.index("if not _nutrition_enabled():")
        for name in ("WITHDRAW_CALLBACK", "WITHDRAW_CONFIRM_CALLBACK", "WITHDRAW_KEEP_CALLBACK"):
            branch = src.index(f"text == {name}")
            assert branch < gate, f"{name}: ветка отзыва стоит ниже ворот выключателя"
        # А согласие — ниже ворот, по замыслу: дать согласие в выключенный контур нельзя.
        assert src.index("text == CONSENT_GRANT_CALLBACK") > gate
