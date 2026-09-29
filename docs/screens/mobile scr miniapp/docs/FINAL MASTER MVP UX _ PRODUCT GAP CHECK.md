ЗАДАЧА: FINAL MASTER MVP UX / PRODUCT GAP CHECK — ОПРЕДЕЛИТЬ ТОЛЬКО РЕАЛЬНЫЕ БЛОКЕРЫ НЕДЕЛЬНОГО ЗАПУСКА

Контекст

Мы практически закончили проектирование Master MVP Ayla.

Сейчас НЕ нужно продолжать создавать UX-документы, придумывать новые экраны или улучшать продукт.

Нужен финальный системный аудит актуального canonical knowledge, чтобы ответить на один вопрос:

ЧТО ЕЩЁ РЕАЛЬНО БЛОКИРУЕТ ЗАПУСК MASTER MVP?

Все найденные gaps необходимо жёстко разделить на:

1. BLOCKS LAUNCH
2. DOES NOT BLOCK LAUNCH

Главный принцип:

Если отсутствие решения не мешает мастеру пройти минимальный end-to-end рабочий день в MVP, оно не является launch blocker.

Мы сознательно хотим остановить дальнейшее проектирование и дать engineering-команде закончить реализацию.

Не превращать этот аудит в новый roadmap всего продукта.

==================================================
0. РЕЖИМ РАБОТЫ — AUDIT FIRST
==================================================

На первом проходе:

НЕ создавать новый canonical document.
НЕ менять существующие canonical documents.
НЕ исправлять найденные gaps.
НЕ проектировать новые capabilities.
НЕ писать implementation code.
НЕ создавать новые events/statuses/commands/entities.
НЕ закрывать Open Questions автоматически.

Сначала провести READ-ONLY аудит и подготовить итоговый gap report.

После отчёта владелец отдельно решит, какие blockers закрывать.

Исключение:

можно создать только summary/report в docs/, если repository conventions требуют сохранить результат аудита.

Canonical knowledge на первом проходе не менять.

==================================================
1. SOURCE OF TRUTH
==================================================

Использовать только актуальный canonical knowledge repository.

Не использовать handoff documents как Source of Truth.

Не использовать старые чаты, старые implementation notes или исторические mockups как источник продуктовой истины.

Design Evidence можно использовать только для проверки того, что уже принято canonical UX contract.

Приоритет:

Foundation
→ Architecture
→ Product / Product Operations
→ canonical UX
→ Design Evidence
→ implementation evidence.

Если документы противоречат друг другу:

не выбирать молча удобную трактовку.

Зафиксировать contradiction и определить, является ли она реальным launch blocker.

==================================================
2. ОБЯЗАТЕЛЬНО ПРОЧИТАТЬ
==================================================

Сначала определить актуальную repository structure и проверить названия/пути.

Минимально изучить полностью актуальные версии:

00 Foundation/Ayla Knowledge Area Taxonomy.md

00 Foundation/Ayla Repository Responsibility Matrix.md

Ayla Constitution

Ayla Glossary

Ayla Domain Capability Registry

05 Architecture/Ayla MVP Appointment Contract.md

05 Architecture/Ayla Domain Event Registry.md

06 Product/Ayla Salon Operations MVP Contract.md

07 UX/Ayla Master Operations Screen Contract.md

07 UX/Ayla Master Schedule UX Contract.md

07 UX/Ayla Appointment Detail Screen Contract.md

07 UX/Ayla Master Appointment Flow MVP.md

07 UX/Ayla Master App Information Architecture.md

07 UX/Ayla Master System and Recovery UX Contract.md

Также найти и прочитать актуальные canonical документы, определяющие:

- Master authentication;
- session lifecycle;
- device/session re-auth;
- permissions;
- Master identity;
- salon/tenant selection, если применимо;
- Manual Booking;
- Ayla Master conversational UX;
- customer minimum context;
- service selection;
- availability;
- Working Hours;
- Specific Day Exception;
- Time Off.

Если отдельного документа нет — не считать это автоматически проблемой.

Сначала проверить, покрыта ли semantics существующим canonical owner.

==================================================
3. ПРОВЕРИТЬ DEPENDENCY GRAPH
==================================================

Перед функциональным аудитом проверить:

- нет ли depends_on cycles;
- нет ли missing canonical dependencies;
- нет ли UX contract, который самовольно владеет domain semantics;
- нет ли двух canonical owners одного и того же поведения;
- нет ли consumer document, который всё ещё зависит от superseded source.

Новый shared contract:

07 UX/Ayla Master System and Recovery UX Contract.md

должен быть shared UX dependency для surface contracts.

Не переоткрывать уже исправленный dependency graph без обнаруженного фактического дефекта.

==================================================
4. MINIMUM MASTER MVP JOURNEY
==================================================

Использовать следующий end-to-end journey как основной launch test.

Мастер должен иметь возможность:

AUTHENTICATE
↓
ENTER MASTER APP
↓
SEE TODAY
↓
UNDERSTAND:
- кто следующий;
- какая услуга;
- когда запись;
↓
OPEN APPOINTMENT DETAIL
↓
SEE MINIMUM NECESSARY CUSTOMER / SERVICE CONTEXT
↓
WORK THROUGH SCHEDULED DAY
↓
OPEN SCHEDULE
↓
UNDERSTAND:
- appointments;
- free intervals;
- working hours;
- time off;
↓
CREATE MANUAL APPOINTMENT
↓
Customer
↓
Service
↓
Date / valid start
↓
authoritative availability validation
↓
CreateAppointment
↓
result / Conflict / Unknown
↓
MANAGE AVAILABILITY
↓
Working Hours
Specific Day Exception
Time Off
Conflict Guard
↓
USE AYLA
↓
read current operational context
↓
where allowed:
preflight
→ proposal
→ confirmation
→ authoritative command
→ result
↓
FINISH APPOINTMENT DAY
↓
scheduled_end
↓
3-hour Post-Visit Resolution
↓
no exception
↓
authoritative normal completion.

Также Master должен безопасно переживать:

Loading
Empty
Offline
Stale
Error
Permission denial
Conflict
Pending / Unknown.

Если любой обязательный участок этого пути невозможно однозначно реализовать по canon — это кандидат BLOCKS LAUNCH.

==================================================
5. КРИТЕРИЙ BLOCKS LAUNCH
==================================================

Gap относится в BLOCKS LAUNCH только если выполняется хотя бы одно:

A. Без решения невозможно реализовать обязательный P0 journey.

B. Два canonical документа дают несовместимые инструкции для одного P0 поведения.

C. Не определён authoritative owner обязательной P0 write operation.

D. Не определена минимально необходимая permission/security boundary для обязательного P0 action.

E. UI не может определить допустимый следующий шаг без product decision.

F. Реализация по существующему canon может привести к:
- duplicate booking;
- silent overwrite;
- cross-tenant access;
- неправильному Appointment lifecycle transition;
- потере Appointment;
- ложному Completed;
- consequential AI write без confirmation;
- другому серьёзному нарушению P0 invariant.

G. Обязательная P0 capability существует концептуально, но отсутствует минимальный contract, необходимый engineering-команде для реализации.

Не использовать критерий:

«было бы хорошо уточнить».

Это НЕ blocker.

==================================================
6. КРИТЕРИЙ DOES NOT BLOCK LAUNCH
==================================================

Отнести сюда всё, что:

- Deferred;
- Future;
- polish;
- analytics;
- marketing;
- secondary CRM;
- advanced history;
- richer profile;
- convenience capability;
- редкий edge case с безопасным fallback;
- capability, отсутствие которой не ломает minimum journey.

В частности проверить, но по умолчанию НЕ считать blockers без доказательства:

- StartService;
- procedure timer;
- отдельный authoritative In Progress;
- advanced Customer Context;
- полная история клиента;
- photo context;
- Operational Notes;
- Request Change;
- embedded Ayla inside Appointment Detail;
- Notifications inbox;
- Profile;
- Settings Hub;
- Earnings;
- Reviews/Feedback;
- Marketing;
- Loyalty;
- advanced analytics;
- substitution;
- advanced offboarding;
- master-reported non-delivery after customer arrival;
- offline writes;
- offline command queue.

Если какой-то из них всё-таки блокирует launch — нужно доказать конкретной цепочкой зависимости.

==================================================
7. TODAY / OPERATIONS HOME
==================================================

Проверить:

- information hierarchy;
- current / next Appointment;
- empty day;
- completed/past Appointment presentation;
- post-visit resolution presentation;
- ATTENTION semantics;
- navigation to Appointment Detail;
- relationship with Schedule;
- relationship with System & Recovery;
- minimum necessary data;
- no fake lifecycle inference.

Ответить:

может ли мастер в начале рабочего дня открыть Today и без дополнительного owner decision понять:

WHO
WHAT
WHEN
WHAT NEXT?

Статус:

READY / BLOCKED / PARTIAL.

==================================================
8. SCHEDULE
==================================================

Проверить:

- day timeline;
- Appointment cards;
- compact Who / What / When;
- available intervals;
- current-time indicator;
- fully free day;
- clickable available intervals;
- Working Hours;
- Specific Day Exception;
- full-day «Не работаю»;
- partial Time Off;
- Conflict Guard;
- freshness/revalidation;
- system/recovery behavior;
- contextual Manual Booking entry.

Не переоткрывать уже frozen/approved visual decisions без contradiction.

Статус:

READY / BLOCKED / PARTIAL.

==================================================
9. MANUAL BOOKING — ОСОБО ВАЖНО
==================================================

Проверить, что ПОСЛЕДНЯЯ owner-approved Manual Booking semantics действительно канонизирована.

Не ограничиваться наличием раннего flow.

Проверить наличие и согласованность:

Booking Draft
↓
Customer
↓
Service
↓
Date / Time
↓
Review / Create
↓
Result / Conflict / Unknown.

Обязательные owner decisions:

- никакого stepper;
- Draft является главным persistent interaction context;
- picker клиента — temporary selection surface;
- picker услуги — temporary selection surface;
- picker даты/времени — temporary selection surface;
- после выбора пользователь возвращается в Draft;
- Customer обязателен;
- Service обязателен;
- Date/time обязателен;
- valid starts зависят от duration выбранной Service;
- выбранный свободный Schedule interval является context/constraint, а не автоматически Appointment duration;
- final CreateAppointment использует authoritative availability revalidation;
- Conflict сохраняет всё ещё валидный draft;
- Customer ✓
  Service ✓
  Date ✓
  Time ✕
  если именно время стало конфликтным;
- Unknown не разрешает blind Create повторно;
- duplicate phone/new customer path не должен молча создавать очевидный duplicate, если canonical customer identity rules это позволяют определить.

Проверить preservation/invalidation:

CHANGE SERVICE
→ Customer сохраняется;
→ Service меняется;
→ Time revalidated/invalidated, если duration/eligibility изменились.

CHANGE DATE
→ Customer сохраняется;
→ Service сохраняется;
→ available time пересчитывается.

CHANGE CUSTOMER
→ Service/Time могут сохраняться только если customer-specific eligibility/constraints не требуют revalidation.

Не придумывать customer-specific eligibility, если её нет в canon.

Главный вопрос:

Есть ли один canonical implementation-readable contract для последней Manual Booking модели?

Если UX существует только в conversation/mockup/Design Evidence, но не в canonical knowledge — это кандидат BLOCKS LAUNCH.

==================================================
10. CUSTOMER SELECTION
==================================================

Проверить минимальный P0:

- existing customer search/select;
- minimum visible identity;
- create new customer;
- required fields;
- duplicate identity handling;
- privacy boundary;
- tenant boundary.

Не требовать полноценный CRM.

Если точная duplicate-resolution semantics не определена, определить:

может ли безопасный fallback позволить launch?

Например:

existing phone match
→ предложить existing customer
→ не создавать duplicate автоматически.

Если безопасный fallback возможен без новой domain model, не завышать gap до blocker.

==================================================
11. SERVICE SELECTION
==================================================

Проверить:

- откуда берётся список услуг;
- какие услуги мастер может выбирать;
- duration;
- price, если он нужен в booking UI;
- связь Service → availability duration;
- salon/master scope;
- disabled/ineligible services;
- tenant boundary.

Не проектировать новый service catalog.

Нужно только определить, достаточно ли canon для Manual Booking P0.

==================================================
12. APPOINTMENT DETAIL
==================================================

Appointment Detail уже:

FROZEN FOR IMPLEMENTATION.

Не переоткрывать его.

Проверить только отсутствие новых contradictions после System & Recovery integration.

Должно сохраняться:

Before Appointment
→ Scheduled Interval
→ Post-Visit Resolution
→ Authoritative Outcome.

Normal visit:

ZERO-ACTION HAPPY PATH.

Completed:

только authoritative projection/readback.

No Show:

outcome != automatic Master authority.

Pending / Unknown:

authoritative reconciliation.

Если новых contradictions нет:

READY.

==================================================
13. 3-HOUR POST-VISIT COMPLETION
==================================================

Проверить end-to-end consistency:

scheduled_end
→ 3-hour resolution window
→ no lifecycle exception
→ authoritative normal completion
→ appointment.completed.

Проверить:

- UI не является scheduler;
- local timer не создаёт Completed;
- exception до deadline не стирается blind completion;
- deadline race имеет canonical safe handling/dependency;
- feedback/review != lifecycle outcome.

Не решать:

master-reported non-delivery after customer arrival.

Он остаётся Open Question и не является blocker, если minimum journey безопасен без него.

==================================================
14. AYLA — ОСОБО ВАЖНО
==================================================

Проверить, что последняя owner-approved Ayla Master UX действительно зафиксирована canonical, а не осталась только Design Evidence / conversation.

Минимально проверить:

GLOBAL NAVIGATION:
Сегодня | Расписание | Ayla.

Ayla является отдельной P0 surface.

READ:

- может отвечать на разрешённые operational questions;
- использует authoritative projections;
- показывает uncertainty/freshness;
- stale/offline data не выдаётся за current truth.

WRITE:

conversation
→ intent
→ preflight
→ proposal
→ confirmation
→ authoritative command
→ result.

Правила:

- read != write;
- proposal != committed result;
- confirmation требуется для consequential write;
- known conflict должен блокировать misleading confirmation;
- Ayla не угадывает Service;
- Ayla переиспользует canonical Manual Booking semantics;
- Ayla переиспользует Conflict Guard;
- Ayla переиспользует Appointment Detail;
- Ayla использует shared System & Recovery trust model;
- AI uncertainty never becomes operational certainty.

Проверить, существует ли implementation-readable canonical contract для этого P0 поведения.

Если последняя Ayla UX модель существует только в mockup/conversation — это кандидат BLOCKS LAUNCH.

==================================================
15. SYSTEM & RECOVERY
==================================================

Проверить новый canonical:

07 UX/Ayla Master System and Recovery UX Contract.md

Ожидаемый статус:

FROZEN FOR IMPLEMENTATION.

Проверить:

- no dependency cycles;
- surface contracts являются consumers;
- shared contract не зависит циклически от consumers;
- Loading;
- Empty;
- Offline;
- Stale;
- Error;
- Permission;
- Pending / Unknown;
- Conflict;
- automatic revalidation;
- automatic reconciliation;
- Smallest Failure Surface Principle;
- no consequential offline writes;
- canonical navigation.

Если всё согласовано:

READY.

==================================================
16. AUTH / SESSION / RE-AUTH
==================================================

Это потенциально единственная инфраструктурно-UX зона, которую нельзя забыть.

Проверить canonical knowledge и implementation contracts на:

- initial authentication;
- existing session;
- expired access token/session;
- refresh;
- device re-auth;
- invalid/revoked session;
- logout;
- Master identity;
- tenant/salon context;
- unauthorized access;
- recovery path обратно в приложение.

Не проектировать sophisticated account management.

Нужен минимальный ответ:

может ли мастер:

OPEN APP
→ AUTHENTICATE
→ ENTER CORRECT TENANT/MASTER CONTEXT
→ SESSION EXPIRES
→ SAFELY RE-AUTHENTICATE
→ CONTINUE / RETURN TO SAFE CONTEXT?

Если нет canonical/implementation path — это потенциальный BLOCKS LAUNCH.

==================================================
17. APP SHELL / NAVIGATION
==================================================

Проверить только P0:

Сегодня | Расписание | Ayla.

Не возвращать:

Создать

или:

Ещё

как global destinations без нового owner decision.

Manual Booking запускается contextual Add action.

Проверить deep links/back behavior только там, где без него невозможно пройти P0 journey.

Не проектировать full navigation system заново.

==================================================
18. PERMISSIONS
==================================================

Проверить обязательные P0 actions:

- view Today;
- view Schedule;
- view Appointment;
- create manual Appointment;
- manage availability;
- Working Hours;
- Specific Day Exception;
- Time Off;
- Ayla reads;
- Ayla consequential writes;
- No Show, если capability вообще показывается.

Для каждого обязательного write должно быть понятно:

WHO MAY INITIATE
WHO AUTHORITATIVELY DECIDES
WHAT PERMISSION/POLICY GATE APPLIES
WHAT HAPPENS ON DENIAL.

Если exact permission implementation name не определён, это не обязательно blocker.

Blocker — когда сама authority boundary неизвестна.

==================================================
19. DESIGN EVIDENCE STATUS
==================================================

Составить inventory Design Evidence для:

- Operations Home;
- Schedule;
- Availability Management;
- Manual Booking;
- Appointment Detail;
- Ayla;
- System & Recovery.

Для каждого указать:

AVAILABLE / MISSING / SUPERSEDED / NON-CANONICAL CHROME.

Но:

отсутствие красивого final mockup само по себе НЕ является launch blocker, если textual canonical UX contract implementation-readable.

Design Evidence нужен для снятия визуальной неоднозначности, а не для повторного определения product semantics.

==================================================
20. НЕ ПУТАТЬ UX GAP И IMPLEMENTATION GAP
==================================================

Очень важно разделить:

CANONICAL/PRODUCT/UX GAP

и

ENGINEERING IMPLEMENTATION GAP.

Пример:

Canonical contract полностью определяет Manual Booking,
но backend endpoint ещё не реализован.

Это:

ENGINEERING GAP.

Не UX gap.

И наоборот:

backend уже имеет endpoint,
но canon не определяет, какую Service мастер имеет право выбрать.

Это:

CANONICAL/PRODUCT GAP.

В отчёте явно разделять эти категории.

==================================================
21. STATUS MATRIX
==================================================

Составить итоговую таблицу:

Surface / Capability
Canonical status
UX status
Design Evidence
Implementation dependency
Launch blocker?
Reason

Минимум строки:

Authentication / Re-auth
App Shell / Navigation
Today
Schedule
Working Hours
Specific Day Exception
Time Off
Manual Booking
Customer Selection
Service Selection
Availability / Valid Starts
Appointment Detail
3-hour Completion
Ayla Read
Ayla Write
System & Recovery
Permissions
Conflict Guard
Pending / Unknown

Использовать только фактические статусы из canon.

Не писать FROZEN там, где freeze не зафиксирован.

==================================================
22. BLOCKS LAUNCH
==================================================

После полного аудита вывести отдельный список.

Для каждого blocker использовать строгий формат:

P0-BLOCKER-XX — [короткое название]

Surface:
Canonical owner:
Problem:
Evidence:
Why it blocks launch:
Minimum decision/fix required:
What NOT to build:
Dependencies:

Не включать больше информации, чем нужно для снятия blocker.

Если blockers нет — прямо написать:

NO REMAINING UX/PRODUCT BLOCKERS FOUND FOR MASTER MVP.

Не придумывать blocker ради заполнения отчёта.

==================================================
23. DOES NOT BLOCK LAUNCH
==================================================

Составить отдельный список всех известных unresolved / deferred вещей.

Для каждой достаточно:

ID / concept
Why it does not block
When to revisit

Не превращать их в задачи текущей недели.

Особенно убедиться, что сюда попали безопасно отложенные:

- StartService;
- timer;
- advanced Customer Context;
- photo context;
- Operational Notes;
- Request Change;
- embedded Ayla in Appointment Detail;
- Notifications;
- Profile/Settings;
- master non-delivery after arrival;
- offline writes;
- offline queue;
- advanced dispute resolution;
- review/feedback workflow;
- Future CRM capabilities.

==================================================
24. OPEN QUESTIONS CLASSIFICATION
==================================================

Собрать актуальные Open Questions из audited canonical documents.

Каждый классифицировать:

LAUNCH BLOCKER
DEFERRED
FUTURE
IMPLEMENTATION DETAIL
GOVERNANCE / OWNER DECISION LATER.

Не считать Open Question blocker только потому, что он Open.

Главный вопрос:

мешает ли отсутствие ответа пройти minimum Master MVP journey безопасно?

==================================================
25. НЕ ИСПРАВЛЯТЬ BASELINE VALIDATOR ERRORS
==================================================

Knowledge validator сейчас имеет существующий baseline.

Не заниматься общим cleanup repository.

Отчёт должен отдельно сказать:

- baseline errors count;
- появились ли новые errors в audited/current documents;
- есть ли blocker, реально связанный с validator.

Не смешивать старый repository debt с Master MVP launch blockers.

==================================================
26. OUTPUT
==================================================

Создать:

docs/REPLY_MASTER_MVP_FINAL_GAP_CHECK.md

если это соответствует repository conventions.

Это AUDIT REPORT, а не новый canonical product contract.

В нём должны быть разделы:

1. Executive Result

2. Minimum Master MVP Journey

3. Surface Status Matrix

4. BLOCKS LAUNCH

5. DOES NOT BLOCK LAUNCH

6. Open Questions Classification

7. Canonical vs Engineering Gaps

8. Design Evidence Inventory

9. Dependency / Ownership Findings

10. Recommended Closure Order

11. Stop-Designing Boundary

==================================================
27. RECOMMENDED CLOSURE ORDER
==================================================

Если blockers найдены:

ранжировать только по launch criticality.

Например:

BLOCKER 1
→ решить owner decision

BLOCKER 2
→ minimal canonical reconciliation

BLOCKER 3
→ engineering dependency.

Не создавать большой backlog.

Нам нужен shortest path to launch.

==================================================
28. STOP-DESIGNING BOUNDARY
==================================================

В конце отчёта обязательно дать один из двух verdict.

Вариант A:

MASTER MVP UX/PRODUCT IS READY FOR IMPLEMENTATION.

No further product/UX design is required before Controlled Pilot except the
explicit blockers listed above.

или, если blockers отсутствуют:

MASTER MVP UX/PRODUCT IS CLOSED FOR P0.

Further UX/product work requires:
- demonstrated implementation blocker;
- canonical contradiction;
- Controlled Pilot evidence;
- explicit owner decision.

Нельзя продолжать проектирование просто потому, что существуют Future/Open Questions.

==================================================
29. VALIDATION
==================================================

Поскольку первый проход READ-ONLY:

не менять canonical documents.

Проверить:

- repository structure;
- dependency graph;
- canonical ownership;
- wikilinks/depends_on relevant to audited surfaces;
- validator status;
- git diff.

Если создаётся только audit report:

git diff --check

и существующий validator согласно repository conventions.

Не исправлять unrelated baseline.

==================================================
30. DEFINITION OF DONE
==================================================

Аудит завершён только если владелец после чтения одного файла может ответить:

1. Есть ли вообще оставшиеся UX/product blockers?
2. Какие именно?
3. Почему каждый действительно блокирует launch?
4. Какой минимальный fix нужен?
5. Что уже окончательно готово?
6. Зафиксирован ли последний Manual Booking UX?
7. Зафиксирован ли последний Ayla UX?
8. Готовы ли Today и Schedule?
9. Frozen ли Appointment Detail?
10. Frozen ли System & Recovery?
11. Есть ли полноценный минимальный Auth/Re-auth path?
12. Есть ли unresolved authority gap для обязательного P0 action?
13. Какие Open Questions можно спокойно оставить после запуска?
14. Какие gaps являются engineering, а не product/UX?
15. Можно ли прямо сейчас прекратить дальнейшее UX-проектирование и сосредоточиться на реализации?

Главный принцип аудита:

НЕ ИСКАТЬ, ЧТО ЕЩЁ МОЖНО СПРОЕКТИРОВАТЬ.

ИСКАТЬ ТОЛЬКО ТО, БЕЗ ЧЕГО НЕЛЬЗЯ БЕЗОПАСНО ЗАПУСТИТЬ MASTER MVP.

Цель:

SHORTEST SAFE PATH TO CONTROLLED PILOT.