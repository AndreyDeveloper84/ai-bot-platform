"""Running a `golden/` fixture through the path that actually answers a client.

### Why this module exists at all

`apps/replay/fixtures/golden/` holds eighty fixtures. Until now not one of
them had ever been executed against the code that answers a person. The
replay workflow calls itself «replay fixtures (golden + adversarial +
voice)» and the golden third of that title was never true: the only golden
checks in CI (`test_fixtures.py`) assert that the YAML parses, that there
are eighty of them, and that each has at least one rule. A fixture that
parses is not a fixture that passes.

`test_live_path_gate.py` gates the other two sets and explains why it
excludes this one: `golden/` holds per-tenant skill scenarios, and the
global (tenant-less) handler never dispatches a skill. That reasoning is
correct and stays. The conclusion drawn from it — «so golden cannot be
gated» — did not follow. Golden has its own answering path, one door over:
`apps.channels.max.handler.handle_max_event`, the per-tenant sibling that
`apps/channels/handlers.py` calls in production.

Not `apps.orchestrator.pipeline.turn`, which is what `python -m apps.replay
run --fixture-set …/golden` drives. `turn()` has no production callers
outside docstrings and tests (grep it), so the CLI has always been running
the golden set against a system nobody talks to. A green CLI run would have
proved nothing about the pilot.

### What can honestly be gated, and what cannot

Same split as the live-path gate, drawn with a sharper instrument.

A golden fixture is gateable when its reply came out of our own
deterministic code. It is NOT gateable when producing the reply required
something outside this process — a language model, or the Ayla nutrition /
booking API. In CI neither exists: calling them would make the gate paid,
slow and third-party-dependent, and mocking them turns the fixture into a
check of the mock.

The live-path gate decides this by mocking the one LLM entry point it knows
about, and its own docstring admits the weakness — «the fixture mocks
`generate_concierge_reply` but not the resolver, so the promise depended on
one test file remembering every LLM entry point». It cannot, and DRF-1310
proved it: a typo fix turned every fixture turn into a real TLS round trip
and nobody noticed until the job time tripled.

So this module does not enumerate seams. :class:`NetworkTripwire` replaces
the socket layer for the duration of a turn: every outbound TCP connection
is recorded and refused. Nothing can reach a vendor, and «did this turn need
the outside world?» is answered by observation rather than by a list
somebody has to remember to update. A new LLM or HTTP client added tomorrow
is classified correctly on the day it lands, without touching this file.

Postgres is unaffected — psycopg's binary build talks libpq in C and never
touches Python's ``socket`` module — and Redis is faked by the caller.

### Preconditions the fixtures assume but never stated

Two of them, both made explicit here rather than left to luck:

* **The client has been greeted.** `WelcomeSkill` intercepts the first
  message from any `BotUser` with `welcomed_at IS NULL`, so without this
  every golden fixture would assert against the welcome copy. Golden
  scenarios are mid-conversation turns; the gate stamps `welcomed_at`.
* **Prior turns.** A validation-error fixture («150» must be refused as an
  age) is meaningless without the anketa being open. ``input.prior_texts``
  carries the setup turns; the gate replays them, asserts nothing about
  them, and then asserts the fixture on the turn that follows. ``input`` is
  already a free-form mapping in the schema, so this needs no loader change.
"""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.replay.fixtures.schema import Fixture

logger = logging.getLogger(__name__)

#: Log line the skill registry already emits on every match. Read rather
#: than recomputed: asking the registry a second time which skill would
#: match is asking the thing under test to grade itself, and it would go on
#: agreeing with itself after the dispatcher broke.
_MATCH_PREFIX = "skills.dispatch.match name="


class SkillNameProbe(logging.Handler):
    """Capture the matched skill's name from the registry's own log line."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.skill_name: str = ""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — a probe must never break a turn
            return
        if message.startswith(_MATCH_PREFIX):
            self.skill_name = message[len(_MATCH_PREFIX) :].strip()


class NetworkTripwire:
    """Refuse and record every outbound TCP connection made inside the block.

    Two jobs, and the second is the one that matters:

    1. CI cannot reach a vendor. Not «should not» — cannot. A forgotten mock
       costs a fast, deterministic refusal instead of a paid call.
    2. The turn is classified by what it *did*, not by what a test file
       remembered to patch. ``attempts`` non-empty means this reply needed
       the outside world, so the fixture is not CI-checkable and must be
       reported as uncovered rather than asserted.

    Postgres is not affected: psycopg[binary] speaks libpq in C and never
    enters Python's ``socket`` module. If that ever changes the gate goes
    loudly red (every fixture «needs the network») rather than quietly
    wrong — which is the failure direction to prefer.

    ### Why name resolution is hooked too, not just ``connect``

    ``socket.getaddrinfo`` is recorded and refused as well, and that is the
    hook that actually fires on Windows: the Proactor event loop opens
    connections through an overlapped IOCP ``ConnectEx`` that never enters
    ``socket.socket.connect``, so an httpx call would have slipped past a
    connect-only tripwire on a developer box and been caught on Linux CI.
    Resolution happens on both. A gate whose classification depends on the
    developer's operating system is a gate that will be argued with instead
    of believed.

    ### The one exemption, and why it is not an allow-list

    ``socket.socketpair`` is exempt. On Windows it is implemented in pure
    Python as a loopback listen+connect, and `asyncio.run` builds its
    self-pipe from one — so without the exemption every skill that bridges
    async to sync would be reported as «needed the network» on a developer
    box and not in Linux CI, which uses ``os.pipe()``. A gate that
    classifies differently on the two machines is worse than no gate.

    The exemption is on the *call*, not on an address. Allow-listing
    loopback instead would have been the obvious move and it is a trap:
    `replay.yml` deliberately points ``OPENAI_PROXY`` at ``127.0.0.1:1``, so
    a loopback allow-list would classify every model call in CI as our own
    deterministic code — the exact silent mis-classification this class
    exists to prevent.
    """

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._saved: dict[str, Any] = {}
        self._disarmed = 0

    def _record(self, address: Any) -> None:
        try:
            host, port = address[0], address[1]
            if isinstance(host, bytes):
                host = host.decode("utf-8", errors="replace")
            self.attempts.append(f"{host}:{port}")
        except Exception:  # noqa: BLE001
            self.attempts.append(repr(address))

    def __enter__(self) -> NetworkTripwire:
        tripwire = self
        saved_connect = socket.socket.connect
        saved_connect_ex = socket.socket.connect_ex
        saved_create_connection = socket.create_connection
        saved_socketpair = socket.socketpair
        saved_getaddrinfo = socket.getaddrinfo

        def _getaddrinfo(host: Any, port: Any = None, *args: Any, **kwargs: Any) -> Any:
            if tripwire._disarmed:
                return saved_getaddrinfo(host, port, *args, **kwargs)
            tripwire._record((host, port))
            raise socket.gaierror(
                f"replay golden gate: name resolution is closed here (tried {host!r})"
            )

        def _connect(sock: socket.socket, address: Any) -> Any:
            if tripwire._disarmed:
                return saved_connect(sock, address)
            tripwire._record(address)
            raise ConnectionRefusedError(
                f"replay golden gate: outbound network is closed here (tried {address!r})"
            )

        def _connect_ex(sock: socket.socket, address: Any) -> int:
            if tripwire._disarmed:
                return saved_connect_ex(sock, address)
            tripwire._record(address)
            return 111  # ECONNREFUSED

        def _create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
            if tripwire._disarmed:
                return saved_create_connection(address, *args, **kwargs)
            tripwire._record(address)
            raise ConnectionRefusedError(
                f"replay golden gate: outbound network is closed here (tried {address!r})"
            )

        def _socketpair(*args: Any, **kwargs: Any) -> Any:
            tripwire._disarmed += 1
            try:
                return saved_socketpair(*args, **kwargs)
            finally:
                tripwire._disarmed -= 1

        self._saved = {
            "connect": saved_connect,
            "connect_ex": saved_connect_ex,
            "create_connection": saved_create_connection,
            "socketpair": saved_socketpair,
            "getaddrinfo": saved_getaddrinfo,
        }
        socket.socket.connect = _connect  # type: ignore[method-assign]
        socket.socket.connect_ex = _connect_ex  # type: ignore[method-assign]
        socket.create_connection = _create_connection  # type: ignore[assignment]
        socket.socketpair = _socketpair  # type: ignore[assignment]
        socket.getaddrinfo = _getaddrinfo  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: Any) -> None:
        socket.socket.connect = self._saved["connect"]  # type: ignore[method-assign]
        socket.socket.connect_ex = self._saved["connect_ex"]  # type: ignore[method-assign]
        socket.create_connection = self._saved["create_connection"]  # type: ignore[assignment]
        socket.socketpair = self._saved["socketpair"]  # type: ignore[assignment]
        socket.getaddrinfo = self._saved["getaddrinfo"]  # type: ignore[assignment]


class ModelSeamProbe:
    """Record (and refuse) every attempt to acquire a language-model provider.

    The socket tripwire above is the general instrument and it has one blind
    spot in a test process: a turn can decide it wants a model and fail
    before any socket is opened. `apps.llm.cost_tracker` reads the tenant's
    caps through ``sync_to_async(..., thread_sensitive=False)``, so under
    pytest-django's non-committing transaction the lookup runs on a second
    connection, cannot see the test tenant, and raises `UnknownTenantError`
    — which the FAQ / booking envelope turns into the friendly handoff line.

    Without this probe such a turn looks deterministic (no socket was
    touched) and the gate would assert the fixture against an error branch:
    a green check meaning «the model was unreachable and we degraded
    politely», printed as though it meant «this skill answers correctly».

    Every provider in the codebase is acquired through
    ``LLMRouter.get_provider``, so patching the method covers the call sites
    that exist. The socket tripwire is what covers the ones that do not
    exist yet.
    """

    def __init__(self) -> None:
        self.requests: list[str] = []
        self._saved: Any = None

    def __enter__(self) -> ModelSeamProbe:
        from apps.llm.protocol import LLMProviderUnavailable
        from apps.llm.router import LLMRouter

        probe = self
        self._saved = LLMRouter.get_provider

        def _get_provider(self_router: Any, tenant: Any = None, **kwargs: Any) -> Any:
            probe.requests.append(f"{kwargs.get('skill', '?')}:{kwargs.get('op', 'complete')}")
            raise LLMProviderUnavailable(
                "replay golden gate: CI has no model, and this gate will not pretend it does"
            )

        LLMRouter.get_provider = _get_provider  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: Any) -> None:
        from apps.llm.router import LLMRouter

        LLMRouter.get_provider = self._saved  # type: ignore[assignment]


def prior_texts(fixture: Fixture) -> list[str]:
    """Setup turns this fixture needs before its own turn means anything.

    Empty for most fixtures. Declared inside ``input`` because the schema
    already lets ``input`` carry arbitrary keys — no loader change, and the
    precondition lives next to the turn it belongs to.
    """

    raw = fixture.input.get("prior_texts") or []
    if isinstance(raw, str):
        return [raw]
    return [str(item) for item in raw]


def build_max_payload(
    fixture_text: str,
    *,
    user_id: int,
    mid: str,
    has_attachments: bool = False,
) -> dict[str, Any]:
    """One turn of fixture text into the MAX webhook payload production parses.

    A ``cb:`` text is a button tap, and MAX delivers those as
    ``message_callback`` with the payload in a different place. Sending it as
    ordinary message text would happen to work — the parser folds the
    callback payload into ``text`` — but it would take the wrong idempotency
    key and the fixture would exercise a shape production never produces.
    """

    # Milliseconds since the epoch, taken from the clock rather than
    # written down. A fixture timestamp frozen at a point in the past is
    # the shape that has already held `dev` red for four days once.
    # Nothing compares this field today; the cost of not depending on that
    # staying true is one function call. The sibling helper in
    # `live_path.py` carried the literal and was changed with it.
    now_ms = int(time.time() * 1000)

    attachments = (
        [{"type": "image", "payload": {"url": "https://cdn.invalid/replay-photo.jpg"}}]
        if has_attachments
        else []
    )

    if fixture_text.startswith("cb:"):
        return {
            "update_type": "message_callback",
            "timestamp": now_ms,
            "callback": {
                "timestamp": now_ms,
                "callback_id": f"cb-{mid}",
                "payload": fixture_text,
                "user": {"user_id": user_id, "name": "Replay"},
            },
            "message": {
                "sender": {"user_id": user_id, "name": "Replay"},
                "recipient": {"chat_id": user_id, "chat_type": "dialog"},
                "body": {"mid": mid, "seq": 1, "text": "", "attachments": attachments},
            },
        }

    return {
        "update_type": "message_created",
        "timestamp": now_ms,
        "message": {
            "sender": {"user_id": user_id, "name": "Replay"},
            "recipient": {"chat_id": user_id, "chat_type": "dialog"},
            "body": {
                "mid": mid,
                "seq": 1,
                "text": fixture_text,
                "attachments": attachments,
            },
        },
    }


@dataclass
class GoldenPathResult:
    """What one golden fixture produced on the per-tenant answering path."""

    fixture_name: str
    response_text: str
    skill_used: str
    sent_count: int
    #: host:port of every outbound connection the turn attempted.
    network_attempts: list[str] = field(default_factory=list)
    #: skill:op of every language-model provider the turn asked for.
    model_requests: list[str] = field(default_factory=list)
    #: Set when the handler raised instead of answering.
    error: str = ""

    @property
    def needed_the_outside_world(self) -> bool:
        return bool(self.network_attempts or self.model_requests)

    @property
    def outside_world_notes(self) -> list[str]:
        return [f"net {a}" for a in self.network_attempts] + [
            f"model {r}" for r in self.model_requests
        ]

    @property
    def deterministic(self) -> bool:
        """The reply is entirely ours, so every rule in the fixture applies."""

        return not self.needed_the_outside_world and not self.error

    def as_trace(self) -> dict[str, Any]:
        """The shape :mod:`apps.replay.assertions` reads.

        ``intent`` stays empty and no fixture may assert on it: the
        per-tenant path classifies no intent (``classify_intent`` has no
        callers outside tests), and inventing a value here would let a
        fixture assert on something the system never computes.

        ``safety_decision`` is «allow» because a golden fixture the safety
        gate refused would never have reached a skill — the set that gates
        refusals is `adversarial/`, which has its own test for exactly that.
        """

        return {
            "intent": "",
            "skill_used": self.skill_used,
            "safety_decision": "allow",
            "tool_calls": [],
            "response_text": self.response_text,
        }


__all__ = [
    "GoldenPathResult",
    "ModelSeamProbe",
    "NetworkTripwire",
    "SkillNameProbe",
    "build_max_payload",
    "prior_texts",
]
