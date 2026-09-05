"""Переписку открывает пропуск, а не роль (DRF-1514).

Каждая проверка здесь — пара. «Смотрящему нельзя» само по себе не
доказывает ничего: 403 одинаково выдаётся и когда политика работает, и
когда экран не собрался, и когда объекта нет. Поэтому рядом с каждым
запретом стоит разрешение на **тех же** данных и тем же запросом.

Отдельно стоит :func:`test_a_handoff_is_worked_from_queue_to_closure` —
разбор реального обращения от очереди до закрытия. Без него легко
«закрыть» доступ так, что работать станет невозможно, и это была бы не
перевыполненная задача, а невыполненная.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.adminconsole.models import ClientDataAccessGrant, ClientDataAccessLog
from apps.adminconsole.tests.conftest import make_client_thread
from apps.handoff.models import AdminTask
from apps.ingress.models import WebhookJournal

pytestmark = pytest.mark.django_db

MESSAGES_URL = "/admin/conversations/message/"
CONVERSATIONS_URL = "/admin/conversations/conversation/"
QUEUE_URL = "/admin/handoff/admintask/"
GRANT_ADD_URL = "/admin/adminconsole/clientdataaccessgrant/add/"


def _body(response) -> str:  # noqa: ANN001
    return response.content.decode()


# ── список переписок ──────────────────────────────────────────────────


def test_owner_still_reads_the_whole_list(owner_client, salon) -> None:  # noqa: ANN001
    """Присутствие: список вообще существует и содержит переписку.

    Без этой половины отказ ниже мог бы означать «здесь пусто», а не
    «сюда нельзя».
    """
    make_client_thread(salon, channel_user_id="c1", display_name="Аня", text="крашу волосы")

    response = owner_client.get(MESSAGES_URL)

    assert response.status_code == 200
    assert "крашу волосы" in _body(response)


def test_viewer_is_refused_the_list_and_told_why(login_as, salon) -> None:  # noqa: ANN001
    """Не «пусто», а честный отказ с объяснением и что делать дальше."""
    make_client_thread(salon, channel_user_id="c1", display_name="Аня", text="крашу волосы")
    client = login_as("i.smotryashiy", "viewer")

    response = client.get(MESSAGES_URL)

    assert response.status_code == 403, "список переписок всех салонов остался открыт"
    body = _body(response)
    assert "Общий список переписок закрыт" in body
    assert "крашу волосы" not in body, "отказ отдал ровно то, что запрещал"
    # Отказ объясняет и показывает выход, а не просто хлопает дверью.
    assert "обращение" in body.lower()
    assert GRANT_ADD_URL in body
    assert QUEUE_URL in body


def test_viewer_is_refused_the_conversation_list_too(login_as, salon) -> None:  # noqa: ANN001
    make_client_thread(salon, channel_user_id="c1", display_name="Аня", text="крашу волосы")
    client = login_as("i.smotryashiy", "viewer")

    assert client.get(CONVERSATIONS_URL).status_code == 403


def test_the_queue_itself_stays_open(login_as, salon) -> None:  # noqa: ANN001
    """Очередь обращений — рабочий список, а не переписка.

    Если бы закрылась и она, сотрудник не нашёл бы обращение, с которым
    работает, и задача превратилась бы в «работать нельзя».
    """
    make_client_thread(salon, channel_user_id="c1", display_name="Аня", text="крашу волосы")
    client = login_as("i.smotryashiy", "viewer")

    response = client.get(QUEUE_URL)

    assert response.status_code == 200
    assert "крашу волосы" not in _body(response), "в списке очереди оказалась переписка"


# ── причина ───────────────────────────────────────────────────────────


def test_opening_without_a_reason_is_refused(login_as, salon) -> None:  # noqa: ANN001
    """Пустая причина — отказ, и доступ так и не открылся."""
    _, _, _, task = make_client_thread(
        salon, channel_user_id="c1", display_name="Аня", text="крашу волосы"
    )
    client = login_as("i.smotryashiy", "viewer")

    response = client.post(GRANT_ADD_URL, {"admin_task": str(task.id), "reason": "   "})

    assert response.status_code == 200, "форма ушла дальше без причины"
    assert not ClientDataAccessGrant.objects.exists()
    assert client.get(MESSAGES_URL).status_code == 403


def test_a_reason_too_short_to_explain_anything_is_refused(login_as, salon) -> None:  # noqa: ANN001
    _, _, _, task = make_client_thread(
        salon, channel_user_id="c1", display_name="Аня", text="крашу волосы"
    )
    client = login_as("i.smotryashiy", "viewer")

    response = client.post(GRANT_ADD_URL, {"admin_task": str(task.id), "reason": "надо"})

    assert response.status_code == 200
    assert not ClientDataAccessGrant.objects.exists()


def test_with_a_reason_the_clients_correspondence_opens(login_as, salon) -> None:  # noqa: ANN001
    """Та же страница, тот же клиент — но после причины."""
    _, _, _, task = make_client_thread(
        salon, channel_user_id="c1", display_name="Аня", text="крашу волосы"
    )
    client = login_as("i.smotryashiy", "viewer")
    assert client.get(MESSAGES_URL).status_code == 403  # присутствие: было закрыто

    client.post(
        GRANT_ADD_URL,
        {"admin_task": str(task.id), "reason": "разбираю жалобу на запись от 5 сентября"},
    )
    response = client.get(MESSAGES_URL)

    assert response.status_code == 200
    assert "крашу волосы" in _body(response)


# ── один пропуск — один клиент ────────────────────────────────────────


def test_a_grant_for_one_client_does_not_open_another(login_as, salon, other_salon) -> None:  # noqa: ANN001
    _, _, mine, my_task = make_client_thread(
        salon, channel_user_id="c1", display_name="Аня", text="крашу волосы"
    )
    _, foreign_conversation, foreign, _ = make_client_thread(
        other_salon, channel_user_id="c2", display_name="Борис", text="чужая переписка"
    )
    client = login_as("i.smotryashiy", "viewer")
    client.post(
        GRANT_ADD_URL,
        {"admin_task": str(my_task.id), "reason": "разбираю жалобу на запись от 5 сентября"},
    )

    listing = _body(client.get(MESSAGES_URL))
    assert "крашу волосы" in listing  # присутствие: свой клиент виден
    assert "чужая переписка" not in listing

    card = client.get(f"/admin/conversations/conversation/{foreign_conversation.pk}/change/")
    assert card.status_code == 403
    assert "другому клиенту" in _body(card)
    assert str(foreign.pk) not in _body(card)


def test_an_expired_grant_closes_the_door_again(login_as, salon) -> None:  # noqa: ANN001
    _, _, _, task = make_client_thread(
        salon, channel_user_id="c1", display_name="Аня", text="крашу волосы"
    )
    client = login_as("i.smotryashiy", "viewer")
    client.post(
        GRANT_ADD_URL,
        {"admin_task": str(task.id), "reason": "разбираю жалобу на запись от 5 сентября"},
    )
    assert client.get(MESSAGES_URL).status_code == 200  # присутствие: пропуск работал

    ClientDataAccessGrant.objects.update(expires_at=timezone.now() - timedelta(minutes=1))

    assert client.get(MESSAGES_URL).status_code == 403


# ── журнал доступа ────────────────────────────────────────────────────


def test_every_view_leaves_a_row_with_author_client_time_and_reason(login_as, salon) -> None:  # noqa: ANN001
    bot_user, _, _, task = make_client_thread(
        salon, channel_user_id="c1", display_name="Аня", text="крашу волосы"
    )
    client = login_as("i.smotryashiy", "viewer")
    reason = "разбираю жалобу на запись от 5 сентября"

    client.post(GRANT_ADD_URL, {"admin_task": str(task.id), "reason": reason})
    client.get(MESSAGES_URL)

    opened = ClientDataAccessLog.objects.filter(
        outcome=ClientDataAccessLog.Outcome.OPENED, screen="conversations.message"
    )
    assert opened.count() == 1
    row = opened.get()
    assert row.actor_username == "i.smotryashiy"
    assert row.client_id == bot_user.id
    assert row.client_label == "Аня"
    assert row.tenant_slug == salon.slug
    assert row.reason == reason
    assert row.occurred_at is not None
    assert ClientDataAccessLog.objects.filter(outcome=ClientDataAccessLog.Outcome.GRANTED).exists()


def test_a_refusal_is_journaled_too(login_as, salon) -> None:  # noqa: ANN001
    """Попытка полистать чужое — тоже событие доступа."""
    make_client_thread(salon, channel_user_id="c1", display_name="Аня", text="крашу волосы")
    client = login_as("i.smotryashiy", "viewer")

    client.get(MESSAGES_URL)

    denied = ClientDataAccessLog.objects.filter(outcome=ClientDataAccessLog.Outcome.DENIED)
    assert denied.count() == 1
    assert denied.get().actor_username == "i.smotryashiy"
    assert denied.get().screen == "conversations.message"


def test_the_access_journal_is_a_different_journal_from_the_change_journal() -> None:
    """Просмотр ничего не правит — в журнале изменений его нет и быть не может."""
    from django.contrib.admin.models import LogEntry

    assert ClientDataAccessLog._meta.db_table != LogEntry._meta.db_table
    change_fields = {f.name for f in LogEntry._meta.fields}
    assert "reason" not in change_fields, (
        "если бы у журнала изменений была причина просмотра, вторая таблица была бы лишней"
    )


def test_a_viewer_reads_only_their_own_access_trail(login_as, owner_client, salon) -> None:  # noqa: ANN001
    """Журнал доступа не должен стать новым справочником клиентов."""
    _, _, _, task = make_client_thread(
        salon, channel_user_id="c1", display_name="Аня", text="крашу волосы"
    )
    first = login_as("i.pervyy", "viewer")
    first.post(
        GRANT_ADD_URL,
        {"admin_task": str(task.id), "reason": "разбираю жалобу на запись от 5 сентября"},
    )
    second = login_as("i.vtoroy", "viewer")

    journal_url = "/admin/adminconsole/clientdataaccesslog/"
    assert "Аня" in _body(owner_client.get(journal_url))  # присутствие: строка есть
    assert "Аня" not in _body(second.get(journal_url))


# ── медданные и телефон ───────────────────────────────────────────────


def test_health_and_phone_never_appear_no_matter_the_reason(  # noqa: ANN001
    login_as, owner_client, salon
) -> None:
    """152-ФЗ ст. 10 и DRF-1039 — не «по умолчанию скрыто», а никогда."""
    bot_user, _, _, task = make_client_thread(
        salon,
        channel_user_id="c1",
        display_name="Аня",
        text="крашу волосы",
        phone="+79995550101",
        context={"nutrition_proactive": {"last_meal": "гречка с котлетой"}},
    )
    card_url = f"/admin/identity/botuser/{bot_user.pk}/change/"

    # Присутствие: данные действительно лежат в базе и на экране рисуются.
    owner_body = _body(owner_client.get(card_url))
    assert "+79995550101" in owner_body
    assert "гречка с котлетой" in owner_body

    client = login_as("i.smotryashiy", "viewer")
    client.post(
        GRANT_ADD_URL,
        {"admin_task": str(task.id), "reason": "разбираю жалобу на запись от 5 сентября"},
    )
    response = client.get(card_url)

    assert response.status_code == 200, "профиль должен открываться — закрывали не его"
    body = _body(response)
    assert "Аня" in body, "профиль открылся пустым — проверка ниже была бы ни о чём"
    assert "+79995550101" not in body
    assert "гречка с котлетой" not in body
    assert "nutrition_proactive" not in body


def test_raw_webhook_payload_is_owner_only(login_as, owner_client, salon) -> None:  # noqa: ANN001
    """Сырьё вебхука сузить пропуском нельзя — значит, только владельцу."""
    journal = WebhookJournal.objects.create(
        channel="max",
        external_event_id="evt-1",
        raw_payload={"message": {"text": "текст клиента из вебхука"}},
        resolved_tenant=salon,
    )
    card_url = f"/admin/ingress/webhookjournal/{journal.pk}/change/"

    assert "текст клиента из вебхука" in _body(owner_client.get(card_url))  # присутствие

    client = login_as("i.smotryashiy", "viewer")
    response = client.get(card_url)

    assert response.status_code == 200, "разбор инцидента по метаданным должен идти"
    assert "evt-1" in _body(response), "метаданные должны остаться — иначе разбор встал"
    assert "текст клиента из вебхука" not in _body(response)


# ── парная положительная проверка ─────────────────────────────────────


def test_a_handoff_is_worked_from_queue_to_closure(login_as, salon) -> None:  # noqa: ANN001
    """Разбор реального обращения — от очереди до закрытия, без тупиков.

    Проверка обязательна вместе с запретами выше: доступ легко закрыть
    так, что работать станет нельзя, и это будет не перевыполнение, а
    невыполнение задачи.
    """
    from apps.conversations.models import Conversation
    from apps.handoff.services import create_admin_task
    from apps.tenancy.context import tenant_scope

    bot_user, conversation, message, stub = make_client_thread(
        salon, channel_user_id="c1", display_name="Аня", text="меня записали не к тому мастеру"
    )
    # Обращение здесь заводит настоящая служба: два открытых обращения на
    # один разговор держат бота выключенным, и закрытие первого ничего
    # не доказало бы.
    stub.delete()
    with tenant_scope(salon):
        task = create_admin_task(conversation, task_type=AdminTask.TaskType.COMPLAINT)

    client = login_as("i.rabotnik", "editor")
    task_url = f"/admin/handoff/admintask/{task.pk}/change/"

    # 1. Обращение видно в очереди — иначе не с чего начинать.
    queue = client.get(QUEUE_URL)
    assert queue.status_code == 200
    assert str(task.pk)[:8] in _body(queue)

    # 2. Карточка обращения несёт переписку, поэтому закрыта — но отказ
    #    ведёт ровно туда, куда надо, и уже с подставленным обращением.
    refusal = client.get(task_url)
    assert refusal.status_code == 403
    prefilled = f"{GRANT_ADD_URL}?admin_task={task.pk}"
    assert prefilled in _body(refusal)

    # 3. Форма открывается по этой ссылке с уже выбранным обращением.
    form = client.get(prefilled)
    assert form.status_code == 200
    assert str(task.pk) in _body(form)

    # 4. Причина указана — и человека возвращают на обращение.
    opened = client.post(
        prefilled,
        {"admin_task": str(task.pk), "reason": "клиента записали не к тому мастеру, разбираю"},
    )
    assert opened.status_code == 302
    assert opened["Location"] == task_url

    # 5. Карточка обращения открывается, и в ней видна переписка.
    card = client.get(task_url)
    assert card.status_code == 200

    # 6. Разговор, сообщения и профиль клиента — все на месте.
    conversation_card = client.get(f"/admin/conversations/conversation/{conversation.pk}/change/")
    assert conversation_card.status_code == 200
    listing = client.get(MESSAGES_URL)
    assert listing.status_code == 200
    assert "меня записали не к тому мастеру" in _body(listing)
    assert client.get(f"/admin/conversations/message/{message.pk}/change/").status_code == 200
    assert client.get(f"/admin/identity/botuser/{bot_user.pk}/change/").status_code == 200

    # 7. Обращение закрывается — и бот получает разговор обратно.
    closed = client.post(
        task_url,
        {
            "status": AdminTask.Status.RESOLVED,
            "assigned_to": "",
            "resolution_note": "перезаписали к нужному мастеру",
        },
    )
    assert closed.status_code == 302, _body(closed)[:2000]
    task.refresh_from_db()
    assert task.status == AdminTask.Status.RESOLVED
    conversation.refresh_from_db()
    assert conversation.state == Conversation.State.IDLE

    # 8. Весь разбор виден в журнале доступа — с автором и причиной.
    trail = ClientDataAccessLog.objects.filter(actor_username="i.rabotnik")
    assert trail.filter(outcome=ClientDataAccessLog.Outcome.DENIED).exists()
    assert trail.filter(outcome=ClientDataAccessLog.Outcome.GRANTED).exists()
    screens = set(
        trail.filter(outcome=ClientDataAccessLog.Outcome.OPENED).values_list("screen", flat=True)
    )
    assert {
        "handoff.admintask",
        "conversations.conversation",
        "conversations.message",
        "identity.botuser",
    } <= screens
    assert all(row.reason for row in trail.filter(outcome=ClientDataAccessLog.Outcome.OPENED))
