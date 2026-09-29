---
node_id: ayla.docs.reply-master-schedule-mvp-ux-contract
title: Reply — Master Schedule MVP UX Contract
type: specification
status: draft
canonical_status: candidate
version: "0.1"
owner: UX Architecture
owners:
  - UX Architecture
  - Product Operations
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
  - "[[Ayla Master Schedule UX Contract]]"
---

# Summary — Ayla Master Schedule MVP UX Contract

## 1. Result

Created one new canonical-candidate UX contract:
`07 UX/Ayla Master Schedule UX Contract.md`.

**Choice: NEW DOCUMENT.** No existing document independently owns the complete
Schedule information architecture, timeline semantics, manual booking flow, and
availability presentation boundary. The existing IA and Screen Contracts remain
unchanged because they own broader app structure or other screens.

**Canonical area:** `07 UX` / `design`.

## 2. Dependencies read

- Ayla Knowledge Area Taxonomy;
- Ayla Repository Responsibility Matrix;
- Ayla MVP Appointment Contract;
- Ayla Domain Event Registry;
- Ayla Salon Operations MVP Contract;
- Ayla Master Operations Screen Contract;
- Ayla Master App Information Architecture;
- Ayla Appointment Detail Screen Contract;
- Ayla Master Appointment Flow MVP.

Handoff documents were inventoried but not used as canonical sources.

## 3. Owner decisions recorded

- Master MVP global navigation is `Сегодня | Расписание | Ayla`.
- Schedule is P0.
- Operations Home is not Schedule View.
- Existing Operations Home mockup is Design Evidence, not Source of Truth.
- Manual booking order is `Customer → Service → Date/time`.
- Service selection is mandatory before availability.
- Availability is validated again at commit.
- Customer lookup is inside manual booking; no separate Customers tab.
- Minimal new customer flow is `name + phone`.
- Today and Schedule share Appointment Detail.
- Focus states are UI presentation states, not Appointment statuses.

## 4. Main Schedule IA

```text
Schedule
├── Week selector
├── Selected day + timezone + freshness
└── Day timeline
    ├── appointments
    ├── available intervals
    ├── blocked/time-off intervals
    └── non-working intervals
```

Existing appointments open the shared Appointment Detail from either Today or
Schedule. Free intervals start manual booking but do not reserve a slot.

## 5. Manual booking flow

```text
Customer → Service → Date/time → Availability validation → Review → Create Appointment
```

The contract covers existing-customer lookup, minimal new-customer creation,
service selection, free-interval entry, review, authoritative readback, and
pending/conflict/stale/blocked outcomes.

## 6. Availability and time boundaries

Working Hours are the baseline boundary; Time Off/Blocks are exceptions. Both are
separate from Appointment. Availability is computed from working hours, blocks,
existing appointments, and service duration. The Schedule client never treats a
cached interval as a guaranteed booking and never performs the authoritative write.

Block-time is an entry point to the existing canonical time-off capability. The
contract does not invent a command, reason taxonomy, event, or cancellation rule.

## 7. Existing Operations Home mockup

The four-state owner-reviewed concept was considered as visual evidence. It
supports stable `DAY CONTEXT → FOCUS → TODAY TIMELINE` IA and the presentation
states `NEXT`, `NOW_SCHEDULED`, `ATTENTION`, `CHANGE`, `DAY_COMPLETE`, `EMPTY`.
The obsolete navigation `Расписание | Клиенты | Профиль` was explicitly replaced
by `Сегодня | Расписание | Ayla`.

## 8. Reconciliation gaps found

- Schedule/Availability authoritative owner and physical SoR are not fully assigned.
- Existing canonical documents do not close the exact block/time-off command and
  reason/event contract.
- Customer identity/deduplication and consent details for `name + phone` remain
  dependencies.
- Timezone/DST, projection freshness/version, and slot-hold TTL need owner-level
  contracts before implementation.
- Existing IA still describes Schedule as deferred; this new P0 owner decision is
  recorded here, but other canonical documents were not silently edited.

These are follow-ups, not new domain semantics.

## 9. Open Questions

The contract keeps seven implementation-enabling questions explicit: schedule
ownership, block command semantics, customer identity/consent, timezone/DST,
slot-hold behavior, master permissions, and freshness/version contract.

## 10. Files changed

- `07 UX/Ayla Master Schedule UX Contract.md` — added.
- `docs/REPLY_MASTER_SCHEDULE_MVP_UX_CONTRACT.md` — added.

No production code, backend/mobile repository, API specification, migration, new
domain event, new Appointment lifecycle, mockup, PR, push, or commit was created.

## 11. Validation

- Frontmatter follows repository schema fields and controlled values.
- Canonical dependencies are explicit and use existing document names.
- Internal ownership boundaries and non-override rules are explicit.
- The contract includes all requested Schedule, navigation, manual booking,
  availability, time-off, privacy, MVP/deferred, open-question, and validation
  sections.
- `git diff --check` and knowledge validation are to be run after writing.

## OWNER-APPROVED UX REFINEMENTS

The following decisions are incorporated, not Open Questions:

- Appointment cards contain only customer name, service name, and time. Progressive disclosure is Schedule = Who / What / When; Appointment Detail = operational information; Customer Context = extended customer information. Schedule is not a mini-CRM.
- Manual booking remains logically Customer → Service → Date/time → validation → Review/Create, but is presented as one progressive booking draft without a mandatory stepper or long wizard.
- Free-interval entry uses the user-facing term Выбранное окно, not "Исходный интервал". It is a prefilled available range, not Appointment duration; valid service start times are shown explicitly and are never silently shifted.
- A working day with Working Hours and zero appointments keeps available intervals visible and clickable. Записей пока нет may accompany the timeline but cannot replace it.
- Today has a compact current-time line/dot/time indicator; past and future dates do not.
- Visual priority is Appointment, then neutral Time Off, then calm Available time, then minimally visible Non-working time. Red/error semantics are reserved for real conflicts/errors.
- Interaction is unified: Appointment → Detail; Available → booking or block; Time Off → permitted details; + → booking or block. + and Available share one draft, differing only by prefilled context.

The updated four-state Schedule visual reference (Normal Working Day, Completely Free Working Day, Day With Time Off, Manual Appointment Creation From Available Interval) is recorded as Design Evidence / Visual Reference, not Source of Truth.
## OWNER-APPROVED AVAILABILITY MANAGEMENT UX

The following P0 decisions are incorporated into the canonical Schedule UX
contract, not treated as Open Questions:

- **Working Hours:** recurring weekly baseline answering when the master usually works; editing communicates recurring scope and never changes one specific date.
- **Specific Day Exception:** one-date choice between По обычному графику, Другие часы, and Не работаю. The first and third choices use Сохранить immediately; only Другие часы proceeds to Start/End and may use Далее. The UI states Изменение только на этот день.
- **Time Off:** temporary partial-day unavailability inside working time; reason is optional unless canonical policy requires it; state is neutral, with permitted view/edit/delete and authoritative availability refresh.
- **Full-day absence:** uses Specific Day Exception → Не работаю, not full-day Time Off.
- **Mental model:** recurring future changes → Working Hours; one-date changes → Specific Day Exception; several-hour absence → Time Off; free interval → Manual Booking.
- **Conflict Guard:** availability changes never implicitly mutate an Appointment. Full-day, partial-day, and Time Off overlaps stop the write and show minimal Appointment context (time, customer, service), with Appointment flow recovery and no silent override.
- **Specific Day Exception is UX behavior, not a new backend/domain entity.**
- **MVP scope:** Working Hours, Specific Day Exception, full-day absence choice, partial-day Time Off, permitted existing Time Off actions, Conflict Guard, and availability refresh after authoritative change.
- **Visual Evidence:** the new four-screen composite is Design Evidence / Visual Reference: Working Hours, Specific Day Exception, Time Off, Conflict Guard.

## OWNER-APPROVED AYLA MASTER MVP UX

The following owner decisions are incorporated into the canonical Schedule UX
contract:

- Start / Day Context: Ayla starts with compact day context and contextual suggestions; it does not duplicate Operations Home or become a generic empty chatbot.
- Global model: Today is execution, Schedule is planning/availability management, Ayla is conversational interaction.
- Read/write distinction: permitted reads may answer directly; writes use Understand → Resolve Required Context → Preflight → Proposal → User Confirmation → Authoritative Command → Result.
- Missing required information: Ayla asks only for missing booking inputs. Service is mandatory and is never silently inferred from history, popularity, defaults, or AI inference.
- Booking preflight and proposal: free intervals are called Свободные интервалы until Service is known; duration and canonical availability are validated before proposal. Proposal contains Customer, Service, Duration, Date, and Start/end time.
- Standard booking draft reuse: Изменить детали reuses the Schedule booking draft and selection surfaces.
- Authoritative result: successful creation shows a compact result and Открыть запись to the shared Appointment Detail.
- Availability preflight: conversational availability changes use the existing Working Hours / Specific Day Exception / Time Off model.
- Conflict Guard reuse: known conflicts suppress executable confirmation; no silent override, cancellation, reschedule, hiding, invalidation, or time mutation of Appointment is allowed.
- P0 classes: Understand Day, Find Availability, Prepare Appointment, Prepare Availability Change.
- Design Evidence: the four-state Ayla composite (Start/Day Context; Ask Schedule/Find Availability; Create Appointment/Confirmation/Result; Change Availability/Conflict) is owner-approved Design Evidence, not Source of Truth.
## OWNER-APPROVED POST-VISIT COMPLETION MODEL

- scheduled_end opens the authoritative 3-hour post-visit resolution window.
- Normal visit = zero-action happy path; no manual completion is required.
- No exception before deadline = authoritative normal completed outcome.
- Exception before deadline prevents blind normal auto-completion.
- Master minimal exception intent is Клиент не пришёл, subject to existing no_show
  authority/evidence and appointment.no_show semantics.
- Customer intent Визит не состоялся reuses an existing canonical mechanism if
  one exists; otherwise exact command/outcome is a dependency.
- Feedback/review is not lifecycle outcome.
- MASTER-REPORTED NON-DELIVERY AFTER CUSTOMER ARRIVAL remains Open Question; no
  new status, event, command, or Master action was invented.
- Appointment Detail, Master Appointment Flow, Operations Home, IA, and Salon
  Operations were reconciled; Schedule ATTENTION wording was reconciled without expanding Schedule lifecycle ownership.
- Existing appointment.completed and appointment.no_show names were reused; no
  new status, event, or domain entity was created.
- Exact authoritative producer/scheduler and deadline race remain dependencies;
  mobile is not the scheduler.

### Changed files

- 05 Architecture/Ayla MVP Appointment Contract.md
- 05 Architecture/Ayla Domain Event Registry.md
- 06 Product/Ayla Salon Operations MVP Contract.md
- 07 UX/Ayla Appointment Detail Screen Contract.md
- 07 UX/Ayla Master Appointment Flow MVP.md
- 07 UX/Ayla Master Operations Screen Contract.md
- 07 UX/Ayla Master App Information Architecture.md
- docs/REPLY_MASTER_SCHEDULE_MVP_UX_CONTRACT.md