---
node_id: ayla.docs.reply-master-mvp-final-gap-check
title: Reply — Final Master MVP UX / Product Gap Check
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
  - "[[Ayla Knowledge Area Taxonomy]]"
  - "[[Ayla Repository Responsibility Matrix]]"
  - "[[Ayla Constitution]]"
  - "[[Ayla Glossary]]"
  - "[[Ayla Domain Capability Registry]]"
  - "[[Ayla MVP Appointment Contract]]"
  - "[[Ayla Domain Event Registry]]"
  - "[[Ayla Salon Operations MVP Contract]]"
  - "[[Ayla Master System and Recovery UX Contract]]"
  - "[[Ayla Master Operations Screen Contract]]"
  - "[[Ayla Master Schedule UX Contract]]"
  - "[[Ayla Appointment Detail Screen Contract]]"
  - "[[Ayla Master Appointment Flow MVP]]"
  - "[[Ayla Master App Information Architecture]]"
---

# Reply — Final Master MVP UX / Product Gap Check

## Audit mode and verdict

Это был read-only аудит. Канонические Product, Domain и UX документы не
изменялись. Новые capability, status, event, command, entity или Open Question
не создавались.

Текущий Master MVP UX-контур согласован на уровне presentation model, но ещё
не достаточен для безопасного production-capable Controlled Pilot без закрытия
двух authority-boundary решений.

Подтверждённая модель:

- Today / Schedule / Ayla;
- Today — execution surface;
- Schedule — planning и availability management;
- Ayla — conversational entry point к тем же authoritative operations;
- Appointment Detail P0 UX frozen;
- System and Recovery — shared consumer contract;
- normal visit — zero-action path с трёхчасовым post-visit resolution window.

Главный вывод: P0 implementation нельзя безопасно считать готовой до закрытия
auth/session/tenant boundary и authority/permission boundary обязательных Master
writes.

## P0 blockers

### P0-B1 — Master authentication, session, identity, tenant context

Канонический текущий контракт не определяет минимальный путь initial
authentication, session expiry, re-authentication, logout, invalid-session
recovery, Master identity и salon/tenant scope. UX предполагает авторизованный
shell и scope, но implementation-readable Master Auth/Re-auth contract не
найден; в strategy material Mobile Auth / Account-Linking Flow обозначен как
to be created.

Impact: команда не может безопасно гарантировать tenant isolation, правильный
Master context и recovery после истечения сессии.

Класс: P0 BLOCKER. OWNER DECISION REQUIRED.

Минимальный repair: определить canonical owner и минимальный контракт
authentication, session recovery, identity и tenant/salon context. Не создавать
полный account-management/profile surface.

### P0-B2 — Authority и permissions обязательных Master writes

Schedule определяет P0 Manual Booking, Working Hours, Specific Day Exception,
Time Off и Conflict Guard. Ayla определяет тот же preflight/proposal/
confirmation/authoritative-command path. Но SCH-OQ-01, SCH-OQ-06 и
Appointment OQ-AC-7 не закрывают:

- кто может инициировать write;
- tenant/provider scope;
- canonical authority;
- permission/policy gate;
- denial и conflict behavior.

Класс: P0 BLOCKER. OWNER DECISION REQUIRED.

Минимальный repair: закрыть минимальную Master authority/permission policy для
CreateAppointment и availability writes. Переиспользовать существующие domain
commands, events, Conflict Guard и readback; новые не придумывать.

### P0-B3 — Customer identity, deduplication, consent

Manual Booking и Ayla booking требуют customer resolution. Schedule OQ-03 и
связанные Appointment/consent материалы не дают полностью implementation-ready
правил поиска, deduplication, new-customer handling, disambiguation и consent.

Класс: P0 BLOCKER для booking-capable Controlled Pilot.
OWNER DECISION REQUIRED.

Минимальный repair: определить minimum customer resolution/dedup/consent и
minimum-necessary projection rules. Не расширять Customer Context до CRM/history.

### P0-B4 — Authoritative three-hour completion producer и deadline race

Трёхчасовое окно и zero-action normal completion утверждены. При этом
Appointment Contract OQ-AC-3, OQ-AC-15, OQ-AC-16 и Event Registry оставляют
producer, evidence/correction, ordering/race и authoritative registration
details незакрытыми.

Класс: P0 BLOCKER для lifecycle implementation.
OWNER DECISION REQUIRED для authority/evidence policy; producer/race —
implementation dependency.

Минимальный repair: определить producer, ordering/version, idempotency,
readback/reconciliation и correction semantics, сохранив модель:

scheduled_end → 3-hour resolution window → no exception →
authoritative normal completion.

Новые status/event не создавать.

### P0-B5 — Customer-side «Визит не состоялся»

Owner intent допускает customer exception в том же окне, но OQ-AC-17 не
определяет canonical command/outcome/authority. Это необходимо для полного
cross-party pilot, но не блокирует Master-only normal presentation shell.

Класс: P0 lifecycle dependency для полного pilot; OWNER DECISION REQUIRED.

Минимальный repair: переиспользовать существующую customer-side semantics либо
явно ограничить pilot. Не создавать dispute status, event, command или support
workflow.

## P0 canon-repair findings

Это не новые product decisions. Более сильные/поздние источники уже определяют
нужное поведение, но stale wording может ввести engineering в заблуждение.

| ID | Evidence | Finding | Класс |
| --- | --- | --- | --- |
| CR-1 | Master App Information Architecture, §§4.1–4.2, 5.4, 7, 9.1, 11–12 | Schedule описан как deferred/read-only, Ayla как не standalone, Schedule/Detail как future; конфликтует с approved P0 model | P0 CANON REPAIR ONLY |
| CR-2 | Master Schedule UX, §§2, 5, 7, 25A–25G | Одновременно заявлены read-only/no write authority и P0 booking/Time Off/availability/Ayla writes | P0 CANON REPAIR ONLY |
| CR-3 | Master Appointment Flow, §§9–10 | Ayla ограничена contextual-only, что конфликтует с approved write pattern | P0 CANON REPAIR ONLY |
| CR-4 | Salon Operations, §§4.4, 10 | Completion описан как unresolved/manual, хотя утверждён scheduled_end + 3 hours + zero-action | P0 CANON REPAIR ONLY |
| CR-5 | Domain Event Registry, appointment.completed | proposed/incomplete wording слишком широко оставляет normal completion undefined | P0 CANON REPAIR ONLY |
| CR-6 | AYLA-DEC-0021 и Schedule Open Questions | Уже утверждённые availability decisions повторно перечислены как unresolved | P0 CANON REPAIR ONLY |

Repairs должны только reconcile ownership/scope/wording. Не менять lifecycle,
не создавать новые domain entities и не расширять frozen UX.

## Cross-document consistency matrix

| Concern | Result | P0 impact |
| --- | --- | --- |
| Global navigation | Product/UX model approved; IA has stale wording | Canon repair |
| Today | Consistent execution surface | None |
| Schedule | Responsibility clear; write authority incomplete | Blocker + repair |
| Appointment Detail | Consistent and frozen | None |
| Manual Booking | Logical order consistent | Customer/permission blockers |
| Working Hours | Product model supported by AYLA-DEC-0021 | Command/permission dependency |
| Specific Day Exception | Approved UX; existing availability mapping required | No new entity |
| Time Off | Approved UX; authorized command incomplete | Authority blocker |
| Conflict Guard | No silent mutation; consistent | None if authoritative guard exists |
| Completion | Timing approved; producer/race incomplete | Blocker + repair |
| No Show | Read outcome separate from conditional action | Deferred authority |
| StartService | Deferred consistently | Not a blocker |
| Customer Context | Minimum necessary and privacy-aligned | Identity/consent blocker |
| Ayla reads | Same authoritative projections | None |
| Ayla writes | Same canonical path; stale IA/Flow text | Repair + authority blocker |
| Loading/Empty/Offline/Stale/Unknown/Conflict | Shared contract consistent | None |
| Permission | Presentation model exists; exact Master rights do not | Authority blocker |

## Implementability journey check

| Journey | Result | Reason |
| --- | --- | --- |
| Start working day | Blocked | Auth/session/tenant contract |
| Open Appointment Detail | Presentation-ready | Needs auth and projection binding |
| Normal visit | UX-ready, implementation-blocked | Producer/race/readback |
| Manual booking | Flow-ready, blocked | Customer identity and write permissions |
| Free interval booking | Same blockers | Slot/hold integration also required |
| Working Hours | Model exists | Command/permission realization |
| Specific Day Exception | UX approved | Map to existing availability model |
| Time Off | UX approved | Authorized command/conflict realization |
| Ayla read | UX-ready | Authoritative availability projection |
| Ayla booking | Same canonical path | Customer/permission blockers |
| Ayla availability change | Same Conflict Guard | Authority blocker |
| Degraded states | Shared model sufficient | Client realization only |

## Shared System / Recovery audit

Все шесть Master P0 surfaces потребляют shared System and Recovery contract.
Shared contract зависит только от Foundation/Architecture/Product и не зависит от
surface consumers. Циклов в dependency graph нет.

Проверенные invariants согласованы:

- Loading: skeleton без usable data; usable content сохраняется при refresh;
- Empty: authoritative normal business state, не failed/offline/stale/denied;
- Stale: read допустим, consequential write требует revalidation;
- Offline: last-known read только с freshness/trust marker; P0 writes не
  принимаются молча;
- Unknown write: neither success nor confirmed failure, нужен
  reconciliation/readback, blind duplicate запрещён;
- object-level и action-level permission различаются;
- Conflict сохраняет валидный контекст и не делает silent overwrite/shift;
- локальная ошибка ломает минимальную часть surface.

P0 противоречий в shared System/Recovery не найдено.

## Information architecture audit

Утверждённая навигация: Today / Schedule / Ayla. Manual Booking —
contextual action, не global destination. Customers/Profile/Create/More не
являются обязательными P0 destinations.

Найдены stale statements:

- Schedule в отдельных местах назван deferred/read-only;
- standalone Ayla в отдельных местах назван open;
- Schedule и Appointment Detail местами объявлены future;
- старые navigation assumptions могут подразумевать Profile/Customers.

Это CR-1: canon repair, а не новый продуктовый выбор.

## Domain invention guard

Новых domain entities/statuses/events/commands в Master UX chain не выявлено.
Available, Blocked, Non-working, Stale, Unknown, Conflict, Scheduled Interval,
Post-Visit Resolution, Specific Day Exception, FOCUS/ATTENTION/NEXT/
NOW_SCHEDULED остаются presentation/projection concepts.

Specific Day Exception — утверждённый UX-сценарий, отображаемый через existing
availability model; это не требование создавать backend entity.

## Privacy / security audit

UX сохраняет tenant/provider scope, minimum necessary customer context,
fail-closed behavior, отсутствие hidden AI inference, semantic memory leakage,
unrelated history/marketing данных и несанкционированного PII. Ayla использует
те же разрешённые projections и operations, а visibility не равна write
permission.

Фактические security/P0 blockers — auth/session/tenant contract и
customer identity/consent/permission boundaries. Дополнительный privacy scope
для Master MVP не требуется.

## Open Questions classification

### P0 / owner decision required

- auth, session, re-auth, identity, tenant/salon context;
- Master authority/permissions для CreateAppointment и availability writes;
- customer identity, deduplication и consent для booking;
- completion producer, evidence/correction и deadline race;
- customer-side «Визит не состоялся» для полного cross-party pilot.

### P1 / deferred

StartService, timer, authoritative In Progress, Master Mark No Show
authority/evidence, Request Change, Operational Notes, history/photo context,
embedded Ayla in Detail, non-delivery after arrival, offline retry/queue,
Notifications/Profile/Settings, advanced recurrence, CRM, analytics, earnings,
reviews, marketing и complex dispute/support flows.

### Resolved but stale in documents

Availability owner/model, Working Hours/Specific Day/Time Off mental model,
Slot Hold TTL, UTC/IANA timezone и existing availability mapping закреплены
AYLA-DEC-0021; physical API/permission realization остаётся engineering
dependency. Эти product decisions не должны повторно открываться как OQ.

## Validator baseline

Knowledge validator выполнен в read-only режиме:

- 109 knowledge nodes;
- 130 errors;
- 20 warnings.

Baseline не изменился относительно зафиксированных 130/20. Findings в основном
относятся к legacy frontmatter/metadata и unresolved links в несвязанных областях,
включая UX Agents и temporary documents. Finding, который создаёт новый P0
blocker в audited Master chain, не выявлен.

Validator baseline в рамках аудита не исправлялся.

## Closure order

1. Auth/session/tenant minimum contract.
2. Master permission/authority boundary for CreateAppointment and availability.
3. Customer resolution/deduplication/consent minimum contract.
4. Completion producer, race, idempotency, readback, correction.
5. Customer-side exception semantics or explicit pilot boundary.
6. Canon-only repairs: IA, Schedule, Appointment Flow, Salon Operations,
   Domain Event Registry, resolved Availability wording.

Не создавать более широкий backlog из deferred OQ. Не переоткрывать frozen
Appointment Detail или System/Recovery UX.

## Audit status

READ-ONLY. Canonical documents не изменялись. Encoding repair остаётся
encoding-only. Appointment Detail P0 UX и System/Recovery P0 UX остаются
FROZEN FOR IMPLEMENTATION.

SHORTEST SAFE PATH TO CONTROLLED PILOT:
закрыть минимальные authority contracts, выполнить canon-only repairs и
перейти к реализации без расширения frozen UX scope.
