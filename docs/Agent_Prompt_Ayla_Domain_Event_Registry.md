# Agent Prompt: Create `05 Architecture/Ayla Domain Event Registry.md`

## Role

Ты работаешь как Senior Domain Architect, Event-Driven Architecture
Architect и Documentation Architect проекта Ayla.

Создай канонический документ:

    05 Architecture/Ayla Domain Event Registry.md

Это третий фундаментальный контракт после:

    00 Foundation/Ayla Repository Responsibility Matrix.md
    05 Architecture/Ayla MVP Appointment Contract.md

Документ должен стать основой для:

-   backend event contracts;
-   AI orchestration flows;
-   notifications;
-   analytics;
-   mobile synchronization;
-   integrations;
-   audit/history.

------------------------------------------------------------------------

# Главный принцип

НЕ проектируй события отдельно от доменной модели.

Сначала изучи:

    00 Foundation/Ayla Repository Responsibility Matrix.md

и:

    05 Architecture/Ayla MVP Appointment Contract.md

Event Registry должен следовать:

-   domain ownership;
-   System of Record;
-   write authority;
-   command ownership;
-   AI boundary;
-   projection boundary.

------------------------------------------------------------------------

# Обязательные источники

Изучить:

## Foundation

-   Ayla Constitution
-   Ayla Domain Capability Registry
-   Ayla Glossary
-   Ayla Decision Log
-   Ayla Repository Responsibility Matrix

## Architecture

-   Ayla Domain Context Map
-   Ayla Core Domain Model Specification
-   Ayla MVP Appointment Contract
-   существующий Domain Event Registry (если есть)

## Product

-   Ayla MVP Scope and Release Contract
-   Ayla MVP User Journey Specification
-   Ayla Single-Provider Technical Pilot Execution Scope
-   Ayla Multi-Provider Product Validation Execution Scope

## Governance

-   Consent Scope Registry
-   Data Inventory Matrix

------------------------------------------------------------------------

# Ключевые правила

## Event is a fact, not a command

Зафиксировать:

    Command
       ↓
    Validation
       ↓
    State change
       ↓
    Event

Событие означает уже совершившийся факт.

------------------------------------------------------------------------

## Single authoritative producer

Для каждого domain event:

    Domain owner
          ↓
    publishes event

Consumer не может стать producer.

------------------------------------------------------------------------

## Events are immutable

После публикации событие не изменяется.

Изменение формата требует:

-   новой версии;
-   migration strategy;
-   compatibility rules.

------------------------------------------------------------------------

# Структура документа

# 1. Purpose

Описать:

-   зачем нужен Event Registry;
-   какие проблемы предотвращает;
-   какие системы используют документ.

# 2. Event Architecture Principles

Добавить:

-   event = committed fact;
-   один authoritative producer;
-   events не являются API;
-   commands и events имеют разные роли.

# 3. Event Categories

Разделить:

## Domain Events

Бизнес-факты:

    appointment.created
    appointment.confirmed
    appointment.cancelled

## Integration Events

Для межсистемного взаимодействия.

## Notification Events

Не смешивать с domain events.

## Analytics Events

Поведенческие события.

# 4. Naming Convention

Зафиксировать правило:

    <aggregate>.<past-tense-action>

Примеры:

    appointment.created
    appointment.completed
    appointment.cancelled

Не использовать:

    createAppointment
    bookingSuccess
    doBooking

# 5. Common Event Contract

Определить общий формат:

``` yaml
event_id:
event_type:
event_version:
aggregate_type:
aggregate_id:
tenant_id:
occurred_at:
actor:
correlation_id:
causation_id:
payload:
```

Для каждого поля указать назначение и источник.

# 6. Appointment Domain Events

Создать registry.

Для каждого события:

-   Event name;
-   Purpose;
-   Producer;
-   Trigger condition;
-   Consumers;
-   Payload;
-   Privacy considerations.

Проверить минимум:

## appointment.created

Только после успешного сохранения Appointment.

Не после:

-   клика пользователя;
-   AI предложения;
-   API request.

## appointment.confirmed

После authoritative confirmation.

## appointment.rescheduled

После успешного сохранения reschedule.

## appointment.cancelled

После committed cancellation.

## appointment.completed

После committed completion.

## appointment.no_show

Отдельно:

    no_show != cancelled

# 7. Event Consumer Matrix

Создать таблицу:

  Event   Producer   Consumers   Purpose
  ------- ---------- ----------- ---------

Consumer не становится владельцем.

# 8. AI Boundary

Зафиксировать:

AI может получать:

    approved event projection

AI не может:

-   публиковать domain events;
-   подтверждать бизнес-факты;
-   заменять backend state.

# 9. Projection Rules

Описать:

Events могут создавать:

-   read models;
-   caches;
-   screen projections;
-   analytics projections.

Но:

    Projection != Source of Truth

# 10. Versioning

Описать:

-   event_version;
-   backward compatibility;
-   migration;
-   deprecation.

# 11. Delivery Semantics

Описать:

-   retries;
-   duplicate handling;
-   idempotent consumers;
-   ordering.

Не придумывать конкретную инфраструктуру без источника.

# 12. Privacy Boundary

Для каждого события определить допустимый payload.

Запрещено без отдельного основания:

-   semantic memory;
-   hidden AI reasoning;
-   данные других providers;
-   лишние персональные данные.

# 13. Event vs Timeline

Объяснить:

    Domain Events
    +
    Appointment Revision
    +
    Audit Records
    =
    Operational Timeline

Event Registry не заменяет Timeline.

# 14. Open Questions

Создать таблицу:

  ID   Question   Status
  ---- ---------- --------

Проверить:

-   check-in events;
-   operational exception events;
-   provider sync events;
-   notification event model;
-   delivery infrastructure.

# Validation Checklist

Проверить:

-   каждый domain event имеет одного producer;
-   событие создаётся только после commit;
-   AI не является producer бизнес-фактов;
-   payload соблюдает privacy;
-   события не заменяют API;
-   projections не становятся SoR.

# Final output

После создания:

Создать:

    docs/REPLY_DOMAIN_EVENT_REGISTRY.md

Summary должен содержать:

## Created

Что создано.

## Canonical decisions used

Какие решения использованы.

## Proposed items

Что осталось Proposed/Open Question.

## Validation

Результаты проверки.

# Ограничения

Не менять без отдельного задания:

-   Repository Responsibility Matrix;
-   MVP Appointment Contract;
-   Decision Log;
-   Consent Registry;
-   другие архитектурные документы.
