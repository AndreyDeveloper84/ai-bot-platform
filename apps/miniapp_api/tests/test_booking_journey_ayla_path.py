"""Путь записи целиком на пути Ayla — create → lookup → cancel, одним узлом.

## Зачем этот файл, если глаголы уже покрыты

Покрыты — порознь: create в трёх файлах, lookup и cancel в
``test_ayla_read_model.py``. Но ни один собираемый узел не проходит ПУТЬ:
связка «записался → увидел → отменил» жила только в ручном харнессе
``tests/acceptance/drf916_e2e.py``, который pytest не собирает (имя не
матчится ``python_files``, README называет это прямо). Регрессию пути, а
не глагола, не ловил никто. Ворота DRF-916 закрыты 08.08 на дереве
``4406c0c6``; с тех пор 706 коммитов и 110 файлов в этом контуре.

## Почему глаголов три, а не четыре

На пути Ayla переноса в мини-аппе НЕТ: оба endpoint'а отвечают
``409 invalid_state`` (DRF-1349), и докстринг говорит «the seam itself is
genuinely absent, not merely unrouted». Августовский харнесс переносил
через приложение Ayla с OTP — другой продукт. Мок, «умеющий» перенос,
которого нет, узаконил бы ложь; поэтому перенос здесь проверяется
отдельным узлом как честный 409, а не как четвёртый глагол.

## Почему середина пути идёт через настоящий ingest

На пути Ayla create не пишет ``BookingRequest``; lookup и cancel читают
``RemoteBookingProxy``, а его «пишут ТОЛЬКО потребители событий — никогда
эти views» (``views.py``). Значит между create и lookup стоит шов
EventBus, и харнесс пересекал его ожиданием до 120 секунд. Здесь шов
пересекается ЧЕСТНО: событие ``booking.created`` подписывается HMAC и
уходит в настоящий ``/api/v1/internal/events/ingest`` с настоящим
реестром обработчиков — рецепт взят у ``test_e2e_ingest_smoke.py``.
Вставить строку прокси руками значило бы подделать путь ровно в том
месте, где он рвётся.

Присутствие шва доказывается отдельно: ДО ingest список пуст, ПОСЛЕ —
запись есть. Без этой пары зелёный узел не отличал бы «шов пройден» от
«шва не было».

## Род

Это узел CI против стаба Ayla. Он ловит регрессию ПУТИ на стороне бота.
Он НЕ доказывает, что живой стенд отвечает так же, и не заменяет ручной
прогон харнесса с мастером, заведённым оператором.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from datetime import datetime, timedelta
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.utils import timezone as dj_timezone

from apps.booking.models import RemoteBookingProxy
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import AylaBookingRecord
from apps.integrations.ayla.identity_client import ResolvedIdentity
from apps.tenancy.models import Tenant
from tests.support.catalog_mirror import sync_shaped

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-journey"  # noqa: S105 — test fixture  # pragma: allowlist secret
INGEST_SECRET = "journey-ingest-secret"  # pragma: allowlist secret
INGEST_URL = "/api/v1/internal/events/ingest"
SALON_TZ = ZoneInfo("Europe/Moscow")

#: Идентификаторы фиксированы, потому что потребитель резолвит тенанта
#: по ``Tenant.id`` из события, а человека — по ``(tenant, ayla_user_id)``.
#: Случайные значения потребовали бы читать их обратно в событие; так
#: событие и посев говорят об одном и том же по построению.
TENANT_ID = uuid.UUID("11111111-2222-4333-8444-555555555555")
AYLA_UID = uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
MASTER_AYLA_ID = uuid.uuid4()
SERVICE_AYLA_ID = uuid.uuid4()


# ── MaxInitData ───────────────────────────────────────────────────────────────


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str = "12345") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Мария"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


def _visit_at() -> datetime:
    # Относительная дата: прибитая к календарю однажды стала прошлым и
    # уронила три теста, к которым никто не прикасался.
    return (datetime.now(SALON_TZ) + timedelta(days=7)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )


# ── EventBus ingest ───────────────────────────────────────────────────────────


def _ingest(client: Client, envelope: dict) -> Any:
    body = json.dumps(envelope).encode()
    ts_ms = str(int(time_module.time() * 1000))
    sig = "sha256=" + hmac.new(INGEST_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        INGEST_URL,
        data=body,
        content_type="application/json",
        HTTP_X_AYLA_EVENT_SIGNATURE=sig,
        HTTP_X_AYLA_EVENT_TIMESTAMP=ts_ms,
    )


def _envelope(name: str, appointment_id: str, data: dict) -> dict:
    """Конверт по контракту ``tests/fixtures/contracts/<name>.v1.json``."""
    return {
        "event_id": str(uuid.uuid4()).replace("-", "")[:26].upper(),
        "event_name": name,
        "event_version": 1,
        "occurred_at": dj_timezone.now().isoformat().replace("+00:00", "Z"),
        "tenant_id": str(TENANT_ID),
        "user_id": str(AYLA_UID),
        "actor": "user",
        "correlation_id": str(uuid.uuid4()),
        "causation_id": None,
        "data": {"appointment_id": appointment_id, **data},
    }


# ── стаб Ayla — ОДИН на весь путь ─────────────────────────────────────────────


class _JourneyAylaClient:
    """Единый стаб на create и cancel.

    Существующие стабы приватны в своих файлах (``_StubAylaClient`` умеет
    только create, ``_StubCancelClient`` — только cancel). Пути нужен
    один объект, который помнит оба вызова: иначе «отменили ту же запись,
    что создали» не утверждается.
    """

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.cancelled: list[dict] = []

    def create_appointment(self, **kwargs):
        self.created.append(kwargs)
        appointment_id = str(uuid.uuid4())
        return AylaBookingRecord(
            appointment_id=appointment_id,
            raw={"id": appointment_id, "status": "confirmed"},
        )

    def cancel_appointment(self, **kwargs):
        self.cancelled.append(kwargs)
        return True


# ── фикстуры ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _wired(settings, monkeypatch) -> Iterator[None]:
    """Мини-апп на пути Ayla + настоящий ingest с настоящими обработчиками.

    Проводка ingest взята у ``test_e2e_ingest_smoke.py`` дословно: тот же
    секрет, отключённый ratelimit, прямой диспетчер вместо threadpool
    (SQLite в тестах на нём зависает) и ПРОИЗВОДСТВЕННЫЙ реестр
    обработчиков booking.* — не тестовые заглушки.
    """
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = "journey-salon"
    settings.BOOKING_VIA_AYLA_REST = True

    settings.EVENT_INGEST_HMAC_SECRET = INGEST_SECRET
    settings.RATELIMIT_ENABLE = False
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True

    from apps.eventbus import views as _views
    from apps.eventbus.ingest_dispatcher import dispatch_envelope as _direct

    monkeypatch.setattr(_views, "dispatch_with_timeout", _direct)

    import apps.eventbus.ingest_dispatcher as dispatcher_module
    from apps.eventbus.consumers.booking import register_booking_handlers

    snapshot = dict(dispatcher_module._REGISTRY)
    dispatcher_module._REGISTRY.clear()
    register_booking_handlers()
    try:
        yield
    finally:
        dispatcher_module._REGISTRY.clear()
        dispatcher_module._REGISTRY.update(snapshot)


@pytest.fixture
def ayla(monkeypatch) -> _JourneyAylaClient:
    stub = _JourneyAylaClient()
    monkeypatch.setattr(
        "apps.integrations.ayla.booking_client.get_ayla_booking_client",
        lambda: stub,
    )
    return stub


@pytest.fixture
def stub_resolve(monkeypatch) -> None:
    """Плечо идентичности (DRF-1057) — человек уже связан, резолв не ходит."""

    def _fake(external_user_id: str) -> ResolvedIdentity:
        return ResolvedIdentity(ayla_user_id=AYLA_UID, is_proxy=False)

    monkeypatch.setattr(
        "apps.integrations.ayla.identity_client.resolve_identity", _fake, raising=True
    )


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(id=TENANT_ID, slug="journey-salon", name="Салон пути")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    person = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="12345",
        chat_id="12345",
        ayla_user_id=AYLA_UID,
        ayla_user_id_is_proxy=False,
    )
    # Как в ingest-smoke: у потребителя есть, кому написать.
    Conversation.all_tenants.create(
        tenant=tenant,
        bot_user=person,
        state=Conversation.State.IDLE,
        last_message_at=dj_timezone.now(),
    )
    return person


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return sync_shaped(
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_updated_at=dj_timezone.now(),
            name="Ольга",
            specialization="Маникюр",
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            ayla_user_id=MASTER_AYLA_ID,
        )
    )


@pytest.fixture
def service(tenant: Tenant, master: CatalogMaster) -> CatalogService:
    svc = CatalogService.all_tenants.create(
        tenant=tenant,
        external_updated_at=dj_timezone.now(),
        name="Маникюр",
        slug="manikyur",
        duration_min=60,
        is_active=True,
        ayla_service_id=SERVICE_AYLA_ID,
    )
    # DRF-1164: услуга без исполнителя отвергается ДО ветки Ayla.
    MasterService.all_tenants.create(tenant=tenant, master=master, service=svc)
    return svc


@pytest.fixture
def client() -> Client:
    return Client()


# ── шаги пути ─────────────────────────────────────────────────────────────────


def _create(client: Client, service: CatalogService, master: CatalogMaster):
    return client.post(
        "/api/v1/customer/bookings",
        data=json.dumps(
            {
                "service_id": str(service.id),
                "master_id": str(master.id),
                "visit_at": _visit_at().isoformat(),
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header(),
    )


def _list(client: Client):
    return client.get("/api/v1/customer/bookings/list", HTTP_AUTHORIZATION=_init_data_header())


def _detail(client: Client, appointment_id: str):
    return client.get(
        f"/api/v1/customer/bookings/{appointment_id}", HTTP_AUTHORIZATION=_init_data_header()
    )


def _cancel(client: Client, appointment_id: str):
    return client.post(
        f"/api/v1/customer/bookings/{appointment_id}/cancel",
        data="{}",
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header(),
    )


def _reschedule(
    client: Client, appointment_id: str, master: CatalogMaster, service: CatalogService
):
    return client.post(
        f"/api/v1/customer/bookings/{appointment_id}/reschedule",
        data=json.dumps(
            {
                "new_master_id": str(master.id),
                "new_service_id": str(service.id),
                "new_visit_at": (_visit_at() + timedelta(days=1)).isoformat(),
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header(),
    )


def _ids_in_list(resp) -> set[str]:
    body = resp.json()
    items = body.get("bookings") or body.get("items") or []
    return {str(item.get("id")) for item in items}


def _walk_to_visible(client: Client, ayla: _JourneyAylaClient, service, master) -> str:
    """create → ingest created → запись видна. Возвращает appointment_id.

    Вынесено, потому что узел про перенос стартует с того же места:
    перенос имеет смысл спрашивать только у записи, которую человек видит.
    """
    resp = _create(client, service, master)
    assert resp.status_code == 201, resp.content[:300]
    appointment_id = str(resp.json()["booking"]["id"])
    assert len(ayla.created) == 1

    ingest = _ingest(
        client,
        _envelope(
            "booking.created",
            appointment_id,
            {
                "specialist_id": str(MASTER_AYLA_ID),
                "service_id": str(SERVICE_AYLA_ID),
                "start_at": _visit_at().isoformat(),
                "end_at": (_visit_at() + timedelta(hours=1)).isoformat(),
                "status": "confirmed",
                "price_total": "1800.00",
                "source": "mobile_app",
            },
        ),
    )
    assert ingest.status_code in (200, 202), ingest.content[:300]
    assert appointment_id in _ids_in_list(_list(client))
    return appointment_id


class TestTheJourney:
    def test_create_then_lookup_then_cancel_as_one_story(
        self,
        client: Client,
        ayla: _JourneyAylaClient,
        stub_resolve: None,
        bot_user: BotUser,
        service: CatalogService,
        master: CatalogMaster,
    ) -> None:
        # 1. CREATE — через стаб Ayla, ответ несёт канонический id.
        resp = _create(client, service, master)
        assert resp.status_code == 201, resp.content[:300]
        appointment_id = str(resp.json()["booking"]["id"])
        assert len(ayla.created) == 1

        # 2. ШОВ СУЩЕСТВУЕТ: до события записи в списке НЕТ. Это не
        #    придирка — без этой строки узел не отличал бы «шов пройден»
        #    от «шва не было» (прокси пишут только потребители событий).
        #
        #    ПРИСУТСТВИЕ впереди — и не «200», а СОДЕРЖИМОЕ: пустое тело
        #    тоже 200 (это и был DRF-1406, сторож 200 не считает). Поэтому
        #    в списке заранее лежит ЧУЖАЯ, давняя запись этого же человека:
        #    она доказывает, что список отдаёт строки вообще, и только
        #    тогда «новой там нет» перестаёт быть пустым утверждением.
        #    Фоновая строка — фикстура, а не подделка шва: запись пути
        #    по-прежнему появится только событием.
        prior_id = str(uuid.uuid4())
        RemoteBookingProxy.all_tenants.create(
            appointment_id=prior_id,
            tenant=bot_user.tenant,
            bot_user=bot_user,
            start_at=_visit_at() + timedelta(days=3),
            end_at=_visit_at() + timedelta(days=3, hours=1),
            status="confirmed",
        )
        ids_before = _ids_in_list(_list(client))
        assert prior_id in ids_before, "список не отдаёт даже фоновую запись"
        assert appointment_id not in ids_before
        assert not RemoteBookingProxy.all_tenants.filter(appointment_id=appointment_id).exists()

        # 3. INGEST booking.created — настоящий endpoint, настоящий
        #    обработчик, HMAC.
        ingest = _ingest(
            client,
            _envelope(
                "booking.created",
                appointment_id,
                {
                    "specialist_id": str(MASTER_AYLA_ID),
                    "service_id": str(SERVICE_AYLA_ID),
                    "start_at": _visit_at().isoformat(),
                    "end_at": (_visit_at() + timedelta(hours=1)).isoformat(),
                    "status": "confirmed",
                    "price_total": "1800.00",
                    "source": "mobile_app",
                },
            ),
        )
        assert ingest.status_code in (200, 202), ingest.content[:300]

        # 4. LOOKUP — список и деталь читают прокси, и он привязан к ЭТОМУ
        #    человеку (иначе cancel ниже ответит 404 «не найдено»).
        assert appointment_id in _ids_in_list(_list(client))
        detail = _detail(client, appointment_id)
        assert detail.status_code == 200, detail.content[:300]
        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=appointment_id)
        assert proxy.bot_user_id == bot_user.id
        assert proxy.status == "confirmed"

        # 5. CANCEL — уходит в Ayla на ТУ ЖЕ запись; прокси view НЕ трогает
        #    (dual-write запрещён), поэтому статус пока прежний.
        cancel = _cancel(client, appointment_id)
        assert cancel.status_code == 200, cancel.content[:300]
        assert len(ayla.cancelled) == 1
        assert ayla.cancelled[0]["appointment_id"] == appointment_id
        proxy.refresh_from_db()
        assert proxy.status == "confirmed", "view переписал прокси мимо события"

        # 6. INGEST booking.cancelled — и только теперь запись закрыта.
        done = _ingest(
            client,
            _envelope(
                "booking.cancelled",
                appointment_id,
                {
                    "cancelled_by": "user",
                    "reason_code": "user_changed_plans",
                    "cancelled_at": dj_timezone.now().isoformat().replace("+00:00", "Z"),
                },
            ),
        )
        assert done.status_code in (200, 202), done.content[:300]
        proxy.refresh_from_db()
        assert proxy.status == RemoteBookingProxy.Status.CANCELLED


class TestRescheduleIsHonestlyAbsentOnTheAylaPath:
    def test_reschedule_answers_409_not_404(
        self,
        client: Client,
        ayla: _JourneyAylaClient,
        stub_resolve: None,
        bot_user: BotUser,
        service: CatalogService,
        master: CatalogMaster,
    ) -> None:
        """Перенос на пути Ayla отвечает 409 invalid_state — DRF-1349.

        Отдельный узел, а не четвёртый шаг пути: мок, «умеющий» перенос,
        которого нет, узаконил бы ложь. Проверяется не просто «не 200», а
        именно 409 против 404: докстринг гейта говорит, что без него оба
        endpoint'а отвечали бы «booking not found» на запись, которую
        человек видит в своём списке, — ложь того же рода, от которой
        закрыт cancel.
        """
        appointment_id = _walk_to_visible(client, ayla, service, master)

        # ПРИСУТСТВИЕ: запись видна человеку — значит 404 был бы ложью.
        assert _detail(client, appointment_id).status_code == 200

        resp = _reschedule(client, appointment_id, master, service)
        assert resp.status_code == 409, resp.content[:300]
        assert resp.json()["error"] == "invalid_state"
        # И клиенту это сказано заранее, а не только на попытке.
        assert _detail(client, appointment_id).json()["booking"]["reschedulable"] is False
