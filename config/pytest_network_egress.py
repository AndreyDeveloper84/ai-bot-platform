"""No test reaches a host that is not this machine.

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
  tests. They leave no machine, and they still ran the real transport up to
  the resolver.

None of it was visible. The callers swallow the failure — a 401 with the CI's
placeholder key, a refused connection, a failed lookup — so the tests were
green whatever the network did. The first sign was a different test going red
on what one of these left behind in the database (DRF-2691).

With a real key or token in the environment the same tests would have made
paid calls and sent actions to real chats. Nothing in the suite would have
said so.

### What this plugin does

**A call to a foreign host does not leave the process, and the run fails.**

* The call is answered the way an unreachable network answers:
  ``httpx.ConnectError`` from the transport, ``socket.gaierror`` from the
  resolver. Not a new exception type — the callers already handle these two,
  and a test whose subject is «what happens when the service is down» keeps
  meaning exactly that. Nothing is sent, nothing is looked up.
* The violation is not lost in whichever ``except`` swallowed it: it is
  recorded against the test, printed in the run's summary as ``host — test``,
  and the **run exits non-zero**. The test itself may well be green; the run
  is not. Read the summary, not the traceback.

That split — quiet for the test, loud for the run — is deliberate. Raising
something the callers do not catch would have turned every swallowed failure
into a red test at once, and what a red test would then be asserting is that
the plugin exists.

### Reserved names are not egress

``*.invalid``, ``*.test``, ``*.example`` and ``*.localhost`` (RFC 6761) cannot
resolve, by definition. A base URL like ``http://ayla-api.invalid`` is the
honest way for a test to say «there is no such service here», and 54 tests in
7 files say it. They are answered at once, with the same two errors and
without asking a resolver, and they are not a violation. The summary lists
them under ``reserved``.

### How this was introduced — the radius was measured before anything refused

``apps/llm/conftest.py`` says why a repository-wide autouse check is not
introduced in one go: nobody has weighed its blast radius. This one ran in
registration mode first (#2245) and its report in CI was the weighing: with
the vendor call sites repaired, the runner's own list held no vendor host at
all — only reserved names. Refusal became the default on that evidence.

``AYLA_TEST_EGRESS=record`` is the way back without a deploy of anything: the
plugin then only observes and reports, calls go through, the run's exit code
is untouched. Reserved names are short-circuited in both modes.

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
  boundary. Their calls go through, and the summary lists them as
  ``expected``.

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

#: ``forbid`` (default) — refuse and fail the run. ``record`` — only observe.
MODE_ENV = "AYLA_TEST_EGRESS"
MODE_RECORD = "record"
MODE_FORBID = "forbid"

#: Tests carrying one of these marks cross a real boundary on purpose.
EXPECTED_EGRESS_MARKS: frozenset[str] = frozenset({"smoke", "cross_boundary", "e2e"})

#: RFC 6761 — top-level names that never resolve. Not ``example.com`` and its
#: siblings: those are reserved too, and they DO resolve, so a call to one
#: would leave the machine.
RESERVED_SUFFIXES: tuple[str, ...] = (".invalid", ".test", ".example", ".localhost")

_LOCAL_NAMES: frozenset[str] = frozenset({"localhost", "testserver", ""})
_OUTSIDE = "<outside any test>"

KIND_VIOLATION = "EGRESS"
KIND_RESERVED = "reserved"
KIND_EXPECTED = "expected"

_lock = threading.Lock()
#: (host, nodeid, layer) -> count. ``layer`` is ``http`` or ``dns``.
_events: Counter[tuple[str, str, str]] = Counter()
_current: dict[str, Any] = {"nodeid": _OUTSIDE, "expected": False}


def mode() -> str:
    value = os.environ.get(MODE_ENV, MODE_FORBID).strip().lower()
    return MODE_RECORD if value == MODE_RECORD else MODE_FORBID


def _host_text(host: object) -> str:
    if isinstance(host, bytes | bytearray):
        return bytes(host).decode("ascii", "replace")
    return str(host)


def _normalized(host: object) -> str:
    return _host_text(host).strip().lower().strip("[]").rstrip(".")


def is_local(host: object) -> bool:
    """True for this machine: loopback, ``localhost``, Django's ``testserver``."""
    if host is None:
        return True
    name = _normalized(host)
    if name in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def is_reserved(host: object) -> bool:
    """True for a name that cannot resolve (RFC 6761)."""
    if host is None:
        return False
    name = _normalized(host)
    return any(name.endswith(suffix) or name == suffix[1:] for suffix in RESERVED_SUFFIXES)


def _note(host: object, layer: str) -> bool:
    """Record one event for the running test. True when it must not go out."""
    name = _host_text(host)
    with _lock:
        _events[(name, _current["nodeid"], layer)] += 1
    if is_reserved(name):
        return True
    return mode() == MODE_FORBID and not _current["expected"]


def _why(host: object) -> str:
    name = _host_text(host)
    if is_reserved(name):
        return f"{name} is a reserved name that cannot resolve (config/pytest_network_egress.py)"
    return (
        f"test egress refused: {name} is not this machine. Stub the call where it is "
        "made; the run fails on this (config/pytest_network_egress.py)"
    )


def events_for(nodeid: str) -> dict[tuple[str, str], int]:
    """``{(host, layer): count}`` recorded so far for one test."""
    with _lock:
        return {(h, layer): n for (h, t, layer), n in _events.items() if t == nodeid}


def discard_events_for(nodeid: str) -> None:
    """Drop what was recorded for one test.

    For the tests of this plugin: they reach for a foreign name on purpose,
    and must neither appear in the report they are testing nor fail the run.
    """
    with _lock:
        for key in [k for k in _events if k[1] == nodeid]:
            del _events[key]


# --- layer 1: name resolution -------------------------------------------------

_real_getaddrinfo = socket.getaddrinfo


def _getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
    if not is_local(host) and _note(host, "dns"):
        raise socket.gaierror(socket.EAI_NONAME, _why(host))
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
        host = request.url.host
        if not is_local(host) and _note(host, "http"):
            raise httpx.ConnectError(_why(host), request=request)
        return await real_async(self, request)

    def handle_request(self: Any, request: Any) -> Any:
        host = request.url.host
        if not is_local(host) and _note(host, "http"):
            raise httpx.ConnectError(_why(host), request=request)
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


def pytest_testnodedown(node: Any, error: Any) -> None:
    rows = getattr(node, "workeroutput", {}).get(_WORKER_KEY, [])
    with _lock:
        for host, test, layer, count in rows:
            _events[(host, test, layer)] += count


def summarize(
    events: dict[tuple[str, str, str], int],
    expected_tests: frozenset[str] | set[str] = frozenset(),
) -> list[tuple[str, str, str, int]]:
    """``[(kind, host, test, requests)]`` — violations first, busiest first.

    One row per (host, test). The count is HTTP requests when the HTTP layer
    saw any, lookups otherwise — the two layers see the same call, and adding
    them would count it twice.
    """
    http: Counter[tuple[str, str]] = Counter()
    dns: Counter[tuple[str, str]] = Counter()
    for (host, test, layer), count in events.items():
        (http if layer == "http" else dns)[(host, test)] += count
    pairs = [(h, t, n) for (h, t), n in http.items()]
    pairs += [(h, t, n) for (h, t), n in dns.items() if (h, t) not in http]

    def kind(host: str, test: str) -> str:
        if is_reserved(host):
            return KIND_RESERVED
        return KIND_EXPECTED if test in expected_tests else KIND_VIOLATION

    order = {KIND_VIOLATION: 0, KIND_EXPECTED: 1, KIND_RESERVED: 2}
    rows = [(kind(h, t), h, t, n) for h, t, n in pairs]
    return sorted(rows, key=lambda row: (order[row[0]], -row[3], row[1], row[2]))


def violations(rows: list[tuple[str, str, str, int]]) -> list[tuple[str, str, str, int]]:
    return [row for row in rows if row[0] == KIND_VIOLATION]


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    workeroutput = getattr(session.config, "workeroutput", None)
    if workeroutput is not None:
        # An xdist worker: hand the events over, the controller decides.
        with _lock:
            workeroutput[_WORKER_KEY] = [[h, t, layer, n] for (h, t, layer), n in _events.items()]
        return
    if mode() != MODE_FORBID:
        return
    with _lock:
        rows = summarize(dict(_events), _EXPECTED_TESTS)
    if violations(rows) and session.exitstatus == pytest.ExitCode.OK:
        # Every test may be green — the callers swallow a refused connection.
        # The run is not: that is the whole point of refusing at this level.
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: Any) -> None:
    with _lock:
        rows = summarize(dict(_events), _EXPECTED_TESTS)
    refusing = mode() == MODE_FORBID
    terminalreporter.section(f"network egress from tests (DRF-2696, mode: {mode()})")
    if not rows:
        terminalreporter.write_line("EGRESS none: no test reached for a host outside this machine.")
        return

    bad = violations(rows)
    if bad:
        hosts = sorted({row[1] for row in bad})
        verdict = "refused, RUN FAILED" if refusing else "let through (record mode)"
        terminalreporter.write_line(
            f"EGRESS tests={len({row[2] for row in bad})} requests={sum(row[3] for row in bad)} "
            f"hosts={len(hosts)} ({', '.join(hosts)}) — {verdict}"
        )
    else:
        terminalreporter.write_line("EGRESS none: no test reached for a host outside this machine.")

    for label in (KIND_EXPECTED, KIND_RESERVED):
        some = [row for row in rows if row[0] == label]
        if some:
            names = sorted({row[1] for row in some})
            terminalreporter.write_line(
                f"{label} tests={len({row[2] for row in some})} "
                f"requests={sum(row[3] for row in some)} ({', '.join(names)})"
            )

    for kind, host, test, count in rows:
        terminalreporter.write_line(f"  {kind} {count:4d}  {host}  {test}")
