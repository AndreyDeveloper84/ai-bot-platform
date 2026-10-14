"""DRF-2902 — отказ каталога в чтении графика не превращается в 500.

Найдено по логам пилота 08.10: ``GET /api/v1/master/schedule`` ответил 500 —
каталог отказал в чтении рамки расписания (403 «не администратор этого
салона»), а сборщик экрана этот отказ не ловил. Тот же вызов в дашборде и в
заявке на доступность обёрнут давно.

* r1 — любой отказ каталога (нет прав, не найдено, не настроен, недоступен)
  отвечает 503 ``schedule_unavailable`` существующей фразой;
* r2 — текст отказа каталога наружу не уходит;
* r3 — положительная пара: без отказа тот же запрос отвечает 200.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.integrations.ayla.salon_client import (
    SalonForbidden,
    SalonNotConfigured,
    SalonNotFound,
    SalonUnavailable,
)
from apps.master_api.tests.conftest import init_data_header

pytestmark = pytest.mark.django_db

FRAME = "apps.master_api.services.schedule.load_day_frame"
CATALOG_WORDS = "Изменять график может только администратор салона"
URL = "?from=2026-05-21&to=2026-05-21"


def _get(client: Client):
    return client.get(
        reverse("master_api:schedule") + URL, HTTP_AUTHORIZATION=init_data_header("12345")
    )


@pytest.mark.parametrize(
    "refusal",
    [SalonForbidden, SalonNotFound, SalonNotConfigured, SalonUnavailable],
    ids=lambda cls: cls.__name__,
)
def test_r1_a_catalog_refusal_is_a_named_503_not_a_500(
    client: Client, bot_user: BotUser, accepted_master: CatalogMaster, refusal: type[Exception]
) -> None:
    with patch(FRAME, side_effect=refusal(CATALOG_WORDS)) as frame:
        answer = _get(client)

    assert frame.called  # отказ пришёл именно с чтения рамки
    assert answer.status_code == 503, answer.content[:200]
    assert answer.json()["error"] == "schedule_unavailable"


def test_r2_the_catalogs_words_do_not_leave(
    client: Client, bot_user: BotUser, accepted_master: CatalogMaster
) -> None:
    with patch(FRAME, side_effect=SalonForbidden(CATALOG_WORDS)):
        answer = _get(client)

    assert answer.json() == {
        "error": "schedule_unavailable",
        "detail": "Расписание сейчас недоступно.",
    }
    assert CATALOG_WORDS not in str(answer.json())


def test_r3_without_a_refusal_the_same_request_is_200(
    client: Client, bot_user: BotUser, accepted_master: CatalogMaster
) -> None:
    answer = _get(client)

    assert answer.status_code == 200, answer.content[:200]
    assert "days" in answer.json()
