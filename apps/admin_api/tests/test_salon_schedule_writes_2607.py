"""DRF-2607: the salon writes time off and date exceptions on the PERSON's token.

Owner ruling 29.09: «(а) — подпись MAX, служебный ключ в записи не участвует».

Pairs that must differ:

* a write that got the person's token reaches the catalog carrying THAT token
  and nothing of the service's — against a refused token, where no request
  leaves at all;
* «no rights» and «we failed to issue» answer the person byte-for-byte the
  same, and the journal tells them apart — the case that is ours to fix must
  not hide behind the case that is theirs.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone as dt_timezone

import httpx
import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.channels.bot_registry import SALON_STREAM, BotEntry
from apps.identity.models import BotUser
from apps.integrations.ayla.person_token import PersonToken, PersonTokenRefused
from apps.integrations.ayla.salon_client import AylaSalonClient
from apps.tenancy.models import Tenant
from tests.support.catalog_mirror import sync_shaped

from .conftest import BOT_TOKEN, _sign, init_data_header

pytestmark = pytest.mark.django_db

SERVICE_TOKEN = "service-token-under-test"  # pragma: allowlist secret
PERSON_TOKEN = "person-token-under-test"  # pragma: allowlist secret
JOURNAL = "apps.admin_api.views_salon_schedule_writes"


TIME_OFF_ID = str(uuid.uuid4())


def _registry(tenant: Tenant, *, stream: str) -> tuple[BotEntry, ...]:
    # The bot whose token signs the test initData — declared as the salon bot
    # (or, for the pair, as a client bot on stream «max»).
    return (
        BotEntry(
            slug="signer",
            webhook_secret="",
            api_token=BOT_TOKEN,
            tenant_slug=tenant.slug,
            stream=stream,
        ),
    )


@pytest.fixture(autouse=True)
def _salon_bot_signs(settings, tenant: Tenant) -> None:
    settings.MAX_BOT_REGISTRY = _registry(tenant, stream=SALON_STREAM)


@pytest.fixture(autouse=True)
def _empty_token_cache():
    from apps.integrations.ayla.person_token import _CACHE

    _CACHE.clear()
    yield
    _CACHE.clear()


def _launch(auth_date: int, user_id: int = 5001) -> str:
    """An initData header for one Mini App launch — ``auth_date`` names it."""
    params = {
        "user": json.dumps({"id": user_id, "first_name": "Карина"}),
        "auth_date": str(auth_date),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture
def synced_master(tenant: Tenant) -> CatalogMaster:
    return sync_shaped(
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=2607,
            external_updated_at=datetime.now(tz=dt_timezone.utc),
            name="Ольга",
            ayla_user_id=uuid.uuid4(),
        )
    )


class _Wire:
    """The exchange and the catalog, both recorded."""

    def __init__(
        self, monkeypatch, *, refuse: PersonTokenRefused | None = None, catalog_status: int = 201
    ):
        self.exchanged: list[tuple[str, str]] = []
        self.sent: list[httpx.Request] = []
        self.refuse = refuse
        self.catalog_status = catalog_status

        def exchange(*, init_data: str, tenant_slug: str, **_kw) -> PersonToken:
            self.exchanged.append((init_data, tenant_slug))
            if self.refuse is not None:
                raise self.refuse
            return PersonToken(access_token=PERSON_TOKEN, tenant_slug=tenant_slug, expires_in=600)

        def handler(request: httpx.Request) -> httpx.Response:
            self.sent.append(request)
            if self.catalog_status == 204:
                return httpx.Response(204)
            body = (
                {"data": {"id": "off-1"}} if self.catalog_status < 300 else {"error": {"code": "X"}}
            )
            return httpx.Response(self.catalog_status, json=body)

        client = AylaSalonClient(
            base_url="https://ayla.example",
            service_token=SERVICE_TOKEN,
            transport=httpx.MockTransport(handler),
        )
        # Names bound in MY module at import — patch them there.
        monkeypatch.setattr(f"{JOURNAL}.obtain_person_token", exchange)
        monkeypatch.setattr(f"{JOURNAL}.get_salon_client", lambda: client)


@pytest.fixture
def journal(caplog):
    """Attach caplog to the module logger directly (``apps`` may not propagate)."""
    lg = logging.getLogger(JOURNAL)
    lg.addHandler(caplog.handler)
    lg.setLevel(logging.INFO)
    yield caplog
    lg.removeHandler(caplog.handler)


def _lines(caplog) -> list[tuple[int, str]]:
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == JOURNAL]


def _time_off(client: Client, master: CatalogMaster, header: str):
    return client.post(
        reverse("admin_api:master_time_off", args=[str(master.id)]),
        data=json.dumps(
            {"start_at": "2031-01-10T09:00:00+03:00", "end_at": "2031-01-10T18:00:00+03:00"}
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=header,
    )


class TestTheWriteIsThePersons:
    def test_the_persons_token_and_nothing_of_the_service(
        self,
        client: Client,
        owner_bot_user: BotUser,
        synced_master: CatalogMaster,
        monkeypatch,
        journal,
    ) -> None:
        wire = _Wire(monkeypatch)
        header = init_data_header("5001")

        resp = _time_off(client, synced_master, header)

        assert resp.status_code == 201, resp.content
        # the exchange got exactly the initData the person presented
        assert wire.exchanged == [(header.removeprefix("MaxInitData "), synced_master.tenant.slug)]
        (sent,) = wire.sent
        headers = {k.lower(): v for k, v in sent.headers.items()}
        assert headers["authorization"] == f"Bearer {PERSON_TOKEN}"
        assert "x-external-user-id" not in headers
        assert SERVICE_TOKEN not in " ".join(headers.values())
        assert any("via=person_token" in m for _, m in _lines(journal))

    @pytest.mark.parametrize(
        ("name", "method", "extra", "body", "catalog_status", "expected"),
        [
            ("master_time_off_detail", "delete", [TIME_OFF_ID], None, 204, 204),
            (
                "master_date_exception",
                "put",
                [],
                {"date": "2031-01-11", "is_working_day": False},
                200,
                200,
            ),
            ("master_date_exception_detail", "delete", ["2031-01-11"], None, 204, 204),
        ],
    )
    def test_every_opened_write_rides_the_persons_token(
        self,
        client,
        owner_bot_user,
        synced_master,
        monkeypatch,
        name,
        method,
        extra,
        body,
        catalog_status,
        expected,
    ) -> None:
        wire = _Wire(monkeypatch, catalog_status=catalog_status)
        kwargs = {"HTTP_AUTHORIZATION": init_data_header("5001")}
        if body is not None:
            kwargs.update(data=json.dumps(body), content_type="application/json")
        resp = getattr(client, method)(
            reverse(f"admin_api:{name}", args=[str(synced_master.id), *extra]), **kwargs
        )
        assert resp.status_code == expected, resp.content
        (sent,) = wire.sent
        assert sent.headers["authorization"] == f"Bearer {PERSON_TOKEN}"


class TestNoTokenNoWrite:
    def test_no_rights_is_refused_and_nothing_is_sent(
        self, client, owner_bot_user, synced_master, monkeypatch, journal
    ) -> None:
        wire = _Wire(monkeypatch, refuse=PersonTokenRefused("NO_SALON_ROLE", status=403))
        resp = _time_off(client, synced_master, init_data_header("5001"))
        assert (resp.status_code, resp.json()) == (403, {"error": "salon_write_refused"})
        assert wire.sent == []
        assert any("reason=TOKEN_NOT_ISSUED:NO_SALON_ROLE ours=no" in m for _, m in _lines(journal))

    def test_we_failed_to_issue_answers_the_same_and_the_journal_says_it_is_ours(
        self, client, owner_bot_user, synced_master, monkeypatch, journal
    ) -> None:
        """The main window's pair. The same owner — who HAS the right — once
        refused by the catalog for want of a role, once because issuance is
        broken on our side. The person sees the same; the journal does not."""

        _Wire(monkeypatch, refuse=PersonTokenRefused("NO_SALON_ROLE", status=403))
        theirs = _time_off(client, synced_master, init_data_header("5001"))
        theirs_lines = _lines(journal)
        journal.clear()

        wire = _Wire(monkeypatch, refuse=PersonTokenRefused("NOT_CONFIGURED", status=503))
        ours = _time_off(client, synced_master, init_data_header("5001"))
        ours_lines = _lines(journal)

        assert (
            (theirs.status_code, theirs.content)
            == (ours.status_code, ours.content)
            == (
                403,
                b'{"error": "salon_write_refused"}',
            )
        )
        assert wire.sent == []  # no fallback: nothing reached the catalog
        assert any("reason=TOKEN_NOT_ISSUED:NO_SALON_ROLE ours=no" in m for _, m in theirs_lines), (
            theirs_lines
        )
        assert any(
            level == logging.ERROR and "reason=TOKEN_NOT_ISSUED:NOT_CONFIGURED ours=yes" in m
            for level, m in ours_lines
        ), ours_lines

    def test_an_unreachable_exchange_is_a_refusal_not_a_retry(
        self, client, owner_bot_user, synced_master, monkeypatch, journal
    ) -> None:
        wire = _Wire(monkeypatch, refuse=PersonTokenRefused("EXCHANGE_UNREACHABLE"))
        resp = _time_off(client, synced_master, init_data_header("5001"))
        assert (resp.status_code, resp.json()) == (403, {"error": "salon_write_refused"})
        assert wire.sent == []
        assert any("ours=yes" in m for _, m in _lines(journal))

    def test_the_catalog_refusing_the_persons_token_answers_the_same(
        self, client, owner_bot_user, synced_master, monkeypatch, journal
    ) -> None:
        _Wire(monkeypatch, catalog_status=403)
        resp = _time_off(client, synced_master, init_data_header("5001"))
        assert (resp.status_code, resp.json()) == (403, {"error": "salon_write_refused"})
        assert any("reason=CATALOG_REFUSED" in m for _, m in _lines(journal))

    def test_live_bookings_in_the_period_are_named(
        self, client, owner_bot_user, synced_master, monkeypatch
    ) -> None:
        _Wire(monkeypatch, catalog_status=409)
        resp = _time_off(client, synced_master, init_data_header("5001"))
        assert (resp.status_code, resp.json()["error"]) == (409, "has_active_appointments")


class TestOneExchangePerLaunch:
    """Main window, 29.09: the catalog's 10/min guards an authentication
    endpoint and stays; the bot exchanges once per (person, salon, launch)."""

    def test_two_writes_in_one_launch_exchange_once(
        self, client, owner_bot_user, synced_master, monkeypatch
    ):
        wire = _Wire(monkeypatch)
        header = _launch(int(time.time()))
        assert _time_off(client, synced_master, header).status_code == 201
        assert _time_off(client, synced_master, header).status_code == 201
        assert (len(wire.exchanged), len(wire.sent)) == (1, 2)

    def test_a_new_launch_exchanges_again(self, client, owner_bot_user, synced_master, monkeypatch):
        """Pair with the above: the same person, the Mini App reopened."""
        wire = _Wire(monkeypatch)
        now = int(time.time())
        assert _time_off(client, synced_master, _launch(now - 30)).status_code == 201
        assert _time_off(client, synced_master, _launch(now)).status_code == 201
        assert len(wire.exchanged) == 2

    def test_a_refusal_is_not_kept(self, client, owner_bot_user, synced_master, monkeypatch):
        header = _launch(int(time.time()))
        refused = _Wire(monkeypatch, refuse=PersonTokenRefused("NO_SALON_ROLE", status=403))
        assert _time_off(client, synced_master, header).status_code == 403
        granted = _Wire(monkeypatch)
        assert _time_off(client, synced_master, header).status_code == 201
        assert (len(refused.exchanged), len(granted.exchanged)) == (1, 1)

    def test_a_token_the_catalog_stops_accepting_is_forgotten(
        self, client, owner_bot_user, synced_master, monkeypatch
    ):
        header = _launch(int(time.time()))
        rejected = _Wire(monkeypatch, catalog_status=401)
        assert _time_off(client, synced_master, header).status_code == 403
        again = _Wire(monkeypatch)
        assert _time_off(client, synced_master, header).status_code == 201
        assert (len(rejected.exchanged), len(again.exchanged)) == (1, 1)


class TestAnOldLaunchIsNotNoRights:
    def test_stale_says_stale_and_no_rights_says_refused(
        self, client, owner_bot_user, synced_master, monkeypatch
    ):
        """Pair on the same person: a launch too old for a write is told apart
        from «no rights» — the cure is to reopen, not to go ask for rights."""
        _Wire(monkeypatch, refuse=PersonTokenRefused("INIT_DATA_STALE", status=401))
        stale = _time_off(client, synced_master, init_data_header("5001"))
        wire = _Wire(monkeypatch, refuse=PersonTokenRefused("NO_SALON_ROLE", status=403))
        refused = _time_off(client, synced_master, init_data_header("5001"))
        assert (stale.status_code, stale.json()) == (401, {"error": "stale"})
        assert (refused.status_code, refused.json()) == (403, {"error": "salon_write_refused"})
        assert wire.sent == []


class TestWhichBotSigned:
    def test_a_signature_of_another_bot_is_refused_before_any_exchange(
        self, client, owner_bot_user, synced_master, monkeypatch, settings, tenant, journal
    ) -> None:
        """The bot's gate accepts any registered bot; the catalog only the salon
        one. Pair with every accepted write above (same token, stream
        ``max_salon``): here the same token is declared a client bot."""
        settings.MAX_BOT_REGISTRY = _registry(tenant, stream="max")
        wire = _Wire(monkeypatch)
        resp = _time_off(client, synced_master, init_data_header("5001"))
        assert (resp.status_code, resp.json()) == (403, {"error": "salon_write_refused"})
        assert wire.exchanged == [] and wire.sent == []
        assert any("reason=WRONG_BOT ours=no" in m for _, m in _lines(journal))


class TestLocalChecks:
    def test_a_dotted_time_off_id_is_not_found_without_an_exchange(
        self, client, owner_bot_user, synced_master, monkeypatch
    ) -> None:
        wire = _Wire(monkeypatch)
        resp = client.delete(
            f"/api/v1/admin/masters/{synced_master.id}/time-off/%2E%2E/",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code in (400, 404)
        assert wire.exchanged == [] and wire.sent == []

    def test_is_working_day_must_be_a_boolean(
        self, client, owner_bot_user, synced_master, monkeypatch
    ) -> None:
        wire = _Wire(monkeypatch)
        resp = client.put(
            reverse("admin_api:master_date_exception", args=[str(synced_master.id)]),
            data=json.dumps({"date": "2031-01-11", "is_working_day": "false"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )
        assert resp.status_code == 400
        assert wire.exchanged == []

    def test_a_lost_answer_is_not_nothing_happened(
        self, client, owner_bot_user, synced_master, monkeypatch
    ) -> None:
        from apps.integrations.ayla.salon_client import SalonUnavailable

        _Wire(monkeypatch)

        def boom(*a, **k):
            raise SalonUnavailable("network: ReadTimeout")

        monkeypatch.setattr(AylaSalonClient, "create_time_off", boom)
        resp = _time_off(client, synced_master, init_data_header("5001"))
        assert (resp.status_code, resp.json()["error"]) == (503, "salon_write_outcome_unknown")

    def test_a_token_the_catalog_does_not_recognise_is_ours(
        self, client, owner_bot_user, synced_master, monkeypatch, journal
    ) -> None:
        _Wire(monkeypatch, catalog_status=401)
        resp = _time_off(client, synced_master, init_data_header("5001"))
        assert (resp.status_code, resp.json()) == (403, {"error": "salon_write_refused"})
        assert any(
            level == logging.ERROR and "reason=CATALOG_REJECTED_TOKEN ours=yes" in m
            for level, m in _lines(journal)
        )


class TestWhoMayAsk:
    def test_reception_cannot_write_and_no_token_is_asked_for(
        self, client, receptionist_bot_user, synced_master, monkeypatch
    ) -> None:
        wire = _Wire(monkeypatch)
        resp = _time_off(client, synced_master, init_data_header("5003"))
        assert resp.status_code == 403
        assert wire.exchanged == [] and wire.sent == []

    def test_a_master_of_another_salon_is_not_found(
        self, client, owner_bot_user, other_tenant, monkeypatch
    ) -> None:
        wire = _Wire(monkeypatch)
        foreign = sync_shaped(
            CatalogMaster.all_tenants.create(
                tenant=other_tenant,
                external_id=2608,
                external_updated_at=datetime.now(tz=dt_timezone.utc),
                name="Чужая",
                ayla_user_id=uuid.uuid4(),
            )
        )
        resp = _time_off(client, foreign, init_data_header("5001"))
        assert resp.status_code == 404
        assert wire.exchanged == [] and wire.sent == []
