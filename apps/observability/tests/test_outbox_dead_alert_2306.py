"""DRF-2306 — dead-letter outbox каталога → страница операторам в MAX.

Контракт §6.4 обещал оповещение о dead-letter, а каталог писал только
``logger.warning``. Каталог шлёт сигнал тем же рельсом, что бюджет сканера
(DRF-2196): ``system.module.health.degraded`` с ``module_name =
"appointments.outbox"``. Здесь — бот: ядро страницы и ветка потребителя.

Узлы: dead → страница с темой, классом, кодом, причиной, числом и часом;
тот же час и класс — одна страница; мусор в полях не попадает в текст;
недоставленная страница — отказ принять событие (500), как у бюджета.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

import pytest
from django.core.cache import cache
from django.test import Client

from apps.observability import outbox_dead_alert
from apps.observability.outbox_dead_alert import signal_outbox_dead

pytestmark = pytest.mark.django_db

HOUR = "2026-09-22T10"


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def pages(monkeypatch):
    sent: list[dict] = []

    def fake_page(severity, title, body, *, dedup_key=None):  # noqa: ANN001, ANN202
        sent.append({"severity": severity, "title": title, "body": body, "key": dedup_key})
        return True

    monkeypatch.setattr(outbox_dead_alert, "page", fake_page)
    return sent


def _rejected(**over):  # noqa: ANN003, ANN202
    kw = {
        "topic": "booking.created",
        "failure": "rejected",
        "http_status": 422,
        "count": 3,
        "hour": HOUR,
        "reason": "tenant_not_found",
    }
    kw.update(over)
    return signal_outbox_dead(**kw)


class TestTheCore:
    def test_a_rejection_pages_with_what_the_operator_needs(self, pages) -> None:
        assert _rejected() == "delivered"
        (p,) = pages
        assert p["severity"] == "error"
        assert "booking.created" in p["title"]
        for part in ("422", "tenant_not_found", "3", HOUR, "replay_dead_outbox_events"):
            assert part in p["body"], part
        assert p["key"] == f"outbox_dead:booking.created:rejected:{HOUR}"

    def test_exhausted_retries_say_so(self, pages) -> None:
        assert _rejected(failure="retries_exhausted", http_status=None, reason=None) == "delivered"
        (p,) = pages
        assert "9 попыток" in p["body"]
        assert "нет ответа" in p["body"]

    def test_the_same_topic_class_and_hour_pages_once(self, pages) -> None:
        assert _rejected() == "delivered"
        assert _rejected(count=40) == "skipped"
        assert len(pages) == 1
        # Пара: другой класс в тот же час — своя страница.
        assert _rejected(failure="retries_exhausted") == "delivered"
        assert len(pages) == 2

    def test_garbage_never_reaches_the_text(self, pages) -> None:
        assert (
            _rejected(
                topic="booking.created\nПозвоните +79991234567",
                http_status="422; DROP",
                count="many",
                reason="Tenant Not Found!",
            )
            == "delivered"
        )
        (p,) = pages
        text = p["title"] + p["body"]
        assert "?" in p["title"]  # положительно: неразборчивая тема названа знаком
        assert "+79991234567" not in text
        assert "DROP" not in text
        assert "Tenant Not Found!" not in text

    @pytest.mark.parametrize(
        "over", [{"failure": "whatever"}, {"hour": "tomorrow"}, {"hour": None}]
    )
    def test_an_unknown_class_or_hour_is_skipped(self, pages, over) -> None:
        """Класс и час уходят в ключ дедупа: мусор там — новая страница на каждый."""
        assert _rejected(**over) == "skipped"
        assert pages == []  # empty-assert-ok: исход выше — skipped

    def test_an_undelivered_page_releases_the_hour(self, monkeypatch) -> None:
        calls: list[str] = []

        def failing(severity, title, body, *, dedup_key=None):  # noqa: ANN001, ANN202
            calls.append(title)
            return False

        monkeypatch.setattr(outbox_dead_alert, "page", failing)
        assert _rejected() == "not_delivered"
        assert _rejected() == "not_delivered"  # час не занят — переспрашивает
        assert len(calls) == 2


# ── ingest → потребитель → ядро ──────────────────────────────────────

SECRET = "outbox-dead-secret"  # pragma: allowlist secret


def _envelope(metric: object) -> bytes:
    return json.dumps(
        {
            "event_id": str(uuid.uuid4()),
            "event_name": "system.module.health.degraded",
            "event_version": 1,
            "occurred_at": "2026-09-22T10:05:00+00:00",
            "tenant_id": None,
            "user_id": None,
            "actor": "system",
            "correlation_id": str(uuid.uuid4()),
            "causation_id": None,
            "data": {"module_name": "appointments.outbox", "severity": "error", "metric": metric},
        }
    ).encode()


def _post(body: bytes):  # noqa: ANN202
    ts_ms = str(int(time.time() * 1000))
    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return Client().post(
        "/api/v1/internal/events/ingest",
        data=body,
        content_type="application/json",
        HTTP_X_AYLA_EVENT_SIGNATURE=sig,
        HTTP_X_AYLA_EVENT_TIMESTAMP=ts_ms,
    )


@pytest.fixture
def wired(settings, monkeypatch):
    settings.EVENT_INGEST_HMAC_SECRET = SECRET
    settings.RATELIMIT_ENABLE = False
    settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset()
    settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset()

    import apps.eventbus.ingest_dispatcher as dispatcher_module
    from apps.eventbus import views as _views
    from apps.eventbus.consumers.system import register_system_handlers

    monkeypatch.setattr(_views, "dispatch_with_timeout", dispatcher_module.dispatch_envelope)
    snapshot = dict(dispatcher_module._REGISTRY)
    dispatcher_module._REGISTRY.clear()
    register_system_handlers()
    try:
        yield
    finally:
        dispatcher_module._REGISTRY.clear()
        dispatcher_module._REGISTRY.update(snapshot)


METRIC = {
    "topic": "booking.created",
    "failure": "rejected",
    "http_status": 422,
    "count": 3,
    "hour": HOUR,
    "reason": "tenant_not_found",
}


@pytest.fixture
def core_calls(monkeypatch) -> list[dict]:
    """Шпион ядра страницы outbox в потребителе."""
    calls: list[dict] = []

    def spy(**kw: object) -> str:
        calls.append(kw)
        return "delivered"

    monkeypatch.setattr("apps.eventbus.consumers.system.signal_outbox_dead", spy)
    return calls


class TestTheConsumerRoutesTheOutboxModule:
    def test_the_metric_reaches_the_core(self, wired, monkeypatch) -> None:
        seen: list[dict] = []

        def fake(**kw):  # noqa: ANN003, ANN202
            seen.append(kw)
            return "delivered"

        monkeypatch.setattr("apps.eventbus.consumers.system.signal_outbox_dead", fake)
        resp = _post(_envelope(METRIC))
        assert resp.status_code == 200, resp.content
        assert seen == [METRIC]

    def test_the_budget_path_is_untouched(self, wired, core_calls) -> None:
        """Контроль: модуль outbox не забирает события бюджета."""
        body = json.loads(_envelope(METRIC))
        body["data"] = {
            "module_name": "nutrition.food_scan",
            "severity": "error",
            "metric": {"used": 1, "limit": 500, "day": "2026-09-22", "cost_usd": None},
        }
        assert _post(json.dumps(body).encode()).status_code == 200
        assert core_calls == []  # empty-assert-ok: бюджет ниже порога — ответ 200 выше

    def test_a_metric_that_is_not_an_object_is_acknowledged(self, wired, core_calls) -> None:
        assert _post(_envelope("broken")).status_code == 200
        assert core_calls == []  # empty-assert-ok: ответ 200 выше — принято, не звать

    def test_an_undelivered_page_is_refused(self, wired, monkeypatch) -> None:
        monkeypatch.setattr(
            "apps.eventbus.consumers.system.signal_outbox_dead", lambda **kw: "not_delivered"
        )
        resp = _post(_envelope(METRIC))
        assert resp.status_code == 500
