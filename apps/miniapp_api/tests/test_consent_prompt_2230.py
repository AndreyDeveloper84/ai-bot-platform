"""DRF-2230 — «Дать согласие в чате» с Главной реально зовёт в чат.

Скрин владельца: блок «Чтобы вести дневник, нужно согласие — дай его в чате с
Ayla» + кнопка, которая только закрывала Mini App. В чате о согласии — ни
слова, последнее сообщение — напоминание о записи. Владелец: «надо тогда
сообщение от бота информационное о согласии, а то так вообще не понятно что
надо сделать».

Замер: блок Главной рисуется по ``wellness/today.consent_required``, а тот —
только по PERSONAL_DATA (``personal_records_consent_open``). Это ровно та
ветка, где кнопка чата «Дать согласие» (DRF-1968) согласие выдаёт — петли нет.
Ветка реестра дневника (``food-diary-v1``) до этого блока не доходит: её
отказ приходит на запись (403 ``food_diary_consent_required``) и ведёт на
экран согласия в самом Mini App.

Ручка: нажатие → в чат MAX ЭТОГО человека уходит приглашение с кнопкой
«Дать согласие» (тот же вход DRF-1968, происхождение ``miniapp``).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.miniapp_api.tests.test_wellness_today import _init_data_header
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CONSENT = "apps.orchestrator.personal_surface.personal_records_consent_open"
SEND = "apps.channels.max.outbound.send_message"


@pytest.fixture(autouse=True)
def _env(settings):
    from apps.miniapp_api.tests.test_wellness_today import BOT_TOKEN

    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.NUTRITION_ENABLED = True
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "consent-prompt-2230",
        }
    }
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def bot_user(db, settings) -> BotUser:
    tenant = Tenant.objects.create(slug="consent-2230", name="Consent 2230")
    settings.MAX_BOT_TENANT_SLUG = "consent-2230"
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="92230",
        display_name="Анна",
    )


def _tap(client: Client, bot_user: BotUser):
    return client.post(
        reverse("miniapp_api:customer_wellness_consent_prompt"),
        HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
    )


def _callbacks(attachments) -> list[str]:
    out: list[str] = []
    for att in attachments or []:
        for row in att.get("payload", {}).get("buttons", []):
            out.extend(b.get("payload", "") for b in row)
    return out


class TestTheInvitationGoesToThisPersonsChat:
    def test_tap_sends_the_invitation_with_the_consent_button(self, client, bot_user):
        with patch(CONSENT, return_value=False), patch(SEND, return_value={}) as send:
            response = _tap(client, bot_user)

        assert response.status_code == 200, response.content
        assert response.json() == {"sent": True}
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        # Бот пишет первым — адрес по человеку, не по диалогу (DRF-1558).
        assert kwargs["user_id"] == "92230"
        assert "chat_id" not in kwargs or kwargs["chat_id"] is None
        assert "согласие" in kwargs["text"].lower()
        # Тот же вход, что у отказов DRF-1968, — с происхождением miniapp.
        assert "cb:welcome:consent_offer_miniapp" in _callbacks(kwargs.get("attachments"))

    def test_second_tap_within_the_window_sends_no_duplicate(self, client, bot_user):
        with patch(CONSENT, return_value=False), patch(SEND, return_value={}) as send:
            first = _tap(client, bot_user)
            second = _tap(client, bot_user)

        assert first.json() == {"sent": True}
        assert second.status_code == 200
        # Приглашение уже в чате — экран может закрыться, дубль не нужен.
        assert second.json() == {"sent": False, "reason": "recently_sent"}
        assert send.call_count == 1

    def test_send_failure_is_loud_and_a_retry_really_retries(self, client, bot_user):
        from apps.channels.max.outbound import MaxAPIError

        with (
            patch(CONSENT, return_value=False),
            patch(SEND, side_effect=MaxAPIError(502, "boom")) as send,
        ):
            failed = _tap(client, bot_user)
        assert failed.status_code == 502, failed.content
        assert failed.json()["error"] == "consent_prompt_not_sent"
        assert send.call_count == 1

        # Окно дубля не заняла неудачная попытка — повтор отправляет.
        with patch(CONSENT, return_value=False), patch(SEND, return_value={}) as send:
            retried = _tap(client, bot_user)
        assert retried.json() == {"sent": True}
        send.assert_called_once()


class TestFalseInputs:
    def test_consent_already_given_sends_nothing(self, client, bot_user):
        """Ложный вход: согласие есть — приглашать не к чему, блока на Главной нет."""
        with patch(CONSENT, return_value=True), patch(SEND, return_value={}) as send:
            response = _tap(client, bot_user)
        assert response.status_code == 200
        assert response.json() == {"sent": False, "reason": "already_granted"}
        send.assert_not_called()

    def test_nutrition_contour_off_sends_nothing(self, client, bot_user, settings):
        settings.NUTRITION_ENABLED = False
        with patch(CONSENT, return_value=False), patch(SEND, return_value={}) as send:
            response = _tap(client, bot_user)
        assert response.status_code == 404
        assert response.json()["error"] == "nutrition_disabled"
        send.assert_not_called()

    def test_get_is_not_a_tap(self, client, bot_user):
        with patch(SEND, return_value={}) as send:
            response = client.get(
                reverse("miniapp_api:customer_wellness_consent_prompt"),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        assert response.status_code == 405
        send.assert_not_called()


class TestTheButtonLandsInTheExistingConsentFlow:
    def test_miniapp_is_a_known_consent_origin_with_its_own_return_text(self):
        from apps.skills.welcome.skill import (
            CONSENT_RECOVERY_ORIGINS,
            CONSENT_RECOVERY_RETURN_TEXTS,
        )

        assert "miniapp" in CONSENT_RECOVERY_ORIGINS
        back = CONSENT_RECOVERY_RETURN_TEXTS["miniapp"]
        # После согласия человека возвращают туда, откуда он пришёл, — в приложение.
        assert "приложени" in back.lower(), back


class TestSentAsTheBotThatOpenedTheMiniApp:
    """Приглашение уходит от бота, подписавшего initData, а не от токена по умолчанию.

    Mini App проверяет initData по всем ботам реестра и помнит, какой подошёл
    (``VerifiedInitData.bot_slug``). Отправка без ``bot_scope`` шла как
    ``settings.MAX_BOT_TOKEN`` — на стенде это совпало с ботом Mini App, но при
    втором клиентском боте приглашение ушло бы в чужой диалог.
    """

    @pytest.fixture
    def two_bots(self, settings):
        from apps.miniapp_api.tests.test_auth_multi_bot import CLIENT_TOKEN, REGISTRY

        settings.MAX_BOT_REGISTRY = REGISTRY
        settings.MAX_BOT_TOKEN = CLIENT_TOKEN  # «по умолчанию» — другой бот
        return settings

    def _tap_signed_by(self, client, token: str):
        from apps.miniapp_api.tests.test_auth_multi_bot import make_init_data

        return client.post(
            reverse("miniapp_api:customer_wellness_consent_prompt"),
            HTTP_AUTHORIZATION=f"MaxInitData {make_init_data(token, user_id=92230)}",
        )

    def _capture(self, monkeypatch) -> list[str]:
        seen: list[str] = []

        def _fake_send(**_kwargs):
            from apps.channels.max.outbound import _token

            seen.append(_token())
            return {}

        monkeypatch.setattr(SEND, _fake_send)
        return seen

    def test_salon_bot_opened_it_salon_bot_sends(self, client, bot_user, two_bots, monkeypatch):
        from apps.miniapp_api.tests.test_auth_multi_bot import SALON_TOKEN

        seen = self._capture(monkeypatch)
        with patch(CONSENT, return_value=False):
            response = self._tap_signed_by(client, SALON_TOKEN)
        assert response.status_code == 200, response.content
        assert seen == [SALON_TOKEN]

    def test_client_bot_opened_it_client_bot_sends(self, client, bot_user, two_bots, monkeypatch):
        """Положительная пара: подписал клиентский — шлёт клиентский."""
        from apps.miniapp_api.tests.test_auth_multi_bot import CLIENT_TOKEN

        seen = self._capture(monkeypatch)
        with patch(CONSENT, return_value=False):
            response = self._tap_signed_by(client, CLIENT_TOKEN)
        assert response.status_code == 200, response.content
        assert seen == [CLIENT_TOKEN]
