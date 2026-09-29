---
node_id: ayla.docs.reply-master-appointment-flow-mvp
title: Reply — Master Appointment Flow MVP
type: specification
status: draft
canonical_status: candidate
version: "0.1"
owner: UX Architecture
knowledge_area: design
system_owner:
  - ayla-knowledge
source_kind: canonical
classification: internal
data_sensitivity: medium
data_categories:
  - pii
security_sensitivity: medium
ai_indexing: allowed
export_policy: sanitized
domain: booking
updated: 2026-08-15
review_cycle: monthly
depends_on:
  - "[[Ayla Master Appointment Flow MVP]]"
---

# Summary — Ayla Master Appointment Flow MVP

## Created

Created the canonical MVP UX flow contract connecting the existing `Master Operations Screen Contract` and `Appointment Detail Screen Contract`.

The document defines the minimum path, screen transitions, logical data flow, action boundaries, exception handling, Ayla Assistant boundary, MVP scope, Open Questions, and validation rules. It does not create a screen, capability, domain event, API specification, or new business rule.

## Flow

The covered MVP path is:

`Operations Home → select appointment → Appointment Detail → authorized action → authoritative result/projection update → Operations Home`.

## Dependencies

The document uses these canonical sources:

- `Ayla Knowledge Area Taxonomy`;
- `Ayla Repository Responsibility Matrix`;
- `Ayla MVP Appointment Contract`;
- `Ayla Domain Event Registry`;
- `Ayla Salon Operations MVP Contract`;
- `Ayla Master App Information Architecture`;
- `Ayla Master Operations Screen Contract`;
- `Ayla Appointment Detail Screen Contract`.

Temporary handoff documents were not used as canonical dependencies.

## Preserved

- Appointment Domain remains the owner of appointment lifecycle, status, invariants, and committed events.
- Projections remain read models and are not promoted to source of truth.
- Existing Screen Contracts remain responsible for their own screen boundaries.
- No new domain events, capabilities, UI layouts, APIs, or backend contracts were introduced.
- `AI suggestion != Business action`; Ayla receives no write authority.
- Privacy remains bounded by authorized projections and existing consent/data rules.

## Open Questions

The flow keeps unresolved decisions explicit:

- separate confirmation after an action;
- intermediate appointment/service status;
- authority, evidence, and correction rules for completion;
- offline retry and reconciliation;
- additional post-completion steps;
- master permissions for cancellation, no-show, and change requests.

Open Questions do not authorize implementation.

## Validation

- MVP path is traceable from entry point through action result and return to the working day.
- Each transition defines intent, data, action, and result.
- Architecture boundaries and Appointment ownership are preserved.
- No new domain events or capabilities were created.
- Exception states include changed appointment, cancellation, offline, blocked action, and failed/pending update.
- AI has no write authority and AI output is not a business fact.
- Privacy exclusions are explicit.
