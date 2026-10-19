"""DRF-2945 Slice C — действия мастера на своей записи через Ayla.

Перенос, отмена, «состоялся», «не пришёл» — предложение → подписанный талон
→ отдельное подтверждение, исполнение — тем же движком, что экран записи
(DRF-2943, ``booking_actions.execute_master_booking_action``).

* id визита модель берёт из данных ``my_day`` (второй вызов видит только эти
  четыре действия, и только в Mini App); чужой/выдуманный id — отказ без
  признания, что запись есть;
* версию модель не передаёт: талон фиксирует версию строки зеркала, из
  которой собрана сводка; без версии (кроме отмены) талон не выписывается;
* подтверждение берёт аргументы только из талона, тело — только ``token``;
* успех — только при ``committed`` канона; «нет ответа» → «Проверяем
  результат», «запись изменилась» → не выполнено; ни одного повтора;
* окончательный отказ — не повторяемый слаг (карточка снимается).
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from django.core.signing import TimestampSigner
from django.test import Client
from django.urls import reverse
from django.utils import timezone as dj_timezone

from apps.booking.models import RemoteBookingProxy
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import BookingBadRequestError, BookingUnavailableError
from apps.master_api.pii import find_forbidden_pii
from apps.master_api.services import assistant as assistant_mod
from apps.master_api.services import assistant_actions as actions_mod
from apps.master_api.services import booking_actions as booking_actions_mod
from apps.master_api.tests.conftest import init_data_header, make_master
from apps.master_api.tests.test_assistant_api import FakeResult, FakeToolCall
from apps.tenancy.timezones import salon_zone

pytestmark = pytest.mark.django_db

ASK_URL = reverse("master_api:assistant_ask")
CONFIRM_URL = reverse("master_api:assistant_confirm")
CUSTOMER_PHONE = "+79997772945"
BOOKING_ACTIONS = {"reschedule_booking", "cancel_booking", "complete_booking", "mark_no_show"}


# ─── фикстуры ───────────────────────────────────────────────────────────────


@pytest.fixture
def llm():
    calls: list[dict[str, Any]] = []
    scripted: list[FakeResult] = []

    def fake_complete(messages, *, tenant, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        return scripted.pop(0) if scripted else FakeResult(text="ответ")

    with patch.object(assistant_mod, "_complete", side_effect=fake_complete):
        yield {"calls": calls, "script": scripted}


class StubActions:
    def __init__(self, *, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[dict] = []

    def act_as_specialist(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(
            appointment_id=kwargs["appointment_id"],
            status="cancelled",
            version=(kwargs.get("expected_version") or 0) + 1,
            start_at=kwargs.get("new_start_datetime") or "2026-10-21T15:00:00+03:00",
        )


@pytest.fixture
def stub_actions(monkeypatch):
    def install(stub: StubActions) -> StubActions:
        monkeypatch.setattr(
            "apps.master_api.services.booking_actions.get_ayla_booking_client",
            lambda: stub,
        )
        return stub

    return install


@pytest.fixture
def anna(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="294500",
        display_name="anna_p",
        client_name="Анна Петрова",
        chat_id="294500",
        phone=CUSTOMER_PHONE,
    )


def _visit(master: CatalogMaster, *, version: int | None = 7, client=None) -> RemoteBookingProxy:
    start = (dj_timezone.now() + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
    return RemoteBookingProxy.all_tenants.create(
        tenant=master.tenant,
        appointment_id=uuid.uuid4(),
        specialist_id=master.id,
        bot_user=client,
        start_at=start,
        end_at=start + timedelta(hours=1),
        status="confirmed",
        appointment_version=version,
    )


def _ask(client: Client, text: str):
    return client.post(
        ASK_URL,
        data=json.dumps({"text": text}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("12345"),
    )


def _confirm(client: Client, body: dict[str, Any]):
    return client.post(
        CONFIRM_URL,
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("12345"),
    )


def _script_day_then(llm, row: RemoteBookingProxy, action: str, **args: Any) -> None:
    """Модель: сначала my_day на день визита, потом действие по его данным."""

    day = row.start_at.astimezone(salon_zone(row.tenant)).date().isoformat()
    llm["script"].append(
        FakeResult(tool_calls=[FakeToolCall(name="my_day", arguments={"date": day})])
    )
    llm["script"].append(
        FakeResult(
            tool_calls=[
                FakeToolCall(
                    name=action,
                    arguments={"appointment_id": str(row.appointment_id), **args},
                    id="call-2",
                )
            ]
        )
    )


def _payload(token: str) -> dict[str, Any]:
    """Содержимое талона — проверить, ЧТО зафиксировано подписью."""

    from django.conf import settings

    key = getattr(settings, "MASTER_SESSION_SECRET", "") or settings.SECRET_KEY
    signer = TimestampSigner(key=key, salt=actions_mod.ACTION_TOKEN_SALT)
    return json.loads(signer.unsign(token))


def _prepare(client, llm, row, action: str, **args: Any) -> dict[str, Any]:
    _script_day_then(llm, row, action, **args)
    response = _ask(client, "сделай это с визитом")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["pending_action"] is not None, body
    return body["pending_action"]


# ─── где и когда действия предлагаются ──────────────────────────────────────


class TestOffered:
    def test_the_first_call_does_not_offer_actions_on_a_booking(self, client, accepted_master, llm):
        """Без данных my_day id визита модели взять неоткуда — действий нет."""

        _ask(client, "отмени запись Анны")

        offered = {spec["name"] for spec in llm["calls"][0]["tools"]}
        assert offered.isdisjoint(BOOKING_ACTIONS)

    def test_after_my_day_only_the_four_actions_are_offered(self, client, accepted_master, llm):
        row = _visit(accepted_master)
        _script_day_then(llm, row, "cancel_booking")

        _ask(client, "отмени визит")

        second = llm["calls"][1]
        assert {spec["name"] for spec in second["tools"]} == BOOKING_ACTIONS
        data = second["messages"][-1]["content"]
        assert str(row.appointment_id) in data, "id визита модель видит в данных my_day"

    def test_salon_bot_without_a_confirm_card_never_sees_them(self, accepted_master, llm):
        """allow_actions=False (салонный бот): второй вызов — текст, как раньше."""

        row = _visit(accepted_master)
        _script_day_then(llm, row, "cancel_booking")

        reply = assistant_mod.answer_master_question(
            master=accepted_master, text="отмени визит", allow_actions=False
        )

        assert llm["calls"][1]["tools"] is None
        assert reply.pending_action is None

    def test_another_tool_than_my_day_offers_no_followup(self, client, accepted_master, llm):
        llm["script"].append(
            FakeResult(
                tool_calls=[
                    FakeToolCall(
                        name="my_week",
                        arguments={"date_from": "2026-10-19", "date_to": "2026-10-25"},
                    )
                ]
            )
        )

        _ask(client, "что на неделе")

        assert llm["calls"][1]["tools"] is None


# ─── предложение ────────────────────────────────────────────────────────────


class TestProposal:
    def test_the_token_fixes_exact_id_mirror_version_and_action(
        self, client, accepted_master, llm, anna
    ):
        row = _visit(accepted_master, version=7, client=anna)
        # Версию модель прислать «может» — сервер её не читает.
        pending = _prepare(client, llm, row, "complete_booking", expected_version=99)

        payload = _payload(pending["token"])
        assert payload["action"] == "complete_booking"
        assert payload["args"] == {"appointment_id": str(row.appointment_id), "expected_version": 7}
        assert payload["master_id"] == str(accepted_master.id)
        assert pending["confirm_label"] == "Отметить: состоялся"
        assert "Анна Петрова" in pending["summary"]
        assert "details" not in pending, "карточка «Проверьте запись» — для создания, не здесь"

    def test_reschedule_fixes_the_new_time_in_the_token(self, client, accepted_master, llm):
        row = _visit(accepted_master, version=3)
        tz = salon_zone(accepted_master.tenant)
        new_local = (row.start_at + timedelta(days=1)).astimezone(tz)

        pending = _prepare(
            client, llm, row, "reschedule_booking", new_start=f"{new_local:%Y-%m-%dT%H:%M}"
        )

        args = _payload(pending["token"])["args"]
        assert args["expected_version"] == 3
        assert args["new_start_datetime"] == new_local.replace(second=0).isoformat()
        assert f"{new_local:%H:%M}" in pending["summary"]

    def test_reschedule_into_the_past_is_not_proposed(self, client, accepted_master, llm):
        row = _visit(accepted_master)
        _script_day_then(llm, row, "reschedule_booking", new_start="2020-01-01T10:00")

        body = _ask(client, "перенеси").json()

        assert body["pending_action"] is None

    @pytest.mark.parametrize("which", ["foreign", "missing", "garbage"])
    def test_not_own_booking_is_refused_the_same_way(
        self, client, tenant, accepted_master, llm, which
    ):
        colleague = make_master(tenant, name="Ольга", external_id=902945)
        foreign = _visit(colleague)
        appointment_id = {
            "foreign": str(foreign.appointment_id),
            "missing": str(uuid.uuid4()),
            "garbage": "anna-14-00",
        }[which]
        own = _visit(accepted_master)
        day = own.start_at.astimezone(salon_zone(tenant)).date().isoformat()
        llm["script"].append(
            FakeResult(tool_calls=[FakeToolCall(name="my_day", arguments={"date": day})])
        )
        # Модель вместо id из данных вставила чужой / выдуманный.
        llm["script"].append(
            FakeResult(
                tool_calls=[
                    FakeToolCall(
                        name="cancel_booking", arguments={"appointment_id": appointment_id}
                    )
                ]
            )
        )

        body = _ask(client, "отмени").json()

        assert body["pending_action"] is None
        assert body["answer"] == actions_mod.BOOKING_NOT_FOUND_TEXT

    def test_without_a_mirror_version_only_a_door_not_a_doomed_token(
        self, client, accepted_master, llm
    ):
        row = _visit(accepted_master, version=None)
        _script_day_then(llm, row, "mark_no_show")

        body = _ask(client, "не пришла").json()

        assert body["pending_action"] is None
        assert body["answer"] == actions_mod.BOOKING_VERSION_UNKNOWN_TEXT
        assert [c["kind"] for c in body["cards"]] == ["open"]
        assert str(row.appointment_id) in body["cards"][0]["url"]

    def test_cancel_without_a_mirror_version_is_proposed_as_canon_allows(
        self, client, accepted_master, llm
    ):
        row = _visit(accepted_master, version=None)

        pending = _prepare(client, llm, row, "cancel_booking", reason="заболела")

        args = _payload(pending["token"])["args"]
        assert args == {
            "appointment_id": str(row.appointment_id),
            "expected_version": None,
            "reason": "заболела",
        }

    def test_no_customer_phone_anywhere_in_the_proposal(self, client, accepted_master, llm, anna):
        row = _visit(accepted_master, client=anna)
        _script_day_then(llm, row, "cancel_booking")

        response = _ask(client, "отмени")

        # Присутствие прежде отсутствия: клиент в предложении есть — именем.
        assert "Анна Петрова" in response.json()["pending_action"]["summary"]
        assert find_forbidden_pii(response.json()) == []
        assert CUSTOMER_PHONE[-4:] not in response.content.decode()


# ─── подтверждение ──────────────────────────────────────────────────────────


class TestConfirm:
    @pytest.mark.parametrize(
        ("action", "canonical"),
        [
            ("cancel_booking", "cancel"),
            ("complete_booking", "complete"),
            ("mark_no_show", "no-show"),
        ],
    )
    def test_committed_is_the_only_success_and_goes_through_the_shared_engine(
        self, client, accepted_master, llm, stub_actions, action, canonical
    ):
        row = _visit(accepted_master, version=7)
        pending = _prepare(client, llm, row, action)
        stub = stub_actions(StubActions())

        with patch.object(
            booking_actions_mod,
            "execute_master_booking_action",
            wraps=booking_actions_mod.execute_master_booking_action,
        ) as engine:
            response = _confirm(client, {"token": pending["token"]})

        assert response.status_code == 200, response.content
        body = response.json()
        assert body["executed"] is True
        assert body["answer"].startswith("Готово.")
        assert engine.call_count == 1
        assert len(stub.calls) == 1
        call = stub.calls[0]
        assert call["action"] == canonical
        assert call["appointment_id"] == str(row.appointment_id)
        assert call["expected_version"] == 7
        assert call["external_user_id"] == "bot:max:12345"
        assert [c["kind"] for c in body["cards"]] == ["open"]

    def test_reschedule_sends_the_time_from_the_token(
        self, client, accepted_master, llm, stub_actions
    ):
        row = _visit(accepted_master, version=4)
        tz = salon_zone(accepted_master.tenant)
        new_local = (row.start_at + timedelta(days=2)).astimezone(tz)
        pending = _prepare(
            client, llm, row, "reschedule_booking", new_start=f"{new_local:%Y-%m-%dT%H:%M}"
        )
        stub = stub_actions(StubActions())

        _confirm(client, {"token": pending["token"]})

        assert stub.calls[0]["action"] == "reschedule"
        assert stub.calls[0]["new_start_datetime"] == new_local.replace(second=0).isoformat()
        assert stub.calls[0]["expected_version"] == 4

    def test_confirm_body_cannot_bring_new_mutation_arguments(
        self, client, accepted_master, llm, stub_actions
    ):
        row = _visit(accepted_master, version=7)
        other = _visit(accepted_master, version=1)
        pending = _prepare(client, llm, row, "complete_booking")
        stub = stub_actions(StubActions())

        _confirm(
            client,
            {
                "token": pending["token"],
                "action": "cancel",
                "appointment_id": str(other.appointment_id),
                "expected_version": 1,
                "new_start_datetime": "2030-01-01T10:00:00+03:00",
            },
        )

        assert len(stub.calls) == 1
        call = stub.calls[0]
        assert (call["action"], call["appointment_id"], call["expected_version"]) == (
            "complete",
            str(row.appointment_id),
            7,
        )
        assert call["new_start_datetime"] is None

    def test_unknown_result_is_not_success_and_is_not_retried(
        self, client, accepted_master, llm, stub_actions
    ):
        row = _visit(accepted_master)
        pending = _prepare(client, llm, row, "cancel_booking")
        stub = stub_actions(StubActions(exc=BookingUnavailableError("timeout")))

        body = _confirm(client, {"token": pending["token"]}).json()

        assert body["executed"] is False
        assert body["answer"] == "Проверяем результат"
        assert len(stub.calls) == 1, "ни одного скрытого повтора"

    @pytest.mark.parametrize("code", ["STALE_VERSION", "INVALID_STATUS", "APPOINTMENT_TERMINAL"])
    def test_a_changed_booking_is_not_success_and_is_not_retried(
        self, client, accepted_master, llm, stub_actions, code
    ):
        row = _visit(accepted_master)
        pending = _prepare(client, llm, row, "complete_booking")
        stub = stub_actions(
            StubActions(exc=BookingBadRequestError("changed", status_code=409, code=code))
        )

        body = _confirm(client, {"token": pending["token"]}).json()

        assert body["executed"] is False
        assert body["answer"] == actions_mod.BOOKING_CHANGED_TEXT
        assert [c["kind"] for c in body["cards"]] == ["open"]
        assert len(stub.calls) == 1

    @pytest.mark.parametrize(("status", "code"), [(400, "SOMETHING_ELSE"), (403, None)])
    def test_a_final_refusal_is_not_retriable(
        self, client, accepted_master, llm, stub_actions, status, code
    ):
        row = _visit(accepted_master)
        pending = _prepare(client, llm, row, "mark_no_show")
        stub_actions(StubActions(exc=BookingBadRequestError("no", status_code=status, code=code)))

        response = _confirm(client, {"token": pending["token"]})

        assert response.status_code == 400
        error = response.json()
        assert error["error"] == actions_mod.ACTION_UNAVAILABLE_SLUG
        assert error["details"]["retriable"] is False

    def test_a_booking_gone_from_the_mirror_is_refused_before_any_call(
        self, client, accepted_master, llm, stub_actions
    ):
        row = _visit(accepted_master)
        pending = _prepare(client, llm, row, "cancel_booking")
        stub = stub_actions(StubActions())
        row.delete()

        response = _confirm(client, {"token": pending["token"]})

        assert response.status_code == 400
        assert response.json()["error"] == actions_mod.ACTION_UNAVAILABLE_SLUG
        assert stub.calls == []

    def test_a_token_of_another_master_does_not_work(
        self, client, tenant, accepted_master, llm, stub_actions
    ):
        row = _visit(accepted_master)
        pending = _prepare(client, llm, row, "cancel_booking")
        stub = stub_actions(StubActions())
        colleague = make_master(tenant, name="Ольга", external_id=902946)

        with pytest.raises(actions_mod.ActionError) as refused:
            actions_mod.execute(pending["token"], master=colleague, actor=None)

        assert refused.value.slug == "action_not_yours"
        assert stub.calls == []
