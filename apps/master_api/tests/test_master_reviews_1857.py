"""«Мои отзывы» — прокси в каталог (DRF-1857, K14).

Что заперто:

- ``GET /reviews`` идёт в ``/internal/specialists/{id}/reviews/`` каталога с
  bot-личностью мастера как субъектом;
- строка отзыва — белым списком: лишнее из ответа каталога (телефон,
  username, фамилия отдельным полем) до экрана не доходит;
- оценки нет, пока нет отзывов, даже если каталог прислал число;
- отказы честно: 403 → ``not_linked``, недоступность → 503, ответ без числа
  или списка — 502, а не «отзывов нет»; только GET.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import (
    BookingBadRequestError,
    BookingUnavailableError,
)
from apps.master_api import views
from apps.master_api.tests.conftest import init_data_header

pytestmark = pytest.mark.django_db

ROW = {
    "id": "r1",
    "rating": 5,
    "text": "Спасибо",
    "client_name": "Ксения Л.",
    "service_name": "Маникюр",
    "created_at": "2026-09-10T10:00:00+00:00",
}


@pytest.fixture
def ayla(monkeypatch, accepted_master: CatalogMaster) -> MagicMock:
    client = MagicMock()
    client.get_specialist_reviews.return_value = {
        "specialist_id": str(accepted_master.id),
        "review_count": 1,
        "rating": 5.0,
        "reviews": [dict(ROW)],
    }
    monkeypatch.setattr(views, "get_ayla_booking_client", lambda: client)
    return client


def _get(client: Client):
    return client.get(reverse("master_api:reviews"), HTTP_AUTHORIZATION=init_data_header("12345"))


class TestProxy:
    def test_names_the_master_as_subject_and_returns_the_rows(
        self, client: Client, accepted_master, bot_user: BotUser, ayla
    ):
        resp = _get(client)
        assert resp.status_code == 200, resp.content
        kwargs = ayla.get_specialist_reviews.call_args.kwargs
        assert kwargs["specialist_id"] == str(accepted_master.id)
        assert kwargs["external_user_id"].endswith(str(bot_user.channel_user_id))
        assert resp.json() == {"review_count": 1, "rating": 5.0, "reviews": [ROW]}

    def test_rows_are_whitelisted(self, client: Client, ayla):
        leaky = dict(ROW, phone="+79997775544", client_username="user_79997775544",
                     client_last_name="Леонова")
        ayla.get_specialist_reviews.return_value["reviews"] = [leaky]
        resp = _get(client)
        body = resp.content.decode("utf-8")
        assert resp.json()["reviews"] == [ROW]
        assert "79997775544" not in body and "Леонова" not in body

    def test_no_reviews_means_no_rating(self, client: Client, ayla):
        ayla.get_specialist_reviews.return_value = {"review_count": 0, "rating": 0.0, "reviews": []}
        assert _get(client).json() == {"review_count": 0, "rating": None, "reviews": []}

    def test_only_get(self, client: Client, ayla):
        resp = client.post(reverse("master_api:reviews"), HTTP_AUTHORIZATION=init_data_header("12345"))
        assert resp.status_code == 405
        ayla.get_specialist_reviews.assert_not_called()


class TestRefusals:
    def test_403_is_not_linked(self, client: Client, ayla):
        ayla.get_specialist_reviews.side_effect = BookingBadRequestError(
            "http_403", status_code=403, code="PERMISSION_DENIED"
        )
        resp = _get(client)
        assert resp.status_code == 403
        assert resp.json()["error"] == "not_linked"

    def test_unavailable_is_503(self, client: Client, ayla):
        ayla.get_specialist_reviews.side_effect = BookingUnavailableError("down")
        assert _get(client).status_code == 503

    @pytest.mark.parametrize(
        "payload",
        [{"reviews": []}, {"review_count": 1}, {"review_count": "1", "reviews": []}, {}],
    )
    def test_an_unreadable_answer_is_502_not_empty(self, client: Client, ayla, payload):
        ayla.get_specialist_reviews.return_value = payload
        resp = _get(client)
        assert resp.status_code == 502
        assert "reviews" not in resp.json()
