"""«Забудь всё» опустошает состояние навыков в диалоге (DRF-2181, Память-6).

Человек начал анкету питания — назвал вес, рост, возраст, цель, — бросил на
середине и сказал «забудь всё». Ответы анкеты живут не в памяти
(``MemoryEntry``), а в ``Conversation.skill_state["nutrition_anketa"]`` —
пока анкета не дописана, ничего никуда не переносится. Свип «забудь всё»
снимает память, обезличивает сообщения и черновики, а ``skill_state`` не
трогает никто: ``anonymize_dialogue`` двигает только ``anonymized_through``.

Матрица удаления (DRF-2134) объявляет это прямо и держит strict-xfail с
пометкой «лист следом»:

    "conversations.Conversation": «skill_state держит незавершённые анкеты
    (питание: вес/рост/цель); после „забудь всё" должен быть пуст»

# Почему весь ``skill_state``, а не только анкета

Кроме анкеты там лежат незаконченная запись (``booking_flow``), разбор еды
(``food_text``, ``food_scan*``), план (``plan_lite``), ручные ориентиры
(``nutrition_manual_target``) — всё это состояние, выведенное из того, что
человек сказал. Защит (согласий, запретов, замков) среди ключей нет: это
проверено замером. Матрица требует ``{}``.

# Граница по времени — та же, что у сообщений

``anonymize_dialogue`` берёт только диалоги, начатые ДО просьбы
(``created_at__lte=through``), и пропускает уже обезличенные до того же
момента. Отсюда два свойства, которые здесь пришпилены:

* диалог, начатый ПОСЛЕ просьбы, не трогается — там уже своё, новое;
* повторный прогон (свип идёт раз в час) не стирает анкету, начатую после
  первого прогона. То есть «лишнее» стирание ограничено первым прогоном —
  тем же выбором «ошибаться в сторону стирания», что уже сделан для
  черновиков мастера в той же функции.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.identity.services.forget_all_sweep import sweep_forget_all
from apps.identity.services.memory_deleter import request_forget_all
from apps.identity.services.tests.test_forget_all_sweep import (
    TestTheDialogueHalf,
    _upc,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

#: Незавершённая анкета: ровно то, что нельзя пережить «забудь всё».
ANKETA = {
    "step": "activity",
    "answers": {"weight_kg": 71, "height_cm": 168, "age": 34, "goal": "lose"},
}


@pytest.fixture()
def redis(monkeypatch):
    from apps.llm import pii_tokenizer
    from apps.orchestrator.memory import short_term

    fake = TestTheDialogueHalf._FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    monkeypatch.setattr(pii_tokenizer, "_redis_client", lambda: fake)
    return fake


def _forgotten_with_dialogue(settings, skill_state: dict):
    upc = _upc()
    request_forget_all(upc.user_id)
    upc.refresh_from_db()
    conversation, _message = TestTheDialogueHalf._dialogue(upc, settings)
    Conversation.all_tenants.filter(pk=conversation.pk).update(skill_state=skill_state)
    conversation.refresh_from_db()
    return upc, conversation


def _state(conversation) -> dict:
    return Conversation.all_tenants.get(pk=conversation.pk).skill_state


class TestTheAnketaDoesNotSurvive:
    def test_an_unfinished_nutrition_anketa_is_emptied(self, redis, settings) -> None:
        """Сердце листа: вес, рост, возраст из брошенной анкеты — после «забудь всё»."""
        upc, conversation = _forgotten_with_dialogue(settings, {"nutrition_anketa": ANKETA})

        sweep_forget_all(upc.user_id)

        assert _state(conversation) == {}

    def test_every_skill_key_goes_not_only_the_anketa(self, redis, settings) -> None:
        """Всё состояние навыков выведено из сказанного человеком — снимается всё."""
        upc, conversation = _forgotten_with_dialogue(
            settings,
            {
                "nutrition_anketa": ANKETA,
                "booking_flow": {"service": "массаж", "slot": "2026-09-22T15:00"},
                "food_text": {"last": "борщ 250"},
                "plan_lite": {"day": 3},
                "nutrition_manual_target": {"kcal": 1650},
            },
        )

        sweep_forget_all(upc.user_id)

        assert _state(conversation) == {}

    def test_the_dialogue_is_still_marked_anonymised(self, redis, settings) -> None:
        """Опустошение — тем же обновлением, что и отметка обезличивания."""
        upc, conversation = _forgotten_with_dialogue(settings, {"nutrition_anketa": ANKETA})

        sweep_forget_all(upc.user_id)

        row = Conversation.all_tenants.get(pk=conversation.pk)
        assert row.anonymized_through is not None
        assert row.skill_state == {}


class TestTheTimeBoundary:
    def test_a_dialogue_started_after_the_request_is_untouched(self, redis, settings) -> None:
        """Положительная пара к «снимает всё»: новый диалог — уже своё, новое."""
        upc = _upc()
        request_forget_all(upc.user_id)
        upc.refresh_from_db()
        settings.STRICT_TENANT_SCOPE = "off"
        tenant = Tenant.objects.create(slug=f"fas-{uuid.uuid4().hex[:8]}", name="Sweep")
        bot_user = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id=f"fas-{uuid.uuid4().hex[:8]}",
            ayla_user_id=upc.user_id,
        )
        fresh = Conversation.all_tenants.create(
            tenant=tenant, bot_user=bot_user, skill_state={"nutrition_anketa": ANKETA}
        )
        Conversation.all_tenants.filter(pk=fresh.pk).update(
            created_at=upc.forget_all_requested_at + timedelta(minutes=5)
        )

        sweep_forget_all(upc.user_id)

        assert _state(fresh) == {"nutrition_anketa": ANKETA}

    def test_a_re_run_does_not_wipe_an_anketa_begun_after_the_first_sweep(
        self, redis, settings
    ) -> None:
        """«Лишнее» стирание ограничено первым прогоном — повторный идёт мимо.

        Свип идёт раз в час. Анкета, начатая человеком после первого прогона
        в ТОМ ЖЕ диалоге, — уже его новое; второй прогон пропускает диалог,
        обезличенный до того же момента, и её не трогает.
        """
        upc, conversation = _forgotten_with_dialogue(settings, {"nutrition_anketa": ANKETA})
        sweep_forget_all(upc.user_id)
        assert _state(conversation) == {}

        new_anketa = {"nutrition_anketa": {"step": "weight", "answers": {}}}
        Conversation.all_tenants.filter(pk=conversation.pk).update(skill_state=new_anketa)
        sweep_forget_all(upc.user_id)

        assert _state(conversation) == new_anketa

    def test_a_person_who_never_asked_keeps_their_state(self, redis, settings) -> None:
        """Положительная пара: снимается только у попросивших."""
        upc = _upc()
        settings.STRICT_TENANT_SCOPE = "off"
        tenant = Tenant.objects.create(slug=f"fas-{uuid.uuid4().hex[:8]}", name="Sweep")
        bot_user = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id=f"fas-{uuid.uuid4().hex[:8]}",
            ayla_user_id=upc.user_id,
        )
        conversation = Conversation.all_tenants.create(
            tenant=tenant, bot_user=bot_user, skill_state={"nutrition_anketa": ANKETA}
        )

        sweep_forget_all(upc.user_id)

        assert _state(conversation) == {"nutrition_anketa": ANKETA}


class TestEveryErasurePathEmptiesIt:
    """Три пути стирания зовут один и тот же ``anonymize_dialogue``.

    Опустошение живёт там, а не в свипе, — иначе чатовое «забудь всё» (оно
    обезличивает сразу, не дожидаясь часа) оставило бы анкету лежать до
    ближайшего прогона.
    """

    def test_the_chat_command_empties_it_at_once(self, redis, settings) -> None:
        from apps.persona.memory_commands import _anonymize_dialogue

        upc, conversation = _forgotten_with_dialogue(settings, {"nutrition_anketa": ANKETA})

        _anonymize_dialogue(conversation.bot_user)

        assert _state(conversation) == {}

    def test_the_anonymiser_itself_empties_it(self, redis, settings) -> None:
        """Ниже по стеку: сама функция, которую зовут все три пути."""
        from apps.conversations.erasure import anonymize_dialogue
        from apps.conversations.models import ArchivedMessage

        upc, conversation = _forgotten_with_dialogue(settings, {"nutrition_anketa": ANKETA})

        anonymize_dialogue(
            [conversation.bot_user_id],
            through=timezone.now(),
            reason=ArchivedMessage.Reason.FORGET_ALL,
        )

        assert _state(conversation) == {}
