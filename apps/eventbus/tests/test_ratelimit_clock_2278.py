"""DRF-2278 — тесты лимита не должны зависеть от того, где на часах граница окна.

django-ratelimit считает фиксированные окна по ``time.time()``. Если первые
запросы попадают в одно окно, а следующий — в новое, лимит не срабатывает и
запрос уходит в обработчик: CI видел 500 ``handler_exception`` вместо 429.
Узел механизма — без фикстуры, с шагом часов через границу; узел фикстуры —
тот же шаг, но окно стоит.
"""

from __future__ import annotations

import json
import time as real_time

import pytest
from django.core.cache import cache
from django.test import Client, override_settings

from apps.eventbus.tests.test_ingest_rate_limit import SECRET, VALID_BODY, _post

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _ingest_settings(settings):
    settings.EVENT_INGEST_HMAC_SECRET = SECRET
    settings.RATELIMIT_ENABLE = True
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def ingest() -> Client:
    return Client()


@pytest.fixture
def wall_clock(monkeypatch):
    """Настенные часы процесса, которые тест может перевести вперёд."""
    clock = {"t": real_time.time()}
    monkeypatch.setattr(real_time, "time", lambda: clock["t"])
    return clock


def _three_across_a_boundary(ingest: Client, clock) -> int:  # noqa: ANN001
    body = json.dumps(VALID_BODY).encode()
    _post(ingest, body)
    _post(ingest, body)
    clock["t"] += 60  # следующее окно «2/m», где бы ни была граница
    return _post(ingest, body).status_code


@override_settings(EVENT_INGEST_RATE_LIMIT="2/m")
def test_the_mechanism_a_boundary_between_requests_lets_the_third_through(
    ingest, wall_clock
) -> None:
    """Почему тесты мигали: через границу окна третий запрос не 429."""
    assert _three_across_a_boundary(ingest, wall_clock) != 429


@override_settings(EVENT_INGEST_RATE_LIMIT="2/m")
def test_frozen_ratelimit_clock_holds_the_window(
    ingest, wall_clock, frozen_ratelimit_clock
) -> None:
    assert _three_across_a_boundary(ingest, wall_clock) == 429


def test_the_frozen_clock_is_local_to_ratelimit(wall_clock, frozen_ratelimit_clock) -> None:
    """Часы стоят только у django-ratelimit — остальной процесс их не видит."""
    import django_ratelimit.core as core  # type: ignore[import-untyped]

    before = core.time.time()
    wall_clock["t"] += 3600
    assert core.time.time() == before
    assert real_time.time() == wall_clock["t"]
