"""Fixture schema (DRF-508 / Sprint 5 / C1).

Frozen dataclass per PHASE0_DESIGN §7.3 — the in-memory shape of a
single YAML fixture. Used by:
- C2 loader (parse YAML → Fixture)
- D2 assertions (evaluate trace against must_pass/forbidden)
- D1 runner (input → pipeline → assertions)
- D3 voice check (delegate to apps.orchestrator.safety.voice_check)

### Why frozen

A loaded fixture is immutable input to the runner. Mutability would
let the runner accidentally mutate the fixture state mid-suite —
making one fixture failure depend on another fixture's run order.

### cosmetologist_reviewed flag

Per Sprint 5 plan decision #7: adversarial fixtures ship with
``cosmetologist_reviewed=False`` until a domain expert audits them.
Sprint 5 doesn't block on expert availability; Phase 1 expert pass
flips the flag in the YAML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Fixture:
    """One YAML fixture parsed into Python.

    Fields per design §7.3. Defaults align with "no assertion" so
    sparse fixtures (just an input + one must_pass) stay readable.
    """

    name: str
    description: str
    input: dict[str, Any]
    must_pass: list[dict[str, Any]] = field(default_factory=list)
    forbidden: list[dict[str, Any]] = field(default_factory=list)
    voice_check: dict[str, Any] = field(default_factory=dict)
    expected_action_type: str | None = None
    # Per Sprint 5 plan #7 — adversarial fixtures ship false; Phase-1
    # expert audit flips per YAML.
    cosmetologist_reviewed: bool = False


# Top-level YAML keys we recognise. C2 loader rejects unknown keys.
_KNOWN_KEYS = frozenset(
    {
        "name",
        "description",
        "input",
        "must_pass",
        "forbidden",
        "voice_check",
        "expected_action_type",
        "cosmetologist_reviewed",
    }
)


def from_dict(data: dict[str, Any], *, source: str = "<dict>") -> Fixture:
    """Build a Fixture from a parsed YAML dict.

    Args:
      data: parsed YAML.
      source: filesystem path or description — included in error
              messages for operator UX.

    Raises:
      ValueError: missing required keys or unknown keys present.
    """

    if not isinstance(data, dict):
        raise ValueError(
            f"Fixture {source}: top-level must be a mapping, got {type(data).__name__}"
        )

    unknown = set(data.keys()) - _KNOWN_KEYS
    if unknown:
        raise ValueError(
            f"Fixture {source}: unknown top-level keys {sorted(unknown)}. "
            f"Allowed: {sorted(_KNOWN_KEYS)}."
        )

    for required in ("name", "description", "input"):
        if required not in data:
            raise ValueError(f"Fixture {source}: missing required key {required!r}")

    inp = data["input"]
    if not isinstance(inp, dict) or "text" not in inp:
        raise ValueError(f"Fixture {source}: 'input' must be a mapping with at least 'text' key")

    return Fixture(
        name=str(data["name"]),
        description=str(data["description"]),
        input=dict(inp),
        must_pass=list(data.get("must_pass") or []),
        forbidden=list(data.get("forbidden") or []),
        voice_check=dict(data.get("voice_check") or {}),
        expected_action_type=data.get("expected_action_type"),
        cosmetologist_reviewed=bool(data.get("cosmetologist_reviewed", False)),
    )


def to_dict(fixture: Fixture) -> dict[str, Any]:
    """Inverse of from_dict — for round-trip + admin renders."""

    return {
        "name": fixture.name,
        "description": fixture.description,
        "input": dict(fixture.input),
        "must_pass": list(fixture.must_pass),
        "forbidden": list(fixture.forbidden),
        "voice_check": dict(fixture.voice_check),
        "expected_action_type": fixture.expected_action_type,
        "cosmetologist_reviewed": fixture.cosmetologist_reviewed,
    }
