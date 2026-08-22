"""Running a fixture through the path that actually answers people.

### The problem this solves

`replay.yml` calls itself a prompt-regression gate — «the fixture diff IS
the design review for AI changes» — and blocks every PR. What it runs is
the replay engine's own unit tests plus a check that the YAML parses. No
fixture has ever reached the code that answers a person. The workflow says
so itself: «when Sprint 6 lands `pipeline.turn`, this upgrades to invoke
`python -m apps.replay run`». `pipeline.turn` landed and the upgrade did
not.

Worse, `pipeline.turn` is not the path that answers anyone: it has zero
callers outside docstrings and tests. On the pilot a message goes through
`apps.channels.max.handler._handle_global_max_event_inner` — a chain of
deterministic branches ending at the concierge LLM.

### What can honestly be gated in CI, and what cannot

An adversarial fixture asserts something about the **model's words** («must
not say вам подойдёт»). CI has no model: calling one would make the gate
non-deterministic, paid, and dependent on a third party. Mock the model and
the assertion checks the mock — a green light that means nothing.

So this module splits the fixture set by what the *code* guarantees:

* **Deterministic branches** — safety short-circuits, the no-criteria
  clarification, rendered master cards, onboarding, refusals. The model is
  never consulted, so the reply is entirely ours, and every assertion in
  the fixture is a real assertion. These are gated.
* **Model turns** — everything else. The code hands off, and only a live
  model can be judged. These are reported by name, never silently counted
  as covered.

### The canary

When the model IS consulted, the stub returns the fixture's own forbidden
phrases verbatim. That inverts the usual mock problem: instead of a stub
that trivially passes, we get one that trivially FAILS the moment its
output reaches the user. So the gate can state something strong and
checkable — *on this input, the system answered without consulting a model,
and the forbidden text provably could not have come through*.

It also pins the property nobody was checking: a red-flag input must never
reach an LLM at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.replay.fixtures.schema import Fixture

#: Text the stub model emits when a fixture declares no forbidden phrases.
#: Recognisable on sight in a failure message, and deliberately nothing a
#: real reply would contain.
CANARY_FALLBACK = "КАНАРЕЙКА-ОТВЕТ-МОДЕЛИ"


def canary_reply_for(fixture: Fixture) -> str:
    """The worst reply this fixture could receive, assembled from itself.

    Every phrase the fixture forbids, concatenated. If this text reaches
    the person, the fixture fails loudly — which is the point: it can only
    reach them by way of the model, and then the fixture was never
    CI-checkable to begin with.
    """

    phrases: list[str] = []
    for constraint in fixture.forbidden:
        for key, expected in constraint.items():
            if key not in ("response_contains_any", "response_contains_all"):
                continue
            if isinstance(expected, list):
                phrases.extend(str(p) for p in expected)
            else:
                phrases.append(str(expected))
    if not phrases:
        return CANARY_FALLBACK
    return " ".join(phrases)


def build_max_payload(fixture: Fixture, *, user_id: int, mid: str) -> dict[str, Any]:
    """Fixture input → a MAX webhook payload the live handler accepts.

    Each fixture gets its own ``user_id``: the global path resolves a
    BotUser per channel user and keeps short-term history per conversation,
    so a shared id would let one fixture's turn leak into the next one's
    prompt.
    """

    return {
        "update_type": "message_created",
        "timestamp": 1_731_320_000_000,
        "message": {
            "sender": {"user_id": user_id, "name": "Replay"},
            "recipient": {"chat_id": user_id, "chat_type": "dialog"},
            "body": {
                "mid": mid,
                "seq": 1,
                "text": str(fixture.input.get("text", "")),
                "attachments": [],
            },
        },
    }


@dataclass
class LivePathResult:
    """What one fixture produced on the live path."""

    fixture_name: str
    response_text: str
    llm_called: bool
    #: True when the safety gate refused the input before anything else.
    safety_blocked: bool
    sent_count: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def deterministic(self) -> bool:
        """The reply came from our own code, so the fixture is gateable."""

        return not self.llm_called

    def as_trace(self) -> dict[str, Any]:
        """Shape the replay assertion engine expects.

        ``intent`` and ``skill_used`` stay empty: the global path has
        neither — intent classification is not wired into it (`classify_intent`
        has no callers outside tests). Reporting a made-up value would let a
        fixture assert on something the system never computes.
        """

        return {
            "intent": "",
            "skill_used": "",
            "safety_decision": "block" if self.safety_blocked else "allow",
            "tool_calls": [],
            "response_text": self.response_text,
        }


__all__ = [
    "CANARY_FALLBACK",
    "LivePathResult",
    "build_max_payload",
    "canary_reply_for",
]
