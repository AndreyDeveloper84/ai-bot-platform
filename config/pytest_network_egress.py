"""Name the tests that open a connection to a host that is not this machine.

DRF-2696. Loaded via ``-p`` in ``pyproject.toml``'s ``addopts``, next to the
three plugins already there and for the same reason: it has to be in place
before any test, and before any conftest, runs.

### What was happening

Measured on ``dev`` f8579a5e (Postgres 16, the five CI shards at ``-n 4`` plus
``tests/``, 20 418 tests, none red): **420 real HTTP requests per run, from 99
tests in 16 files.**

* ``api.openai.com`` — 257 requests, 6 tests. 254 of them from one file, a
  latency SLO test whose provider patch had stopped intercepting anything.
* ``botapi.max.ru`` — 62 requests, 29 tests: typing indicators and
  ``POST /messages``.
* names that cannot resolve (``*.invalid``, ``*.test``) — 101 requests, 64
  tests. They leave no machine, and they still run the real transport up to
  the resolver.

None of it was visible. The callers swallow the failure — a 401 with the CI's
placeholder key, a refused connection, a failed lookup — so the tests were
green whatever the network did. The first sign was a different test going red
on what one of these left behind in the database (DRF-2691).

With a real key or token in the environment the same tests would have made
paid calls and sent actions to real chats. Nothing in the suite would have
said so.

### What this plugin does — registration, not refusal

It records, and passes every call through unchanged. At the end of the run it
prints one line per (host, test). Nothing fails because of it.

That is deliberate and temporary. ``apps/llm/conftest.py`` says why a
repository-wide autouse check is not introduced in one go: nobody has weighed
its blast radius. This is the weighing — the list in the CI log is the radius,
measured where it matters. ``AYLA_TEST_EGRESS=forbid`` turns the same
observation into a refusal; it becomes the default when that list is empty.

### Two layers, because each sees what the other cannot

* **httpx real transports** (``HTTPTransport`` / ``AsyncHTTPTransport``). Gives
  the method and the URL. ``httpx.MockTransport`` and anything else that is
  not one of these two classes does not pass through here — a mocked call is
  not egress and is not recorded as such.
* **``socket.getaddrinfo``**. Library-agnostic: ``requests``, ``aiohttp``,
  asyncio's executor resolver and a bare ``socket.create_connection`` all end
  in it when they are given a name.

### What it does not see — stated, because a silent limit reads as coverage

* A connection to a **literal IP address** made without httpx: no lookup, no
  transport, no record. (Through httpx an IP is seen like any other host.)
* Anything a test does in a **subprocess**.
* A transport that a test **replaces at class level** itself.
* ``smoke``, ``cross_boundary`` and ``e2e`` tests are meant to cross a real
  boundary. Their egress is recorded like any other and marked as expected in
  the report; under ``forbid`` it is allowed.

What counts as local: loopback addresses, ``localhost``, and ``testserver``
(Django's test client host). CI's Postgres and Redis are on ``localhost``.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import threading
from collections import Counter
from typing import Any

import pytest

#: ``record`` (default) — observe and report. ``forbid`` — refuse.
MODE_ENV = "AYLA_TEST_EGRESS"
MODE_RECORD = "record"
MODE_FORBID = "forbid"

#: Tests carrying one of these marks cross a real boundary on purpose.
EXPECTED_EGRESS_MARKS: frozenset[str] = frozenset({"smoke", "cross_boundary", "e2e"})

_LOCAL_NAMES: frozenset[str] = frozenset({"localhost", "testserver", ""})
_OUTSIDE = "<outside any test>"

_lock = threading.Lock()
#: (host, nodeid, layer) -> count. ``layer`` is ``http`` or ``dns``.
_events: Counter[tuple[str, str, str]] = Counter()
_current: dict[str, Any] = {"nodeid": _OUTSIDE, "expected": False}


class NetworkEgressForbidden(RuntimeError):
    """A test tried to reach a host that is not this machine (``forbid`` mode)."""


def mode() -> str:
    value = os.environ.get(MODE_ENV, MODE_RECORD).strip().lower()
    return MODE_FORBID if value == MODE_FORBID else MODE_RECORD


def is_local(host: object) -> bool:
    """True for this machine: loopback, ``localhost``, Django's ``testserver``."""
    if host is None:
        return True
    if isinstance(host, bytes | bytearray):
        host = bytes(host).decode("ascii", "replace")
    name = str(host).strip().lower().strip("[]")
    if name in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _host_text(host: object) -> str:
    if isinstance(host, bytes | bytearray):
        return bytes(host).decode("ascii", "replace")
    return str(host)


def _note(host: object, layer: str) -> None:
    """Record one event for the running test; refuse it in ``forbid`` mode."""
    name = _host_text(host)
    with _lock:
        _events[(name, _current["nodeid"], layer)] += 1
    if mode() == MODE_FORBID and not _current["expected"]:
        raise NetworkEgressForbidden(
            f"a test reached for the network: {name} (seen at the {layer} layer). "
            "Stub the call where it is made; a test must not depend on a host "
            "outside this machine. config/pytest_network_egress.py explains."
        )


def events_for(nodeid: str) -> dict[tuple[str, str], int]:
    """``{(host, layer): count}`` recorded so far for one test."""
    with _lock:
        return {(h, layer): n for (h, t, layer), n in _events.items() if t == nodeid}


def discard_events_for(nodeid: str) -> None:
    """Drop what was recorded for one test.

    For the tests of this plugin: they reach for a non-local name on purpose,
    and must not appear in the report they are testing.
    """
    with _lock:
        for key in [k for k in _events if k[1] == nodeid]:
            del _events[key]


# --- layer 1: name resolution -------------------------------------------------

_real_getaddrinfo = socket.getaddrinfo


def _getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
    if not is_local(host):
        _note(host, "dns")
    return _real_getaddrinfo(host, port, *args, **kwargs)


# --- layer 2: httpx real transports -------------------------------------------


def _install_httpx() -> None:
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a hard dependency today
        return

    real_async = httpx.AsyncHTTPTransport.handle_async_request
    real_sync = httpx.HTTPTransport.handle_request

    async def handle_async_request(self: Any, request: Any) -> Any:
        if not is_local(request.url.host):
            _note(request.url.host, "http")
        return await real_async(self, request)

    def handle_request(self: Any, request: Any) -> Any:
        if not is_local(request.url.host):
            _note(request.url.host, "http")
        return real_sync(self, request)

    httpx.AsyncHTTPTransport.handle_async_request = handle_async_request  # type: ignore[method-assign]
    httpx.HTTPTransport.handle_request = handle_request  # type: ignore[method-assign]


def _install() -> None:
    socket.getaddrinfo = _getaddrinfo
    _install_httpx()


_install()


# --- which test is running ----------------------------------------------------


def _enter(item: pytest.Item) -> None:
    _current["nodeid"] = item.nodeid
    _current["expected"] = any(item.get_closest_marker(m) for m in EXPECTED_EGRESS_MARKS)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Any:
    _enter(item)
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Any:
    _enter(item)
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> Any:
    _enter(item)
    try:
        return (yield)
    finally:
        _current["nodeid"] = _OUTSIDE
        _current["expected"] = False


# --- report: workers hand their events to the controller ----------------------

_EXPECTED_TESTS: set[str] = set()
_WORKER_KEY = "ayla_network_egress"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if any(item.get_closest_marker(m) for m in EXPECTED_EGRESS_MARKS):
            _EXPECTED_TESTS.add(item.nodeid)


def pytest_sessionfinish(session: pytest.Session) -> None:
    workeroutput = getattr(session.config, "workeroutput", None)
    if workeroutput is not None:
        with _lock:
            workeroutput[_WORKER_KEY] = [[h, t, layer, n] for (h, t, layer), n in _events.items()]


def pytest_testnodedown(node: Any, error: Any) -> None:
    rows = getattr(node, "workeroutput", {}).get(_WORKER_KEY, [])
    with _lock:
        for host, test, layer, count in rows:
            _events[(host, test, layer)] += count


def summarize(events: dict[tuple[str, str, str], int]) -> list[tuple[str, str, int]]:
    """``[(host, test, requests)]``, busiest first.

    One row per (host, test). The count is HTTP requests when the HTTP layer
    saw any, lookups otherwise — the two layers see the same call, and adding
    them would count it twice.
    """
    http: Counter[tuple[str, str]] = Counter()
    dns: Counter[tuple[str, str]] = Counter()
    for (host, test, layer), count in events.items():
        (http if layer == "http" else dns)[(host, test)] += count
    rows = [(h, t, n) for (h, t), n in http.items()]
    rows += [(h, t, n) for (h, t), n in dns.items() if (h, t) not in http]
    return sorted(rows, key=lambda row: (-row[2], row[0], row[1]))


def pytest_terminal_summary(terminalreporter: Any) -> None:
    with _lock:
        rows = summarize(dict(_events))
    terminalreporter.section(f"network egress from tests (DRF-2696, mode: {mode()})")
    if not rows:
        terminalreporter.write_line("EGRESS none: no test reached a host outside this machine.")
        return
    unexpected = [r for r in rows if r[1] not in _EXPECTED_TESTS]
    hosts = sorted({r[0] for r in unexpected})
    terminalreporter.write_line(
        f"EGRESS tests={len({r[1] for r in unexpected})} requests={sum(r[2] for r in unexpected)} "
        f"hosts={len(hosts)} ({', '.join(hosts)})"
    )
    for host, test, count in rows:
        tag = "expected" if test in _EXPECTED_TESTS else "EGRESS"
        terminalreporter.write_line(f"  {tag} {count:4d}  {host}  {test}")
