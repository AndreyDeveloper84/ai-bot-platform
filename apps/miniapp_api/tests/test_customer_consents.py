"""DRF-1520 — /customer/me/consents/ и его подресурсы.

Проверяется не «поле переключилось», а «право реализовано»: читается ли
состояние всех согласий, меняется ли то, что человеку обещано менять,
останавливается ли планировщик после отзыва, и не может ли человек
дотянуться до чужого.

Каждое отрицание здесь стоит за утверждением о наличии на тех же данных
(DRF-1411): «не отправлено» без «до этого отправлялось» — не проверка.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.booking.models import BookingReminder
from apps.bookings import followups as followups_mod
from apps.bookings.followups import send_post_visit_followups
from apps.consent import customer as customer_consents, health as health_consent
from apps.consent.models import ConsentRecord
from apps.consent.services import has_global_consent, record_global_consent
from apps.identity.models import BotUser, UserPreferences
from apps.identity.services.profile import DELETE_CONFIRMATION_TOKEN
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-consents"  # noqa: S105 — test fixture  # pragma: allowlist secret
CHANNEL_USER_ID = "1520100"
OTHER_CHANNEL_USER_ID = "1520200"

MSK = ZoneInfo("Europe/Moscow")
# Заморозка как в наборе follow-up'ов: «вчера по МСК» = 2026-05-15.
NOW_MSK = datetime(2026, 5, 16, 19, 0, tzinfo=MSK)
NOW_UTC = NOW_MSK.astimezone(dt_timezone.utc)


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Анна"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture(autouse=True)
def _no_ayla_link():
    """Резолв личности не ходит в сеть из тестов.

    ``revoke_data_storage`` → ``delete_personal_data`` → ``_resolve_person_link``
    → ``ensure_ayla_link`` → ``resolve_identity`` — реальный HTTP. Без этой
    заглушки исход отзыва зависел бы от того, доступна ли Ayla из окружения:
    там, где доступна, ``ayla_delete`` прошёл бы и статус стал бы ``revoked``.
    Тест, который краснеет от доступности внешнего сервиса, проверяет не код.
    """
    with patch(
        "apps.integrations.ayla.identity_client.resolve_identity",
        side_effect=RuntimeError("ayla недоступна в тестах"),
    ):
        yield


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="consents-api", name="Consents API", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "consents-api"
    return t


def _make_user(tenant: Tenant, channel_user_id: str, *, consented: bool = True) -> BotUser:
    user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
        chat_id=f"chat-{channel_user_id}",
        display_name="Анна",
    )
    if consented:
        record_global_consent(user, source="test:welcome")
    return user


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return _make_user(tenant, CHANNEL_USER_ID)


@pytest.fixture
def other_user(tenant: Tenant) -> BotUser:
    """Другой человек в том же тенанте — сосед, до которого дотянуться нельзя."""
    return _make_user(tenant, OTHER_CHANNEL_USER_ID)


@pytest.fixture
def auth() -> dict:
    return {"HTTP_AUTHORIZATION": _init_data_header(CHANNEL_USER_ID)}


@pytest.fixture
def url() -> str:
    return reverse("miniapp_api:customer_consents")


@pytest.fixture
def hints_url() -> str:
    return reverse("miniapp_api:customer_proactive_hints")


@pytest.fixture
def marketing_url() -> str:
    return reverse("miniapp_api:customer_marketing_consent")


@pytest.fixture
def revoke_url() -> str:
    return reverse("miniapp_api:customer_data_storage_consent")


class _NoOpCascade:
    """Каскад, отрапортовавший успех, не сняв ни одного согласия."""

    steps: tuple = ()
    all_ok = True
    failed_steps: list = []  # noqa: RUF012 — тестовая заглушка, не модель


def _revoke_body(**overrides) -> str:
    body = {
        "confirmation": DELETE_CONFIRMATION_TOKEN,
        "disclosure_version": customer_consents.DATA_STORAGE_REVOCATION_DISCLOSURE_VERSION,
    }
    body.update(overrides)
    return json.dumps(body)


def _revoke(client: Client, revoke_url: str, auth: dict, **overrides):
    return client.delete(
        revoke_url,
        data=_revoke_body(**overrides),
        content_type="application/json",
        **auth,
    )


# ── Чтение ──────────────────────────────────────────────────────────────────


def test_read_returns_every_consent_type(client: Client, bot_user, url, auth) -> None:
    """Все типы, а не выборка: список строится обходом ConsentType."""
    res = client.get(url, **auth)

    assert res.status_code == 200
    body = res.json()
    assert set(body["consents"]) == {c.value for c in ConsentRecord.ConsentType}
    # Приветственное согласие 152-ФЗ действует, остальные — нет.
    assert body["consents"]["personal_data"]["granted"] is True
    assert body["consents"]["marketing"]["granted"] is False
    assert body["consents"]["health"]["granted"] is False
    assert body["proactive_hints"]["enabled"] is True


def test_read_exposes_the_revocation_disclosure(client: Client, bot_user, url, auth) -> None:
    """Последствия отзыва отдаются ДО действия, вместе с версией текста."""
    revocation = client.get(url, **auth).json()["data_storage"]["revocation"]

    assert (
        revocation["disclosure_version"]
        == customer_consents.DATA_STORAGE_REVOCATION_DISCLOSURE_VERSION
    )
    assert revocation["consequences"] == list(
        customer_consents.DATA_STORAGE_REVOCATION_CONSEQUENCES
    )
    # То, что отзыв НЕ трогает, названо там же — иначе человек узнает потом.
    assert revocation["retained"] == list(customer_consents.DATA_STORAGE_REVOCATION_RETAINED)


def test_read_never_grants_anything(client: Client, bot_user, url, auth) -> None:
    """Чтение — это чтение. Ни одна строка согласия от GET не появляется."""
    before = ConsentRecord.all_tenants.filter(bot_user=bot_user).count()
    assert before == 1  # приветственное personal_data — есть что не менять

    client.get(url, **auth)
    client.get(url, **auth)

    assert ConsentRecord.all_tenants.filter(bot_user=bot_user).count() == before


def test_granted_follows_the_registry_not_the_denormalised_stamp(
    client: Client, bot_user, url, auth
) -> None:
    """Отзыв не снимает ``BotUser.consent_at`` — и ручка на него не смотрит.

    На пилоте это не гипотетика: четыре из пяти строк с непустым
    ``consent_at`` уже отозвали ``personal_data``. Ручка, отвечающая по
    колонке, сказала бы им «согласие действует», а датой отзыва не
    располагает вовсе. Наружу идёт только реестр.
    """
    BotUser.all_tenants.filter(pk=bot_user.pk).update(consent_at=NOW_UTC)
    bot_user.refresh_from_db()
    granted_first = client.get(url, **auth).json()["data_storage"]
    assert granted_first["granted"] is True  # есть что опровергать
    assert granted_first["granted_at"] is not None

    ConsentRecord.all_tenants.filter(bot_user=bot_user).update(withdrawn_at=NOW_UTC)

    after = client.get(url, **auth).json()["data_storage"]
    assert after["granted"] is False
    assert after["granted_at"] is None
    # Колонка осталась заполненной в базе — и именно поэтому её здесь нет.
    assert BotUser.all_tenants.get(pk=bot_user.pk).consent_at is not None
    assert "granted_at" in after  # тело не пустое: есть чему не оказаться рядом
    assert "consent_at" not in after


def test_consent_given_on_the_chat_shell_is_visible_in_the_app(
    client: Client, bot_user, url, auth, db
) -> None:
    """Согласие, данное в чате, видно в мини-приложении.

    Приветственный поток пишет ``personal_data`` ПОСТРОЧНО на ту оболочку,
    которая вела разговор, — а разговор ведёт чат под сентинелом
    ``global_bot``, тогда как мини-приложение резолвит свою строку под
    ``MAX_BOT_TENANT_SLUG``. Построчное чтение показало бы «согласия нет»
    ровно тем, у кого оно есть, и экран спрятал бы от них кнопку отзыва:
    недостижимость отзыва осталась бы на месте для основного сценария.
    """
    # Убираем согласие с miniapp-строки и оставляем только на chat-строке —
    # ровно та форма данных, которая есть на пилоте.
    ConsentRecord.all_tenants.filter(bot_user=bot_user).delete()
    sentinel = Tenant.objects.create(slug="consents-global-shell", name="Global")
    chat_shell = BotUser.all_tenants.create(
        tenant=sentinel,
        channel="max",
        channel_user_id=CHANNEL_USER_ID,
        chat_id=f"chat-{CHANNEL_USER_ID}",
    )
    assert client.get(url, **auth).json()["data_storage"]["granted"] is False  # есть чему меняться

    record_global_consent(chat_shell, source="test:welcome_in_chat")

    body = client.get(url, **auth).json()
    assert body["data_storage"]["granted"] is True
    assert body["consents"]["personal_data"]["granted"] is True


def test_read_is_read_only(client: Client, bot_user, url, auth) -> None:
    """Ручка чтения не принимает записи ни в какой форме."""
    assert client.get(url, **auth).status_code == 200  # читать — можно

    for res in (client.post(url, **auth), client.delete(url, **auth)):
        assert res.status_code == 405


def test_health_consent_shows_up_in_the_same_document(client: Client, bot_user, url, auth) -> None:
    """Одно состояние на платформу, а не отдельная правда у каждой ручки."""
    assert client.get(url, **auth).json()["consents"]["health"]["granted"] is False

    client.post(
        reverse("miniapp_api:health_consent"),
        data=json.dumps({"document_version": health_consent.HEALTH_CONSENT_DOCUMENT_VERSION}),
        content_type="application/json",
        **auth,
    )

    health = client.get(url, **auth).json()["consents"]["health"]
    assert health["granted"] is True
    # Версия раскрытия, под которой согласие стоит, — та же, что записала
    # ручка медданных. Содержимое медданных при этом не отдаётся.
    assert health["document_version"] == health_consent.HEALTH_CONSENT_DOCUMENT_VERSION


def test_malformed_body_is_refused_without_touching_state(
    client: Client, bot_user, hints_url, auth
) -> None:
    ok = client.post(
        hints_url, data=json.dumps({"enabled": False}), content_type="application/json", **auth
    )
    assert ok.status_code == 200  # форма, которая принимается

    for payload in ("не json", json.dumps([1, 2])):
        res = client.post(hints_url, data=payload, content_type="application/json", **auth)
        assert res.status_code == 400
        assert res.json()["error"] == "malformed"
    assert BotUser.all_tenants.get(pk=bot_user.pk).proactive_messages_opt_out is True


# ── Подсказки Ayla ──────────────────────────────────────────────────────────


def test_proactive_hints_can_be_turned_off_and_back_on(
    client: Client, bot_user, hints_url, auth
) -> None:
    off = client.post(
        hints_url, data=json.dumps({"enabled": False}), content_type="application/json", **auth
    )

    assert off.status_code == 200
    assert off.json()["proactive_hints"]["enabled"] is False
    assert BotUser.all_tenants.get(pk=bot_user.pk).proactive_messages_opt_out is True

    on = client.post(
        hints_url, data=json.dumps({"enabled": True}), content_type="application/json", **auth
    )

    assert on.json()["proactive_hints"]["enabled"] is True
    assert BotUser.all_tenants.get(pk=bot_user.pk).proactive_messages_opt_out is False


def test_proactive_hints_reach_every_shell_of_the_person(
    client: Client, bot_user, hints_url, auth, db
) -> None:
    """Чат и мини-приложение — разные строки; опт-аут обязан накрыть обе."""
    sentinel = Tenant.objects.create(slug="consents-global", name="Global")
    chat_shell = BotUser.all_tenants.create(
        tenant=sentinel,
        channel="max",
        channel_user_id=CHANNEL_USER_ID,
        chat_id=f"chat-{CHANNEL_USER_ID}",
    )
    assert chat_shell.proactive_messages_opt_out is False  # есть чему меняться

    client.post(
        hints_url, data=json.dumps({"enabled": False}), content_type="application/json", **auth
    )

    assert BotUser.all_tenants.get(pk=bot_user.pk).proactive_messages_opt_out is True
    assert BotUser.all_tenants.get(pk=chat_shell.pk).proactive_messages_opt_out is True


def test_proactive_hints_require_a_boolean(client: Client, bot_user, hints_url, auth) -> None:
    ok = client.post(
        hints_url, data=json.dumps({"enabled": False}), content_type="application/json", **auth
    )
    assert ok.status_code == 200  # форма, которая принимается

    res = client.post(
        hints_url, data=json.dumps({"enabled": "нет"}), content_type="application/json", **auth
    )

    assert res.status_code == 400
    assert res.json()["error"] == "bad_request"
    # Состояние не сдвинулось от неудачного запроса.
    assert BotUser.all_tenants.get(pk=bot_user.pk).proactive_messages_opt_out is True


def test_proactive_hints_change_is_audited(client: Client, bot_user, hints_url, auth) -> None:
    client.post(
        hints_url, data=json.dumps({"enabled": False}), content_type="application/json", **auth
    )

    rows = AuditLog.all_tenants.filter(action="consent.proactive_hints_changed")
    assert rows.count() == 1
    row = rows.first()
    assert row is not None
    assert row.payload["enabled"] is False


# ── Маркетинговое согласие: один источник правды ────────────────────────────


def test_marketing_grant_and_withdraw_roundtrip(
    client: Client, bot_user, marketing_url, auth
) -> None:
    granted = client.post(marketing_url, **auth)

    assert granted.status_code == 200
    assert granted.json()["consents"]["marketing"]["granted"] is True
    assert granted.json()["consents"]["marketing"]["granted_at"] is not None

    withdrawn = client.delete(marketing_url, **auth)

    assert withdrawn.json()["consents"]["marketing"]["granted"] is False
    assert has_global_consent(bot_user, ConsentRecord.ConsentType.MARKETING.value) is False


def test_marketing_single_source_of_truth(client: Client, bot_user, marketing_url, auth) -> None:
    """Реестр и зеркало ``notify_promo`` не расходятся ни на одном переходе.

    Оба пути записи — ручка согласий и ``PATCH /me`` — ведут в один и тот
    же :func:`apps.consent.customer.set_marketing`, поэтому расходиться
    нечему; тест держит это утверждение на всех четырёх переходах.
    """

    def _both() -> tuple[bool, bool]:
        registry = has_global_consent(bot_user, ConsentRecord.ConsentType.MARKETING.value)
        mirror = UserPreferences.all_tenants.get(bot_user=bot_user).notify_promo
        return registry, mirror

    me_url = reverse("miniapp_api:me")

    client.post(marketing_url, **auth)
    assert _both() == (True, True)

    client.patch(
        me_url,
        data=json.dumps({"notify_promo": False}),
        content_type="application/json",
        **auth,
    )
    assert _both() == (False, False)

    client.patch(
        me_url,
        data=json.dumps({"notify_promo": True}),
        content_type="application/json",
        **auth,
    )
    assert _both() == (True, True)

    client.delete(marketing_url, **auth)
    assert _both() == (False, False)


def test_patch_me_still_reports_the_actual_marketing_state(client: Client, bot_user, auth) -> None:
    """Совместимость: старый контракт ``PATCH /me`` цел и отвечает фактом."""
    me_url = reverse("miniapp_api:me")

    res = client.patch(
        me_url,
        data=json.dumps({"notify_promo": True}),
        content_type="application/json",
        **auth,
    )

    assert res.status_code == 200
    assert res.json()["preferences"]["notify_promo"] is True
    assert has_global_consent(bot_user, ConsentRecord.ConsentType.MARKETING.value) is True


def test_marketing_reaches_every_shell_of_the_person(
    client: Client, bot_user, marketing_url, auth, db
) -> None:
    """Согласие пишется по всем оболочкам, иначе читающая сторона его не видит."""
    sentinel = Tenant.objects.create(slug="consents-global-mkt", name="Global")
    chat_shell = BotUser.all_tenants.create(
        tenant=sentinel,
        channel="max",
        channel_user_id=CHANNEL_USER_ID,
        chat_id=f"chat-{CHANNEL_USER_ID}",
    )
    assert has_global_consent(chat_shell, "marketing") is False  # есть чему появиться

    client.post(marketing_url, **auth)

    assert has_global_consent(bot_user, "marketing") is True
    assert has_global_consent(chat_shell, "marketing") is True
    assert UserPreferences.all_tenants.get(bot_user=chat_shell).notify_promo is True

    client.delete(marketing_url, **auth)

    assert has_global_consent(chat_shell, "marketing") is False
    assert UserPreferences.all_tenants.get(bot_user=chat_shell).notify_promo is False


def test_marketing_records_the_document_version(
    client: Client, bot_user, marketing_url, auth
) -> None:
    """Реестр главнее колонки ровно тем, что хранит — в том числе редакцию текста."""
    client.post(marketing_url, **auth)

    row = ConsentRecord.all_tenants.get(
        bot_user=bot_user, consent_type=ConsentRecord.ConsentType.MARKETING
    )
    assert row.document_version == customer_consents.MARKETING_CONSENT_DOCUMENT_VERSION


def test_erasure_withdraws_marketing_too(client: Client, bot_user, marketing_url, auth) -> None:
    """Соседняя ручка стирания не должна оставлять действующее согласие.

    ``DELETE /me/personal-data/`` удаляет строку ``UserPreferences``
    целиком, а ``get_profile`` пересоздаёт её с ``notify_promo=False``.
    Если бы каскад §8.4 не снимал ``marketing``, человек, реализовавший
    право на стирание, остался бы с действующим маркетинговым согласием в
    реестре и выключенным зеркалом — расхождение двух источников правды,
    только через другую дверь.
    """
    client.post(marketing_url, **auth)
    assert has_global_consent(bot_user, "marketing") is True  # есть что снимать

    res = client.delete(
        reverse("miniapp_api:personal_data_delete"),
        data=json.dumps({"confirmation": DELETE_CONFIRMATION_TOKEN}),
        content_type="application/json",
        **auth,
    )

    assert res.status_code in (200, 502)  # шаг Ayla на пилоте не адресуем
    assert has_global_consent(bot_user, "marketing") is False
    assert not UserPreferences.all_tenants.filter(bot_user=bot_user, notify_promo=True).exists()


def test_marketing_grant_is_idempotent(client: Client, bot_user, marketing_url, auth) -> None:
    client.post(marketing_url, **auth)
    client.post(marketing_url, **auth)

    active = ConsentRecord.all_tenants.filter(
        bot_user=bot_user,
        consent_type=ConsentRecord.ConsentType.MARKETING,
        granted=True,
        withdrawn_at__isnull=True,
    )
    assert active.count() == 1


def test_marketing_withdraw_is_idempotent_and_keeps_the_trail(
    client: Client, bot_user, marketing_url, auth
) -> None:
    client.post(marketing_url, **auth)
    client.delete(marketing_url, **auth)

    res = client.delete(marketing_url, **auth)

    assert res.status_code == 200
    assert res.json()["consents"]["marketing"]["granted"] is False
    row = ConsentRecord.all_tenants.get(
        bot_user=bot_user, consent_type=ConsentRecord.ConsentType.MARKETING
    )
    assert row.withdrawn_at is not None  # строка не удалена, а датирована


def test_marketing_transitions_are_audited(
    client: Client, bot_user, marketing_url, auth, django_capture_on_commit_callbacks
) -> None:
    """И выдача, и отзыв оставляют след. Без него доказывать нечем.

    Реестр откладывает событие и audit на ``on_commit`` — чтобы подписчик
    никогда не увидел согласие раньше строки в базе. В тесте транзакция не
    коммитится, поэтому колбэки исполняются явно; иначе проверка «след
    есть» проходила бы мимо самой записи.
    """
    with django_capture_on_commit_callbacks(execute=True):
        client.post(marketing_url, **auth)
    with django_capture_on_commit_callbacks(execute=True):
        client.delete(marketing_url, **auth)

    granted = AuditLog.all_tenants.filter(action="consent.granted")
    withdrawn = AuditLog.all_tenants.filter(action="consent.withdrawn")
    assert granted.filter(payload__consent_type="marketing").exists()
    assert withdrawn.filter(payload__consent_type="marketing").exists()


# ── Отзыв согласия на хранение данных ───────────────────────────────────────


def test_revocation_requires_the_confirmation_token(
    client: Client, bot_user, url, revoke_url, auth
) -> None:
    assert client.get(url, **auth).json()["data_storage"]["granted"] is True  # есть что отзывать

    res = _revoke(client, revoke_url, auth, confirmation="")

    assert res.status_code == 400
    assert res.json()["error"] == "confirmation_mismatch"
    assert has_global_consent(bot_user, ConsentRecord.ConsentType.PERSONAL_DATA.value) is True


def test_revocation_requires_the_current_disclosure_version(
    client: Client, bot_user, url, revoke_url, auth
) -> None:
    """Отзыв под текстом последствий, которого сервер не знает, не проходит."""
    assert client.get(url, **auth).json()["data_storage"]["granted"] is True

    res = _revoke(client, revoke_url, auth, disclosure_version="data-storage-revocation-v0")

    assert res.status_code == 409
    assert res.json()["error"] == "stale_disclosure"
    assert has_global_consent(bot_user, ConsentRecord.ConsentType.PERSONAL_DATA.value) is True


def test_revocation_withdraws_and_returns_the_actual_state(
    client: Client, bot_user, url, revoke_url, auth
) -> None:
    """Ответ — пересчитанное состояние, а не «принято»."""
    assert client.get(url, **auth).json()["data_storage"]["granted"] is True

    res = _revoke(client, revoke_url, auth)

    assert res.status_code == 200
    body = res.json()
    assert body["data_storage"]["granted"] is False
    assert has_global_consent(bot_user, ConsentRecord.ConsentType.PERSONAL_DATA.value) is False
    # Ответ честно называет, что процедура по накопленному отработала не вся:
    # ayla_user_id не проставлен, значит удаление наверху не подтверждено.
    assert body["revocation"]["status"] == "revoked_partial_processing"
    assert "ayla_delete" in body["revocation"]["failed_steps"]
    assert body["revocation"]["failed_details"]["ayla_delete"] == "not_linked"


def test_revocation_cascades_to_health_and_memory(
    client: Client, bot_user, revoke_url, auth
) -> None:
    """Отзыв базы снимает надстроенное над ней, а не только её саму."""
    client.post(reverse("miniapp_api:customer_marketing_consent"), **auth)
    for consent_type in ("health", "memory_green"):
        record_global_consent(bot_user, consent_type=consent_type, source="test:seed")
    for consent_type in ("health", "memory_green", "marketing"):
        assert has_global_consent(bot_user, consent_type) is True  # есть что снимать
    assert UserPreferences.all_tenants.get(bot_user=bot_user).notify_promo is True

    _revoke(client, revoke_url, auth)

    for consent_type in ("personal_data", "health", "memory_green", "marketing"):
        assert has_global_consent(bot_user, consent_type) is False
    # Зеркало не может остаться включённым: каскад стирает саму строку
    # настроек, а до неё :func:`set_marketing` свёл её к реестру.
    assert not UserPreferences.all_tenants.filter(bot_user=bot_user, notify_promo=True).exists()


def test_revocation_is_idempotent(client: Client, bot_user, revoke_url, auth) -> None:
    first = _revoke(client, revoke_url, auth)
    assert first.status_code == 200
    assert first.json()["data_storage"]["granted"] is False

    second = _revoke(client, revoke_url, auth)

    assert second.status_code == 200
    assert second.json()["data_storage"]["granted"] is False
    # Второй отзыв ничего не «переотзывает»: активных грантов уже нет.
    assert (
        ConsentRecord.all_tenants.filter(
            bot_user=bot_user, granted=True, withdrawn_at__isnull=True
        ).count()
        == 0
    )


def test_revocation_switches_the_hints_off_on_every_shell(
    client: Client, bot_user, url, revoke_url, auth, db
) -> None:
    """§35 п.9: после отзыва «Подсказки Ayla» показывают выключено.

    Тумблер — орган управления, и включённым он врал бы: проактивные
    сообщения после отзыва всё равно не уйдут, их останавливает
    ``consent_blocker`` по ``consent_withdrawn``. Человек видел бы
    включённый орган при выключенном по другой причине эффекте.

    Гасится по ВСЕМ оболочкам человека — чат и мини-приложение разные
    строки, и опт-аут, поставленный на одной, не остановил бы
    планировщик, читающий другую.
    """
    sentinel = Tenant.objects.create(slug="consents-global-revoke", name="Global")
    chat_shell = BotUser.all_tenants.create(
        tenant=sentinel,
        channel="max",
        channel_user_id=CHANNEL_USER_ID,
        chat_id=f"chat-{CHANNEL_USER_ID}",
    )
    # Есть чему меняться: до отзыва тумблер включён на обеих оболочках.
    assert client.get(url, **auth).json()["proactive_hints"]["enabled"] is True
    assert BotUser.all_tenants.get(pk=bot_user.pk).proactive_messages_opt_out is False
    assert chat_shell.proactive_messages_opt_out is False

    res = _revoke(client, revoke_url, auth)

    # Ответ ручки собирается из того же экземпляра — он обязан совпасть
    # со строкой, а не показать значение, которого в базе уже нет.
    assert res.json()["proactive_hints"]["enabled"] is False
    assert BotUser.all_tenants.get(pk=bot_user.pk).proactive_messages_opt_out is True
    assert BotUser.all_tenants.get(pk=chat_shell.pk).proactive_messages_opt_out is True
    # Следующее чтение отдаёт то же: значение легло в базу, а не в память.
    assert client.get(url, **auth).json()["proactive_hints"]["enabled"] is False


def test_repeated_revocation_leaves_the_hints_off(
    client: Client, bot_user, revoke_url, auth
) -> None:
    """Идемпотентность распространяется и на тумблер: то же состояние, не откат."""
    first = _revoke(client, revoke_url, auth)
    assert first.json()["proactive_hints"]["enabled"] is False

    second = _revoke(client, revoke_url, auth)

    assert second.status_code == 200
    assert second.json()["proactive_hints"]["enabled"] is False
    assert BotUser.all_tenants.get(pk=bot_user.pk).proactive_messages_opt_out is True


def test_consent_is_withdrawn_even_when_the_processing_step_fails(
    client: Client, bot_user, url, revoke_url, auth
) -> None:
    """Отзыв стоит ПЕРЕД процедурой, а не внутри неё.

    В каскаде снятие согласий — третий шаг из шести, и его исключение
    каскад ловит по-шаговой изоляцией: он отрапортует ``consent_withdraw:
    False`` и пойдёт дальше. Если бы отзыв жил только там, человек после
    неудачи остался бы с действующим согласием — то есть кнопка «отозвать»
    не отозвала бы ничего. Здесь шаг каскада ломается намеренно, и
    согласие всё равно снято.
    """
    assert client.get(url, **auth).json()["data_storage"]["granted"] is True

    with patch(
        "apps.identity.services.privacy.withdraw_personal_data_for_bot_users",
        side_effect=RuntimeError("нет связи"),
    ):
        res = _revoke(client, revoke_url, auth)

    assert res.status_code == 200
    assert res.json()["data_storage"]["granted"] is False
    assert "consent_withdraw" in res.json()["revocation"]["failed_steps"]
    assert has_global_consent(bot_user, "personal_data") is False


def test_revocation_does_not_lock_the_person_out(
    client: Client, bot_user, url, revoke_url, auth
) -> None:
    """Отзыв — не удаление аккаунта: согласие можно дать заново."""
    _revoke(client, revoke_url, auth)

    assert BotUser.all_tenants.get(pk=bot_user.pk).deleted_at is None
    assert client.get(url, **auth).status_code == 200


def test_revocation_is_audited(client: Client, bot_user, revoke_url, auth) -> None:
    _revoke(client, revoke_url, auth)

    revoked = AuditLog.all_tenants.filter(action="consent.data_storage_revoked")
    assert revoked.count() == 1
    row = revoked.first()
    assert row is not None
    assert row.payload["actor"] == "customer"
    # Гашение тумблера подсказок — часть отзыва, и след у него тот же.
    assert row.payload["proactive_hints_disabled"] is True
    # Процедура по накопленному пишет собственную строку со списком шагов.
    assert AuditLog.all_tenants.filter(action="privacy.personal_data_deleted").exists()


def test_failed_revocation_answers_with_a_refusal(
    client: Client, bot_user, url, revoke_url, auth
) -> None:
    """Если согласие осталось действующим — статус обязан быть отказом.

    Единственная ветка, где ручка отвечает 502: состояние после вызова
    по-прежнему «согласие действует». Без неё частично отработавший отзыв
    мог бы вернуть 200 и человек считал бы вопрос закрытым.
    """
    assert client.get(url, **auth).json()["data_storage"]["granted"] is True

    # Процедура «отработала» и отрапортовала успех, не сняв ничего — ровно
    # тот исход, ради которого сторож и стоит.
    with patch("apps.consent.customer.revoke_data_storage", return_value=_NoOpCascade()):
        res = _revoke(client, revoke_url, auth)

    assert res.status_code == 502
    assert res.json()["revocation"]["status"] == "failed"
    assert has_global_consent(bot_user, "personal_data") is True


def test_no_phone_and_no_health_content_leaves_the_endpoint(
    client: Client, bot_user, url, auth
) -> None:
    """Ручка оперирует фактом согласия, а не данными за ним."""
    BotUser.all_tenants.filter(pk=bot_user.pk).update(phone="79991234567")
    client.post(
        reverse("miniapp_api:health_consent"),
        data=json.dumps({"document_version": health_consent.HEALTH_CONSENT_DOCUMENT_VERSION}),
        content_type="application/json",
        **auth,
    )

    raw = client.get(url, **auth).content.decode()

    # Тело не пустое и действительно про согласия — есть чему не утечь рядом.
    assert "health" in raw
    assert json.loads(raw)["consents"]["health"]["granted"] is True
    assert "79991234567" not in raw
    assert "phone" not in raw


def test_consequences_match_the_actual_cascade(bot_user) -> None:
    """Обещание на экране и код не могут разойтись.

    Список последствий отдаётся человеку ДО нажатия. Если каскад заведёт
    новый шаг, а список останется прежним, человек согласится не на то,
    что произойдёт — этот тест краснеет раньше, чем это случится.
    """
    result = customer_consents.revoke_data_storage(bot_user)

    assert [s.step for s in result.steps] == list(
        customer_consents.DATA_STORAGE_REVOCATION_CONSEQUENCES
    )


# ── Чужое недостижимо ───────────────────────────────────────────────────────


def test_another_persons_consents_are_untouched(
    client: Client, bot_user, other_user, revoke_url, auth
) -> None:
    """Человек управляет своими согласиями и только своими."""
    assert has_global_consent(other_user, "personal_data") is True  # есть что уцелеть
    assert has_global_consent(bot_user, "personal_data") is True

    _revoke(client, revoke_url, auth)

    assert has_global_consent(bot_user, "personal_data") is False
    assert has_global_consent(other_user, "personal_data") is True


def test_body_cannot_name_another_subject(
    client: Client, bot_user, other_user, marketing_url, auth
) -> None:
    """Подмена субъекта в теле не действует: субъект берётся из initData.

    Проверяется отсутствие кода: ручка тело вообще не разбирает. Тест
    поэтому не может покраснеть от содержимого — он краснеет ровно тогда,
    когда кто-то заведёт разбор субъекта из тела, и ради этого стоит."""
    res = client.post(
        marketing_url,
        data=json.dumps({"bot_user_id": str(other_user.id), "granted": True}),
        content_type="application/json",
        **auth,
    )

    assert res.status_code == 200
    # Сработало на аутентифицированном — есть с чем сравнивать отсутствие.
    assert has_global_consent(bot_user, "marketing") is True
    assert has_global_consent(other_user, "marketing") is False


def test_unauthenticated_requests_are_rejected(
    client: Client, bot_user, url, hints_url, marketing_url, revoke_url, auth
) -> None:
    """Без initData ничьё состояние не отдаётся и ничьё не меняется."""
    assert "consents" in client.get(url, **auth).json()  # с аутентификацией — отдаётся

    read = client.get(url)
    hints = client.post(
        hints_url, data=json.dumps({"enabled": False}), content_type="application/json"
    )
    marketing = client.post(marketing_url)
    revoke = client.delete(revoke_url, data=_revoke_body(), content_type="application/json")

    for res in (read, hints, marketing, revoke):
        # 400 — платформенный слог отказа require_init_data (не 401).
        assert res.status_code == 400
    assert "consents" not in read.json()
    assert BotUser.all_tenants.get(pk=bot_user.pk).proactive_messages_opt_out is False
    assert has_global_consent(bot_user, "marketing") is False
    assert has_global_consent(bot_user, "personal_data") is True


# ── Поведение планировщика, а не значение колонки ───────────────────────────


def _make_due_reminder(tenant: Tenant, bot_user: BotUser, yc_id: str) -> BookingReminder:
    """Напоминание о вчерашнем визите — ровно то, что берёт follow-up beat."""
    visit_at = datetime(2026, 5, 15, 14, 0, tzinfo=MSK).astimezone(dt_timezone.utc)
    return BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        yclients_record_id=yc_id,
        chat_id=bot_user.chat_id,
        visit_at=visit_at,
        kind=BookingReminder.Kind.DAY_BEFORE,
        status=BookingReminder.Status.SENT,
        scheduled_at=visit_at - timedelta(hours=24),
        master_name="Лера",
        service_name="Массаж",
    )


@pytest.fixture
def _followup_beat(settings):
    """Открыть рубильники follow-up'ов и заморозить время беата."""
    settings.POST_VISIT_FOLLOWUP_ENABLED = True
    settings.POST_VISIT_FOLLOWUP_DRY_RUN = False
    with patch("apps.bookings.followups.timezone.now", return_value=NOW_UTC):
        yield


def test_person_with_live_consent_still_receives(tenant, bot_user, _followup_beat) -> None:
    """Парная положительная проверка: чинить так, чтобы не получал никто, — не починка."""
    _make_due_reminder(tenant, bot_user, "yc-1520-ok")

    assert [d.send for d in followups_mod.plan_post_visit_followups()] == [True]
    with patch("apps.bookings.followups.send_message") as mock_send:
        result = send_post_visit_followups()

    assert result["sent"] == 1
    assert mock_send.call_count == 1


def test_revocation_stops_the_scheduler(
    client: Client, tenant, bot_user, revoke_url, auth, _followup_beat
) -> None:
    """Отзыв проверяется по поведению: поле без эффекта — тот же обман.

    До §35 п.9 отозвавший оставался в плане с вето ``consent_withdrawn``:
    колонка ``proactive_messages_opt_out`` отзывом не трогалась, и он
    доходил до по-строчной проверки согласия. Теперь отзыв гасит и
    тумблер, а опт-аут — вето уровня отбора: человек не становится
    кандидатом вовсе, поэтому план пуст. Вето по согласию никуда не
    делось и осталось вторым рубежом — просто до него больше не доходит.
    """
    _make_due_reminder(tenant, bot_user, "yc-1520-revoke")
    plan_before = followups_mod.plan_post_visit_followups()
    assert [d.send for d in plan_before] == [True]  # человек был получателем

    res = _revoke(client, revoke_url, auth)
    assert res.json()["data_storage"]["granted"] is False

    plan_after = followups_mod.plan_post_visit_followups()
    assert plan_after == []
    with patch("apps.bookings.followups.send_message") as mock_send:
        result = send_post_visit_followups()
    mock_send.assert_not_called()
    assert result["sent"] == 0


def test_hints_off_stops_the_scheduler(
    client: Client, tenant, bot_user, hints_url, auth, _followup_beat
) -> None:
    """Тумблер подсказок влияет на поведение, а не только на экран."""
    _make_due_reminder(tenant, bot_user, "yc-1520-hints")
    assert [d.send for d in followups_mod.plan_post_visit_followups()] == [True]

    client.post(
        hints_url, data=json.dumps({"enabled": False}), content_type="application/json", **auth
    )

    # Опт-аут — вето уровня отбора: человек не становится кандидатом вовсе,
    # поэтому здесь список пуст, а не «кандидат с send=False».
    assert followups_mod.plan_post_visit_followups() == []
    with patch("apps.bookings.followups.send_message") as mock_send:
        result = send_post_visit_followups()
    mock_send.assert_not_called()
    assert result["sent"] == 0
