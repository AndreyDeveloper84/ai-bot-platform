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
from apps.consent import customer as customer_consents
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
    """``consent_at`` остаётся после отзыва — решает реестр, и он в ``granted``.

    На пилоте это не гипотетика: четыре из пяти строк с непустым
    ``consent_at`` уже отозвали ``personal_data``. Ручка, отвечающая по
    колонке, сказала бы им «согласие действует».
    """
    BotUser.all_tenants.filter(pk=bot_user.pk).update(consent_at=NOW_UTC)
    bot_user.refresh_from_db()
    granted_first = client.get(url, **auth).json()["data_storage"]
    assert granted_first["granted"] is True  # есть что опровергать
    assert granted_first["consent_at"] is not None

    ConsentRecord.all_tenants.filter(bot_user=bot_user).update(withdrawn_at=NOW_UTC)

    after = client.get(url, **auth).json()["data_storage"]
    assert after["granted"] is False
    # Колонка показана как есть — расхождение видно, а не замазано.
    assert after["consent_at"] is not None


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
    # Процедура по накопленному пишет собственную строку со списком шагов.
    assert AuditLog.all_tenants.filter(action="privacy.personal_data_deleted").exists()


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
    """Подмена субъекта в теле не действует: субъект берётся из initData."""
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
        assert res.status_code != 200
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
    """Отзыв проверяется по поведению: поле без эффекта — тот же обман."""
    _make_due_reminder(tenant, bot_user, "yc-1520-revoke")
    plan_before = followups_mod.plan_post_visit_followups()
    assert [d.send for d in plan_before] == [True]  # человек был получателем

    res = _revoke(client, revoke_url, auth)
    assert res.json()["data_storage"]["granted"] is False

    # Отозвавший из отбора не исчезает — он остаётся в плане с вето и
    # названной причиной, чтобы сухой прогон показывал оператору, ЧТО
    # сработало. Проверяем именно это, а не пустой список.
    plan_after = followups_mod.plan_post_visit_followups()
    assert [(d.send, d.reason) for d in plan_after] == [(False, "consent_withdrawn")]
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
