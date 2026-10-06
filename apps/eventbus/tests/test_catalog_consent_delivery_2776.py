"""Смена согласия доезжает до каталога, а живой режим без доставщика отказывает (DRF-2776).

Решение владельца D от 05.10: смену согласия получают системы, чьё поведение
от неё зависит; журнала мало. Накопленные события владелец решил доставить
задним числом — значит, до появления доставщика их нельзя пометить
«доставленными».

Три предмета, у каждого свои узлы:

* **клиент** ``consent_events_client`` — запрос и чтение ответа ровно по
  контракту, объявленному каталогом (окно ayla-00): субъект в заголовке,
  ``customer_id`` в теле нет; любой ``outcome`` из пяти — успех; 4xx —
  постоянный отказ; 5xx и сеть — временный; ``200`` без конверта —
  нарушение контракта, а не успех;
* **подписчик** ``CatalogConsentSubscriber`` — шлёт только смену согласия,
  остальное пропускает без исключения; пишет журнал доставки, не отнимая у
  журнала само изменение; стёртый ``BotUser`` — «доставлено, субъекта нет»;
  отказ каталога оставляет строку ящика недоставленной;
* **сторож живого режима** — отказывает, пока смена согласия лежит без
  доставщика; события без потребителя (``booking.created``) не держит.

HTTP перехватывается на уровне транспорта (``httpx.MockTransport``): клиент
работает своим настоящим кодом, сокет не открывается. Ответы каталога —
дословно по объявленному контракту.

``transaction=True``: диспетчер берёт строки ``select_for_update(skip_locked=True)``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from apps.audit.models import AuditLog
from apps.eventbus import dispatcher, services, vocabulary as V
from apps.eventbus.dispatcher import (
    dispatch_pending_events,
    dispatch_pending_events_beat,
    undelivered_required_names,
)
from apps.eventbus.models import DomainEvent
from apps.identity.models import BotUser
from apps.integrations.ayla.consent_events_client import (
    ConsentEventContractViolation,
    ConsentEventRejected,
    ConsentEventUnavailable,
    post_consent_event,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)

BASE = "https://ayla.test"
TOKEN = "consent-events-test-token"  # noqa: S105  # pragma: allowlist secret
AUDIT = "apps.eventbus.subscribers.AuditSubscriber"
LOYALTY = "apps.loyalty.subscribers.LoyaltySubscriber"
CONSENT = "apps.eventbus.subscribers.CatalogConsentSubscriber"


class _Catalog:
    """Ручка ``internal/me/consent-events/`` в форме, объявленной каталогом."""

    def __init__(self, *, status: int = 200, body: Any = None, raise_exc: Exception | None = None):
        self.status = status
        self.body = body
        self.raise_exc = raise_exc
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.body is not None:
            return httpx.Response(self.status, json=self.body)
        sent = json.loads(request.content)
        return httpx.Response(
            self.status,
            json={"data": {"event_id": sent["event_id"], "outcome": "applied", "erased": []}},
        )


@pytest.fixture
def catalog(settings, monkeypatch):
    """Подменяет транспорт httpx; по умолчанию каталог отвечает ``applied``."""
    settings.AYLA_BASE_URL = BASE
    settings.AYLA_INTERNAL_API_TOKEN = TOKEN
    fake = _Catalog()
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda r: fake.handler(r))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    return fake


@pytest.fixture(autouse=True)
def _registry_reset():
    dispatcher.reset_registry_cache()
    yield
    dispatcher.reset_registry_cache()


def _bot_user(slug: str = "consent-delivery") -> BotUser:
    tenant = Tenant.objects.create(slug=slug, name=slug)
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id=slug)


def _emit_withdrawal(bot_user: BotUser, ctype: str = "personal_calculation") -> DomainEvent:
    services.emit_customer_consent_changed(
        customer_id=str(bot_user.id),
        consent_type=ctype,
        granted=False,
        granted_at="2026-10-05T10:00:00+00:00",
        granted_via="miniapp:settings",
        tenant=bot_user.tenant,
    )
    return DomainEvent.objects.filter(event_name=V.CUSTOMER_CONSENT_CHANGED).latest("created_at")


def _emit_booking(i: int = 1) -> None:
    services.emit(
        V.BOOKING_CREATED,
        {
            "booking_id": f"cd-b{i}",
            "customer_id": f"cd-c{i}",
            "service_id": "s",
            "slot_start": "2026-10-05T10:00:00Z",
            "booking_source": "ai_direct",
        },
        actor_type="system",
    )


BODY = {
    "event_id": "01J9CONSENTEVENTTEST00001",
    "consent_type": "personal_calculation",
    "granted": False,
    "granted_at": "2026-10-05T10:00:00+00:00",
}


# ─── клиент: запрос ──────────────────────────────────────────────────────────


def test_the_request_names_the_subject_in_the_header_only(catalog) -> None:
    post_consent_event(external_user_id="bot:max:42", body=dict(BODY))

    (request,) = catalog.requests
    assert request.method == "POST"
    assert request.url.path == "/api/v1/internal/me/consent-events/"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["X-External-User-ID"] == "bot:max:42"
    sent = json.loads(request.content)
    assert sent == BODY
    assert "customer_id" not in sent


# ─── клиент: чтение ответа ───────────────────────────────────────────────────


@pytest.mark.parametrize("outcome", ["applied", "duplicate", "ignored", "stale", "no_subject"])
def test_every_declared_outcome_is_delivered(catalog, outcome) -> None:
    catalog.body = {"data": {"event_id": BODY["event_id"], "outcome": outcome, "erased": []}}

    receipt = post_consent_event(external_user_id="bot:max:42", body=dict(BODY))

    assert receipt.outcome == outcome


#: Живые ответы каталога — сняты окном ayla-00 исполнением на голове его
#: PR #646 (тестовый клиент Django, синтетические event_id), не придуманы.
LIVE_APPLIED_HEALTH = {
    "data": {
        "event_id": "01JAEXAMPLE0000000000000002",
        "outcome": "applied",
        "erased": [
            "health_flags",
            "bmr",
            "daily_kcal",
            "daily_protein_g",
            "daily_fat_g",
            "daily_carbs_g",
            "daily_vitamin_d_iu",
            "daily_vitamin_b12_mcg",
            "daily_vitamin_c_mg",
            "daily_iron_mg",
            "daily_calcium_mg",
            "daily_magnesium_mg",
            "daily_omega3_g",
            "daily_fiber_g",
            "calories_source",
            "calories_confirmed_at",
            "pending_proposal",
            "last_overrides_applied",
        ],
    }
}
LIVE_IGNORED = {
    "data": {"event_id": "01JAEXAMPLE0000000000000003", "outcome": "ignored", "erased": []}
}
LIVE_NO_SUBJECT = {
    "data": {"event_id": "01JAEXAMPLE0000000000000004", "outcome": "no_subject", "erased": []}
}
LIVE_BAD_FORM = {
    "error": {
        "code": "VALIDATION_ERROR",
        "message": "invalid consent event",
        "details": {"granted": ["granted must be a JSON boolean"]},
    }
}


def test_erased_field_names_come_back_in_the_receipt(catalog) -> None:
    catalog.body = LIVE_APPLIED_HEALTH

    receipt = post_consent_event(external_user_id="bot:max:42", body=dict(BODY))

    assert receipt.outcome == "applied"
    assert receipt.erased == tuple(LIVE_APPLIED_HEALTH["data"]["erased"])


@pytest.mark.parametrize("live", [LIVE_IGNORED, LIVE_NO_SUBJECT], ids=["ignored", "no_subject"])
def test_live_success_answers_are_delivered(catalog, live) -> None:
    catalog.body = live

    receipt = post_consent_event(external_user_id="bot:max:42", body=dict(BODY))

    assert receipt.outcome == live["data"]["outcome"]
    assert receipt.erased == ()


def test_the_live_validation_error_is_a_permanent_refusal(catalog) -> None:
    catalog.status, catalog.body = 400, LIVE_BAD_FORM

    with pytest.raises(ConsentEventRejected) as exc:
        post_consent_event(external_user_id="bot:max:42", body=dict(BODY))
    assert exc.value.body == LIVE_BAD_FORM


def test_throttling_is_temporary_not_a_refusal(catalog) -> None:
    """Своё ведро ручки 300/мин: разбор 65 накопленных может в него упереться."""
    catalog.status, catalog.body = 429, {"error": {"code": "THROTTLED"}}

    with pytest.raises(ConsentEventUnavailable):
        post_consent_event(external_user_id="bot:max:42", body=dict(BODY))


@pytest.mark.parametrize("status", [400, 401, 403])
def test_a_refusal_is_permanent(catalog, status) -> None:
    catalog.status, catalog.body = status, {"error": "nope"}

    with pytest.raises(ConsentEventRejected) as exc:
        post_consent_event(external_user_id="bot:max:42", body=dict(BODY))
    assert exc.value.status == status


def test_a_server_error_is_temporary(catalog) -> None:
    catalog.status, catalog.body = 503, {"error": "down"}

    with pytest.raises(ConsentEventUnavailable):
        post_consent_event(external_user_id="bot:max:42", body=dict(BODY))


def test_a_network_failure_is_temporary(catalog) -> None:
    catalog.raise_exc = httpx.ConnectError("refused")

    with pytest.raises(ConsentEventUnavailable):
        post_consent_event(external_user_id="bot:max:42", body=dict(BODY))


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"data": {}},
        {"data": {"outcome": "done"}},
        {"outcome": "applied"},
        {"data": {"outcome": "applied", "erased": "weight_kg"}},
    ],
    ids=["empty", "no-outcome", "unknown-outcome", "no-envelope", "erased-not-a-list"],
)
def test_a_200_off_contract_is_a_violation_not_a_success(catalog, body) -> None:
    catalog.body = body

    with pytest.raises(ConsentEventContractViolation):
        post_consent_event(external_user_id="bot:max:42", body=dict(BODY))


def test_unconfigured_catalog_is_unavailable_not_a_crash(settings) -> None:
    settings.AYLA_BASE_URL = ""

    with pytest.raises(ConsentEventUnavailable):
        post_consent_event(external_user_id="bot:max:42", body=dict(BODY))


# ─── подписчик ───────────────────────────────────────────────────────────────


def test_the_subscriber_delivers_a_withdrawal_and_journals_the_result(settings, catalog) -> None:
    settings.DOMAIN_EVENT_SUBSCRIBERS = [CONSENT, AUDIT]
    catalog.body = None
    bot_user = _bot_user()
    row = _emit_withdrawal(bot_user)

    counters = dispatch_pending_events()

    assert counters["dispatched"] == 1
    (request,) = catalog.requests
    assert request.headers["X-External-User-ID"] == f"bot:max:{bot_user.channel_user_id}"
    sent = json.loads(request.content)
    assert sent["event_id"] == row.event_id
    assert (sent["consent_type"], sent["granted"]) == ("personal_calculation", False)
    assert sent["granted_via"] == "miniapp:settings"
    assert "customer_id" not in sent, "UUID BotUser каталогу не нужен — субъект в заголовке"
    delivery = AuditLog.all_tenants.get(action="consent.delivery.catalog")
    assert delivery.payload["consent_event_id"] == row.event_id
    assert delivery.payload["outcome"] == "applied"
    # Журнал самого изменения не отнят — хотя доставка стоит в реестре первой.
    assert (
        AuditLog.all_tenants.filter(
            action=V.CUSTOMER_CONSENT_CHANGED, payload__event_id=row.event_id
        ).count()
        == 1
    )


def test_other_events_are_not_sent_and_do_not_fail(settings, catalog) -> None:
    settings.DOMAIN_EVENT_SUBSCRIBERS = [CONSENT]
    _emit_booking()

    counters = dispatch_pending_events()

    assert counters["dispatched"] == 1
    assert catalog.requests == []
    assert not AuditLog.all_tenants.filter(action="consent.delivery.catalog").exists(), (
        "чужое событие получило строку журнала доставки согласия"
    )


def test_an_erased_bot_user_is_delivered_without_a_request(settings, catalog) -> None:
    settings.DOMAIN_EVENT_SUBSCRIBERS = [CONSENT]
    bot_user = _bot_user("consent-erased")
    row = _emit_withdrawal(bot_user)
    BotUser.all_tenants.filter(pk=bot_user.pk).delete()

    counters = dispatch_pending_events()

    assert counters["dispatched"] == 1
    assert catalog.requests == []
    delivery = AuditLog.all_tenants.get(action="consent.delivery.catalog")
    assert (delivery.payload["consent_event_id"], delivery.payload["outcome"]) == (
        row.event_id,
        "no_subject_on_bot",
    )


@pytest.mark.parametrize("status", [503, 400])
def test_a_catalog_failure_leaves_the_row_undelivered(settings, catalog, status) -> None:
    settings.DOMAIN_EVENT_SUBSCRIBERS = [CONSENT]
    catalog.status, catalog.body = status, {"error": "x"}
    row = _emit_withdrawal(_bot_user("consent-fail"))

    counters = dispatch_pending_events()

    row.refresh_from_db()
    assert counters["failed"] == 1
    assert not row.is_dispatched
    assert row.dispatch_attempts == 1
    assert str(status) in row.last_error
    assert not AuditLog.all_tenants.filter(action="consent.delivery.catalog").exists()


def test_a_redelivery_writes_one_journal_row(settings, catalog) -> None:
    """Повтор доставки (at-least-once) — одна строка журнала, каталог ответит ``duplicate``."""
    from apps.eventbus.envelope import Envelope
    from apps.eventbus.subscribers import CatalogConsentSubscriber

    row = _emit_withdrawal(_bot_user("consent-redeliver"))
    sub = CatalogConsentSubscriber()

    sub.handle(Envelope.from_row(row))
    catalog.body = {"data": {"event_id": row.event_id, "outcome": "duplicate", "erased": []}}
    sub.handle(Envelope.from_row(row))

    assert len(catalog.requests) == 2
    assert AuditLog.all_tenants.filter(action="consent.delivery.catalog").count() == 1


# ─── сторож живого режима ────────────────────────────────────────────────────


@pytest.fixture
def live(settings):
    settings.EVENTBUS_DISPATCH_BEAT_ENABLED = True
    settings.EVENTBUS_DISPATCH_BEAT_DRY_RUN = False
    return settings


def test_live_mode_refuses_while_consent_has_no_deliverer(live, catalog) -> None:
    """Журнал + лояльность — настоящие подписчики, но смену согласия не доставляют."""
    live.DOMAIN_EVENT_SUBSCRIBERS = [AUDIT, LOYALTY]
    _emit_withdrawal(_bot_user("consent-guard"))
    _emit_booking()

    result = dispatch_pending_events_beat()

    assert result == {
        "mode": "refused_undelivered",
        "pending": 2,
        "names": [V.CUSTOMER_CONSENT_CHANGED],
    }
    assert DomainEvent.objects.filter(is_dispatched=True).count() == 0
    assert catalog.requests == []


def test_an_event_without_any_consumer_does_not_hold_the_box(live, catalog) -> None:
    """Владелец: событие без потребителя не задерживает независимые."""
    live.DOMAIN_EVENT_SUBSCRIBERS = [AUDIT, LOYALTY]
    _emit_booking(1)
    _emit_booking(2)

    result = dispatch_pending_events_beat()

    assert result["mode"] == "live"
    assert result["dispatched"] == 2


def test_with_the_deliverer_in_the_registry_live_mode_delivers(live, catalog) -> None:
    live.DOMAIN_EVENT_SUBSCRIBERS = [AUDIT, CONSENT]
    _emit_withdrawal(_bot_user("consent-live"))

    assert undelivered_required_names() == []
    result = dispatch_pending_events_beat()

    assert result["mode"] == "live"
    assert result["dispatched"] == 1
    assert len(catalog.requests) == 1


def test_dry_run_is_untouched_by_the_new_refusal(settings, catalog) -> None:
    settings.EVENTBUS_DISPATCH_BEAT_ENABLED = True
    settings.EVENTBUS_DISPATCH_BEAT_DRY_RUN = True
    settings.DOMAIN_EVENT_SUBSCRIBERS = [AUDIT, LOYALTY]
    _emit_withdrawal(_bot_user("consent-dry"))

    assert dispatch_pending_events_beat() == {"mode": "dry_run", "pending": 1}


def test_the_journal_alone_is_not_a_deliverer(settings) -> None:
    settings.DOMAIN_EVENT_SUBSCRIBERS = [AUDIT]
    _emit_withdrawal(_bot_user("consent-journal"))

    assert undelivered_required_names() == [V.CUSTOMER_CONSENT_CHANGED]


def test_an_unknown_bot_user_id_shape_is_handled(settings, catalog) -> None:
    """``customer_id`` не UUID (старые строки) — как стёртый субъект, без падения."""
    from apps.eventbus.envelope import Envelope
    from apps.eventbus.subscribers import CatalogConsentSubscriber

    services.emit_customer_consent_changed(
        customer_id="not-a-uuid",
        consent_type="health",
        granted=False,
        granted_at="2026-10-05T10:00:00+00:00",
    )
    row = DomainEvent.objects.get(event_name=V.CUSTOMER_CONSENT_CHANGED)

    CatalogConsentSubscriber().handle(Envelope.from_row(row))

    assert catalog.requests == []
    delivery = AuditLog.all_tenants.get(action="consent.delivery.catalog")
    assert delivery.payload["outcome"] == "no_subject_on_bot"
