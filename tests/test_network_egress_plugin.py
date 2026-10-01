"""``config/pytest_network_egress.py`` — the plugin sees what it claims to see.

DRF-2696. The plugin is loaded for the whole run (``addopts``), so these tests
exercise the live instance: they make a call and read what was recorded for
their own node id.

They reach for a name that cannot resolve (``*.invalid``, RFC 6761) through
httpx's real transport. That is the thing the plugin exists to report, so each
test drops its own events afterwards — otherwise the report would list the
tests that test the report.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from config import pytest_network_egress as egress

PROBE_HOST = "egress-probe.invalid"
PROBE_URL = f"http://{PROBE_HOST}/v1/anything"

#: How the call to a name that cannot resolve ends is the resolver's business:
#: a failed lookup (``ConnectError``) where the resolver answers, a timeout
#: (``ConnectTimeout``) where it does not. Either is «did not get through»;
#: what these tests assert is what was RECORDED. The timeout is short so a
#: slow resolver costs seconds, not httpx's default five per test.
NOT_DELIVERED = httpx.TransportError
TIMEOUT = 2.0


@pytest.fixture
def own_events(request):
    """Reader for this test's events; drops them when the test is over."""
    nodeid = request.node.nodeid
    yield lambda: egress.events_for(nodeid)
    egress.discard_events_for(nodeid)


@pytest.mark.parametrize(
    ("host", "local"),
    [
        ("localhost", True),
        ("LOCALHOST", True),
        ("127.0.0.1", True),
        ("127.8.9.10", True),
        ("::1", True),
        ("[::1]", True),
        (b"localhost", True),
        ("testserver", True),
        (None, True),
        ("", True),
        ("api.openai.com", False),
        (b"api.openai.com", False),
        ("botapi.max.ru", False),
        ("ayla.test", False),
        ("ayla-api.invalid", False),
        ("10.0.0.5", False),
        ("192.168.1.1", False),
        ("0.0.0.0", False),
        ("localhost.evil.example", False),
    ],
)
def test_what_counts_as_this_machine(host, local):
    assert egress.is_local(host) is local


def test_real_transport_to_a_foreign_host_is_recorded_on_both_layers(own_events):
    with pytest.raises(NOT_DELIVERED), httpx.Client(timeout=TIMEOUT) as client:
        client.get(PROBE_URL)

    seen = own_events()
    assert seen.get((PROBE_HOST, "http")) == 1
    assert seen.get((PROBE_HOST, "dns"), 0) >= 1


@pytest.mark.asyncio
async def test_async_transport_is_recorded_too(own_events):
    """The async transport is the one both vendor SDKs use."""
    with pytest.raises(NOT_DELIVERED):
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            await client.post(PROBE_URL, json={})

    assert own_events().get((PROBE_HOST, "http")) == 1


def test_a_mocked_transport_is_not_egress(own_events):
    """The same URL through ``MockTransport`` never touches the network."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    with httpx.Client(transport=transport) as client:
        response = client.get(PROBE_URL)

    # The call happened — and nothing was recorded for it.
    assert response.json() == {"ok": True}
    assert own_events() == {}


def test_this_machine_is_not_egress(own_events):
    """Loopback through the real transport: refused connection, no record."""
    with pytest.raises(NOT_DELIVERED), httpx.Client(timeout=TIMEOUT) as client:
        client.get("http://127.0.0.1:9/")

    assert own_events() == {}


def test_a_lookup_without_httpx_is_recorded(own_events):
    """``requests``, ``aiohttp`` and a bare socket all end in ``getaddrinfo``."""
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo(PROBE_HOST, 443)

    assert own_events() == {(PROBE_HOST, "dns"): 1}


class TestForbidMode:
    """``AYLA_TEST_EGRESS=forbid`` — the same observation, turned into a refusal."""

    def test_default_mode_is_record(self, monkeypatch):
        monkeypatch.delenv(egress.MODE_ENV, raising=False)
        assert egress.mode() == egress.MODE_RECORD

    @pytest.mark.parametrize("value", ["forbid", "FORBID", " forbid "])
    def test_forbid_is_opt_in_by_name(self, monkeypatch, value):
        monkeypatch.setenv(egress.MODE_ENV, value)
        assert egress.mode() == egress.MODE_FORBID

    @pytest.mark.parametrize("value", ["", "1", "true", "record", "off"])
    def test_anything_else_is_record(self, monkeypatch, value):
        monkeypatch.setenv(egress.MODE_ENV, value)
        assert egress.mode() == egress.MODE_RECORD

    def test_a_foreign_host_is_refused_before_the_lookup(self, monkeypatch, own_events):
        monkeypatch.setenv(egress.MODE_ENV, "forbid")

        with pytest.raises(egress.NetworkEgressForbidden) as refused, httpx.Client() as client:
            client.get(PROBE_URL)

        assert PROBE_HOST in str(refused.value)
        # Refused at the transport: the resolver was never asked.
        assert own_events() == {(PROBE_HOST, "http"): 1}

    def test_a_bare_lookup_is_refused(self, monkeypatch, own_events):
        monkeypatch.setenv(egress.MODE_ENV, "forbid")

        with pytest.raises(egress.NetworkEgressForbidden):
            socket.getaddrinfo(PROBE_HOST, 443)

        assert own_events() == {(PROBE_HOST, "dns"): 1}

    def test_this_machine_is_never_refused(self, monkeypatch, own_events):
        monkeypatch.setenv(egress.MODE_ENV, "forbid")

        with pytest.raises(NOT_DELIVERED), httpx.Client(timeout=TIMEOUT) as client:
            client.get("http://127.0.0.1:9/")

        assert own_events() == {}

    def test_a_test_marked_to_cross_a_boundary_is_let_through(self, monkeypatch, own_events):
        """``smoke`` / ``cross_boundary`` / ``e2e`` reach a real host on purpose."""
        monkeypatch.setenv(egress.MODE_ENV, "forbid")
        monkeypatch.setitem(egress._current, "expected", True)

        # Let through by the plugin: the failure is the network's, not ours.
        with pytest.raises(NOT_DELIVERED), httpx.Client(timeout=TIMEOUT) as client:
            client.get(PROBE_URL)

        assert own_events().get((PROBE_HOST, "http")) == 1


class TestSummary:
    def test_one_call_seen_by_two_layers_is_counted_once(self):
        rows = egress.summarize(
            {
                ("api.openai.com", "a.py::t", "http"): 3,
                ("api.openai.com", "a.py::t", "dns"): 1,
            }
        )
        assert rows == [("api.openai.com", "a.py::t", 3)]

    def test_a_lookup_with_no_http_behind_it_still_shows(self):
        rows = egress.summarize({("cdn.max.test", "b.py::t", "dns"): 2})
        assert rows == [("cdn.max.test", "b.py::t", 2)]

    def test_busiest_first_then_by_name(self):
        rows = egress.summarize(
            {
                ("b.example", "x.py::t", "http"): 1,
                ("a.example", "x.py::t", "http"): 1,
                ("c.example", "y.py::t", "http"): 9,
            }
        )
        assert rows == [
            ("c.example", "y.py::t", 9),
            ("a.example", "x.py::t", 1),
            ("b.example", "x.py::t", 1),
        ]

    def test_nothing_recorded_is_an_empty_report(self):
        assert egress.summarize({}) == []
