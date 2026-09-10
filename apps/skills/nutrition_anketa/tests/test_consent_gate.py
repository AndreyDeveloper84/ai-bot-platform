"""§92 п.1 — до согласия на расчёт анкета не задаёт вопросов о теле.

## Почему гейт на ВХОДЕ, а не на шаге веса

Правило владельца названо через вес: «до второго согласия вопрос о весе не
задаётся». Но состав согласия шире — «вес, рост, возраст, физиологический
пол, активность, цель», шесть параметров. Анкета спрашивает пол первым,
возраст вторым, рост четвёртым, и только потом вес.

Гейт на шаге веса исполнил бы букву и оставил четыре параметра из шести
собранными без основания. Поэтому проверка стоит на входе — там, где
начинается сбор, а не там, где он становится самым заметным.

## Что гейт НЕ делает

Он останавливает спрашивание, а не гасит уже посчитанное. Человек, у
которого ориентир есть, его не теряет: параметры он сообщил, просто без
отдельного экрана — это процессный пробел, а не его вина. Правило 4
(удалить либо обезличить) срабатывает при ОТЗЫВЕ, а отзывать пока нечего.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from apps.consent.models import ConsentRecord
from apps.consent.services import grant, withdraw
from apps.identity.models import BotUser
from apps.skills.base import SkillContext
from apps.skills.nutrition_anketa.skill import NutritionAnketaSkill
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

CALC = ConsentRecord.ConsentType.PERSONAL_CALCULATION.value

_GRANTED = "apps.consent.nutrition.calculation_is_granted"


class _Conversation:
    def __init__(self, initial: dict | None = None) -> None:
        self.id = "conv-consent"
        self.skill_state = initial or {}

    def save(self, update_fields: list[str] | None = None) -> None:
        return


def _context(text: str, *, state: dict | None = None) -> tuple[SkillContext, _Conversation]:
    conversation = _Conversation(state)
    bot_user = Mock(channel="max", channel_user_id="42")
    ctx = SkillContext(
        conversation=conversation,  # type: ignore[arg-type]
        bot_user=bot_user,
        message_text=text,
    )
    return ctx, conversation


class TestTheAnketaDoesNotOpenWithoutConsent:
    def test_entry_is_refused_and_says_the_diary_stays(self) -> None:
        ctx, conversation = _context("/anketa")

        with patch(_GRANTED, return_value=False):
            result = NutritionAnketaSkill().handle(ctx)

        assert result.action_type == "anketa_consent_required"
        # Ни один вопрос о теле не задан — даже первый.
        assert not result.action_type.startswith("anketa_step_")
        assert "дневник" in result.reply_text.lower(), "правило 2: отказ не закрывает дневник"
        # Незавершённой анкеты не остаётся: иначе следующий ход человека
        # попал бы в FSM, стоящую на шаге тела.
        assert "nutrition_anketa" not in conversation.skill_state

    def test_with_consent_the_anketa_opens_as_before(self) -> None:
        """Положительная стража. Без неё предыдущий тест зеленел бы и на
        анкете, закрытой навсегда, — то есть доказывал бы поломку."""
        ctx, conversation = _context("/anketa")

        with patch(_GRANTED, return_value=True):
            result = NutritionAnketaSkill().handle(ctx)

        assert result.action_type == "anketa_step_gender"
        assert conversation.skill_state["nutrition_anketa"]["current_step"] == "gender"

    def test_consent_withdrawn_mid_flow_stops_the_running_anketa(self) -> None:
        """Отзыв посреди анкеты обязан сработать в тот же ход.

        Иначе человек, забравший согласие, доходит до вопроса о весе —
        то есть отзыв действует «со следующего раза», а такого понятия
        в §92 нет.
        """
        ctx, conversation = _context(
            "30",
            state={
                "nutrition_anketa": {
                    "current_step": "age",
                    "answers": {"gender": "female"},
                    "is_complete": False,
                }
            },
        )

        with patch(_GRANTED, return_value=False):
            result = NutritionAnketaSkill().handle(ctx)

        assert result.action_type == "anketa_consent_required"
        assert "nutrition_anketa" not in conversation.skill_state

    def test_an_unreadable_consent_closes_rather_than_opens(self) -> None:
        """Fail-closed: неизвестное основание — не основание.

        Единственное место в моей работе, где молчание допустимо, и
        допустимо оно ровно потому, что здесь молчание ЗАКРЫВАЕТ.
        """
        ctx, _conversation = _context("/anketa")

        with patch(_GRANTED, side_effect=RuntimeError("БД недоступна")):
            result = NutritionAnketaSkill().handle(ctx)

        assert result.action_type == "anketa_consent_required"


@pytest.mark.django_db(transaction=True)
class TestTheGateReadsTheRealRecord:
    """Замер на стыке: гейт против настоящего `ConsentRecord`, без подмен.

    Предыдущий класс проверяет ветвление, подменяя предикат. Он зеленел бы
    и в мире, где предикат не связан с таблицей вовсе. Здесь связь и
    проверяется — целиком, от `grant()` до ответа навыка.
    """

    @pytest.fixture
    def tenant(self) -> Tenant:
        return Tenant.objects.create(slug="anketa-consent", name="A")

    @pytest.fixture
    def person(self, tenant: Tenant) -> BotUser:
        return BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="anketa-consent-1"
        )

    def _ctx(self, person: BotUser) -> tuple[SkillContext, _Conversation]:
        conversation = _Conversation()
        return (
            SkillContext(
                conversation=conversation,  # type: ignore[arg-type]
                bot_user=person,
                message_text="/anketa",
            ),
            conversation,
        )

    def test_grant_opens_and_withdraw_closes_the_same_anketa(
        self, tenant: Tenant, person: BotUser
    ) -> None:
        """Пара в обе стороны на одних и тех же данных.

        Одно направление проверяет не свойство, а совпадение состояния:
        «закрыто без согласия» зеленеет и у сломанной анкеты, «открыто с
        согласием» — у анкеты без гейта вовсе. Свойство доказывает только
        переход между ними.
        """
        with tenant_scope(tenant):
            ctx, _conv = self._ctx(person)
            assert NutritionAnketaSkill().handle(ctx).action_type == "anketa_consent_required"

            grant(person, consent_type=CALC, source="test", document_version="nutrition-v1")
            ctx, _conv = self._ctx(person)
            assert NutritionAnketaSkill().handle(ctx).action_type == "anketa_step_gender"

            withdraw(person, consent_type=CALC, source="test")
            ctx, _conv = self._ctx(person)
            assert NutritionAnketaSkill().handle(ctx).action_type == "anketa_consent_required"
