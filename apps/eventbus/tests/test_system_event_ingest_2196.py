"""Системное событие без пользователя — третий путь приёма (DRF-2196, вариант а1).

Каталог публикует ``system.module.health.degraded`` — сигнал бюджета
распознавания фото (#524 в beautygo_backend). У системного сигнала нет
пользователя и нет тенанта: каталог считает снимки, а не тех, кто их
прислал. А контракт бота требовал ``user_id`` у КАЖДОГО события без
исключения (``event-contract.md`` §2: «Always present — even
``tenant_id=null`` events have a user»), и конверт такой сигнал отвергал
на разборе (``missing_field: user_id``).

Владелец выбрал (а1), §64: ``user_id`` может отсутствовать ТОЛЬКО у
событий закрытого набора ``system.*``. Это второе ослабление контракта
после «имя в закрытый набор», и оно сознательно узкое.

# Узлы, которые держат ослабление узким

* системное событие без ``user_id`` проходит;
* НЕсистемное без ``user_id`` — по-прежнему ``missing_field`` (иначе
  ослабление расползётся на весь конверт);
* имя с префиксом ``system.``, но вне закрытого набора — отказ (префикс
  не пропуск: набор закрыт, а не шаблон);
* системное событие С ``user_id`` или С ``tenant_id`` — отказ: у системы
  нет субъекта, и событие, которое его называет, противоречит себе;
* подпись неверна — отказ (единственная авторизация системного события —
  HMAC и имя; без подписи от него не остаётся ничего).

# Принято сознательно

Путь ``tenant_id=null`` обходит allowlist событий целиком:
``assert_envelope_tenant_authorized`` возвращает управление раньше, чем
консультирует ``EVENT_INGEST_ALLOWED_EVENTS``. Для четырёх user-global
событий это смягчено проверкой пользователя; у системного события
пользователя нет, и остаются только HMAC и закрытый набор имён. Владелец
это принял (§64) — узел ниже делает решение видимым, чтобы его смена была
решением, а не побочным эффектом.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from unittest.mock import patch

import pytest
from django.test import Client

from apps.eventbus.ingest_envelope import IngestEnvelopeError, parse_envelope

pytestmark = pytest.mark.django_db

INGEST_URL = "/api/v1/internal/events/ingest"
SECRET = "system-event-secret"  # pragma: allowlist secret
NAME = "system.module.health.degraded"


def _envelope(**overrides) -> dict:
    """Конверт той формы, которую шлёт каталог (#524)."""
    env = {
        "event_id": str(uuid.uuid4()),
        "event_name": NAME,
        "event_version": 1,
        "occurred_at": "2026-09-21T10:00:00+00:00",
        "tenant_id": None,
        "user_id": None,
        "actor": "system",
        "correlation_id": str(uuid.uuid4()),
        "causation_id": None,
        "data": {
            "module_name": "nutrition.food_scan",
            "severity": "error",
            "metric": {"used": 500, "limit": 500, "day": "2026-09-21", "cost_usd": None},
        },
    }
    env.update(overrides)
    return env


def _post(client: Client, body: bytes, *, secret: str = SECRET):
    ts_ms = str(int(time.time() * 1000))
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        INGEST_URL,
        data=body,
        content_type="application/json",
        HTTP_X_AYLA_EVENT_SIGNATURE=sig,
        HTTP_X_AYLA_EVENT_TIMESTAMP=ts_ms,
    )


@pytest.fixture()
def wired(settings, monkeypatch):
    """Настоящий ingest с настоящим реестром системных обработчиков."""
    settings.EVENT_INGEST_HMAC_SECRET = SECRET
    settings.RATELIMIT_ENABLE = False
    # allowlist событий пуст (fail-closed) — как на стенде сегодня.
    settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset()
    settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset()

    from apps.eventbus import views as _views
    from apps.eventbus.ingest_dispatcher import dispatch_envelope as _direct

    monkeypatch.setattr(_views, "dispatch_with_timeout", _direct)

    import apps.eventbus.ingest_dispatcher as dispatcher_module
    from apps.eventbus.consumers.system import register_system_handlers

    snapshot = dict(dispatcher_module._REGISTRY)
    dispatcher_module._REGISTRY.clear()
    register_system_handlers()
    try:
        yield
    finally:
        dispatcher_module._REGISTRY.clear()
        dispatcher_module._REGISTRY.update(snapshot)


class TestTheWeakeningStaysNarrow:
    def test_a_system_event_without_a_user_parses(self) -> None:
        envelope = parse_envelope(json.dumps(_envelope()).encode())
        assert envelope.event_name == NAME
        assert envelope.user_id is None
        assert envelope.tenant_id is None

    def test_a_non_system_event_without_a_user_is_still_rejected(self) -> None:
        """Ослабление не расползается: `user_id` обязателен у всех прочих."""
        body = _envelope(
            event_name="booking.created",
            tenant_id=str(uuid.uuid4()),
            data={"booking_id": "b"},
        )
        with pytest.raises(IngestEnvelopeError) as exc:
            parse_envelope(json.dumps(body).encode())
        assert exc.value.reason == "missing_field"

    def test_a_system_prefixed_name_outside_the_closed_set_is_rejected(self) -> None:
        """Префикс — не пропуск: набор закрыт, а не шаблон `system.*`."""
        with pytest.raises(IngestEnvelopeError) as exc:
            parse_envelope(json.dumps(_envelope(event_name="system.anything.else")).encode())
        assert exc.value.reason == "invalid_event_name"

    def test_a_system_event_that_names_a_user_is_rejected(self) -> None:
        """У системы нет субъекта — событие, которое его называет, противоречит себе."""
        with pytest.raises(IngestEnvelopeError) as exc:
            parse_envelope(json.dumps(_envelope(user_id=str(uuid.uuid4()))).encode())
        # Причина — именно субъект, а не имя: иначе узел проходил бы и сегодня,
        # когда имя не пропускается вовсе, то есть по чужой причине.
        assert exc.value.reason == "invalid_user_id"

    def test_a_system_event_that_names_a_tenant_is_rejected(self) -> None:
        with pytest.raises(IngestEnvelopeError) as exc:
            parse_envelope(json.dumps(_envelope(tenant_id=str(uuid.uuid4()))).encode())
        assert exc.value.reason == "invalid_tenant_id"

    def test_a_system_event_from_a_user_actor_is_rejected(self) -> None:
        """Системный сигнал рождает система; `actor=user` — подделка формы."""
        with pytest.raises(IngestEnvelopeError) as exc:
            parse_envelope(json.dumps(_envelope(actor="user")).encode())
        assert exc.value.reason == "invalid_actor"


class TestTheSignatureIsTheWholeAuthorisation:
    def test_a_bad_signature_is_rejected(self, wired, client) -> None:
        body = json.dumps(_envelope()).encode()
        with patch("apps.eventbus.consumers.system.signal_budget") as signal:
            resp = _post(client, body, secret="not-the-secret")
        assert resp.status_code == 401
        assert signal.call_args_list == []

    def test_positive_pair_a_good_signature_is_accepted(self, wired, client) -> None:
        body = json.dumps(_envelope()).encode()
        with patch("apps.eventbus.consumers.system.signal_budget") as signal:
            resp = _post(client, body)
        assert resp.status_code == 200, resp.content
        assert len(signal.call_args_list) == 1

    def test_the_tenant_null_path_bypasses_the_event_allowlist_on_purpose(
        self, wired, client
    ) -> None:
        """Принято сознательно (§64): allowlist событий пуст — событие проходит.

        Путь `tenant_id=null` возвращается раньше, чем консультирует
        `EVENT_INGEST_ALLOWED_EVENTS`. Для системного события остаются
        только HMAC и закрытый набор имён. Узел делает решение видимым:
        сменить это — значит покраснеть здесь и решить заново.
        """
        body = json.dumps(_envelope()).encode()
        with patch("apps.eventbus.consumers.system.signal_budget"):
            resp = _post(client, body)
        assert resp.status_code == 200, resp.content


class TestTheConsumerFeedsTheCore:
    def test_the_numbers_reach_the_core(self, wired, client) -> None:
        body = json.dumps(_envelope()).encode()
        with patch("apps.eventbus.consumers.system.signal_budget") as signal:
            _post(client, body)

        (call,) = signal.call_args_list
        assert call.kwargs == {
            "used": 500,
            "limit": 500,
            "day": "2026-09-21",
            "cost_usd": None,
        }

    def test_a_redelivery_does_not_page_twice(self, wired, client) -> None:
        """Потребитель идемпотентен (ADR-0009, правило 7): тот же `event_id` — один раз."""
        body = json.dumps(_envelope()).encode()
        with patch("apps.eventbus.consumers.system.signal_budget") as signal:
            _post(client, body)
            _post(client, body)
        assert len(signal.call_args_list) == 1

    def test_an_unknown_module_is_acknowledged_not_paged(self, wired, client) -> None:
        """Чужой модуль — не страница и не мёртвое письмо.

        Имя события известно, модуль — нет. Мёртвое письмо на каждый новый
        модуль было бы шумом; страница по данным, которых ядро не понимает, —
        ложью. Принять, записать в лог, ничего не звать.
        """
        body = json.dumps(
            _envelope(data={"module_name": "somewhere.else", "severity": "warning", "metric": "x"})
        ).encode()
        with patch("apps.eventbus.consumers.system.signal_budget") as signal:
            resp = _post(client, body)
        assert resp.status_code == 200, resp.content
        assert signal.call_args_list == []


class TestTheClosedSetsAgree:
    def test_the_name_is_in_every_closed_set(self) -> None:
        """Четыре закрытых набора — одно имя; расхождение даёт 400 vs 422."""
        from apps.eventbus.ingest_dispatcher import _KNOWN_NAMES
        from apps.eventbus.ingest_envelope import (
            ALLOWED_EVENT_NAMES,
            SYSTEM_EVENT_NAMES,
            TENANT_NULLABLE_EVENT_NAMES,
        )
        from apps.eventbus.ingest_tenancy import _TENANT_NULLABLE_EVENT_NAMES

        assert NAME in SYSTEM_EVENT_NAMES
        assert NAME in ALLOWED_EVENT_NAMES
        assert NAME in _KNOWN_NAMES
        assert NAME in TENANT_NULLABLE_EVENT_NAMES
        assert NAME in _TENANT_NULLABLE_EVENT_NAMES
