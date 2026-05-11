# ADR-0007: Conversation State enum — minimal-first, grow with consumers

**Status:** Accepted — 2026-05-11 (Sprint 2 / E4)
**Related:** DRF-435 (B1 Conversation model), DRF-448 (this ADR), PHASE0_DESIGN.md §3.2

## Context

PHASE0_DESIGN.md §3.2 specifies `Conversation.state` as an enum with **seven** values:

```
IDLE, CONSULTING, BOOKING_FLOW, AWAITING_CONFIRMATION,
FOOD_LOGGING, HUMAN_HANDOFF, ESCALATED
```

Sprint 2 only ships the wire-up between `apps/identity`, `apps/conversations`, and `apps/channels/max` (echo handler). No skill dispatch, no booking pipeline, no food logging, no handoff workflow. Five of the seven states have **zero** writer code in Sprint 2 — they would exist only as dead choices in the enum.

The pull is between two failure modes:

1. **Ship full enum upfront.** Future-proof: when Sprint 3 lands BOOKING_FLOW, the migration cost is zero (just code paths writing the existing state value). Downside: untested state-transition code paths land in the codebase prematurely; the choices list becomes documentation that disagrees with reality.

2. **Ship the minimal set.** Only states with active writers exist as choices. Each new state lands with its own migration when the writer arrives. Cost per state: one `add_choices` migration + one tuple addition to `STATE_CHOICES`. Mechanical.

## Decision

**Ship the minimal set in Sprint 2:**

```python
class State(models.TextChoices):
    IDLE = "idle", "IDLE"
    CONSULTING = "consulting", "CONSULTING"
    ESCALATED = "escalated", "ESCALATED"
```

Plus `outcome` as a separate enum (closed conversations):

```python
class Outcome(models.TextChoices):
    SUCCESS = "success", "Success"
    ABANDONED = "abandoned", "Abandoned"
    REDIRECTED = "redirected", "Redirected"
    ERROR = "error", "Error"
```

State transitions in Sprint 2 are limited to:
- `idle → consulting` when a non-`/start` user message arrives (Sprint 3 may flip this when AI Concierge lands)
- `* → escalated` via explicit handoff trigger (Sprint 3 `apps/handoff/` work)

Outcomes are set only on conversation close (cleanup task in Sprint 1 retention pattern, or explicit `close_conversation()` from `apps/conversations/services.py`).

## Consequences

- **Sprint 3 adds**: BOOKING_FLOW, AWAITING_CONFIRMATION, HUMAN_HANDOFF — each as a migration that grows the choices tuple. Three migrations total in Sprint 3, all trivial.
- **Sprint 4+ adds**: FOOD_LOGGING — when nutrition skill lands.
- **Code paths never reference states that don't exist**. The `Conversation.state` field defaults to `"idle"` and writer code is grep-able by the literal value, not by enum member access. If `state.BOOKING_FLOW` doesn't exist in choices, the writer doesn't exist either.
- **Choice display in admin stays honest**. Five unused choices in the dropdown would mislead operators about what the platform actually does today.
- **The contract surface for `state` is "string from the choices list"** — clients of `Conversation.state` should not assume any specific value beyond what the choices list says is valid right now. Linters / IDE autocompletion match reality.

## Rejected alternatives

- **Ship full 7-state enum upfront.** Rejected per "no code without callers" principle. State transitions without writers are untested by definition; they ship as latent bugs masquerading as features.
- **Use `django-fsm` or `transitions` library.** Rejected. Sprint 2 has 1 forward transition (`idle → consulting`) and 1 sink (`* → escalated`). A state-machine library would be 100× the lines of code of the actual logic. Re-evaluate in Sprint 4 when skill-dispatch transitions land.
- **Skip the state field, derive state from message history.** Rejected. The state is a *projection* of (last message role, time since last activity, current skill), and recomputing it on every read multiplies query cost. The explicit field is cheap to update at write-time.

## How to add a new state

When Sprint 3+ work needs a new state:

1. Add the tuple member to `apps/conversations/models.py::Conversation.State`.
2. `python manage.py makemigrations conversations` — generates a `0002_alter_conversation_state.py` (or similar) that updates the choices.
3. Wire the writer code that transitions *into* the new state in the relevant `apps/skills/<skill>/` or `apps/handoff/` module.
4. Add an admin filter row to `apps/conversations/admin.py::ConversationAdmin.list_filter` if operators need to slice by it.
5. Update this ADR's "Sprint X adds:" line under Consequences.

The migration is `alter_state_choices`-only — no data migration needed because existing rows keep their literal value (no enum re-renumbering happens in Python `TextChoices`).
