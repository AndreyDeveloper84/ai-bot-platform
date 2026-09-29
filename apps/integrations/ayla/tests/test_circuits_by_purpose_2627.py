"""DRF-2627 — автоматы защиты клиента каталога разведены по назначению.

До DRF-2627 44 пути клиента делили один автомат с записью: серия таймаутов на
правке профиля мастера, списке портфолио или публикации открывала автомат
ЗАПИСИ, а пока он был открыт из-за чтения, запись отказывала по чужой
причине. Теперь: ``booking`` — путь клиента к записи, ``read`` — чтения и
правки каталога и кабинета мастера, медиа — свой (DRF-2618).

Узлы — парами, которые обязаны различаться: узел «автомат открывается» в
одиночку прошёл бы и при общем предохранителе, то есть при самом дефекте.

* p1 — серия таймаутов на пути НЕ про запись (портфолио) открывает автомат
  чтений и НЕ открывает автомат записи; запись в тот же момент проходит;
* p2 — серия таймаутов на ``create_appointment`` открывает автомат записи и
  НЕ открывает автомат чтений; чтение в тот же момент проходит;
* p3 — 5xx засчитываются в тот автомат, через который шёл вызов: их считает
  ``_ok`` уже ПОСЛЕ ``_request``, и назначения вызова он сам не знает;
* p4 — ``get_tenant_kind``: нарочно короткий таймаут пробы (``feeds_circuit=
  False``) не открывает и автомат чтений, а без флага — открывает чтений, не
  записи;
* p5 — имена автоматов различимы в журнале;
* p6 — перепись: какие публичные методы на каком автомате — числом.
"""

from __future__ import annotations

import ast
import inspect
import logging

import httpx
import pytest

from apps.integrations.ayla import booking_client as bc

SPEC = "spec-1"
USER = "bot:max:99001"
APPOINTMENT = {
    "data": {
        "id": "appt-uuid",
        "status": "confirmed",
        "start_datetime": "2026-06-10T14:00:00+03:00",
        "end_datetime": "2026-06-10T15:00:00+03:00",
        "service": {"id": "svc-1"},
        "specialist": {"id": SPEC},
    }
}
PROFILE = {"data": {"id": SPEC, "name": "Анна", "bio": "", "avatar": None}}


def _client(*, fail: dict[str, str]) -> bc.AylaBookingHTTPClient:
    """Настоящий клиент; ``fail`` — подстрока пути → ``timeout`` | ``500``."""

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        for needle, how in fail.items():
            if needle in path:
                if how == "timeout":
                    raise httpx.ReadTimeout("slow network", request=req)
                return httpx.Response(int(how), json={})
        if path.endswith("/appointments/"):
            return httpx.Response(201, json=APPOINTMENT)
        if path.endswith("/profile/"):
            return httpx.Response(200, json=PROFILE)
        if path.endswith("/portfolio/"):
            return httpx.Response(200, json={"data": []})
        if path.endswith("/kind/"):
            return httpx.Response(200, json={"data": {"kind": "salon"}})
        return httpx.Response(404, json={})

    return bc.AylaBookingHTTPClient(
        base_url="https://ayla.test", api_token="tok", transport=httpx.MockTransport(handler)
    )


def _book(client: bc.AylaBookingHTTPClient) -> bc.AylaBookingRecord:
    return client.create_appointment(
        external_user_id=USER,
        client_id="client-uuid",
        specialist_id=SPEC,
        service_id="svc-1",
        start_datetime="2026-06-10T14:00:00+03:00",
        idempotency_key="idem-2627",
    )


def _portfolio(client: bc.AylaBookingHTTPClient) -> object:
    return client.list_specialist_portfolio(specialist_id=SPEC, external_user_id=USER)


class TestP1ReadFailuresDoNotStopBooking:
    def test_portfolio_timeouts_open_only_the_read_breaker(self) -> None:
        client = _client(fail={"/portfolio/": "timeout"})
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(bc.BookingUnavailableError):
                _portfolio(client)

        assert client._read_circuit.opened_at is not None
        assert client._circuit.opened_at is None
        with pytest.raises(bc.BookingUnavailableError, match="read_circuit_open"):
            _portfolio(client)
        # Обратная половина: при открытом автомате чтений запись проходит.
        assert _book(client).appointment_id == "appt-uuid"


class TestP2BookingFailuresOpenTheBookingBreaker:
    def test_appointment_timeouts_open_only_the_booking_breaker(self) -> None:
        client = _client(fail={"/appointments/": "timeout"})
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(bc.BookingUnavailableError):
                _book(client)

        assert client._circuit.opened_at is not None
        assert client._read_circuit.opened_at is None
        with pytest.raises(bc.BookingUnavailableError, match="^circuit_open$"):
            _book(client)
        # Чтение кабинета мастера в тот же момент проходит.
        assert _portfolio(client) == []


class TestP3FiveHundredsFollowThePurpose:
    def test_read_5xx_open_the_read_breaker_not_the_booking_one(self) -> None:
        client = _client(fail={"/profile/": "500"})
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(bc.BookingUnavailableError, match="http_500"):
                client.get_specialist_profile(specialist_id=SPEC, external_user_id=USER)

        assert client._read_circuit.opened_at is not None
        assert client._circuit.opened_at is None
        assert _book(client).appointment_id == "appt-uuid"


class TestP4TenantKindProbe:
    def test_short_probe_timeouts_do_not_open_the_read_breaker(self) -> None:
        client = _client(fail={"/kind/": "timeout"})
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD + 2):
            with pytest.raises(bc.BookingUnavailableError):
                client.get_tenant_kind(tenant_id="t-1", feeds_circuit=False)

        assert client._read_circuit.opened_at is None
        assert client._circuit.opened_at is None

    def test_counted_kind_timeouts_open_the_read_breaker_not_booking(self) -> None:
        client = _client(fail={"/kind/": "timeout"})
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(bc.BookingUnavailableError):
                client.get_tenant_kind(tenant_id="t-1")

        assert client._read_circuit.opened_at is not None
        assert client._circuit.opened_at is None
        assert _book(client).appointment_id == "appt-uuid"


class TestP5NamedApart:
    def test_three_breakers_three_names(self) -> None:
        client = _client(fail={})
        names = {client._circuit.name, client._read_circuit.name, client._media_circuit.name}
        assert names == {"ayla.booking", "ayla.booking.read", "ayla.booking.media"}

    def test_the_journal_names_the_breaker_that_opened(self, caplog) -> None:
        client = _client(fail={"/portfolio/": "timeout"})
        with caplog.at_level(logging.WARNING, logger=bc.logger.name):
            for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD):
                with pytest.raises(bc.BookingUnavailableError):
                    _portfolio(client)
        opened = [r.getMessage() for r in caplog.records if "circuit_opened" in r.getMessage()]
        assert opened == [opened[0]] and "breaker=ayla.booking.read" in opened[0]


#: Путь клиента к записи — то, что стережёт автомат ``ayla.booking``.
BOOKING_PATHS = {
    "get_services",
    "get_masters",
    "get_available_times",
    "get_available_dates",
    "create_appointment",
    "cancel_appointment",
    "reschedule_appointment",
    "get_accepting_bookings",
    "get_user_bookings_page",
    "get_user_appointments",
    "get_booking_detail",
    "get_appointment_version",
    "get_repeat_intent",
}


def _purposes_by_method() -> dict[str, set[str]]:
    """Какие назначения достижимы из каждого публичного метода — по коду."""
    src = inspect.getsource(bc.AylaBookingHTTPClient)
    cls = ast.parse(src).body[0]
    assert isinstance(cls, ast.ClassDef)
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}

    def direct(m: ast.FunctionDef) -> tuple[set[str], set[str]]:
        purposes: set[str] = set()
        calls: set[str] = set()
        for n in ast.walk(m):
            if (
                isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id == "self"
            ):
                if n.attr == "_read_circuit":
                    purposes.add("read")
                if n.attr == "_media_circuit":
                    purposes.add("media")
                if n.attr in methods:
                    calls.add(n.attr)
            if (
                isinstance(n, ast.keyword)
                and n.arg == "purpose"
                and isinstance(n.value, ast.Constant)
            ):
                purposes.add(str(n.value.value))
        return purposes, calls

    facts = {k: direct(v) for k, v in methods.items()}

    # Механизм выбора упоминает ОБА автомата — это не путь метода, а развилка.
    infra = {"_breaker", "__init__"}

    def closure(k: str, seen: set[str]) -> set[str]:
        if k in seen or k in infra:
            return set()
        seen.add(k)
        own, calls = facts[k]
        out = set(own)
        for c in calls:
            out |= closure(c, seen)
        return out

    return {k: closure(k, set()) for k in methods if not k.startswith("_") and k != "close"}


class TestP6Census:
    def test_every_public_path_has_exactly_one_breaker_and_booking_is_thirteen(self) -> None:
        purposes = _purposes_by_method()
        # Положительный контроль сканера — известные носители каждого автомата.
        assert purposes["create_appointment"] == {"booking"}
        assert purposes["list_specialist_portfolio"] == {"read"}
        assert purposes["specialist_media_file"] == {"media"}

        ambiguous = {k: v for k, v in purposes.items() if len(v) != 1}
        assert ambiguous == {}
        booking = {k for k, v in purposes.items() if v == {"booking"}}
        read = {k for k, v in purposes.items() if v == {"read"}}
        media = {k for k, v in purposes.items() if v == {"media"}}
        assert booking == BOOKING_PATHS
        # До DRF-2627 на автомате записи — 44 пути; после — 13, чтений — 31.
        assert (len(booking), len(read), len(media)) == (13, 31, 1)
