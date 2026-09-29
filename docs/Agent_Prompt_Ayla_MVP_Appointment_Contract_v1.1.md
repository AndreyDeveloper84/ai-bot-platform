# Agent Prompt: Update `05 Architecture/Ayla MVP Appointment Contract.md` to v1.1

## Role

Ты работаешь как Senior Domain Architect, Product Systems Architect и
Documentation Architect проекта Ayla.

Твоя задача --- выполнить аккуратное расширение существующего документа:

`05 Architecture/Ayla MVP Appointment Contract.md`

до версии `v1.1-draft`.

Цель: закрыть недостающие операционные аспекты Appointment для MVP
мастера/салона, не меняя существующую модель ownership.

------------------------------------------------------------------------

# Главный принцип

НЕ переписывать контракт заново.

Сохранить:

-   Domain ownership;
-   Repository Responsibility Matrix;
-   AI boundary;
-   provider boundary;
-   разделение Domain Contract / API / UI Projection / AI Tool
    Representation.

Новые разделы должны расширять существующий контракт.

------------------------------------------------------------------------

# Изучить перед изменением

Обязательно прочитать:

## Foundation

-   Ayla Repository Responsibility Matrix
-   Ayla Constitution
-   Ayla Domain Capability Registry
-   Ayla Glossary

## Architecture

-   Ayla MVP Appointment Contract
-   Ayla Core Domain Model Specification
-   Ayla Domain Context Map
-   Ayla Domain Event Registry

## Product

-   Ayla MVP User Journey Specification
-   Ayla Single-Provider Technical Pilot Execution Scope
-   Ayla MVP Scope and Release Contract

## Governance

-   Consent Scope Registry
-   Data Inventory Matrix

------------------------------------------------------------------------

# Правила

Если решение не подтверждено:

использовать:

    Proposed
    Pending owner decision
    Open question

Не превращать рекомендации в канон.

------------------------------------------------------------------------

# Требуемые изменения v1.1

Добавить следующие разделы:

1.  Operational Appointment Projections
2.  Appointment Operational Exceptions
3.  Extended Permission Model
4.  Idempotency Contract
5.  Appointment Timeline Model
6.  Operational Notes Model
7.  Customer Arrival / Check-in boundary

------------------------------------------------------------------------

# 1. Operational Appointment Projections

Добавить раздел, объясняющий:

Projection != Domain Entity.

Зафиксировать:

    Appointment
        ↓
    Operational Projection
        ↓
    Screen DTO
        ↓
    UI

Добавить:

## Master Today Projection

Минимальные данные:

-   appointment_id
-   time_range
-   service_name
-   duration
-   customer operational name
-   status
-   allowed actions
-   specialist
-   notes availability
-   exception flags

Разделить:

-   domain data;
-   projection data;
-   UI state.

------------------------------------------------------------------------

## Admin Calendar Projection

Описать:

-   appointments;
-   availability;
-   schedule blocks;
-   assignments;
-   conflicts;
-   unresolved items;
-   sync state.

Указать:

Admin Calendar не является Source of Truth.

------------------------------------------------------------------------

## Customer Appointment Projection

Описать:

-   service;
-   provider;
-   specialist;
-   time;
-   status;
-   customer actions;
-   confirmation state.

Не включать:

-   semantic memory;
-   AI reasoning;
-   hidden recommendation context.

------------------------------------------------------------------------

# 2. Appointment Operational Exceptions

Добавить отдельную модель:

    Operational Exception

Важно:

Exception != Appointment Status.

Примеры:

    customer_late
    master_late
    master_absent
    equipment_unavailable
    needs_reassignment
    provider_sync_issue
    payment_issue

Для каждой определить:

-   purpose;
-   owner;
-   visibility;
-   влияние на lifecycle.

Пример:

    master_absent

    не означает

    Appointment = cancelled

------------------------------------------------------------------------

# 3. Extended Permission Model

Расширить права через:

    READ
    PROPOSE
    COMMAND
    WRITE

Сохранить правило:

WRITE принадлежит domain owner.

Добавить матрицу:

  Action   Read   Propose   Command   Write
  -------- ------ --------- --------- -------

Для:

-   view appointment;
-   create;
-   reschedule;
-   cancel;
-   complete;
-   no-show;
-   reassign.

------------------------------------------------------------------------

# 4. Idempotency Contract

Расширить раздел Concurrency.

Описать idempotency для:

    CreateAppointment
    CancelAppointment
    RescheduleAppointment

Зафиксировать:

    same command
    +
    same idempotency key

    =

    same business result

Покрыть случаи:

-   двойной клик;
-   retry мобильного клиента;
-   повтор после timeout.

------------------------------------------------------------------------

# 5. Appointment Timeline Model

Добавить:

Timeline != lifecycle.

Источник:

    AppointmentRevision
    Domain Events
    Audit Records

Пример:

    10:00 Created
    10:02 Confirmed
    12 Aug Rescheduled
    13 Aug Specialist Changed
    14 Aug Completed

Определить:

-   visibility;
-   доступ ролей;
-   скрываемые данные.

------------------------------------------------------------------------

# 6. Operational Notes Model

Не добавлять простое поле:

    Appointment.notes

Создать:

    Operational Note

Минимально:

    note_id
    appointment_id
    author
    purpose
    visibility
    created_at
    content

Visibility:

    customer visible
    provider visible
    internal only
    AI unavailable

------------------------------------------------------------------------

# 7. Customer Arrival / Check-in Boundary

Не добавлять автоматически:

    Appointment.status = checked_in

без решения владельца.

Описать как отдельную capability:

    Customer Arrival Event

или:

    Check-in capability

Зафиксировать:

    arrival != completed

------------------------------------------------------------------------

# 8. Schedule → Availability → Appointment

Добавить раздел:

    Schedule influence model

Описать:

    Master schedule
          ↓
    Availability calculation
          ↓
    Bookable slots
          ↓
    Appointment

Учесть:

-   time off;
-   schedule changes;
-   reassignment;
-   membership revoke.

Не создавать новые решения без источников.

------------------------------------------------------------------------

# 9. Events

Не создавать новые canonical events.

Если требуется новый event:

добавить Open Question.

Пример:

    customer_arrived event

------------------------------------------------------------------------

# 10. Validation

Проверить:

## Ownership

-   один владелец Appointment;
-   projection не становится SoR.

## Operations

-   master workflow реализуем;
-   admin workflow реализуем;
-   customer workflow реализуем.

## AI

-   AI не получил WRITE authority.

## Privacy

-   provider получает только operational projection.

## Lifecycle

-   exceptions не стали status.

------------------------------------------------------------------------

# 11. Summary

Создать:

    docs/REPLY_MVP_APPOINTMENT_CONTRACT_v1.1.md

Включить:

## Added

Какие разделы добавлены.

## Preserved

Какие решения сохранены.

## Open Questions

Что требует owner decision.

## Validation

Результаты проверок.

------------------------------------------------------------------------

# Ограничения

Не менять без отдельного задания:

-   Decision Log;
-   Domain Event Registry;
-   Repository Responsibility Matrix;
-   другие архитектурные документы.
