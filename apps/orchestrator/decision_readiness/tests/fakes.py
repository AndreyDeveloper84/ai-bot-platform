"""In-memory Redis surface for this package's tests.

Same reason as `apps/orchestrator/memory/tests/test_short_term.py`'s
`_FakeRedis`: the contract under test is INCR/SET/GET/EXPIRE semantics, not
Redis itself, and rule 26 (`docs/EXECUTOR-RULES.md`) already makes a live Redis
a prerequisite for a full local `pytest apps/` — these tests should not add
another reason for that.

`expire_key()` exists because the thing being tested is what happens *after* a
TTL fires. A test that could not make a key disappear on demand could not tell
`EXPIRED` from `ABSENT`, which is exactly what `state.load()` is for.

The pipeline is a real buffer rather than a pass-through: `state.next_revision`
reads `execute()[0]`, so a fake whose `execute()` returned nothing would pass a
broken implementation and fail a correct one.
"""

from __future__ import annotations

from typing import Any


class FakePipeline:
    """Buffers commands and replays them against the parent on `execute()`."""

    def __init__(self, parent: "FakeRedis") -> None:
        self._parent = parent
        self._pending: list[tuple[str, tuple[Any, ...]]] = []

    def incr(self, key: str) -> "FakePipeline":
        self._pending.append(("incr", (key,)))
        return self

    def expire(self, key: str, ttl: int) -> "FakePipeline":
        self._pending.append(("expire", (key, ttl)))
        return self

    def set(self, key: str, value: str, ex: int | None = None) -> "FakePipeline":
        self._pending.append(("set", (key, value, ex)))
        return self

    def execute(self) -> list[Any]:
        results: list[Any] = []
        pending, self._pending = self._pending, []
        for cmd, args in pending:
            if cmd == "incr":
                results.append(self._parent.incr(*args))
            elif cmd == "expire":
                self._parent.expire(*args)
                results.append(True)
            elif cmd == "set":
                key, value, ex = args
                self._parent.set(key, value, ex=ex)
                results.append(True)
        return results


class FakeRedis:
    """GET / SET(ex) / INCR / EXPIRE / pipeline, plus a way to fire a TTL."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex

    def incr(self, key: str) -> int:
        current = int(self.values.get(key, "0")) + 1
        self.values[key] = str(current)
        return current

    def expire(self, key: str, ttl: int) -> None:
        if key in self.values:
            self.ttls[key] = ttl

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    def expire_key(self, key: str) -> None:
        """Fire the TTL on one key, the way Redis eventually would."""

        self.values.pop(key, None)
        self.ttls.pop(key, None)
