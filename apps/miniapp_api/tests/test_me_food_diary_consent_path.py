"""``/me`` называет, КАКОЙ путь согласия дневника живой — не «дано ли» (DRF-2038).

Поле ``food_diary_consent_canonical`` появляется в ``/me`` только под флагом
``FOOD_DIARY_CANONICAL_CONSENT``. Оно говорит экрану, показывать ли
каноническое раскрытие (Z9), и НИЧЕГО не говорит о праве: право
устанавливает предикат на сервере (``apps.consent.nutrition``), а выдаётся
и читается оно ровно одной ручкой — ``me/food-scanner-consent/`` (половина 1
DRF-1963), которая семантически покрывает дневник и текстом, и фотографией.

Отсутствие поля обязано читаться как старый путь: сборка, не знающая про
канон, и ответ без поля должны означать одно и то же — иначе выключенный
флаг перестал бы быть выключенным.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-food-diary"  # noqa: S105 — test fixture  # pragma: allowlist secret


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
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
    t = Tenant.objects.create(
        slug="food-diary-api", name="Food Diary API", timezone="Europe/Moscow"
    )
    settings.MAX_BOT_TENANT_SLUG = "food-diary-api"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="92451",
        display_name="Анна",
        client_name="Анна К.",
    )


def _me_url() -> str:
    return reverse("miniapp_api:me")


def _rows(bot_user: BotUser):
    """Все строки согласия дневника — журнал целиком, не срез."""
    return ConsentRecord.all_tenants.filter(
        bot_user=bot_user,
        consent_type=ConsentRecord.ConsentType.FOOD_DIARY_PROCESSING.value,
    )


@pytest.mark.django_db
class TestMeSaysWhichPathIsLiveNotWhetherGranted:
    """Условие соседа: поле одно, оно про ПУТЬ, и его отсутствие = старый путь."""

    def test_me_announces_the_canonical_path_while_consent_is_absent(
        self, client: Client, bot_user: BotUser, settings
    ):
        """Поле истинно БЕЗ согласия — значит оно про путь, а не про право.

        Это и есть узел, который ловит второй источник права: если бы
        экран выводил «разрешено» из этого поля, он разрешил бы человеку
        дневник здесь — при пустом журнале согласий.
        """
        settings.FOOD_DIARY_CANONICAL_CONSENT = True
        assert _rows(bot_user).count() == 0

        resp = client.get(_me_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id))
        assert resp.status_code == 200
        body = resp.json()
        # ПРИСУТСТВИЕ на тех же данных: поле пути есть, и оно истинно —
        # при пустом журнале согласий.
        assert "food_diary_consent_canonical" in body
        assert body["food_diary_consent_canonical"] is True
        # ОТСУТСТВИЕ: права отсюда не выводится. Ни «granted», ни момента
        # выдачи канонического согласия `/me` не несёт — иначе экран мог бы
        # сказать «разрешено», не спросив предикат.
        assert "food_diary_granted" not in body

    def test_me_omits_the_field_when_the_flag_is_off(
        self, client: Client, bot_user: BotUser, settings
    ):
        """Выключено — поля нет, и экран ведёт себя как сегодня.

        Отсутствие поля обязано читаться старым путём: сборка, не знающая
        про канон, и ответ без поля должны означать одно и то же.
        """
        settings.FOOD_DIARY_CANONICAL_CONSENT = False

        resp = client.get(_me_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id))
        assert resp.status_code == 200
        body = resp.json()

        # ПРИСУТСТВИЕ впереди: ответ профиля настоящий и не пуст.
        assert body["bot_user_id"] == str(bot_user.id)
        assert "preferences" in body
        # ОТСУТСТВИЕ на тех же данных.
        assert "food_diary_consent_canonical" not in body
