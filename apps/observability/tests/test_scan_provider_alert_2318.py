"""Стойкий отказ распознавателя фото → страница операторам в MAX (DRF-2318).

Каталог (#549) публикует ``system.module.health.degraded`` с
``module_name="nutrition.food_scan.provider"`` и метрикой ``{provider, reason,
hour}`` — один раз на (провайдер, причина) за час. Потребитель бота отдаёт её
ядру :func:`signal_scan_provider_down`, а ядро поднимает страницу — рельсом
dead-letter (DRF-2306): второй дедуп по часу, чужие поля — только закрытыми
формами, недоставленная страница — отказ принять событие.

Своё имя модуля: под ``nutrition.food_scan`` потребитель читает метрику
бюджета и без ``day`` отбросил бы сигнал молча.

* a1 — одна страница на (провайдер, причина, час), в тексте — что чинить;
* a2 — мусор в полях: провайдер и причина — только закрытые формы, час — ISO;
* a3 — страница не ушла → ``not_delivered`` и час возвращён;
* e1 — потребитель: модуль ``nutrition.food_scan.provider`` доходит до ядра,
  бюджетный модуль — как прежде до своего.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.eventbus.consumers import system
from apps.observability import scan_provider_alert
from apps.observability.scan_provider_alert import signal_scan_provider_down


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def pages(monkeypatch):
    sent: list[tuple] = []

    def fake_page(level, title, body, *, dedup_key=None):
        sent.append((level, title, body, dedup_key))
        return True

    monkeypatch.setattr(scan_provider_alert, "page", fake_page)
    return sent


HOUR = "2026-09-22T15"


class TestA1OnePagePerHour:
    def test_one_page_names_what_to_fix(self, pages) -> None:
        first = signal_scan_provider_down(provider="openai", reason="billing_not_active", hour=HOUR)
        second = signal_scan_provider_down(
            provider="openai", reason="billing_not_active", hour=HOUR
        )

        assert (first, second) == ("delivered", "skipped")
        ((level, title, body, _key),) = pages
        assert level == "error"
        assert "openai" in title
        assert "billing_not_active" in body and "оплат" in body.lower()

    def test_another_reason_is_another_page(self, pages) -> None:
        signal_scan_provider_down(provider="openai", reason="billing_not_active", hour=HOUR)
        signal_scan_provider_down(provider="yandex", reason="not_configured", hour=HOUR)
        assert len(pages) == 2


class TestA2ForeignFieldsAreClosed:
    def test_garbage_hour_is_skipped(self, pages) -> None:
        assert (
            signal_scan_provider_down(provider="openai", reason="billing_not_active", hour="x")
            == "skipped"
        )
        assert pages == []

    def test_garbage_provider_and_reason_are_not_carried(self, pages) -> None:
        signal_scan_provider_down(provider="<script>", reason="drop table", hour=HOUR)
        ((_, title, body, _),) = pages
        assert "<script>" not in title + body
        assert "drop table" not in body


class TestA3UndeliveredReleasesTheHour:
    def test_not_delivered_returns_the_hour(self, monkeypatch) -> None:
        monkeypatch.setattr(scan_provider_alert, "page", lambda *a, **k: False)
        assert (
            signal_scan_provider_down(provider="openai", reason="billing_not_active", hour=HOUR)
            == "not_delivered"
        )
        sent: list = []
        monkeypatch.setattr(scan_provider_alert, "page", lambda *a, **k: sent.append(1) or True)
        assert (
            signal_scan_provider_down(provider="openai", reason="billing_not_active", hour=HOUR)
            == "delivered"
        )


class _Envelope:
    def __init__(self, data: dict) -> None:
        self.event_id = uuid.uuid4()
        self.data = data


class TestE1TheConsumerRoutesTheModule:
    def test_provider_module_reaches_its_core(self) -> None:
        env = _Envelope(
            {
                "module_name": "nutrition.food_scan.provider",
                "severity": "error",
                "metric": {"provider": "openai", "reason": "billing_not_active", "hour": HOUR},
            }
        )
        with (
            patch.object(system, "assert_envelope_tenant_authorized"),
            patch.object(system, "signal_scan_provider_down", return_value="delivered") as core,
            patch.object(system, "signal_budget") as budget,
        ):
            system.handle_system_health_degraded(env)
        core.assert_called_once_with(provider="openai", reason="billing_not_active", hour=HOUR)
        budget.assert_not_called()

    def test_undelivered_page_refuses_the_event(self) -> None:
        env = _Envelope(
            {
                "module_name": "nutrition.food_scan.provider",
                "metric": {"provider": "openai", "reason": "billing_not_active", "hour": HOUR},
            }
        )
        with (
            patch.object(system, "assert_envelope_tenant_authorized"),
            patch.object(system, "signal_scan_provider_down", return_value="not_delivered"),
            pytest.raises(system.PageNotDeliveredError),
        ):
            system.handle_system_health_degraded(env)
