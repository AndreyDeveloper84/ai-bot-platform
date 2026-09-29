---
node_id: ayla.docs.reply-master-app-information-architecture
title: Reply — Master App Information Architecture
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
  - "[[Ayla Master App Information Architecture]]"
---

# Summary: Master App Information Architecture

## Created

Создан канонический Information Architecture Contract:
`07 UX/Ayla Master App Information Architecture.md`.

Определены master goals, capability map, screen map, screen responsibilities,
navigation model, MVP/deferred/future scope, projection flow, AI placement,
privacy boundary и relationship с будущими Screen Contracts.

## Screen Map

Выделены следующие logical screens:

- `Operations Home` — рабочий центр текущего дня;
- `Appointment Detail` — работа с конкретной записью;
- `Customer Context` — purpose-limited контекст клиента;
- `Schedule View` — расширенное понимание расписания;
- `Notifications` — operational updates;
- `Profile / Settings` — личные настройки.

Для MVP предложены `Operations Home` и `Appointment Detail`. Остальные поверхности
разделены на proposed, deferred или future согласно зависимостям и owner decisions.

## Dependencies

Использована canonical chain:

`Repository Responsibility Matrix → Appointment Contract → Domain Event Registry → Salon Operations MVP Contract → Master Operations Screen Contract → Master App Information Architecture`.

Также использованы Foundation, domain context/model и privacy constraints.
Handoff-документы в `depends_on` не включались.

## Preserved

Без изменений сохранены:

- `Ayla Knowledge Area Taxonomy.md`;
- `Ayla Repository Responsibility Matrix.md`;
- `Ayla MVP Appointment Contract.md`;
- `Ayla Domain Event Registry.md`;
- `Ayla Salon Operations MVP Contract.md`;
- `Ayla Master Operations Screen Contract.md`.

Не создавались UI mockups, API specifications или новые domain events.

## Open Questions

Зафиксированы вопросы о:

- необходимости отдельного календаря;
- отдельном Customer Context screen;
- размещении Ayla Assistant;
- inbox уведомлений;
- обязательных MVP-экранах;
- действиях мастера без администратора;
- семантике `Request Change`;
- праве мастера выполнять `MarkNoShow`.

## Validation

- Architecture: domain boundaries, ownership, events и projection rules сохранены.
- Product: каждый экран имеет одну основную ответственность; MVP/deferred/future разделены.
- UX: описаны navigation model и data flow между screens.
- AI: write authority отсутствует; `AI suggestion != Business action`.
- Privacy: доступы Master, Customer, Administrator и Ayla ограничены role, purpose и consent.
- Handoff-документы не использованы как canonical dependencies.
