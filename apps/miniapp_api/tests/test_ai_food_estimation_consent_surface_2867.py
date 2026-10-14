# ruff: noqa: F811 — фикстуры набора согласий импортируются и принимаются параметрами
"""DRF-2867 — поверхность выдачи и отзыва согласия на ИИ-оценку еды.

Решение владельца 07.10 (лист решений, п.17): единый экран Mini App — там
полный текст и «Разрешить» / «Не сейчас»; в настройках согласий — «Отозвать
согласие на ИИ-оценку»; после отзыва новые внешние запросы прекращаются,
справочник и дневник остаются. П.18: без согласия внешнего ИИ-вызова нет.

До этого листа у ``ai_food_estimation.grant`` и ``withdraw`` не было ни одной
поверхности: текст согласия обещал бы «отозвать в любой момент», а отозвать
было негде.

* s1 — отзыв через ручку: после него предикат перед отправкой говорит «нет»;
* s2 — отзыв идемпотентен и работает без единой выдачи и при выключенном
  механизме;
* s3 — пока текст не утверждён, выдача закрыта на сервере (409), и в базе
  ничего не появляется;
* s4 — с утверждённым текстом: выдача под показанной версией, чужая версия —
  409;
* s5 — пишется и снимается по всем оболочкам человека;
* s6 — добровольность: отзыв не трогает ``personal_data`` и дневник;
* s7 — документ согласий говорит состояние, включён ли механизм, версию и
  текст; ``granted`` — тем же предикатом, что гейт (версия текста сверяется);
* s8 — чужого согласия отсюда не достать: субъект — из проверенной initData.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.consent import ai_food_estimation as afe
from apps.consent.models import ConsentRecord
from apps.consent.services import has_global_consent, record_person_consent
from apps.identity.models import BotUser
from apps.miniapp_api.tests.test_customer_consents import (  # noqa: F401 — fixtures
    CHANNEL_USER_ID,
    _bot_token,
    _init_data_header,
    _no_ayla_link,
    auth,
    bot_user,
    tenant,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

AFE = ConsentRecord.ConsentType.AI_FOOD_ESTIMATION.value
PD = ConsentRecord.ConsentType.PERSONAL_DATA.value
#: Версия литералом: узел, читающий её из той же константы, согласился бы с любой.
VERSION = "ai-food-estimation-draft-v1"
APPROVED_TEXT = "Текст согласия, утверждённый владельцем (подставлен узлом)."


@pytest.fixture
def url() -> str:
    return reverse("miniapp_api:customer_ai_food_estimation_consent")


@pytest.fixture
def required(settings) -> None:
    settings.AI_FOOD_ESTIMATION_CONSENT_REQUIRED = True


@pytest.fixture
def approved_text(monkeypatch) -> None:
    """Владелец утвердил текст. В коде его ещё нет — узел подставляет свой."""
    monkeypatch.setattr(afe, "AI_FOOD_ESTIMATION_TEXT", APPROVED_TEXT)


def _post(client: Client, url: str, auth: dict, version: str = VERSION):
    return client.post(
        url, data={"document_version": version}, content_type="application/json", **auth
    )


def _granted_directly(bot_user: BotUser) -> None:
    """Согласие, выданное мимо экрана, — чтобы было что отзывать."""
    afe.grant(bot_user, document_version=VERSION, source="test-2867")


def _rows() -> int:
    return ConsentRecord.all_tenants.filter(consent_type=AFE).count()


# --------------------------------------------------------------------- #
# Отзыв — работает всегда
# --------------------------------------------------------------------- #


def test_s1_withdrawal_closes_the_external_estimate(
    client: Client, bot_user: BotUser, url: str, auth: dict, required
) -> None:
    _granted_directly(bot_user)
    assert afe.estimate_permitted(bot_user) is True  # есть что закрывать

    answer = client.delete(url, **auth)

    assert answer.status_code == 200
    assert answer.json()["ai_food_estimation"]["granted"] is False
    assert afe.estimate_permitted(bot_user) is False
    # Строка остаётся в журнале — отозванной, не удалённой.
    row = ConsentRecord.all_tenants.get(bot_user=bot_user, consent_type=AFE)
    assert row.withdrawn_at is not None


def test_s2_withdrawal_is_idempotent_and_needs_no_grant(
    client: Client, bot_user: BotUser, url: str, auth: dict
) -> None:
    # Механизм выключен, выдачи не было — отзыв всё равно отвечает и не падает.
    first = client.delete(url, **auth)
    second = client.delete(url, **auth)

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json()["ai_food_estimation"]["granted"] is False
    assert _rows() == 0


# --------------------------------------------------------------------- #
# Выдача — только под утверждённым текстом
# --------------------------------------------------------------------- #


def test_s3_no_approved_text_no_grant(
    client: Client, bot_user: BotUser, url: str, auth: dict, required
) -> None:
    assert afe.AI_FOOD_ESTIMATION_TEXT is None  # текст утверждает владелец

    refused = _post(client, url, auth)

    assert refused.status_code == 409
    assert refused.json()["error"] == "consent_text_not_approved"
    assert _rows() == 0
    assert afe.estimate_permitted(bot_user) is False


def test_s4_grant_under_the_shown_text(
    client: Client, bot_user: BotUser, url: str, auth: dict, required, approved_text
) -> None:
    assert afe.estimate_permitted(bot_user) is False  # есть чему появиться

    granted = _post(client, url, auth)

    assert granted.status_code == 200
    assert granted.json()["ai_food_estimation"]["granted"] is True
    row = ConsentRecord.all_tenants.get(bot_user=bot_user, consent_type=AFE)
    assert (row.document_version, row.source) == (VERSION, "miniapp")
    assert afe.estimate_permitted(bot_user) is True


def test_s4_a_stale_text_version_is_refused_and_writes_nothing(
    client: Client, bot_user: BotUser, url: str, auth: dict, approved_text
) -> None:
    refused = _post(client, url, auth, version="ai-food-estimation-v0")

    assert refused.status_code == 409
    assert refused.json()["error"] == "stale_disclosure"
    assert _rows() == 0
    # Положительный контроль: с верной версией тот же человек проходит.
    assert _post(client, url, auth).status_code == 200


def test_s4_the_full_cycle_refuse_grant_withdraw(
    client: Client, bot_user: BotUser, url: str, auth: dict, required, approved_text
) -> None:
    """Цикл из п.18 листа: отказ → согласие → отзыв → внешних запросов нет."""
    assert afe.estimate_permitted(bot_user) is False  # «Не сейчас»: ничего не выдано

    _post(client, url, auth)
    assert afe.estimate_permitted(bot_user) is True

    client.delete(url, **auth)
    assert afe.estimate_permitted(bot_user) is False

    # Передумала ещё раз — выдача после отзыва снова открывает.
    _post(client, url, auth)
    assert afe.estimate_permitted(bot_user) is True


# --------------------------------------------------------------------- #
# Человек, а не строка
# --------------------------------------------------------------------- #


def test_s5_written_and_withdrawn_on_every_shell_of_the_person(
    client: Client, bot_user: BotUser, url: str, auth: dict, required, approved_text
) -> None:
    sentinel = Tenant.objects.create(slug="afe-global-2867", name="Global")
    chat_shell = BotUser.all_tenants.create(
        tenant=sentinel,
        channel="max",
        channel_user_id=CHANNEL_USER_ID,
        chat_id=f"chat-{CHANNEL_USER_ID}",
    )
    assert afe.estimate_permitted(chat_shell) is False

    _post(client, url, auth)
    assert afe.estimate_permitted(chat_shell) is True

    client.delete(url, **auth)
    assert afe.estimate_permitted(chat_shell) is False


def test_s6_withdrawal_leaves_the_other_consents_alone(
    client: Client, bot_user: BotUser, url: str, auth: dict
) -> None:
    diary = ConsentRecord.ConsentType.FOOD_DIARY_PROCESSING.value
    record_person_consent(bot_user, consent_type=diary, source="test-2867")
    _granted_directly(bot_user)
    assert has_global_consent(bot_user, PD) is True  # фикстура дала базовое согласие
    assert has_global_consent(bot_user, diary) is True

    client.delete(url, **auth)

    assert afe.is_granted(bot_user) is False
    assert has_global_consent(bot_user, PD) is True
    assert has_global_consent(bot_user, diary) is True


# --------------------------------------------------------------------- #
# Документ согласий
# --------------------------------------------------------------------- #


def test_s7_the_document_says_state_mechanism_version_and_text(
    client: Client, bot_user: BotUser, auth: dict
) -> None:
    document = client.get(reverse("miniapp_api:customer_consents"), **auth).json()

    assert document["ai_food_estimation"] == {
        "granted": False,
        "required": False,
        "grant": {"document_version": VERSION, "pending_legal": True, "text": None},
    }


def test_s7_the_document_follows_the_flag_and_the_text(
    client: Client, bot_user: BotUser, auth: dict, required, approved_text
) -> None:
    block = client.get(reverse("miniapp_api:customer_consents"), **auth).json()[
        "ai_food_estimation"
    ]

    assert block["required"] is True
    assert block["grant"]["text"] == APPROVED_TEXT


def test_s7_granted_is_the_gate_predicate_not_the_bare_row(
    client: Client, bot_user: BotUser, auth: dict
) -> None:
    """Согласие под прежним текстом гейт не признаёт — и экран не должен."""
    record_person_consent(
        bot_user, consent_type=AFE, source="test-2867", document_version="ai-food-estimation-v0"
    )

    document = client.get(reverse("miniapp_api:customer_consents"), **auth).json()

    # Строка есть и действует — общий блок её видит…
    assert document["consents"][AFE]["granted"] is True
    # …а блок экрана отвечает тем же, чем гейт перед отправкой.
    assert document["ai_food_estimation"]["granted"] is False
    assert afe.is_granted(bot_user) is False


# --------------------------------------------------------------------- #
# Чужое согласие недостижимо
# --------------------------------------------------------------------- #


def test_s8_the_subject_is_the_verified_person_not_the_body(
    client: Client, bot_user: BotUser, tenant, url: str, auth: dict
) -> None:
    other = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="999000111", chat_id="chat-999000111"
    )
    _granted_directly(other)
    _granted_directly(bot_user)

    client.delete(
        url,
        data={"bot_user_id": str(other.id), "channel_user_id": "999000111"},
        content_type="application/json",
        **auth,
    )

    assert afe.is_granted(bot_user) is False  # своё снято
    assert afe.is_granted(other) is True  # чужое не тронуто


def test_s8_without_init_data_nothing_happens(client: Client, bot_user: BotUser, url: str) -> None:
    _granted_directly(bot_user)

    answer = client.delete(url)

    assert answer.status_code in (401, 403)
    assert afe.is_granted(bot_user) is True
