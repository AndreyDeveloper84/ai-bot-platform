---
node_id: ayla.docs.reply-appointment-detail-screen-contract
title: Reply — Appointment Detail Screen Contract
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
updated: 2026-08-14
review_cycle: monthly
depends_on:
  - "[[Ayla Appointment Detail Screen Contract]]"
---

# Summary — Ayla Appointment Detail Screen Contract

## Created

Created the second key master-mobile UX Screen Contract. It defines the purpose and boundary of Appointment Detail, logical information blocks, projection-backed data contract, authorized actions, state model, event relationship, privacy rules, AI boundary, screen relationships, MVP scope, and owner questions.

## Dependencies

The canonical dependency chain is preserved:

```text
Repository Responsibility Matrix
  -> MVP Appointment Contract
  -> Domain Event Registry
  -> Salon Operations MVP Contract
  -> Master Operations Screen Contract
  -> Master App Information Architecture
  -> Appointment Detail Screen Contract
```

No handoff document is included as a canonical dependency.

## Data Sources

The contract uses:

- Appointment Operational Projection;
- authorized Customer Operational Projection;
- Service/Appointment Projection;
- approved Operational Notes Projection;
- Authorization/Policy Projection;
- Exception/Operation Result Projection;
- updated appointment and visit-outcome projections after committed domain events.

## Preserved

- Appointment Domain remains the owner of appointment lifecycle and state transitions.
- `Screen State != Domain State` and `Projection != Source of Truth` are explicit.
- AI has no WRITE authority and `AI suggestion != Business action` remains mandatory.
- Existing event names are used without creating new domain events; `appointment.completed` now has a defined normal-completion path under the approved three-hour rule, while producer/evidence/correction details remain separate Appointment Contract questions.
- `StartService`, timer, Request Change, notes, permissions, history, photo context, and AI placement remain unresolved where canonical sources leave them unresolved. No Show outcome is distinct from the conditional Master Mark No Show action.
- Privacy is purpose-limited and defers to Consent Scope Registry and Data Inventory Matrix.

## FINAL APPOINTMENT DETAIL P0 UX RECONCILIATION

1. `appointment.completed` semantics are reconciled with the approved three-hour rule.
2. Remaining producer/evidence/correction questions are separated from approved normal completion.
3. No Show outcome is separated from conditional Master Mark No Show action.
4. In Progress is removed from the current P0 temporal presentation path.
5. Scheduled Interval presentation is defined without a fake service-start state.
6. An explicit MVP Temporal Presentation Model is added.
7. Post-Visit Resolution remains zero-action.
8. Completed requires authoritative projection/readback.
9. No Show read state requires authoritative projection.
10. Pending/Unknown is explicit.
11. The deadline does not trigger local UI completion.
12. Deferred Open Questions remain open, including non-delivery after customer arrival.
13. Appointment Detail P0 UX is frozen for implementation.

## Open Questions

The document records ten UX owner questions covering Start Service, timer semantics, completion authority/evidence, no-show authority, Request Change workflow, operational notes, history, photo context, master permissions, and embedded Ayla Assistant.

## Validation

- Screen responsibility is separated from domain ownership and implementation.
- Data blocks have sources, owners, and refresh rules.
- Normal completion uses the authoritative `scheduled_end` + three-hour resolution window path; `CompleteAppointment` remains only a policy-authorized correction/exception path, and `appointment.completed` is consumed after authoritative completion.
- No new domain events were introduced.
- Before Appointment, Scheduled Interval, Post-Visit Resolution, authoritative Completed/No Show read states, Changed, Offline, Exception, and Pending/Unknown are distinguished. Deferred In Progress is not part of the current P0 temporal path.
- AI write authority and forbidden privacy data are explicitly excluded.
- No new canonical document, status, event, command, entity, UI mockup, or API specification was created.

## Changed Files

- `07 UX/Ayla Appointment Detail Screen Contract.md`
- `docs/REPLY_APPOINTMENT_DETAIL_SCREEN_CONTRACT.md`
