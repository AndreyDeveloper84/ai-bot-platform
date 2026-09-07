"""DRF-1542 — навык помнит, что уже спросил. И чего эта память НЕ гасит.

Живой диалог владельца 06.09, `action_type=health_screening` пять раз
подряд, ответ байт-в-байт один и тот же::

    07:52:28  чел  …тянет поясницу
              бот  Понимаю. Уточню, чтобы посоветовать точно: 1. Где именно…
    07:52:55  чел  После тренировки
              бот  Понимаю. Уточню, чтобы посоветовать точно: 1. Где именно…
    07:53:13  чел  Поясница
              бот  Понимаю. Уточню, чтобы посоветовать точно: 1. Где именно…
    10:50:23  чел  Ну и как тебе донести, что болит спина?
              бот  Понимаю. Уточню, чтобы посоветовать точно: 1. Где именно…
    10:50:37  чел  Что ты понимаешь?
              бот  Понимаю. Уточню, чтобы посоветовать точно: 1. Где именно…

Человек ответил на ОБА заданных вопроса — и получил их снова.

Этот файл держит ПОРЯДОК, а не только факт памяти. Порядок здесь несущий:

    сначала classify() → RED_FLAG проходит ВСЕГДА → память гасит только
    повтор SOFT.

Решение владельца ``docs/OPEN_DECISIONS.md`` §35 п.5: тревожный признак
сразу включает безопасную ветку. Человек может пожаловаться повторно и на
втором заходе сказать то, что классификатор прочитает как красный флаг.
Промолчать про «сначала к врачу» ради разрыва петли — дороже самой петли:
петля раздражает, молчание на тревожном признаке может стоить человеку
здоровья.

:class:`TestRedFlagIsNeverSilenced` краснеет, если порядок переставить —
достаточно перенести чтение памятки выше ``classify()``.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.skills.base import SkillContext
from apps.skills.health_screening.memo import (
    STATE_KEY,
    STATE_TTL_SECONDS,
    remember_screening_asked,
    screening_asked_recently,
)
from apps.skills.health_screening.skill import (
    RED_FLAG_REPLY,
    SOFT_PAIN_REPLY,
    HealthScreeningSkill,
)

# Реплики сняты с боевого диалога 06.09 — те самые пять ходов.
FIRST_COMPLAINT = "Что-то тянет поясницу"
SECOND_COMPLAINT = "Ну и как тебе донести, что болит спина?"
NOT_A_COMPLAINT = "Что ты понимаешь?"
RED_FLAG_COMPLAINT = "Болит спина и онемела рука"


def _conversation(*, asked_ago_seconds: int | None = None) -> SimpleNamespace:
    """Разговор с памяткой заданного возраста (``None`` — памятки нет).

    Чтение памятки — это чтение уже загруженного ``skill_state``, без
    похода в базу; поэтому здесь хватает объекта с полем.
    """

    state: dict = {}
    if asked_ago_seconds is not None:
        stamped = timezone.now() - timedelta(seconds=asked_ago_seconds)
        state[STATE_KEY] = {"at": stamped.isoformat()}
    return SimpleNamespace(id="conv-1542", skill_state=state)


def _context(text: str, conversation: SimpleNamespace) -> SkillContext:
    # Подделки вместо моделей — намеренно: памятка читает у разговора
    # только `skill_state`, а навык у человека не читает ничего, и
    # заводить строки в базе ради этого значило бы проверять ORM вместо
    # порядка проверок. Тот же приём и та же аннотация, что у соседнего
    # `apps/skills/nutrition_anketa/tests/test_skill.py`.
    return SkillContext(
        conversation=conversation,  # type: ignore[arg-type]
        bot_user=SimpleNamespace(id="bu-1542"),  # type: ignore[arg-type]
        message_text=text,
    )


class TestRedFlagIsNeverSilenced:
    """Самое дорогое место тикета — §35 п.5.

    Каждый тест здесь ставится ПОСЛЕ того, как скрининг уже задал свои
    вопросы: именно на повторном ходу память могла бы съесть красный флаг.
    """

    def test_red_flag_passes_even_when_screening_already_asked(self) -> None:
        conversation = _conversation(asked_ago_seconds=30)
        skill = HealthScreeningSkill()

        assert skill.matches(_context(RED_FLAG_COMPLAINT, conversation)) is True
        result = skill.handle(_context(RED_FLAG_COMPLAINT, conversation))
        assert result.reply_text == RED_FLAG_REPLY
        assert "врач" in result.reply_text.lower()

    def test_soft_repeat_is_silenced_on_the_very_same_conversation(self) -> None:
        """Парная отрицательная половина того же факта (DRF-1411).

        Один и тот же разговор с одной и той же памяткой: красный флаг
        выше прошёл, обычная жалоба — нет. Иначе тест выше мог бы
        зеленеть на памятке, которая просто не читается.
        """

        conversation = _conversation(asked_ago_seconds=30)

        assert HealthScreeningSkill().matches(_context(SECOND_COMPLAINT, conversation)) is False

    def test_red_flag_does_not_arm_the_memo(self) -> None:
        """Красный флаг не «занимает» память: гасить его нечем и незачем."""

        conversation = _conversation()
        HealthScreeningSkill().handle(_context(RED_FLAG_COMPLAINT, conversation))

        assert STATE_KEY not in conversation.skill_state


class TestTheLoopIsBroken:
    def test_first_complaint_reaches_the_screening(self) -> None:
        """Положительная стража (DRF-1411, DRF-358 T04) на тех же данных.

        Без неё «повтор не отвечает» зеленело бы и на скрининге, который
        не отвечает никогда.
        """

        conversation = _conversation()

        assert HealthScreeningSkill().matches(_context(FIRST_COMPLAINT, conversation)) is True
        assert (
            HealthScreeningSkill().handle(_context(FIRST_COMPLAINT, conversation)).reply_text
            == SOFT_PAIN_REPLY
        )

    def test_two_complaints_in_a_row_do_not_give_two_identical_screens(self) -> None:
        """Регресс на сам дефект — ровно первые два хода боевого диалога."""

        conversation = _conversation()
        skill = HealthScreeningSkill()

        assert skill.matches(_context(FIRST_COMPLAINT, conversation)) is True
        skill.handle(_context(FIRST_COMPLAINT, conversation))
        # handle() на объекте без базы памятку записать не может — на
        # боевом пути её пишет write_skill_state (см. DB-тест ниже).
        conversation.skill_state = _conversation(asked_ago_seconds=1).skill_state

        assert skill.matches(_context(SECOND_COMPLAINT, conversation)) is False

    def test_a_stale_memo_lets_the_screening_answer_again(self) -> None:
        """TTL 30 минут: жалоба через час — это новая жалоба, не петля."""

        conversation = _conversation(asked_ago_seconds=STATE_TTL_SECONDS + 60)

        assert HealthScreeningSkill().matches(_context(SECOND_COMPLAINT, conversation)) is True

    @pytest.mark.parametrize(
        "row",
        [None, "вчера", 42, {}, {"at": ""}, {"at": "не дата"}],
    )
    def test_unreadable_memo_means_not_asked(self, row) -> None:
        """Памятка гасит ответ — значит ошибаться обязана в сторону ответа."""

        conversation = SimpleNamespace(id="c", skill_state={STATE_KEY: row})

        assert screening_asked_recently(conversation) is False

    def test_no_conversation_is_not_a_memo(self) -> None:
        assert screening_asked_recently(None) is False


class TestNonPainIsStillNotScreening:
    """Классификатор не трогали — эти два хода он и раньше отдавал NONE."""

    @pytest.mark.parametrize("text", [NOT_A_COMPLAINT, "После тренировки", "Поясница", ""])
    def test_no_pain_signal_no_match(self, text: str) -> None:
        assert HealthScreeningSkill().matches(_context(text, _conversation())) is False


@pytest.mark.django_db
class TestTheMemoSurvivesTheDatabase:
    """Круг «записали → прочитали» на настоящей строке Conversation.

    Тесты выше читают памятку из готового ``skill_state``; этот
    показывает, что туда её кладёт сам навык — и кладёт так, что соседние
    подключи (``no_match``, ``nutrition_anketa``) остаются на месте.
    """

    def _conversation(self):
        from apps.conversations.services import resolve_active_global_conversation
        from apps.identity.services import resolve_or_create_global_bot_user

        bot_user = resolve_or_create_global_bot_user(
            channel="max",
            channel_user_id="drf1542-memo-uid",
            chat_id="drf1542-memo-chat",
        )
        return resolve_active_global_conversation(bot_user)

    def test_handle_writes_the_memo_and_keeps_the_neighbours(self) -> None:
        from apps.identity.services.global_tenant import get_global_bot_tenant
        from apps.tenancy.context import tenant_scope

        conversation = self._conversation()
        conversation.skill_state = {"no_match": [{"specialization": "маникюр", "city": "Пенза"}]}
        conversation.save(update_fields=["skill_state"])

        with tenant_scope(get_global_bot_tenant()):
            assert screening_asked_recently(conversation) is False
            HealthScreeningSkill().handle(_context(FIRST_COMPLAINT, conversation))

        conversation.refresh_from_db()
        assert screening_asked_recently(conversation) is True
        assert conversation.skill_state["no_match"] == [
            {"specialization": "маникюр", "city": "Пенза"}
        ]

    def test_remember_never_raises_without_a_tenant_in_scope(self) -> None:
        """Памятка — best-effort: потерять её дешевле, чем потерять ход."""

        remember_screening_asked(self._conversation())
