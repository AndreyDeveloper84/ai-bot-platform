"""DRF-2667 — ассистент админа: аргумент модели не строкой не доходит до человека.

Тот же дефект, что у мастера, плюс эхо в прецедентах: резолверы мастера и
услуги подставляли сырое значение в «мастер «…» не найден», а ``_parse_day``
— в «не понимаю дату …». Узлы проверяют свойство: ни в тексте, который
увидит админ, ни в строке базы, ни в ссылке на форму нет ``{'a': 1}``.
Рядом — страж со строкой.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.admin_api.tests.test_admin_assistant_2119 import NOW, _future_period

pytestmark = pytest.mark.django_db

OBJECT = {"a": 1}
LEAK = "{'a': 1}"


def _propose(name: str, arguments: dict[str, Any], *, tenant, bot_user) -> tuple[str, str]:
    """Что увидит админ (сводка и ссылка или текст отказа) и талон, если он есть."""
    from apps.admin_api.services import assistant as aa
    from apps.master_api.services.assistant_actions import ActionError

    try:
        proposal = aa.propose_admin_action(name, arguments, tenant=tenant, bot_user=bot_user)
    except ActionError as exc:
        return exc.detail, ""
    d = proposal.as_dict()
    return f"{proposal.summary} {d}", str(d.get("token") or "")


def _schedule_args(who, **over: Any) -> dict[str, Any]:
    start, end = _future_period()
    args: dict[str, Any] = {
        "master": who.name,
        "start": start,
        "end": end,
        "reason_class": "personal",
        "reason_text": "семейное",
    }
    args.update(over)
    return args


class TestScheduleChange:
    def test_a_string_reason_is_stored(self, tenant, owner_bot_user, master, settings) -> None:
        from apps.admin_api.services import assistant as aa
        from apps.scheduling.models import ScheduleChangeRequest

        settings.BOOKING_VIA_AYLA_REST = False
        proposal = aa.propose_admin_action(
            "prepare_schedule_change",
            _schedule_args(master),
            tenant=tenant,
            bot_user=owner_bot_user,
        )
        aa.execute_admin_action(proposal.as_dict()["token"], tenant=tenant, bot_user=owner_bot_user)
        assert [r.reason_text for r in ScheduleChangeRequest.all_tenants.all()] == ["семейное"]

    def test_an_object_reason_reaches_neither_the_admin_nor_the_row(
        self, tenant, owner_bot_user, master, settings
    ) -> None:
        from apps.admin_api.services import assistant as aa
        from apps.scheduling.models import ScheduleChangeRequest

        settings.BOOKING_VIA_AYLA_REST = False
        shown, token = _propose(
            "prepare_schedule_change",
            _schedule_args(master, reason_text=OBJECT),
            tenant=tenant,
            bot_user=owner_bot_user,
        )
        assert LEAK not in shown
        if token:
            aa.execute_admin_action(token, tenant=tenant, bot_user=owner_bot_user)
        for row in ScheduleChangeRequest.all_tenants.all():
            assert LEAK not in row.reason_text

    @pytest.mark.parametrize("field", ["master", "start"])
    def test_an_object_is_not_echoed_in_the_refusal(
        self, tenant, owner_bot_user, master, field
    ) -> None:
        shown, _token = _propose(
            "prepare_schedule_change",
            _schedule_args(master, **{field: OBJECT}),
            tenant=tenant,
            bot_user=owner_bot_user,
        )
        assert LEAK not in shown


class TestPrepareBooking:
    def test_an_object_client_name_is_not_in_the_draft(
        self, tenant, owner_bot_user, master
    ) -> None:
        start, _end = _future_period()
        shown, _token = _propose(
            "prepare_booking",
            {"master": master.name, "start_at": start, "client_name": OBJECT},
            tenant=tenant,
            bot_user=owner_bot_user,
        )
        assert LEAK not in shown
        assert "%7B%27a%27" not in shown  # и не в ссылке на форму


class TestFindBookingDate:
    def test_an_object_date_is_not_echoed(self, tenant) -> None:
        from apps.admin_api.services import assistant as aa

        try:
            out = aa.run_admin_tool("find_booking", {"date": OBJECT}, tenant=tenant, now=NOW)
            shown = str(out.data)
        except Exception as exc:  # noqa: BLE001 — любой отказ, важен только текст
            shown = str(exc)
        assert shown
        assert LEAK not in shown
