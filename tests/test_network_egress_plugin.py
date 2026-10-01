"""``config/pytest_network_egress.py`` — the plugin does what it says.

DRF-2696. The plugin is loaded for the whole run (``addopts``), so most tests
here exercise the live instance: they make a call and read what was recorded
for their own node id — and then drop it, because a foreign host recorded
against a test fails the run, and these tests reach for one on purpose.

Nothing here leaves the machine. A foreign name is refused before any lookup;
where a test needs a call to be LET THROUGH (record mode, an ``e2e`` test), the
real resolver behind the plugin is replaced by one that only counts.

The last class runs pytest in a subprocess: the run's exit code is the
plugin's actual product, and it cannot be observed from inside the run.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from config import pytest_network_egress as egress

#: A name that is neither this machine nor reserved — a vendor, to the plugin.
FOREIGN_HOST = "vendor.egress-probe.net"
FOREIGN_URL = f"http://{FOREIGN_HOST}/v1/anything"
#: A name that cannot resolve (RFC 6761).
RESERVED_HOST = "ayla-api.invalid"
RESERVED_URL = f"http://{RESERVED_HOST}/api/v1/anything"

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def own_events(request):
    """Reader for this test's events; drops them when the test is over."""
    nodeid = request.node.nodeid
    yield lambda: egress.events_for(nodeid)
    egress.discard_events_for(nodeid)


@pytest.fixture
def resolver(monkeypatch):
    """The real resolver behind the plugin, replaced by one that only counts.

    What reaches this list was let through by the plugin; nothing is looked up.
    """
    asked: list[str] = []

    def fake(host, port, *args, **kwargs):
        asked.append(host.decode() if isinstance(host, bytes) else str(host))
        raise socket.gaierror(socket.EAI_NONAME, "counted, not resolved")

    monkeypatch.setattr(egress, "_real_getaddrinfo", fake)
    return asked


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


@pytest.mark.parametrize(
    ("host", "reserved"),
    [
        ("ayla-api.invalid", True),
        ("ayla.test", True),
        ("cdn.max.test", True),
        ("AYLA.TEST", True),
        ("ayla.test.", True),
        (b"ayla.test", True),
        ("svc.example", True),
        ("app.localhost", True),
        ("invalid", True),
        # Reserved by the same RFC, and they DO resolve — a call would leave.
        ("example.com", False),
        ("www.example.org", False),
        # A reserved word inside a real name is not a reserved name.
        ("test.api.openai.com", False),
        ("invalid.botapi.max.ru", False),
        ("mytest", False),
        ("api.openai.com", False),
        ("10.0.0.5", False),
        (None, False),
    ],
)
def test_what_counts_as_a_name_that_cannot_resolve(host, reserved):
    assert egress.is_reserved(host) is reserved


class TestModes:
    def test_refusal_is_the_default(self, monkeypatch):
        monkeypatch.delenv(egress.MODE_ENV, raising=False)
        assert egress.mode() == egress.MODE_FORBID

    @pytest.mark.parametrize("value", ["record", "RECORD", " record "])
    def test_record_is_opt_in_by_name(self, monkeypatch, value):
        monkeypatch.setenv(egress.MODE_ENV, value)
        assert egress.mode() == egress.MODE_RECORD

    @pytest.mark.parametrize("value", ["", "0", "off", "false", "forbid", "anything"])
    def test_nothing_else_switches_the_refusal_off(self, monkeypatch, value):
        """A typo in the variable must not open the network."""
        monkeypatch.setenv(egress.MODE_ENV, value)
        assert egress.mode() == egress.MODE_FORBID


class TestRefusal:
    """Default mode: a foreign host is answered like an unreachable network."""

    def test_sync_transport_is_refused_before_any_lookup(self, own_events, resolver):
        with pytest.raises(httpx.ConnectError) as refused, httpx.Client() as client:
            client.get(FOREIGN_URL)

        assert FOREIGN_HOST in str(refused.value)
        assert own_events() == {(FOREIGN_HOST, "http"): 1}
        assert resolver == []

    @pytest.mark.asyncio
    async def test_async_transport_is_refused_too(self, own_events, resolver):
        """The async transport is the one both vendor SDKs use."""
        with pytest.raises(httpx.ConnectError):
            async with httpx.AsyncClient() as client:
                await client.post(FOREIGN_URL, json={})

        assert own_events() == {(FOREIGN_HOST, "http"): 1}
        assert resolver == []

    def test_a_lookup_without_httpx_is_refused(self, own_events, resolver):
        """``requests``, ``aiohttp`` and a bare socket all end in ``getaddrinfo``."""
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo(FOREIGN_HOST, 443)

        assert own_events() == {(FOREIGN_HOST, "dns"): 1}
        assert resolver == []

    def test_the_refusal_is_what_callers_already_handle(self, own_events):
        """A caller that survives an unreachable network survives the refusal.

        This is why the refusal is a ``ConnectError`` and not a type of its
        own: the test stays green, and the RUN fails (see the last class).
        """

        def best_effort() -> str:
            try:
                httpx.post(FOREIGN_URL, json={})
            except httpx.RequestError:
                return "swallowed"
            return "sent"

        assert best_effort() == "swallowed"
        assert own_events() == {(FOREIGN_HOST, "http"): 1}

    def test_a_mocked_transport_is_not_egress(self, own_events):
        """The same URL through ``MockTransport`` never touches the network."""
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
        with httpx.Client(transport=transport) as client:
            response = client.get(FOREIGN_URL)

        # The call happened — and nothing was recorded for it.
        assert response.json() == {"ok": True}
        assert own_events() == {}

    def test_this_machine_is_never_refused(self, own_events):
        """Loopback through the real transport: the network's own answer, no record."""
        with pytest.raises(httpx.TransportError) as failed, httpx.Client(timeout=2.0) as client:
            client.get("http://127.0.0.1:9/")

        # The network's own failure (nothing listens on port 9). That it is not
        # the plugin's refusal is what the empty record below says: the plugin
        # records every call it refuses.
        assert isinstance(failed.value, httpx.TransportError)
        assert own_events() == {}

    def test_a_test_marked_to_cross_a_boundary_is_let_through(
        self, monkeypatch, own_events, resolver
    ):
        """``smoke`` / ``cross_boundary`` / ``e2e`` reach a real host on purpose."""
        monkeypatch.setitem(egress._current, "expected", True)

        with pytest.raises(socket.gaierror):
            socket.getaddrinfo(FOREIGN_HOST, 443)

        # Let through by the plugin: the resolver behind it was asked.
        assert resolver == [FOREIGN_HOST]
        assert own_events() == {(FOREIGN_HOST, "dns"): 1}


class TestReservedNames:
    """``*.invalid`` and friends: answered at once, in both modes, no violation."""

    @pytest.mark.parametrize("mode", [egress.MODE_FORBID, egress.MODE_RECORD])
    def test_transport_answers_without_asking_a_resolver(
        self, monkeypatch, own_events, resolver, mode
    ):
        monkeypatch.setenv(egress.MODE_ENV, mode)

        with pytest.raises(httpx.ConnectError), httpx.Client() as client:
            client.get(RESERVED_URL)

        assert own_events() == {(RESERVED_HOST, "http"): 1}
        assert resolver == []

    @pytest.mark.parametrize("mode", [egress.MODE_FORBID, egress.MODE_RECORD])
    def test_a_bare_lookup_answers_without_asking_a_resolver(
        self, monkeypatch, own_events, resolver, mode
    ):
        monkeypatch.setenv(egress.MODE_ENV, mode)

        with pytest.raises(socket.gaierror):
            socket.getaddrinfo(RESERVED_HOST, 80)

        assert own_events() == {(RESERVED_HOST, "dns"): 1}
        assert resolver == []

    def test_even_for_a_test_marked_to_cross_a_boundary(self, monkeypatch, own_events, resolver):
        monkeypatch.setitem(egress._current, "expected", True)

        with pytest.raises(socket.gaierror):
            socket.getaddrinfo(RESERVED_HOST, 80)

        assert own_events() == {(RESERVED_HOST, "dns"): 1}
        assert resolver == []


class TestRecordMode:
    """``AYLA_TEST_EGRESS=record`` — the way back: observe, do not refuse."""

    def test_a_foreign_lookup_goes_through_and_is_recorded(self, monkeypatch, own_events, resolver):
        monkeypatch.setenv(egress.MODE_ENV, egress.MODE_RECORD)

        with pytest.raises(socket.gaierror):
            socket.getaddrinfo(FOREIGN_HOST, 443)

        assert resolver == [FOREIGN_HOST]
        assert own_events() == {(FOREIGN_HOST, "dns"): 1}

    def test_a_foreign_request_reaches_the_real_transport(self, monkeypatch, own_events, resolver):
        monkeypatch.setenv(egress.MODE_ENV, egress.MODE_RECORD)

        with pytest.raises(httpx.TransportError), httpx.Client(timeout=2.0) as client:
            client.get(FOREIGN_URL)

        # Both layers saw it, and the real transport went as far as a lookup.
        assert resolver == [FOREIGN_HOST]
        seen = own_events()
        assert seen.get((FOREIGN_HOST, "http")) == 1
        assert seen.get((FOREIGN_HOST, "dns"), 0) >= 1


class TestSummary:
    def test_one_call_seen_by_two_layers_is_counted_once(self):
        rows = egress.summarize(
            {
                ("api.openai.com", "a.py::t", "http"): 3,
                ("api.openai.com", "a.py::t", "dns"): 1,
            }
        )
        assert rows == [(egress.KIND_VIOLATION, "api.openai.com", "a.py::t", 3)]

    def test_a_lookup_with_no_http_behind_it_still_shows(self):
        rows = egress.summarize({("botapi.max.ru", "b.py::t", "dns"): 2})
        assert rows == [(egress.KIND_VIOLATION, "botapi.max.ru", "b.py::t", 2)]

    def test_three_kinds_violations_first(self):
        rows = egress.summarize(
            {
                ("ayla.test", "r.py::t", "http"): 50,
                ("api.openai.com", "e2e.py::t", "http"): 20,
                ("api.openai.com", "v.py::t", "http"): 1,
                ("botapi.max.ru", "v.py::u", "http"): 2,
            },
            expected_tests={"e2e.py::t"},
        )
        assert rows == [
            (egress.KIND_VIOLATION, "botapi.max.ru", "v.py::u", 2),
            (egress.KIND_VIOLATION, "api.openai.com", "v.py::t", 1),
            (egress.KIND_EXPECTED, "api.openai.com", "e2e.py::t", 20),
            (egress.KIND_RESERVED, "ayla.test", "r.py::t", 50),
        ]
        assert [row[2] for row in egress.violations(rows)] == ["v.py::u", "v.py::t"]

    def test_a_reserved_name_is_never_a_violation_even_unexpected(self):
        rows = egress.summarize({("ayla-api.invalid", "g.py::t", "http"): 58})
        assert rows == [(egress.KIND_RESERVED, "ayla-api.invalid", "g.py::t", 58)]
        assert egress.violations(rows) == []

    def test_nothing_recorded_is_an_empty_report(self):
        assert egress.summarize({}) == []


class TestTheRunFails:
    """The product of the plugin: a green test, a failed run, a named host."""

    def _session(self):
        return SimpleNamespace(config=SimpleNamespace(), exitstatus=pytest.ExitCode.OK)

    def test_a_violation_turns_a_green_run_red(self, own_events):
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo(FOREIGN_HOST, 443)
        session = self._session()

        egress.pytest_sessionfinish(session)

        assert own_events() == {(FOREIGN_HOST, "dns"): 1}
        assert session.exitstatus == pytest.ExitCode.TESTS_FAILED

    def test_a_reserved_name_does_not(self, own_events):
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo(RESERVED_HOST, 80)
        session = self._session()

        egress.pytest_sessionfinish(session)

        assert own_events() == {(RESERVED_HOST, "dns"): 1}
        assert session.exitstatus == pytest.ExitCode.OK

    def test_record_mode_leaves_the_exit_code_alone(self, monkeypatch, own_events, resolver):
        monkeypatch.setenv(egress.MODE_ENV, egress.MODE_RECORD)
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo(FOREIGN_HOST, 443)
        session = self._session()

        egress.pytest_sessionfinish(session)

        assert own_events() == {(FOREIGN_HOST, "dns"): 1}
        assert session.exitstatus == pytest.ExitCode.OK

    def test_a_run_already_red_keeps_its_own_code(self, own_events):
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo(FOREIGN_HOST, 443)
        session = self._session()
        session.exitstatus = pytest.ExitCode.INTERRUPTED

        egress.pytest_sessionfinish(session)

        assert own_events() == {(FOREIGN_HOST, "dns"): 1}
        assert session.exitstatus == pytest.ExitCode.INTERRUPTED

    _INNER = textwrap.dedent(
        """
        import httpx
        import pytest


        def test_a_caller_that_swallows_the_refusal():
            try:
                httpx.post("http://vendor.egress-probe.net/v1/x", json={})
            except httpx.RequestError:
                pass


        def test_no_such_service_here():
            with pytest.raises(httpx.ConnectError):
                httpx.get("http://ayla-api.invalid/api/v1/x")


        def test_that_stays_home():
            assert 1 + 1 == 2
        """
    )

    def _run(self, tmp_path: Path, *extra: str, inner: str | None = None):
        (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        (tmp_path / "test_inner.py").write_text(inner or self._INNER, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != egress.MODE_ENV}
        env.pop("PYTEST_XDIST_WORKER", None)
        env.pop("PYTEST_XDIST_WORKER_COUNT", None)
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-c",
                str(tmp_path / "pytest.ini"),
                "--rootdir",
                str(tmp_path),
                "-p",
                "config.pytest_network_egress",
                "-p",
                "no:django",
                "-p",
                "no:cacheprovider",
                *extra,
                str(tmp_path / "test_inner.py"),
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

    @pytest.mark.parametrize("extra", [(), ("-n", "2")], ids=["one-process", "xdist"])
    def test_every_test_green_and_the_run_red(self, tmp_path, extra):
        result = self._run(tmp_path, *extra)
        out = result.stdout

        assert "3 passed" in out, out + result.stderr
        assert result.returncode == pytest.ExitCode.TESTS_FAILED, out
        assert "RUN FAILED" in out
        violation = [line for line in out.splitlines() if line.strip().startswith("EGRESS    ")]
        assert len(violation) == 1, out
        assert "vendor.egress-probe.net" in violation[0]
        assert "test_a_caller_that_swallows_the_refusal" in violation[0]
        reserved = [line for line in out.splitlines() if line.strip().startswith("reserved    ")]
        assert len(reserved) == 1, out
        assert "ayla-api.invalid" in reserved[0]

    def test_reserved_names_alone_leave_the_run_green(self, tmp_path):
        inner = self._INNER.replace("http://vendor.egress-probe.net/v1/x", "http://ayla.test/v1/x")
        result = self._run(tmp_path, inner=inner)
        out = result.stdout

        assert "3 passed" in out, out + result.stderr
        assert result.returncode == pytest.ExitCode.OK, out
        assert "EGRESS none" in out
        assert "reserved tests=2" in out
