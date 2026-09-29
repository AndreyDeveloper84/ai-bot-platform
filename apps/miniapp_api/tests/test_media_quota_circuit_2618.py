"""Картинки мастера не делят с записью ни квоту, ни автомат (DRF-2618).

До этого листа прокси фото (DRF-2539) ходил в каталог через общий
``_request`` клиента: таймауты картинок писались в тот же автомат, что у
записи, а квоты на человека не было. Трафика картинок — хватило бы
зацикленной прокрутки на медленной сети — было достаточно, чтобы автомат
открылся и перестала работать запись.

Узлы — парами, которые обязаны различаться (узел «квота срабатывает» в
одиночку прошёл бы и при общем автомате, то есть при самом дефекте):

* q1 — ОДИН клиент каталога: картинки падают таймаутом, автомат картинок
  открылся, человек выбрал квоту — и в тот же момент ``create_appointment``
  на том же клиенте проходит, автомат записи закрыт;
* q2 — изоляция в обе стороны: таймауты картинок открывают автомат картинок
  и НЕ открывают автомат записи; таймауты записи — наоборот;
* q3 — квота на человека: 120-й запрос в минуту проходит, 121-й — 429 с
  ``Retry-After``; другой человек в ту же секунду — не 429; сверх квоты
  каталог не спрошен;
* q4 — 429 каталога на картинке: сразу отказ, без ``time.sleep`` и
  повторов, автомат записи не тронут;
* q5 — кэш недоступен — тормоз не срабатывает (картинки идут), а не
  отказывает всем.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.integrations.ayla import booking_client as bc
from apps.miniapp_api import master_media
from apps.miniapp_api.tests.test_diary_days_2099 import (  # переиспользуем стенд
    _auth,
    _bot_token,  # noqa: F401 — autouse
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CATALOG_ID = uuid.UUID("6b2e3d4c-5f60-4a7b-8c9d-0e1f2a3b4c5d")
JPEG = b"\xff\xd8\xff\xe0" + b"pixels" * 300

#: Ответ каталога на создание записи — из ``test_booking_client.TestWriteRoundTrip``.
APPOINTMENT = {
    "data": {
        "id": "appt-uuid",
        "status": "confirmed",
        "start_datetime": "2026-06-10T14:00:00+03:00",
        "end_datetime": "2026-06-10T15:00:00+03:00",
        "service": {"id": "svc-1"},
        "specialist": {"id": "spec-1"},
    }
}


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def master(db) -> CatalogMaster:
    tenant = Tenant.objects.create(slug="quota-2618", name="Салон", timezone="Europe/Moscow")
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=None,
        external_updated_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        name="Анна Петрова",
        is_active=True,
        catalog_specialist_id=CATALOG_ID,
        photo_url="http://minio:9000/beautygo-media/specialists/avatars/a.jpg",
    )


def _catalog(*, media: str) -> tuple[bc.AylaBookingHTTPClient, list[str]]:
    """Один настоящий клиент каталога: картинки — ``media``, запись — 201."""
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        if "/media/" in req.url.path or "/portfolio/" in req.url.path:
            if media == "timeout":
                raise httpx.ReadTimeout("slow network", request=req)
            if media == "429":
                return httpx.Response(429, headers={"retry-after": "1"})
            return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})
        if req.url.path.endswith("/appointments/"):
            return httpx.Response(201, json=APPOINTMENT)
        if req.url.path.endswith("/slow/"):
            raise httpx.ReadTimeout("slow network", request=req)
        return httpx.Response(404, json={})

    client = bc.AylaBookingHTTPClient(
        base_url="https://ayla.test", api_token="tok", transport=httpx.MockTransport(handler)
    )
    return client, seen


def _book(client: bc.AylaBookingHTTPClient) -> bc.AylaBookingRecord:
    return client.create_appointment(
        external_user_id="bot:max:99001",
        client_id="client-uuid",
        specialist_id="spec-1",
        service_id="svc-1",
        start_datetime="2026-06-10T14:00:00+03:00",
        idempotency_key="idem-2618",
    )


def _photo(client: Client, master_id, user_id: str = "99001"):
    url = reverse("miniapp_api:master_media_photo", kwargs={"master_id": str(master_id)})
    return client.get(url, HTTP_AUTHORIZATION=_auth(user_id))


class TestQ1ImagesExhaustedBookingWorks:
    def test_same_client_same_moment(self, client, master) -> None:
        catalog, _seen = _catalog(media="timeout")
        statuses = []
        with patch.object(master_media, "get_ayla_booking_client", return_value=catalog):
            for _ in range(master_media.MEDIA_PER_PERSON_PER_MINUTE + 1):
                statuses.append(_photo(client, master.id).status_code)

        # Картинки: сперва 502 (таймауты, потом открытый автомат картинок),
        # сверх квоты — 429.
        assert statuses[: bc.CIRCUIT_FAILURE_THRESHOLD] == [502] * bc.CIRCUIT_FAILURE_THRESHOLD
        assert statuses[-1] == 429
        assert catalog._media_circuit.opened_at is not None
        # ...а запись на ТОМ ЖЕ клиенте в тот же момент проходит.
        assert catalog._circuit.opened_at is None
        record = _book(catalog)
        assert record.appointment_id == "appt-uuid"


class TestQ2BreakersAreIsolated:
    def test_image_timeouts_open_only_the_image_breaker(self) -> None:
        catalog, _ = _catalog(media="timeout")
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(bc.BookingUnavailableError):
                catalog.specialist_media_file(specialist_id=str(CATALOG_ID))
        assert catalog._media_circuit.opened_at is not None
        assert catalog._circuit.opened_at is None
        with pytest.raises(bc.BookingUnavailableError, match="media_circuit_open"):
            catalog.specialist_media_file(specialist_id=str(CATALOG_ID))
        assert _book(catalog).appointment_id == "appt-uuid"

    def test_booking_timeouts_do_not_close_images(self) -> None:
        catalog, _ = _catalog(media="ok")
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(bc.BookingUnavailableError):
                catalog._request("GET", "slow/")
        assert catalog._circuit.opened_at is not None
        assert catalog._media_circuit.opened_at is None
        assert catalog.specialist_media_file(specialist_id=str(CATALOG_ID)) == (JPEG, "image/jpeg")

    def test_breakers_are_named_apart(self) -> None:
        catalog, _ = _catalog(media="ok")
        assert catalog._circuit.name == "ayla.booking"
        assert catalog._media_circuit.name == "ayla.booking.media"


class TestQ3PerPersonQuota:
    def test_limit_and_other_person(self, client, master) -> None:
        catalog, seen = _catalog(media="ok")
        limit = master_media.MEDIA_PER_PERSON_PER_MINUTE
        assert limit == 120  # решённое число — литералом, не только константой
        with patch.object(master_media, "get_ayla_booking_client", return_value=catalog):
            codes = [_photo(client, master.id).status_code for _ in range(limit)]
            over = _photo(client, master.id)
            other = _photo(client, master.id, user_id="99002")

        assert codes == [200] * limit
        assert over.status_code == 429
        assert over.json()["error"] == "media_rate_limited"
        assert over["Retry-After"] == "60"
        assert other.status_code == 200
        # Сверх квоты каталог не спрошен: limit + другой человек.
        assert len(seen) == limit + 1


class TestQ4CatalogRateLimitNoSleep:
    def test_429_is_immediate_and_leaves_booking_alone(self, client, master) -> None:
        catalog, seen = _catalog(media="429")
        with patch.object(bc.time, "sleep") as sleep:
            with pytest.raises(bc.BookingRateLimitedError):
                catalog.specialist_media_file(specialist_id=str(CATALOG_ID))
            with patch.object(master_media, "get_ayla_booking_client", return_value=catalog):
                resp = _photo(client, master.id)
        sleep.assert_not_called()
        assert len(seen) == 2  # по одному запросу, без повторов
        assert resp.status_code == 429
        assert catalog._circuit.opened_at is None
        assert catalog._circuit.failures == []


class TestQ5CacheDownIsNotADenial:
    def test_images_flow_when_cache_fails(self, client, master) -> None:
        catalog, _ = _catalog(media="ok")
        broken = MagicMock(side_effect=ConnectionError("redis down"))
        with (
            patch.object(master_media, "get_ayla_booking_client", return_value=catalog),
            patch.object(cache, "add", broken),
        ):
            resp = _photo(client, master.id)
        assert resp.status_code == 200
        assert broken.called
