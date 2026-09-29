"""Переход в запись не заземляет непродаваемое ребро (DRF-1964a).

Тап по услуге, чьё ребро у мастера ``sellable=false``, не уходит в запись
(запись отклонила бы 422) и не называется «консультацией»: для продажи ребра
нет — ответ тот же, что «мастер эту услугу не выполняет», с реальными
услугами кнопками. Меню услуг мастера непродаваемое не предлагает.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.services import resolve_or_create_global_bot_user
from apps.orchestrator.discovery import DiscoveryReply
from apps.orchestrator.handoff import handoff_to_booking
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

_TS = datetime(2026, 9, 15, tzinfo=timezone.utc)


def _tenant(slug: str) -> Tenant:
    return Tenant.objects.create(slug=slug, name=slug, timezone="Europe/Moscow", city="Пенза")


def _master(tenant: Tenant, name: str = "Инна") -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=_TS,
        name=name,
        specialization="массаж",
        ayla_user_id=uuid4(),
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )


def _service(tenant: Tenant, name: str, slug: str) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        slug=slug,
        name=name,
        is_active=True,
        ayla_service_id=uuid4(),
        external_updated_at=_TS,
    )


def _link(
    tenant: Tenant, master: CatalogMaster, service: CatalogService, *, sellable: bool = True
) -> MasterService:
    edge = MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    if not sellable:
        assert {"sellable", "unsellable_reason"} <= {
            f.name for f in MasterService._meta.get_fields()
        }, "у MasterService нет колонок sellable / unsellable_reason"
        MasterService.all_tenants.filter(pk=edge.pk).update(
            sellable=False, unsellable_reason="price_below_minimum"
        )
    return edge


def _buttons(reply: DiscoveryReply) -> list[dict[str, str]]:
    for attachment in (reply.action_data or {}).get("attachments", []):
        if attachment.get("type") == "inline_keyboard":
            return list(attachment["payload"]["buttons"])
    return []


def test_tap_on_unsellable_edge_is_not_dispatched_and_reads_as_not_offered(settings, monkeypatch):
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    t = _tenant("t-sell-tap")
    master = _master(t)
    back = _service(t, "Классический массаж", "c")
    neck = _service(t, "Массаж шейно-воротниковой зоны", "n")
    _link(t, master, back)
    _link(t, master, neck, sellable=False)
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="1988")
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "apps.orchestrator.handoff.emit",
        lambda name, payload=None, **kw: events.append((name, payload or {})),
    )
    called: list = []
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: called.append(1))

    reply = handoff_to_booking(
        global_bot_user=gbu, tenant_id=t.id, master_id=master.id, service_id=neck.id
    )

    assert called == [], "непродаваемое ребро ушло в запись"
    assert "нет услуги «Массаж шейно-воротниковой зоны»" in reply.text
    assert "консультац" not in reply.text.lower()
    assert [b["label"] for b in _buttons(reply)] == ["Классический массаж"]
    assert dict(events)["marketplace.handoff.service_unresolved"]["not_offered_by_master"] is True


def test_service_menu_does_not_offer_unsellable_edge(settings, monkeypatch):
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    t = _tenant("t-sell-menu")
    master = _master(t, "Анна")
    _link(t, master, _service(t, "Спортивный массаж", "s"))
    _link(t, master, _service(t, "Массаж шейно-воротниковой зоны", "n"), sellable=False)
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="1989")
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: None)

    reply = handoff_to_booking(global_bot_user=gbu, tenant_id=t.id, master_id=master.id)

    assert [b["label"] for b in _buttons(reply)] == ["Спортивный массаж"]
    assert "Массаж шейно-воротниковой зоны" not in reply.text
