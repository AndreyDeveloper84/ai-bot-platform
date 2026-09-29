"""Адресация «позовите человека» с глобальной поверхности (DRF-2545).

**Решение главного окна 28.09, вариант (B).** Задача ложится на салонный
разговор, только когда салон у личности ОДИН. Салонов два и больше — очередь
платформы (GLOBAL-разговор под сентинел-тенантом), и **ни один салонный диалог
не переходит в ``HUMAN_HANDOFF``**. Новых слов человеку нет — та же строка
передачи.

**Было** (эти узлы держали его как страховку от тихой правки): последний по
``last_message_at`` салонный разговор. «В салоне Бета нагрубили, позовите
администратора» будило салон Альфа — его бот молчал, персоналу приходило
«клиент ждёт», жалоба на Бета лежала в ``reason`` задачи Альфа (буква (в)).
Замер стенда 28.09 (главное окно, read-only): активных разговоров 22 = 16
салонных + 6 глобальных; личностей с двумя и более салонами — 0. На пилоте
поведение не меняется; дыра закрыта на будущее.

Следующий шаг — вопрос «о каком салоне речь?» — видимый текст, ждёт слова
владельца. Когда он появится, эти узлы покраснеют снова, и это правильно.

Узлы:

* пара, которая обязана различаться: один салон → задача ему и он замолкает;
  два салона → очередь платформы и не замолкает НИ ОДИН. Второе важнее —
  именно оно и есть буква (в);
* считаются САЛОНЫ, а не разговоры: теневой разговор (``is_shadow``) живёт
  рядом с основным в том же салоне — он не делает салонов больше и задачу
  не получает (прежняя выборка его не исключала, и свежая теневая строка
  выигрывала по давности);
* выключенный салон (``Tenant.is_active=False``) не адресат и не второй
  салон: живой рядом с ним получает задачу, один выключенный — очередь
  платформы;
* названный салон, который не последний, — больше не адресат по давности:
  задача у платформы, оба салона не тронуты;
* упоминание сотрудника («я сам администратор») — не просьба, салон не будит
  (DRF-2545, #2106);
* текст жалобы не лежит ни в одной салонной задаче — только в задаче
  платформы.

Уже закреплено в ``test_global_human_handoff.py``: салонов нет — очередь
платформы (``test_no_tenant_context_lands_on_platform_queue``); два салона —
очередь платформы (``test_two_salons_go_to_the_platform_queue_and_no_salon_is_muted``);
один салон — задача ему, глобальный путь молчит до закрытия
(``test_global_dialog_muted_when_task_went_to_tenant``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone as dt_timezone

import pytest

from apps.channels.max import handler as max_handler
from apps.conversations.models import Conversation
from apps.handoff.models import AdminTask
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.models import BotUser
from apps.orchestrator.memory import short_term
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

HANDOFF_REPLY = "Передаю менеджеру — ответят в течение 30 минут."


def _run_global(text: str, *, mid: str, user_id: int = 2545) -> None:
    max_handler.handle_global_max_event(
        {
            "update_type": "message_created",
            "timestamp": 1731320000000,
            "message": {
                "sender": {"user_id": user_id, "name": "Иван"},
                "recipient": {"chat_id": user_id, "chat_type": "dialog"},
                "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
            },
        },
        trace_id=str(uuid.uuid4()),
    )


@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def spy_concierge(monkeypatch):
    from unittest.mock import MagicMock

    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Какая услуга интересует?"))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


def _salon_dialog(slug: str, name: str, *, day: int, user_id: int = 2545) -> Conversation:
    """Прежний разговор того же человека с салоном; ``day`` задаёт давность."""
    tenant = Tenant.objects.create(slug=slug, name=name)
    with tenant_scope(tenant):
        bot_user = BotUser.objects.create(
            tenant=tenant, channel="max", channel_user_id=str(user_id)
        )
        conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
    Conversation.all_tenants.filter(pk=conv.pk).update(
        last_message_at=datetime(2026, 9, day, tzinfo=dt_timezone.utc)
    )
    conv.refresh_from_db()
    return conv


def _shadow_dialog(primary: Conversation, *, day: int) -> Conversation:
    """Теневой разговор рядом с основным: та же личность, тот же салон."""
    with tenant_scope(primary.tenant):
        conv = Conversation.all_tenants.create(
            tenant=primary.tenant, bot_user=primary.bot_user, is_shadow=True
        )
    Conversation.all_tenants.filter(pk=conv.pk).update(
        last_message_at=datetime(2026, 9, day, tzinfo=dt_timezone.utc)
    )
    conv.refresh_from_db()
    return conv


def _muted(*convs: Conversation) -> list[bool]:
    out = []
    for conv in convs:
        conv.refresh_from_db()
        out.append(conv.state == Conversation.State.HUMAN_HANDOFF)
    return out


@pytest.fixture
def two_salons():
    """Салон Б — разговор давний, салон А — последний."""
    salon_b = _salon_dialog("salon-b-2545", "Салон Бета", day=1)
    salon_a = _salon_dialog("salon-a-2545", "Салон Альфа", day=20)
    return salon_a, salon_b


REQUEST = "позовите администратора, пожалуйста"


class TestOneSalonVersusTwo:
    def test_one_salon_gets_the_task_and_its_dialog_is_muted(
        self, mock_send, fake_redis, spy_concierge
    ):
        only = _salon_dialog("salon-only-2545", "Салон Один", day=20)

        _run_global(REQUEST, mid="p1")

        task = AdminTask.all_tenants.get()
        assert task.tenant_id == only.tenant_id
        assert _muted(only) == [True]
        assert mock_send[-1]["text"] == HANDOFF_REPLY

    def test_two_salons_go_to_the_platform_and_no_salon_is_muted(
        self, two_salons, mock_send, fake_redis, spy_concierge
    ):
        salon_a, salon_b = two_salons

        _run_global(REQUEST, mid="p2")

        task = AdminTask.all_tenants.get()
        assert task.tenant.slug == GLOBAL_BOT_TENANT_SLUG
        # Главное утверждение (буква (в)): не замолк НИ ОДИН салон.
        assert _muted(salon_a, salon_b) == [False, False]
        # Человек слышит ту же строку, что и при одном салоне.
        assert mock_send[-1]["text"] == HANDOFF_REPLY


class TestSalonsAreCountedNotConversations:
    """Основной активный разговор у пары (личность, салон) один — его держит
    ``conversation_one_active_per_bot_user_tenant``. Но условие ограничения
    исключает теневые строки, и теневой разговор живёт рядом с основным. Он
    не делает салонов больше и не становится адресатом."""

    def test_a_shadow_dialog_neither_adds_a_salon_nor_takes_the_task(
        self, mock_send, fake_redis, spy_concierge
    ):
        primary = _salon_dialog("salon-shadow-2545", "Салон Тень", day=5)
        shadow = _shadow_dialog(primary, day=20)  # свежее основного

        _run_global(REQUEST, mid="c1")

        task = AdminTask.all_tenants.get()
        # Салон один — задача ему, и на ОСНОВНОЙ разговор, не на теневой.
        assert task.tenant_id == primary.tenant_id
        assert task.conversation_id == primary.id
        assert _muted(primary, shadow) == [True, False]

    def test_a_shadow_in_a_plus_b_is_still_two_salons(self, mock_send, fake_redis, spy_concierge):
        b = _salon_dialog("salon-b2-2545", "Салон Бета", day=1)
        a = _salon_dialog("salon-a2-2545", "Салон Альфа", day=19)
        a_shadow = _shadow_dialog(a, day=20)

        _run_global(REQUEST, mid="c2")

        task = AdminTask.all_tenants.get()
        assert task.tenant.slug == GLOBAL_BOT_TENANT_SLUG
        assert _muted(a, a_shadow, b) == [False, False, False]


class TestADeactivatedSalonIsNotAnAddressee:
    def test_a_live_salon_next_to_a_deactivated_one_gets_the_task(
        self, mock_send, fake_redis, spy_concierge
    ):
        live = _salon_dialog("salon-live-2545", "Салон Живой", day=5)
        dead = _salon_dialog("salon-dead-2545", "Салон Выключен", day=20)
        Tenant.objects.filter(pk=dead.tenant_id).update(is_active=False)

        _run_global(REQUEST, mid="d1")

        task = AdminTask.all_tenants.get()
        assert task.tenant_id == live.tenant_id
        assert _muted(live, dead) == [True, False]

    def test_only_a_deactivated_salon_goes_to_the_platform(
        self, mock_send, fake_redis, spy_concierge
    ):
        dead = _salon_dialog("salon-dead2-2545", "Салон Выключен", day=20)
        Tenant.objects.filter(pk=dead.tenant_id).update(is_active=False)

        _run_global(REQUEST, mid="d2")

        task = AdminTask.all_tenants.get()
        assert task.tenant.slug == GLOBAL_BOT_TENANT_SLUG
        assert _muted(dead) == [False]


class TestTheNamedSalonIsNoLongerOverruledByTime:
    def test_named_salon_that_is_not_the_latest_wakes_no_salon(
        self, two_salons, mock_send, fake_redis, spy_concierge
    ):
        """Было: задача у Альфа (последний), Альфа замолкал. Решение 28.09 (B)."""
        salon_a, salon_b = two_salons

        _run_global("позовите администратора, в салоне Бета мне нагрубили", mid="n1")

        task = AdminTask.all_tenants.get()
        assert task.task_type == AdminTask.TaskType.HANDOFF
        assert mock_send[-1]["text"] == HANDOFF_REPLY
        assert task.tenant.slug == GLOBAL_BOT_TENANT_SLUG
        assert _muted(salon_a, salon_b) == [False, False]

    def test_a_mention_of_staff_does_not_wake_a_salon(
        self, two_salons, mock_send, fake_redis, spy_concierge
    ):
        """«я сам администратор» — упоминание, не просьба (DRF-2545, #2106)."""
        salon_a, _ = two_salons

        _run_global("я сам администратор", mid="n2")

        # Присутствие: сообщение обработано — ответил консьерж.
        spy_concierge.assert_called_once()
        assert len(mock_send) == 1
        assert AdminTask.all_tenants.count() == 0
        assert _muted(salon_a) == [False]


class TestWhereTheComplaintTextGoes:
    def test_the_complaint_sits_with_the_platform_not_with_a_salon(
        self, two_salons, mock_send, fake_redis, spy_concierge
    ):
        """Было: жалоба на Бета лежала в ``reason`` задачи Альфа. Решение 28.09 (B)."""
        salon_a, salon_b = two_salons
        complaint = "позовите администратора, в салоне Бета мне нагрубили"

        _run_global(complaint, mid="n3")

        task = AdminTask.all_tenants.get()
        # Присутствие: текст сохранён — у платформы.
        assert complaint in task.reason
        assert task.tenant.slug == GLOBAL_BOT_TENANT_SLUG
        salon_tenants = {salon_a.tenant_id, salon_b.tenant_id}
        assert not AdminTask.all_tenants.filter(tenant_id__in=salon_tenants).exists()
