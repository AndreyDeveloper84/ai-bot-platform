# Agent Prompt: Create `05 Architecture/Ayla MVP Appointment Contract.md`

## Role

Ты работаешь как Senior Domain Architect и Product Systems Architect
проекта Ayla.

Создай канонический архитектурный документ:

    05 Architecture/Ayla MVP Appointment Contract.md

Документ является вторым фундаментальным контрактом после:

    00 Foundation/Ayla Repository Responsibility Matrix.md

Он должен стать основой для: - Salon Operations MVP - Master/Admin UX -
Screen Data Contracts - Domain Event Registry - Backend API contracts -
AI tool contracts

------------------------------------------------------------------------

# Главный принцип

НЕ проектируй Appointment с нуля.

Сначала изучи:

    00 Foundation/Ayla Repository Responsibility Matrix.md

Используй решения по: - System of Record; - write authority; - command
ownership; - event ownership; - projection rules; - AI boundary; -
provider boundary.

Appointment Contract должен расширять эти решения, а не создавать новые.

------------------------------------------------------------------------

# Обязательные источники

Изучи:

## Foundation

-   Ayla Constitution
-   Ayla Domain Capability Registry
-   Ayla Glossary
-   Ayla Decision Log
-   Ayla Repository Responsibility Matrix

## Product

-   Ayla MVP Scope and Release Contract
-   Ayla MVP User Journey Specification
-   Ayla Single-Provider Technical Pilot Execution Scope
-   Ayla Multi-Provider Product Validation Execution Scope

## Architecture

-   Ayla Domain Context Map
-   Ayla Core Domain Model Specification
-   Ayla Domain Event Registry (если существует)

## Governance

-   Consent Scope Registry
-   Data Inventory Matrix

------------------------------------------------------------------------

# Правила

Не создавать новые решения без источника.

Если решение не подтверждено:

    Proposed
    Pending owner decision
    Open question

Всегда разделять:

    Domain Contract
    Implementation
    API Representation
    UI Projection
    AI Tool Representation

------------------------------------------------------------------------

# Документ должен определить

-   что такое Appointment;
-   владельца lifecycle;
-   участвующие сущности;
-   состояния;
-   допустимые переходы;
-   команды;
-   права Customer/Master/Admin/Owner/Ayla;
-   ручную запись салона;
-   перенос;
-   отмену;
-   завершение визита;
-   события;
-   данные для разных ролей.

------------------------------------------------------------------------

# Обязательная структура

## 1. Purpose

Зачем нужен контракт и какие системы его используют.

## 2. Appointment Definition

Определить Appointment и явно отделить:

    Appointment != Recommendation
    Appointment != Slot
    Appointment != Conversation
    Appointment != Payment
    Appointment != Calendar Event

## 3. Ownership and Source of Truth

Сослаться на Repository Responsibility Matrix.

Зафиксировать:

    Appointment Domain owns lifecycle
    beautygo_backend is operational transactional SoR

Если нет подтверждения --- Proposed.

## 4. Domain Model

Описать:

-   Appointment
-   Provider
-   Tenant
-   Customer
-   Specialist Membership
-   Specialist Assignment
-   Service Offering
-   Availability
-   Slot Hold
-   Payment boundary
-   Consent boundary

## 5. Appointment Attributes

Описать:

    appointment_id
    tenant_id
    provider_id
    customer_id
    service_offering_id
    specialist_membership_id
    specialist_assignment_id
    start_time
    end_time
    timezone
    status
    origin
    price snapshot
    duration snapshot
    version
    created_at

## 6. Lifecycle Model

Описать state machine только на основании подтверждённых источников.

## 7. State Transition Rules

Создать таблицу:

  From   Action   To   Actor   Conditions
  ------ -------- ---- ------- ------------

## 8. Commands

Описать canonical commands:

    CreateAppointment
    ConfirmAppointment
    RescheduleAppointment
    ReplaceAppointment
    CancelAppointment
    CompleteAppointment
    MarkNoShow

Для каждого указать actor, permissions, validation, result.

## 9. Reschedule Rules

Разделить:

Same-ID reschedule: - same service - same price - same duration - same
business meaning

Replacement: - changed Service Offering - changed price - changed
payment boundary - changed consent boundary

## 10. Cancellation Rules

Описать сохранение истории и audit. Не придумывать штрафы и grace period
без источников.

## 11. Manual Booking / Admin Booking

Описать:

    Admin creates appointment
    same Appointment domain
    different origin/actor

Не создавать OfflineAppointment без решения владельца.

## 12. Master Operations

Описать действия мастера: - View appointments - Complete - No-show -
Request changes

Разделить прямые действия и требующие подтверждения.

## 13. Admin Operations

Описать: - Create booking - Move appointment - Cancel - Reassign
specialist - Resolve conflicts

## 14. Customer Operations

Описать: - create booking - confirm - cancel - reschedule - view
appointment

## 15. Ayla Interaction Model

Использовать поток:

    User request
    ↓
    Ayla interpretation
    ↓
    Command
    ↓
    Backend validation
    ↓
    Commit
    ↓
    Event
    ↓
    Ayla response

Зафиксировать:

    AI output != business fact

## 16. Availability and Slot Hold

Описать:

    Availability
    ↓
    Slot projection
    ↓
    Slot Hold
    ↓
    Appointment

## 17. Concurrency and Idempotency

Описать: - optimistic locking - version - duplicate commands - double
booking prevention - retries

## 18. Permissions Matrix

Создать таблицу:

  Action   Customer   Master   Admin   Owner   Ayla
  -------- ---------- -------- ------- ------- ------

## 19. Events

Использовать Domain Event Registry. Для каждого: - Event name -
Producer - When emitted - Consumers - Payload purpose

## 20. Screen Projection Requirements

Описать данные для: - Master Today - Appointment Detail - Admin
Calendar - Customer Appointment

Разделить:

    Domain data
    Projection data
    UI-only state

## 21. Privacy Boundary

Master/Admin получают только:

    purpose-limited operational projection

Не получают: - semantic memory - AI inference - other provider history -
hidden recommendation reasoning

## 22. Open Questions

Создать таблицу решений:

  ID   Question   Status
  ---- ---------- --------

Минимальные вопросы: - specialist direct cancellation - specialist
direct reschedule - admin override - completion authority - no-show
authority - customer PII projection - late cancellation policy

------------------------------------------------------------------------

# Validation Checklist

Проверить:

-   Appointment имеет одного владельца.
-   AI не пишет Appointment напрямую.
-   Formula Tela не создаёт второй Appointment SoR.
-   Каждый экран можно построить из определённых данных.
-   Команды соответствуют lifecycle.
-   Producer события является владельцем факта.

------------------------------------------------------------------------

# После создания документа

1.  Выполнить validation проекта.
2.  Проверить ссылки.
3.  Проверить терминологию.
4.  Создать summary:
    -   что создано;
    -   какие решения использованы;
    -   какие вопросы остались.
5.  Не менять другие документы без отдельного задания.
