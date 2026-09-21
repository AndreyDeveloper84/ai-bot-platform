"""Чтение карточки C04 экраном: `GET customer/recommendation/<id>` (DRF-1769, К-3 N3).

Экран показывает ЗАПИСЬ, а не пересчёт: ровно то, что человек увидел в
чате. Поэтому здесь нет ни сборки WHY, ни таблицы направлений — только
выдача того, что уже показано, и отказ во всём остальном.

Что заперто:

1. своя запись — 200 и ровно те поля, что есть в карточке; **ключей
   услуги, цены, слота, рейтинга, мастера нет по построению** (R11
   выполняется механически: в записи их попросту нет);
2. чужая запись — 404, тем же телом, что и несуществующая: «не твоя» и
   «нет такой» снаружи неразличимы, иначе id становится оракулом;
3. `kind=absence` — 200 с этим видом: экран нарисует C04.4 тем же
   текстом, что DM, а не пустоту;
4. **стёртая запись — 404**: каскад C5 обнуляет слова (`what`/`why`/
   `alternatives`), оставляя tombstone для attribution (B13). Показать
   пустую карточку значило бы соврать, что она есть;
5. другие подходы и причины выдаются как есть — из них экран рисует
   кадры C04.2 и C04.3, не пересобирая ничего.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.recommendation.erasure import anonymize_recommendations
from apps.recommendation.models import Recommendation
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-reco-1769"  # noqa: S105 — test fixture  # pragma: allowlist secret

_WHY = ["Ты написала: «хочу выглядеть свежее»", "Ты выбрала: лицо и кожа"]
_ALTERNATIVES = [
    {"what": "Вернуть лёгкость", "subline": "Про тело."},
    {"what": "Общий уход", "subline": ""},
]


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Ольга"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def bot_user(db, settings) -> BotUser:
    tenant = Tenant.objects.create(slug="reco-1769", name="Reco 1769", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "reco-1769"
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="97769", display_name="Ольга"
    )


@pytest.fixture
def card(bot_user: BotUser) -> Recommendation:
    return Recommendation.objects.create(
        bot_user=bot_user,
        goal_id="goal-1",
        kind=Recommendation.Kind.DIRECTION,
        what="Уменьшить утреннюю отёчность",
        subline="Сфокусируемся на этом.",
        why=_WHY,
        facts={"area": {"value": "Лицо и кожа", "origin": "choice"}},
        alternatives=_ALTERNATIVES,
        fingerprint="fp-1769",
    )


def _get(client: Client, bot_user: BotUser, recommendation_id) -> object:
    return client.get(
        reverse("miniapp_api:customer_recommendation", args=[recommendation_id]),
        HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
    )


@pytest.mark.django_db
class TestOwnCard:
    def test_the_screen_gets_what_the_person_was_shown(self, client, bot_user, card):
        resp = _get(client, bot_user, card.id)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["kind"] == "direction"
        assert data["what"] == "Уменьшить утреннюю отёчность"
        assert data["subline"] == "Сфокусируемся на этом."
        assert data["why"] == _WHY
        assert data["alternatives"] == _ALTERNATIVES

    def test_nothing_bookable_can_leak_because_the_record_has_none(self, client, bot_user, card):
        """R11 механически: ключей услуги/цены/слота/мастера в ответе нет."""
        resp = _get(client, bot_user, card.id)
        data = resp.json()["data"]
        assert set(data) == {"id", "kind", "what", "subline", "why", "alternatives"}
        raw = json.dumps(data, ensure_ascii=False).lower()
        for forbidden in ("service", "master", "specialist", "price", "slot", "rating"):
            assert forbidden not in raw


@pytest.mark.django_db
class TestWhatIsNotShown:
    def test_someone_elses_card_is_the_same_404_as_a_missing_one(
        self, client, bot_user, card, settings
    ):
        other = BotUser.all_tenants.create(
            tenant=bot_user.tenant, channel="max", channel_user_id="97770", display_name="Инна"
        )
        theirs = _get(client, other, card.id)
        missing = _get(client, other, uuid.uuid4())
        assert theirs.status_code == 404
        assert theirs.json() == missing.json()

    def test_an_erased_record_is_gone_for_the_screen(self, client, bot_user, card):
        """Каскад C5 обнулил слова — карточки больше нет, и экран это слышит."""
        anonymize_recommendations([bot_user.id])
        resp = _get(client, bot_user, card.id)
        assert resp.status_code == 404


@pytest.mark.django_db
class TestAbsence:
    def test_absence_is_a_kind_the_screen_can_draw(self, client, bot_user):
        record = Recommendation.objects.create(
            bot_user=bot_user,
            goal_id="goal-1",
            kind=Recommendation.Kind.ABSENCE,
            fingerprint="absence:goal-1",
        )
        resp = _get(client, bot_user, record.id)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["kind"] == "absence"
        assert data["what"] == ""
        assert data["alternatives"] == []
