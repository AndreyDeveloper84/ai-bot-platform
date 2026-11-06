# ruff: noqa: F811 — фикстуры набора согласий импортируются и принимаются параметрами
"""DRF-2779 — умная память Ф4: добровольное согласие на предположения о предпочтениях.

Решение владельца 05.10.2026: отдельное добровольное включение; отказ не
блокирует запись и прочие функции; текст — черновик на юр-проверке (#947).

* p1 — выдача под показанной версией и отзыв, через ручку Mini App;
* p2 — чужая версия текста — 409, ничего не записано;
* p3 — пишется по всем оболочкам человека (иначе писатель памяти, читающий
  чатовую оболочку, согласия не увидит);
* p4 — добровольность: отзыв Ф4 не трогает ``personal_data``, повторная
  выдача хранения Ф4 не выдаёт; но отзыв ``personal_data`` снимает и Ф4 —
  надстройку вместе с основанием, как согласие дневника;
* p5 — выдаёт этот тип только :func:`apps.consent.preference_inference.grant`
  (перепись): «одной галочкой со всем остальным» он появиться не может;
* p6 — документ согласий говорит версию текста и что он на юр-проверке.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

from apps.consent import preference_inference
from apps.consent.models import ConsentRecord
from apps.consent.services import has_global_consent
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

PI = ConsentRecord.ConsentType.PREFERENCE_INFERENCE.value
PD = ConsentRecord.ConsentType.PERSONAL_DATA.value
#: Версия литералом: узел, читающий её из той же константы, согласился бы с любой правкой.
VERSION = "preference-inference-draft-v1"


@pytest.fixture
def pi_url() -> str:
    return reverse("miniapp_api:customer_preference_inference_consent")


def _post(client: Client, url: str, auth: dict, version: str = VERSION):
    return client.post(
        url, data={"document_version": version}, content_type="application/json", **auth
    )


def test_p1_grant_under_the_shown_text_then_withdraw(
    client: Client, bot_user: BotUser, pi_url: str, auth: dict
) -> None:
    assert has_global_consent(bot_user, PI) is False  # есть чему появиться

    granted = _post(client, pi_url, auth)

    assert granted.status_code == 200
    assert granted.json()["preference_inference"]["granted"] is True
    assert granted.json()["consents"][PI]["granted"] is True
    row = ConsentRecord.all_tenants.get(bot_user=bot_user, consent_type=PI)
    assert row.document_version == VERSION

    withdrawn = client.delete(pi_url, **auth)

    assert withdrawn.status_code == 200
    assert withdrawn.json()["preference_inference"]["granted"] is False
    assert preference_inference.is_granted(bot_user) is False


def test_p2_a_stale_text_version_is_refused_and_writes_nothing(
    client: Client, bot_user: BotUser, pi_url: str, auth: dict
) -> None:
    refused = _post(client, pi_url, auth, version="preference-inference-v0")

    assert refused.status_code == 409
    assert refused.json()["error"] == "stale_disclosure"
    assert not ConsentRecord.all_tenants.filter(consent_type=PI).exists()
    # Положительный контроль: с верной версией тот же человек проходит.
    assert _post(client, pi_url, auth).status_code == 200


def test_p3_written_on_every_shell_of_the_person(
    client: Client, bot_user: BotUser, pi_url: str, auth: dict
) -> None:
    sentinel = Tenant.objects.create(slug="pi-global-2779", name="Global")
    chat_shell = BotUser.all_tenants.create(
        tenant=sentinel,
        channel="max",
        channel_user_id=CHANNEL_USER_ID,
        chat_id=f"chat-{CHANNEL_USER_ID}",
    )
    assert preference_inference.is_granted(chat_shell) is False

    _post(client, pi_url, auth)

    assert preference_inference.is_granted(chat_shell) is True
    client.delete(pi_url, **auth)
    assert preference_inference.is_granted(chat_shell) is False


def test_p4_voluntary_both_ways(client: Client, bot_user: BotUser, pi_url: str, auth: dict) -> None:
    from apps.consent.customer import regrant_data_storage
    from apps.consent.services import _PERSONAL_DATA_CASCADE, withdraw_personal_data_for_bot_users

    assert has_global_consent(bot_user, PD) is True  # фикстура дала базовое согласие
    _post(client, pi_url, auth)

    # Отзыв Ф4 — только Ф4.
    client.delete(pi_url, **auth)
    assert has_global_consent(bot_user, PD) is True

    # Отзыв personal_data снимает и Ф4 — надстройку вместе с основанием, иначе
    # после «удалить мои данные» разрешение анализировать обращения осталось бы
    # действующим (тот же род, что согласие дневника в каскаде).
    _post(client, pi_url, auth)
    assert preference_inference.is_granted(bot_user) is True
    withdraw_personal_data_for_bot_users([bot_user], source="test-2779")
    assert has_global_consent(bot_user, PD) is False
    assert preference_inference.is_granted(bot_user) is False
    assert PI in {str(t) for t in _PERSONAL_DATA_CASCADE}

    # Повторная выдача хранения Ф4 не возвращает.
    regrant_data_storage(bot_user)
    assert has_global_consent(bot_user, PD) is True
    assert preference_inference.is_granted(bot_user) is False


APPS = Path(__file__).resolve().parents[2]


def test_p5_only_the_preference_inference_module_grants_this_type() -> None:
    granting = {"record_person_consent", "record_global_consent", "grant"}
    found: list[str] = []
    for path in APPS.rglob("*.py"):
        rel = path.relative_to(APPS.parent).as_posix()
        if "/tests/" in rel or "/migrations/" in rel or path.name.startswith("test_"):
            continue
        source = path.read_text(encoding="utf-8")
        if "PREFERENCE_INFERENCE" not in source and "preference_inference" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in granting:
                continue
            for kw in node.keywords:
                if kw.arg == "consent_type" and (
                    "PREFERENCE_INFERENCE" in ast.dump(kw.value)
                    or "preference_inference" in ast.dump(kw.value)
                ):
                    found.append(f"{rel}:{node.lineno}")
    assert len(found) == 1, found
    assert found[0].startswith("apps/consent/preference_inference.py:"), found


def test_p6_the_consents_document_names_the_text_version_and_its_legal_status(
    client: Client, bot_user: BotUser, auth: dict
) -> None:
    document = client.get(reverse("miniapp_api:customer_consents"), **auth).json()

    block = document["preference_inference"]
    assert block["granted"] is False
    assert block["grant"] == {"document_version": VERSION, "pending_legal": True}
    assert preference_inference.PREFERENCE_INFERENCE_TEXT.startswith(
        "Разрешаю Ayla анализировать мои обращения и действия в сервисе"
    )
