---
node_id: ayla.docs.reply-master-system-recovery-ux-contract
title: Reply — Ayla Master System and Recovery UX Contract
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
updated: 2026-08-16
review_cycle: monthly
depends_on:
  - "[[Ayla Master System and Recovery UX Contract]]"
---

# Summary — Ayla Master System and Recovery UX Contract

## FINAL MASTER MVP SYSTEM & RECOVERY UX

The shared System and Recovery UX contract is incorporated across Today,
Schedule, Appointment Detail, Master Appointment Flow, and Ayla.

Included:

- usable previous data is preserved where safe;
- initial Loading is distinct from background refresh;
- Empty is a normal business state;
- Offline allows marked cached reads but no consequential writes;
- Stale reads are allowed and writes trigger authoritative revalidation;
- Error is localized and distinguishes no-data from previous-data cases;
- Permission is split into object-level and action-level behavior;
- Pending / Unknown is neither success nor failure and is authoritatively reconciled;
- Conflict preserves valid draft context and forbids silent overwrite or time shift;
- Smallest Failure Surface Principle;
- Ayla follows the same trust and recovery rules;
- canonical navigation remains Сегодня | Расписание | Ayla;
- non-goals and Design Evidence boundaries are explicit.

## Reconciliation

Surface ownership remains unchanged. Operations Home keeps Today execution,
Schedule keeps availability and Conflict Guard, Appointment Detail keeps its
frozen lifecycle presentation, Flow keeps sequencing, IA keeps navigation, and
Ayla reuses the same trust, permission, preflight, and recovery rules.

No domain status, event, command, entity, offline queue, or navigation
destination was created.

## Freeze

MASTER MVP SYSTEM & RECOVERY UX — FROZEN FOR IMPLEMENTATION.

Further changes require an implementation blocker, canonical domain
contradiction, or explicit owner decision. Visual polish alone does not reopen
these semantics.

## Changed Files

- 07 UX/Ayla Master System and Recovery UX Contract.md
- 07 UX/Ayla Master Operations Screen Contract.md
- 07 UX/Ayla Master Schedule UX Contract.md
- 07 UX/Ayla Appointment Detail Screen Contract.md
- 07 UX/Ayla Master Appointment Flow MVP.md
- 07 UX/Ayla Master App Information Architecture.md
- docs/REPLY_MASTER_SYSTEM_RECOVERY_UX_CONTRACT.md