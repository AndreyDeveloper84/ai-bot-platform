"""DRF-2826 — the receptionist runs the booking desk of her salon (bot half).

Owner's decision 06.10: the front desk sees the schedule and runs the
booking cycle — create, reschedule, cancel, complete, no-show; finds the
client; picks the master. She does NOT touch master availability (time
off, date exceptions, weekly template), availability requests, roles,
invites or settings.

``require_booking_desk`` is named view by view. These nodes pin:

* the census — exactly the booking views carry it, and no other route;
* the gate — owner / admin / receptionist pass on any method; master and
  customer are refused;
* the refusals — the receptionist still gets 403 on availability, roles,
  invites and master cards.

The catalog checks the same person again (``IsTenantBookingDesk``,
beautygo_backend DRF-2826); this file is the bot's half.
"""

from __future__ import annotations

import json
import uuid

import pytest
from django.http import JsonResponse
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from apps.admin_api.auth import require_booking_desk
from apps.admin_api.tests.conftest import init_data_header
from apps.miniapp_api.transport_refusal import GUARD_ATTR

pytestmark = pytest.mark.django_db

#: The booking desk, by URL name. Widening it is a decision, not a refactor.
DESK_ROUTES = frozenset(
    {
        "create_booking",
        "cancel_booking",
        "booking_version",
        "complete_booking",
        "no_show_booking",
        "reschedule_booking",
        "search_customers",
        "booking_slots",
        "masters_list",
    }
)


def _admin_routes() -> dict[str, object]:
    found: dict[str, object] = {}

    def walk(patterns, namespace: str | None) -> None:
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, p.namespace or namespace)
            elif isinstance(p, URLPattern) and namespace == "admin_api" and p.name:
                found[p.name] = p.callback

    walk(get_resolver().url_patterns, None)
    return found


class TestCensus:
    def test_d1_exactly_the_booking_views_carry_the_desk_gate(self) -> None:
        routes = _admin_routes()
        desk = {
            name for name, cb in routes.items() if getattr(cb, GUARD_ATTR, None) == "booking_desk"
        }

        assert DESK_ROUTES <= set(routes), DESK_ROUTES - set(routes)  # presence: names resolve
        assert desk == DESK_ROUTES

    @pytest.mark.parametrize(
        "url_name",
        [
            "availability_request_approve",
            "availability_request_reject",
            "master_time_off",
            "master_date_exception",
            "staff_revoke",
            "staff_role_change",
            "staff_invite_create",
            "master_detail",
            "services_mapping_bulk",
        ],
    )
    def test_d2_availability_roles_invites_and_settings_stay_admin_only(
        self, url_name: str
    ) -> None:
        routes = _admin_routes()

        assert getattr(routes[url_name], GUARD_ATTR, None) == "admin", url_name


@require_booking_desk
def _desk_view(request):
    return JsonResponse({"ok": True})


class TestGate:
    @pytest.mark.parametrize("method", ["get", "post"])
    def test_d3_the_receptionist_passes_on_any_method(
        self, rf, receptionist_bot_user, tenant, method
    ) -> None:
        resp = _desk_view(getattr(rf, method)("/x", HTTP_AUTHORIZATION=init_data_header("5003")))

        assert resp.status_code == 200

    @pytest.mark.parametrize("who", [("owner_bot_user", "5001"), ("admin_bot_user", "5002")])
    def test_d4_owner_and_admin_still_pass(self, request, rf, tenant, who) -> None:
        fixture, uid = who
        request.getfixturevalue(fixture)

        resp = _desk_view(rf.post("/x", HTTP_AUTHORIZATION=init_data_header(uid)))

        assert resp.status_code == 200

    def test_d5_a_customer_is_refused(self, rf, customer_bot_user, tenant) -> None:
        resp = _desk_view(rf.post("/x", HTTP_AUTHORIZATION=init_data_header("5005")))

        assert resp.status_code == 403
        assert json.loads(resp.content)["error"] == "forbidden"


class TestReceptionistRefusedOffTheDesk:
    @pytest.mark.parametrize(
        ("url_name", "args", "method"),
        [
            ("availability_request_approve", ["req-2826"], "post"),
            ("master_time_off", [str(uuid.uuid4())], "post"),
            ("master_date_exception", [str(uuid.uuid4())], "put"),
            ("staff_revoke", [], "post"),
            ("staff_invite_create", [], "post"),
        ],
    )
    def test_d6_writes_off_the_desk_are_forbidden(
        self, client: Client, receptionist_bot_user, tenant, url_name, args, method
    ) -> None:
        resp = getattr(client, method)(
            reverse(f"admin_api:{url_name}", args=args),
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5003"),
        )

        assert resp.status_code == 403, url_name
        assert resp.json()["error"] == "forbidden"
