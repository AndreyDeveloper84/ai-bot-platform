---
node_id: ayla.docs.reply-master-operations-screen-contract
title: Reply — Master Operations Screen Contract
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
  - "[[Ayla Master Operations Screen Contract]]"
---
# Summary: Master Operations Screen Contract

## Created

Создан канонический UX Screen Contract:
`07 UX/Ayla Master Operations Screen Contract.md`.

Документ описывает:

- назначение рабочего экрана мастера и ежедневный operating cycle;

- pain points мастера, responsibility boundary и логические блоки Information Architecture;
- границу между Business Operation, Operational Capability, Projection,
  Screen Data Contract, User Action, Command, Event и Updated Projection;
- разрешённые данные, состояния, действия, подтверждения и recovery;
- необходимые backend capabilities без создания API specification;
- границу ответственности Ayla AI;
- privacy/role ограничения и список Open Questions.

## Data Sources

Основная модель использует:

- `Master Day Projection`;
- `Appointment Operational Projection`;
- `Customer Operational Context Projection`;
- `Exception Projection`;
- `Updated Appointment Projection`.

Источниками смысла остаются Appointment/Schedule authoritative sources,
`Ayla MVP Appointment Contract`, `Ayla Domain Event Registry`,
`Ayla Salon Operations MVP Contract`, `Consent Scope Registry` и
`Data Inventory Matrix`. Projection не объявляется source of truth.

## Preserved

Без изменений сохранены:

- `Ayla Repository Responsibility Matrix.md`;
- `Ayla MVP Appointment Contract.md`;
- `Ayla Domain Event Registry.md`;
- `Ayla Salon Operations MVP Contract.md`;
- `Consent Scope Registry`;
- `Ayla Decision Log`;
- существующие UX MVP, handoff и interaction documents.

Не создавались UI mockups, React Native descriptions или API specifications.

## Open Questions

В контракт вынесены решения, требующие владельца:

- полный список клиентов дня и объём истории;
- `StartService` и check-in;
- точные права мастера на cancel/reschedule;
- completion evidence и семантика `completed`;
- operational notes;
- точная projection/API boundary;
- canonical path User Journey specification.

## Validation

- Architecture: Screen Contract не стал доменной моделью; Appointment ownership
  и Domain Event Registry preserved; Projection не объявлена SoR.
- UX: рабочий цикл мастера описан; данные имеют source; actions имеют commands,
  preconditions и outcomes; UI layout не описывается.
- AI: write authority не предоставлена; AI output не является business fact.
- Privacy: role/purpose/tenant scope, fail-closed и запрет semantic memory
  зафиксированы.
- Governance: существующие документы не заменены; новые domain decisions не
  созданы; неопределённости отмечены Proposed/Open question.
