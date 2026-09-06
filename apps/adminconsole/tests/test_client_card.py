"""Карточка клиента (DRF-1497): границы и эффекты.

Пары «присутствие/отсутствие» обязательны (DRF-1411): каждое «телефона
нет» стоит рядом с «разрешённые поля на тех же данных есть», каждое
«отклонено» — рядом с «разрешённое проходит».
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.adminconsole.tests.conftest import make_client_thread  # noqa: F401 — фабрика данных
from apps.audit.models import AuditLog
from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser
from apps.identity.services.blocking import block_user, unblock_user

SEARCH_URL = "/admin/console/clients/"


def _body(response) -> str:  # noqa: ANN001, ANN202
    return response.content.decode("utf-8", errors="replace")


def _card_url(bot_user: BotUser) -> str:
    return f"{SEARCH_URL}{bot_user.pk}/"


@pytest.fixture
def client_thread(salon):  # noqa: ANN001, ANN201
    """Человек с телефоном, диалогом, сообщением и обращением."""
    return make_client_thread(
        salon,
        channel_user_id="cid-101",
        display_name="Алина",
        text="хочу записаться на стрижку",
        phone="+79991234567",
    )


# ── телефон: нигде; разрешённые поля: на месте ────────────────────────


def test_phone_appears_nowhere_allowed_fields_do(login_as, client_thread) -> None:  # noqa: ANN001
    bot_user, *_ = client_thread
    client = login_as("i.kartochka", "viewer")

    # Присутствие: поиск по имени находит, разрешённые поля рисуются.
    search = client.get(SEARCH_URL, {"q": "Алина"})
    assert search.status_code == 200
    body = _body(search)
    assert "Алина" in body
    assert "cid-101" in body

    card = client.get(_card_url(bot_user))
    assert card.status_code == 200
    card_body = _body(card)
    assert "Алина" in card_body
    assert "cid-101" in card_body
    assert "Салон DRF-1514" in card_body

    # Отсутствие поверх тех же данных: телефона нет ни в поиске, ни в карточке.
    assert "+79991234567" not in body
    assert "+79991234567" not in card_body
    assert "9991234567" not in card_body

    # Поиск по телефону никого не находит. Пара: по имени находит (выше).
    by_phone = client.get(SEARCH_URL, {"q": "+79991234567"})
    phone_body = _body(by_phone)
    assert "Никого не нашлось" in phone_body
    assert "Алина" not in phone_body


def test_card_shows_dialog_metadata_never_texts(login_as, client_thread) -> None:  # noqa: ANN001
    bot_user, _, message, _ = client_thread
    client = login_as("i.metadannye", "viewer")

    card_body = _body(client.get(_card_url(bot_user)))

    # Присутствие: метаданные диалога есть — состояние и счётчик сообщений.
    assert "IDLE" in card_body
    assert "Диалоги" in card_body
    # Отсутствие: текста сообщения нет — переписка открывается отдельно (DRF-1514).
    assert message.content not in card_body


def test_card_shows_consents_with_dates(login_as, salon, client_thread) -> None:  # noqa: ANN001
    bot_user, *_ = client_thread
    ConsentRecord.all_tenants.create(
        tenant=salon,
        bot_user=bot_user,
        consent_type=ConsentRecord.ConsentType.PERSONAL_DATA,
        granted=True,
        source="registration_form",
    )
    ConsentRecord.all_tenants.create(
        tenant=salon,
        bot_user=bot_user,
        consent_type=ConsentRecord.ConsentType.HEALTH,
        granted=True,
        source="bot_message:abc",
    )
    client = login_as("i.soglasiya", "viewer")

    card_body = _body(client.get(_card_url(bot_user)))

    # Оба воротных типа на месте — факт согласия HEALTH показывать можно.
    assert "Personal data" in card_body
    assert "Health" in card_body
    assert "Согласия" in card_body


# ── согласия из админки не ставятся ──────────────────────────────────


def test_consent_cannot_be_granted_from_admin(login_as, salon, client_thread) -> None:  # noqa: ANN001
    bot_user, *_ = client_thread
    ConsentRecord.all_tenants.create(
        tenant=salon,
        bot_user=bot_user,
        consent_type=ConsentRecord.ConsentType.PERSONAL_DATA,
        granted=True,
        source="registration_form",
    )
    client = login_as("i.stavit", "editor")

    # Присутствие: согласия читаются — экран не пуст, проверка ниже не ни о чём.
    assert ConsentRecord.all_tenants.filter(
        bot_user=bot_user, consent_type=ConsentRecord.ConsentType.PERSONAL_DATA
    ).exists()

    # Отклонено: ни форма, ни прямой POST — согласие за человека не ставится.
    assert client.get("/admin/consent/consentrecord/add/").status_code == 403
    posted = client.post(
        "/admin/consent/consentrecord/add/",
        {
            "tenant": str(salon.pk),
            "bot_user": str(bot_user.pk),
            "consent_type": ConsentRecord.ConsentType.HEALTH,
            "granted": "on",
            "source": "admin:override",
        },
    )
    assert posted.status_code == 403
    assert not ConsentRecord.all_tenants.filter(
        bot_user=bot_user, consent_type=ConsentRecord.ConsentType.HEALTH
    ).exists()


# ── блокировка: причина обязательна, журнал с автором ────────────────


def test_block_without_reason_is_rejected(login_as, client_thread) -> None:  # noqa: ANN001
    bot_user, *_ = client_thread
    client = login_as("i.blokiruyushiy", "editor")

    form = client.get(f"{_card_url(bot_user)}block/")
    assert form.status_code == 200
    assert "Причина" in _body(form), "форма должна открываться — иначе проверка ни о чём"

    response = client.post(f"{_card_url(bot_user)}block/", {"reason": ""})
    assert response.status_code == 400
    assert "Причина не указана" in _body(response)
    bot_user.refresh_from_db()
    assert bot_user.blocked_at is None

    short = client.post(f"{_card_url(bot_user)}block/", {"reason": "плохой"})
    assert short.status_code == 400
    bot_user.refresh_from_db()
    assert bot_user.blocked_at is None


def test_block_and_unblock_are_journaled_with_actor_and_reason(login_as, client_thread) -> None:  # noqa: ANN001
    bot_user, *_ = client_thread
    client = login_as("i.zhurnalny", "editor")

    blocked = client.post(f"{_card_url(bot_user)}block/", {"reason": "шлёт рекламный спам в чат"})
    assert blocked.status_code == 302
    bot_user.refresh_from_db()
    assert bot_user.blocked_at is not None
    assert bot_user.blocked_by_username == "i.zhurnalny"

    row = AuditLog.all_tenants.get(action="admin.client.blocked")
    assert row.payload["actor_username"] == "i.zhurnalny"
    assert row.payload["reason"] == "шлёт рекламный спам в чат"
    assert "phone" not in row.payload, "телефону в журнале не место (DRF-1039)"

    unblocked = client.post(
        f"{_card_url(bot_user)}unblock/", {"reason": "подтвердил, что это был он сам"}
    )
    assert unblocked.status_code == 302
    bot_user.refresh_from_db()
    assert bot_user.blocked_at is None
    assert AuditLog.all_tenants.filter(
        action="admin.client.unblocked", payload__actor_username="i.zhurnalny"
    ).exists()


def test_viewer_cannot_block_editor_can(login_as, client_thread) -> None:  # noqa: ANN001
    bot_user, *_ = client_thread
    viewer = login_as("i.tolko-smotrit", "viewer")

    # Присутствие: карточку смотрящий открывает и видит клиента.
    card = viewer.get(_card_url(bot_user))
    assert card.status_code == 200
    card_body = _body(card)
    assert "Алина" in card_body
    # Кнопки «Заблокировать» у роли без права change нет.
    assert "Заблокировать" not in card_body

    assert viewer.get(f"{_card_url(bot_user)}block/").status_code == 403
    posted = viewer.post(f"{_card_url(bot_user)}block/", {"reason": "проверяю права роли"})
    assert posted.status_code == 403
    bot_user.refresh_from_db()
    assert bot_user.blocked_at is None


# ── эффект: заблокированный перестаёт получать, остальные — нет ──────


@pytest.mark.django_db
def test_blocked_recipient_stops_receiving_others_unaffected(  # noqa: ANN001
    salon, settings, httpx_mock
) -> None:
    from apps.channels.max.outbound import send_message

    settings.MAX_BOT_TOKEN = "test-token-xyz"
    settings.MAX_API_BASE = "https://botapi.max.ru"

    actor = get_user_model().objects.create_user("i.effekt")
    blocked_user = BotUser.all_tenants.create(
        tenant=salon, channel="max", channel_user_id="eff-1", chat_id="chat-eff-1"
    )
    free_user = BotUser.all_tenants.create(
        tenant=salon, channel="max", channel_user_id="eff-2", chat_id="chat-eff-2"
    )
    block_user(actor=actor, bot_user=blocked_user, reason="тест эффекта блокировки")

    httpx_mock.add_response(json={"ok": True}, status_code=200)

    # Парная положительная: незаблокированный получает — запрос ушёл.
    result_free = send_message(chat_id=free_user.chat_id, text="напоминание")
    assert result_free == {"ok": True}
    assert httpx_mock.get_request() is not None

    # Заблокированный — тишина: до MAX запрос не дошёл вообще.
    result_blocked = send_message(chat_id=blocked_user.chat_id, text="напоминание")
    assert result_blocked == {"blocked": True}
    assert len(httpx_mock.get_requests()) == 1, "второй запрос уходить не должен"

    # Снятие блокировки возвращает доставку.
    unblock_user(actor=actor, bot_user=blocked_user, reason="разобрались, возвращаем")
    httpx_mock.add_response(json={"ok": True}, status_code=200)
    result_after = send_message(chat_id=blocked_user.chat_id, text="напоминание")
    assert result_after == {"ok": True}
