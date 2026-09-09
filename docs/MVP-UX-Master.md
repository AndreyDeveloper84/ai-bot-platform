Ты работаешь как Senior Product Architect / UX Systems Designer проекта Ayla.

Репозиторий:
AndreyDeveloper84/ayla-knowledge

Работай с актуальной основной рабочей веткой репозитория и соблюдай все repository-local правила, AGENTS.md, README, taxonomy, validation rules, frontmatter/schema и правила canonical ownership.

ЗАДАЧА

Нужно зафиксировать в ayla-knowledge принятые Product/UX решения по Master MVP и подробно описать UX-контракт Schedule / Расписание для мобильного приложения мастера.

Это НЕ задача на реализацию backend/mobile.
Это НЕ задача на создание новых UI-макетов.
Это НЕ задача на проектирование новой архитектуры Ayla.
Это задача на фиксацию уже принятых решений и формализацию Schedule UX таким образом, чтобы после документа можно было:

1. нарисовать и доработать UI-макеты;
2. сформировать Screen Contracts;
3. поставить engineering-задачи;
4. реализовать mobile/backend без повторного придумывания бизнес-поведения;
5. не потерять уже созданный visual UX context.

КРИТИЧЕСКОЕ ПРАВИЛО

Мы создаём только документы, которые непосредственно продвигают Master MVP к запуску.

Не создавай дополнительные документы, ADR, glossary, research, future vision, handoff или вспомогательные спецификации, если они не обязательны для выполнения этой задачи.

Не расширяй scope за пределы Master MVP.

--------------------------------------------------
1. СНАЧАЛА ИЗУЧИ АКТУАЛЬНЫЙ CANON
--------------------------------------------------

Перед созданием или изменением документа обязательно изучи текущую структуру ayla-knowledge и найди актуальные версии как минимум следующих документов/областей:

- 00 Foundation/Ayla Repository Responsibility Matrix.md
- 00 Foundation/Ayla Knowledge Area Taxonomy.md
- Ayla MVP Appointment Contract
- Ayla Domain Event Registry
- Ayla Salon Operations MVP Contract
- Ayla Master Operations Screen Contract
- Ayla Master App Information Architecture
- Ayla Appointment Detail Screen Contract
- Ayla Master Appointment Flow MVP

Также найди актуальные canonical документы, если они существуют, для:

- specialist/master schedule;
- availability;
- working hours;
- time off;
- salon services;
- manual appointment creation;
- customer identity;
- online booking;
- tenant/salon context.

Не полагайся на названия папок из этого промпта, если repository taxonomy была изменена.

Определи canonical area по содержанию согласно:

00 Foundation/Ayla Knowledge Area Taxonomy.md

Не создавай новую папку только потому, что в промпте упомянут UX.

--------------------------------------------------
2. HANDOFF-ДОКУМЕНТЫ НЕ ЯВЛЯЮТСЯ CANON
--------------------------------------------------

Не использовать старые handoff-документы как нормативный источник:

- master-mobile handoff;
- master-management handoff;
- schedule-management handoff;
- любые другие handoff-файлы.

Их можно обнаружить при inventory, но нельзя переносить из них решения в новый canonical document только потому, что они там написаны.

Canonical dependency chain должна идти через актуальные документы ayla-knowledge.

--------------------------------------------------
3. СНАЧАЛА ПРОВЕРЬ, НЕ СУЩЕСТВУЕТ ЛИ УЖЕ НУЖНЫЙ ДОКУМЕНТ
--------------------------------------------------

Перед созданием нового файла:

1. проверь repository taxonomy;
2. найди документы про Master Schedule / Schedule View;
3. проверь существующий Master App Information Architecture;
4. проверь Salon Operations MVP Contract;
5. проверь Screen Contracts;
6. проверь, можно ли корректно расширить существующий canonical document.

Document Creation Rule обязателен.

Если предмет уже принадлежит существующему canonical document и может быть добавлен туда без смешения ответственности — обнови его.

Если требуется самостоятельный UX contract с собственной ответственностью — создай отдельный документ в правильной canonical area.

Не создавать дублирующий документ.

В итоговом summary явно напиши:

- какой вариант выбран: NEW DOCUMENT или UPDATE EXISTING;
- почему;
- какая canonical area является владельцем документа.

--------------------------------------------------
4. VISUAL REFERENCES / EXISTING UX MOCKUPS
--------------------------------------------------

В этой работе нужно обязательно учесть уже созданные в ходе текущей продуктовой проработки UX-макеты Master Operations Home.

Существует owner-reviewed visual concept, показывающий четыре состояния одного и того же мобильного Operations Home рядом:

1. начало рабочего дня / следующий клиент;
2. состояние между клиентами;
3. время текущей scheduled appointment;
4. день без записей.

Статус этих материалов:

VISUAL UX REFERENCE / OWNER-REVIEWED CONCEPT.

Они НЕ являются отдельным Source of Truth и НЕ переопределяют canonical contracts.

Использовать их как Design Evidence, подтверждающее уже принятые решения по информационной архитектуре.

Макет был создан как широкий composite из четырёх мобильных экранов.

Если агент имеет доступ к локальному изображению текущей сессии, reference path:

/mnt/data/a_clean_product_ui_mockup_image_a_wide_compositio.png

Не считать наличие именно этого пути обязательным: если файл недоступен в среде агента, использовать текстовое описание состояний из owner decisions ниже.

Не копировать изображение автоматически внутрь ayla-knowledge без repository rule или отдельной необходимости.

Если repository conventions позволяют хранить visual references, можно добавить путь/ссылку в раздел:

Visual References / Design Evidence

Если файл недоступен, всё равно зафиксировать текстовое описание существующего visual concept.

--------------------------------------------------
5. ЧТО ИМЕННО ПОДТВЕРЖДАЕТ СУЩЕСТВУЮЩИЙ OPERATIONS HOME MOCKUP
--------------------------------------------------

Макет подтверждает информационную архитектуру:

DAY CONTEXT
↓
FOCUS
↓
TODAY TIMELINE

и принцип:

один Operations Home сохраняет одну и ту же Information Architecture в течение всего рабочего дня, меняя содержание FOCUS presentation state.

После анализа четырёх состояний дополнительно зафиксированы следующие owner-reviewed UX conclusions:

- Operations Home не является Schedule View;
- первая глобальная зона называется «Сегодня», а не «Расписание»;
- завершённые записи могут оставаться в timeline и визуально уходить на второй план;
- timeline не должен нестабильно перестраиваться после каждого завершения;
- FOCUS может отображать:
  NEXT,
  NOW_SCHEDULED,
  ATTENTION,
  CHANGE,
  DAY_COMPLETE,
  EMPTY;
- это UI presentation states, а НЕ новые Appointment statuses;
- NOW_SCHEDULED означает «сейчас по расписанию», а не authoritative факт, что процедура действительно началась;
- если scheduled interval закончился, но Appointment ещё не завершена, FOCUS может показывать ATTENTION;
- нельзя писать «мастер забыл завершить», если система знает только факт незавершённого Appointment;
- свободные окна могут быть видны внутри timeline без отдельного громкого notification card;
- постоянный большой Ayla-блок на Operations Home не обязателен;
- Ayla появляется контекстно только если реально добавляет новую полезную информацию;
- отдельные глобальные tabs «Клиенты» и «Профиль» для Master MVP не нужны;
- initial mockup bottom navigation «Расписание / Клиенты / Профиль» считается устаревшей визуальной итерацией;
- актуальная owner decision по global navigation:
  Сегодня | Расписание | Ayla.

Если visual reference конфликтует с более новым owner decision или canonical contract, приоритет имеет:

Canonical Contract
↓
Owner Decision
↓
Visual Reference

Конфликт фиксировать как UX/CANON RECONCILIATION GAP.

Не исправлять канон молча на основании старого mockup.

--------------------------------------------------
6. ЗАФИКСИРОВАННЫЕ OWNER / PRODUCT РЕШЕНИЯ
--------------------------------------------------

Следующие решения уже приняты владельцем продукта.

Не переводить их обратно в Open Questions.

Не менять их без обнаруженного конфликта с более высоким canonical contract.

Если обнаружен конфликт — не исправлять его молча. Зафиксировать как Conflict / Owner Decision Required.

6.1. Master MVP bottom navigation

Для Master MVP обязательны три глобальные зоны:

Сегодня | Расписание | Ayla

Их ответственность:

СЕГОДНЯ
= execution surface.
Мастер выполняет работу текущего дня.

РАСПИСАНИЕ
= planning / availability management surface.
Мастер видит и изменяет будущую занятость и доступность для записи.

AYLA
= conversational interaction surface.
Мастер взаимодействует с Ayla естественным языком в рамках разрешённого operational context.

Отдельных глобальных вкладок в MVP для:

- Клиенты;
- Профиль;
- Уведомления;
- Доход;
- Аналитика;

не требуется.

Customer context открывается из operational flows.
Profile/settings не должны получать равный navigation priority только потому, что это отдельный экран.

--------------------------------------------------
7. ЗАФИКСИРОВАННЫЙ OPERATIONS HOME
--------------------------------------------------

Не перепроектируй Operations Home.

Он уже определён как:

DAY CONTEXT
↓
FOCUS
↓
TODAY TIMELINE

FOCUS является UI/presentation state, а НЕ новым Appointment domain status.

Подтверждённые presentation states:

- NEXT;
- NOW_SCHEDULED;
- ATTENTION;
- CHANGE;
- DAY_COMPLETE;
- EMPTY.

Operations Home уже проверен концептуально через:

- начало рабочего дня;
- до первого клиента;
- между клиентами;
- время scheduled appointment;
- закончившуюся, но незавершённую запись;
- свободное окно;
- изменение расписания;
- пустой день;
- конец рабочего дня.

Schedule document не должен повторно описывать весь Operations Home.

Нужно только корректно показать связь:

Сегодня
→ Appointment Detail

и:

Расписание
→ Appointment Detail

Оба entry point используют один authoritative Appointment Detail contract.

--------------------------------------------------
8. РЕШЕНИЕ: SCHEDULE ВХОДИТ В MASTER MVP P0
--------------------------------------------------

Расписание больше НЕ является Deferred capability.

Зафиксировать:

Schedule / Расписание является P0 Master MVP.

Причина:

Ayla B2B должна поддерживать реальную операционную модель мастера/салона, где online booking зависит от актуальной availability.

Schedule является частью базовой B2B-ценности наряду с online booking.

Главная ответственность Schedule:

«Показать мастеру, когда он занят, когда доступен для записи и позволить изменить эту картину в пределах разрешённых MVP-действий».

--------------------------------------------------
9. ОСНОВНЫЕ СОСТОЯНИЯ ВРЕМЕНИ В SCHEDULE
--------------------------------------------------

Schedule должен концептуально различать как минимум:

APPOINTMENT

AVAILABLE TIME

BLOCKED TIME / TIME OFF

NON-WORKING TIME

Критически важно:

absence of Appointment != available for booking.

Нельзя определять availability простой проверкой отсутствия Appointment.

Концептуальная зависимость:

Working Hours
+
Time Off
+
Existing Appointments
+
Service Duration
+
Canonical Booking / Availability Constraints
↓
Availability
↓
Available booking start times

Не изобретай новые availability rules, если их нет в canonical source.

Если конкретные правила slot generation не определены — укажи dependency/open question, но не придумывай их.

--------------------------------------------------
10. ОСНОВНОЙ MOBILE SCHEDULE VIEW
--------------------------------------------------

Для Master MVP зафиксировать рекомендуемую информационную архитектуру:

WEEK SELECTOR
↓
SELECTED DAY
↓
VERTICAL DAY TIMELINE

Не использовать monthly grid как основной operational surface телефона.

Week selector должен позволять быстро переключаться между днями.

Day timeline показывает:

- appointments;
- available intervals;
- blocked/time-off intervals;
- рабочий диапазон;
- при необходимости границу non-working time.

Не требуется визуально отображать весь 24-часовой день.

Основной viewport может быть ориентирован на рабочие часы.

Документ должен описать INFORMATION ARCHITECTURE и UX behavior, а не pixel-perfect layout.

--------------------------------------------------
11. ОСНОВНЫЕ ДЕЙСТВИЯ SCHEDULE MVP
--------------------------------------------------

P0 Schedule должен поддерживать:

1. View selected day/week.
2. Open existing Appointment.
3. View available time.
4. View blocked/time-off intervals.
5. Manual appointment creation.
6. Block time / create time off.
7. View/edit working hours, если это уже разрешено canonical product/domain contracts.

Если working-hours editing конфликтует с существующим ownership/permission contract — не придумывать решение, а вынести конфликт.

--------------------------------------------------
12. MANUAL APPOINTMENT CREATION — P0
--------------------------------------------------

Это окончательное owner decision.

Manual Appointment Creation входит в Master MVP P0.

Причина:

Запись может появляться не только через client online booking.

Реальные источники:

- телефон;
- мессенджер;
- личная договорённость;
- повторная запись после визита;
- администратор;
- другие offline/manual channels.

Если такие записи не попадают в Ayla, система перестаёт знать реальную занятость мастера и client online availability становится недостоверной.

Поэтому manual booking является частью integrity availability model, а не convenience feature.

--------------------------------------------------
13. ОБЯЗАТЕЛЬНЫЙ MANUAL BOOKING FLOW
--------------------------------------------------

Основной flow через глобальное Add action:

+ Новая запись
↓
Клиент
↓
Услуга
↓
Дата и время
↓
Availability validation
↓
Review
↓
Create Appointment
↓
Authoritative result
↓
Schedule refresh

Это решение зафиксировано.

Не менять порядок без выявленного canonical conflict.

Критически важно:

УСЛУГА является обязательным отдельным шагом.

Не допускать flow:

Customer → arbitrary time → Service

как основной flow.

Причина:

service определяет как минимум duration и может влиять на availability.

Правильная зависимость:

Customer
+
Service
+
Master
+
Time
↓
Availability validation
↓
Appointment

--------------------------------------------------
14. БЫСТРЫЙ FLOW ИЗ СВОБОДНОГО ИНТЕРВАЛА
--------------------------------------------------

Если мастер нажимает на конкретный available interval:

Available interval
↓
Choose action
↓
Записать клиента
↓
Клиент
↓
Услуга
↓
Validate that selected service fits
↓
Review
↓
Create Appointment

Второе действие из available interval:

Заблокировать время

Таким образом context action может быть:

Что сделать?

- Записать клиента
- Заблокировать время

Не добавлять дополнительные действия без необходимости.

--------------------------------------------------
15. CUSTOMER SELECTION
--------------------------------------------------

Manual booking требует minimal customer lookup.

MVP:

- поиск по имени;
- поиск по телефону;
- выбор существующего клиента;
- возможность создать нового клиента, если он не найден.

Минимальное создание нового клиента:

Имя *
Телефон *

Не добавлять автоматически:

- CRM-анкету;
- теги;
- день рождения;
- адрес;
- медицинские данные;
- AI memory;
- сложный customer profile;
- маркетинговые поля.

После успешного создания нового клиента он должен стать выбранным customer в текущем booking draft.

Не заставлять мастера искать его повторно.

Отдельная глобальная вкладка «Клиенты» для этого flow не требуется.

--------------------------------------------------
16. SERVICE SELECTION
--------------------------------------------------

Service selection является обязательной частью manual booking.

Мастер должен выбирать только услуги, которые разрешены/доступны ему в текущем specialist/salon/tenant context согласно canonical domain rules.

UX должен иметь возможность показать минимум:

- service name;
- duration;
- price, если price является доступным canonical booking snapshot/input.

После выбора service система знает duration, необходимую для availability calculation.

Не использовать весь глобальный каталог Ayla как список услуг мастера.

Не изобретать service ownership rules — использовать canonical model.

--------------------------------------------------
17. DATE / TIME SELECTION
--------------------------------------------------

После выбора услуги система должна показывать допустимые available start times для выбранной service duration.

Не показывать произвольное «пустое место» как гарантированно допустимый booking slot.

Если master вошёл через конкретный available interval, выбранное время может быть prefilled.

После выбора service нужно проверить, помещается ли услуга в этот interval.

Пример:

Available start: 14:00.

Service duration: 60 minutes.

Если 14:30 начинается другая Appointment, нельзя создать 14:00–15:00.

UX должен сообщить, что выбранного интервала недостаточно, и предложить выбрать другое доступное время.

Не выполнять silent time shift.

--------------------------------------------------
18. FINAL REVIEW
--------------------------------------------------

До authoritative CreateAppointment мастер должен видеть review минимум:

Customer

Service

Duration

Price — если применимо по canonical contract

Date

Start/end time

Master

После подтверждения выполняется authoritative CreateAppointment.

Не считать local mobile state фактом существования Appointment.

--------------------------------------------------
19. SERVER-SIDE AVAILABILITY REVALIDATION
--------------------------------------------------

Документ должен явно зафиксировать UX expectation:

availability должна быть повторно authoritative validated при создании Appointment.

Причина:

между отображением слота и CreateAppointment другой actor может изменить расписание.

Например:

- клиент сделал online booking;
- администратор создал запись;
- мастер с другого устройства изменил schedule;
- появился time off.

Если slot больше недоступен:

Appointment не создаётся.

UX должен получить conflict state и предложить перечитать availability / выбрать другое время.

Не фиксируй конкретный HTTP status или API schema, если это не определено architecture/API contract.

Это UX contract, не API specification.

--------------------------------------------------
20. BLOCK TIME / TIME OFF
--------------------------------------------------

Schedule MVP должен позволять мастеру блокировать разрешённый интервал.

Conceptual flow:

Available interval
↓
Block time
↓
Start
↓
End
↓
Optional reason
↓
Confirm
↓
Authoritative result
↓
Availability refresh

Reason не делать обязательным без canonical требования.

После successful block interval не должен оставаться доступным для client online booking.

Не придумывать новую domain entity, если canonical backend/domain уже использует TimeOff или эквивалент.

Используй существующую терминологию.

--------------------------------------------------
21. WORKING HOURS
--------------------------------------------------

Разделить:

Working Hours
=
базовый рабочий график.

Time Off
=
исключение из рабочего графика.

Не смешивать их в одну сущность.

Working-hours editing не обязательно размещать непосредственно внутри day timeline.

Допустимый UX:

Schedule
→ menu/settings
→ Working Hours

Но конкретный presentation pattern не делать domain requirement.

--------------------------------------------------
22. SCHEDULE STATES, КОТОРЫЕ ДОКУМЕНТ ДОЛЖЕН ПОКРЫТЬ
--------------------------------------------------

Как минимум:

A. Normal working day
appointments + available intervals.

B. Completely free working day
working hours существуют, appointments отсутствуют.

C. Fully booked day
рабочее время занято appointments/blocked intervals.

D. Non-working day
master не работает.

E. Day with Time Off
часть рабочего дня недоступна.

F. Working schedule not configured
если это валидное состояние существующей модели.

G. Appointment conflict during manual creation
slot изменился до commit.

H. Selected service does not fit selected interval.

I. Empty/no future data state, если он реально возможен.

Для каждого состояния описать:

- что видит мастер;
- какой primary action;
- какие actions запрещены/отсутствуют;
- authoritative source;
- куда ведёт interaction.

--------------------------------------------------
23. СВЯЗЬ С ONLINE BOOKING
--------------------------------------------------

Документ должен явно показать продуктовую петлю:

Working Hours
+
Time Off
+
Existing Appointments
+
Service Duration
↓
Availability
↓
┌────────────────────────┐
│                        │
Client Online Booking    Manual Booking
│                        │
└───────────┬────────────┘
            ↓
       Appointment
            ↓
         Schedule
            ↓
          Today
            ↓
   Appointment Detail
            ↓
         Complete

Это conceptual product/data flow.

Не объявлять Schedule Source of Truth для Appointment.

Repository Responsibility Matrix и Appointment Contract остаются выше UX документа по authority.

--------------------------------------------------
24. AYLA BOUNDARY
--------------------------------------------------

Ayla является отдельной обязательной global navigation zone, но Schedule contract не должен превращаться в AI specification.

Допускается зафиксировать conceptual future/allowed interaction:

«В среду после 14:00 меня не записывай»

Ayla может интерпретировать намерение и подготовить действие.

Но write должен соответствовать canonical AI/tool/action authority.

Не утверждать silent autonomous schedule writes, если они не разрешены canonical contracts.

Если требуется confirmation:

AI interpretation
↓
proposed action
↓
human confirmation
↓
authoritative command

Не проектировать tool schemas в этом документе.

--------------------------------------------------
25. НЕ ВКЛЮЧАТЬ В MASTER MVP БЕЗ ОТДЕЛЬНОГО РЕШЕНИЯ
--------------------------------------------------

Не расширять документ следующими возможностями:

- drag & drop appointment;
- сложный recurring schedule editor;
- waitlist;
- room/resource scheduling;
- staff management;
- payroll;
- earnings;
- advanced CRM;
- marketing;
- complex appointment reschedule UX;
- complex cancellation UX;
- calendar synchronization;
- Google/Apple calendar sync;
- advanced service buffers, если они не являются уже обязательным canonical rule;
- AI autonomous writes;
- medical information;
- analytics dashboard.

Если такие вещи упоминаются существующим canon, классифицируй их корректно как Deferred/Future/Out of Scope, если они не обязательны для текущего MVP.

--------------------------------------------------
26. НЕ ПРИДУМЫВАТЬ DOMAIN SEMANTICS
--------------------------------------------------

Очень важно.

UX document может определять:

- information hierarchy;
- navigation;
- presentation states;
- user flows;
- screen responsibility;
- interaction expectations.

UX document НЕ должен самостоятельно создавать:

- Appointment statuses;
- domain events;
- write authority;
- availability formulas;
- permission model;
- tenant ownership;
- payment semantics;
- cancellation semantics;
- service ownership semantics;
- customer identity semantics.

Если UX требует чего-то, чего нет в canonical domain/architecture contract:

Open Question / Dependency / Owner Decision Required.

Не маскировать пробел красивым UX-описанием.

--------------------------------------------------
27. DOCUMENT STRUCTURE
--------------------------------------------------

Используй repository-local canonical template и frontmatter.

Содержание документа должно как минимум включать:

1. Status / Purpose

2. Scope

3. Canonical Dependencies

4. Visual References / Design Evidence

5. Master MVP Global Navigation
   - Сегодня
   - Расписание
   - Ayla

6. Existing Operations Home Visual Concept
   - 4-state mockup
   - что он подтверждает
   - какие элементы считаются устаревшей визуальной итерацией

7. Schedule Responsibility

8. Schedule Information Architecture
   - week selector
   - selected day
   - day timeline

9. Schedule Time Presentation
   - appointment
   - available
   - blocked/time off
   - non-working

10. Schedule Actions

11. Appointment Entry Point

12. Manual Appointment Creation
    - primary flow
    - free-interval flow

13. Customer Selection

14. New Customer Minimal Flow

15. Service Selection

16. Date / Time Selection

17. Availability Validation

18. Review and Create

19. Conflict / Stale Availability Behavior

20. Block Time / Time Off

21. Working Hours Boundary

22. Schedule States

23. Relationship with Today

24. Relationship with Online Booking

25. Ayla Boundary

26. Privacy Boundary

27. MVP / Deferred / Future

28. Dependencies / Open Questions

29. Validation Checklist

Структуру можно адаптировать под repository canonical style, но не потерять перечисленные смыслы.

--------------------------------------------------
28. ДИАГРАММЫ
--------------------------------------------------

Добавь простые Mermaid или ASCII diagrams, если repository conventions это позволяют.

Минимум нужны:

A. Global Master navigation

Сегодня | Расписание | Ayla

B. Operations Home IA

DAY CONTEXT
↓
FOCUS
↓
TODAY TIMELINE

C. Manual booking flow

Customer
↓
Service
↓
Date/time
↓
Availability validation
↓
Review
↓
Create Appointment

D. Availability/product loop

Working Hours
+
Time Off
+
Appointments
+
Service Duration
↓
Availability
↓
Online + Manual Booking
↓
Appointment

E. Shared Appointment Detail entry points

Today ─────┐
           ├→ Appointment Detail
Schedule ──┘

Диаграммы должны помогать реализации, а не украшать документ.

--------------------------------------------------
29. CURRENT DECISIONS VS OPEN QUESTIONS
--------------------------------------------------

Не превращай следующие решения в Open Questions:

- Today / Schedule / Ayla navigation;
- Schedule = P0;
- manual booking = P0;
- Customer → Service → Date/time ordering;
- service selection mandatory;
- availability validation mandatory;
- block time = P0;
- customer lookup inside manual booking;
- minimal new customer = name + phone;
- no separate Customers tab for MVP;
- Schedule and Today share Appointment Detail;
- Operations Home uses stable IA through the day;
- Operations Home mockup is valid as Design Evidence, not Source of Truth;
- initial bottom navigation from the mockup is obsolete and replaced by Today / Schedule / Ayla.

Open Questions оставлять только там, где действительно нет canonical/owner решения.

--------------------------------------------------
30. RECONCILIATION С СУЩЕСТВУЮЩИМ CANON
--------------------------------------------------

После drafting обязательно проверь новый документ против:

- Appointment Contract;
- Domain Event Registry;
- Salon Operations MVP Contract;
- Master Operations Screen Contract;
- Master App Information Architecture;
- Appointment Detail Screen Contract;
- Master Appointment Flow MVP;
- Repository Responsibility Matrix;
- Knowledge Area Taxonomy.

Особенно проверь, не остались ли где-то старые утверждения:

Schedule = Deferred

или navigation, противоречащая:

Today | Schedule | Ayla.

Также проверь, не существуют ли визуальные/текстовые references на старую навигацию:

Расписание | Клиенты | Профиль.

НО:

Не изменяй автоматически другие canonical документы в рамках этой задачи, если это не требуется repository rules.

Сначала составь список найденных reconciliation gaps.

Если изменения других canonical документов необходимы для устранения прямого противоречия, перечисли их в summary как FOLLOW-UP, если задача не требует атомарного обновления dependency chain.

Не устраивай массовую миграцию документов.

--------------------------------------------------
31. VALIDATION
--------------------------------------------------

После создания/обновления:

- git diff --check;
- targeted repository validation;
- полный knowledge validator, если он предусмотрен и разумно запускается;
- проверить internal links / depends_on;
- проверить frontmatter;
- проверить отсутствие случайных изменений других файлов.

Существующие baseline validator errors не исправлять в этой задаче.

Отделить:

NEW ERRORS CAUSED BY THIS CHANGE

от:

EXISTING BASELINE ERRORS.

--------------------------------------------------
32. SUMMARY FILE
--------------------------------------------------

Создай отдельный итоговый отчёт в принятой repository/project структуре, аналогично существующим REPLY_* документам.

Предпочтительное имя:

docs/REPLY_MASTER_SCHEDULE_MVP_UX_CONTRACT.md

Если repository conventions требуют другое имя — используй их.

Summary должен содержать:

1. Canonical document created/updated.
2. Почему выбран new/update.
3. Canonical area.
4. Dependencies read.
5. Visual references / mockups considered.
6. Owner decisions recorded.
7. Main Schedule IA.
8. Manual booking flow.
9. Availability boundary.
10. Time-off / working-hours boundary.
11. Today/Schedule/Ayla navigation.
12. Existing Operations Home mockup status.
13. Reconciliation gaps found.
14. Open Questions.
15. Files changed.
16. Validation results.

--------------------------------------------------
33. ЗАПРЕТЫ
--------------------------------------------------

Не писать production code.

Не менять beautygo_backend.

Не менять mobile repository.

Не менять ai-bot-platform.

Не создавать API specification.

Не создавать database migrations.

Не создавать новые domain events.

Не создавать новый Appointment lifecycle.

Не создавать новые UI mockups/Figma в рамках этой задачи.

Не использовать handoff как canonical source.

Не делать git push.

Не создавать PR.

Не коммитить, если это отдельно не разрешено текущими repository rules/owner instruction.

Не исправлять unrelated repository problems.

--------------------------------------------------
34. DEFINITION OF DONE
--------------------------------------------------

Работа считается завершённой, когда существует один canonical UX-документ, из которого однозначно понятно:

1. почему Schedule входит в Master MVP;
2. место Schedule между Today и Ayla;
3. как выглядит информационная архитектура mobile Schedule;
4. какие состояния времени различает интерфейс;
5. как мастер открывает существующую Appointment;
6. как мастер вручную создаёт Appointment;
7. что порядок manual booking:
   Customer → Service → Date/time;
8. почему Service выбирается до availability;
9. как создаётся минимальный новый customer;
10. как обрабатывается stale/conflicting availability;
11. как мастер блокирует время;
12. чем Working Hours отличаются от Time Off;
13. как Schedule влияет на client online booking;
14. какие возможности входят в P0;
15. какие возможности сознательно не входят в MVP;
16. какие вопросы остаются реально нерешёнными;
17. какие visual references уже существуют;
18. какие решения подтверждены Operations Home mockup;
19. какие части старого mockup больше не актуальны;
20. как visual evidence связано с canonical UX contract.

Главный критерий:

После чтения документа дизайнер и engineering-агенты не должны заново решать продуктовые вопросы:

«Как мастер работает в Today?»
«Как мастер управляет своим расписанием?»
«Как мастер вручную создаёт запись в Ayla MVP?»
«Как связаны Today, Schedule и Ayla?»
«Какие уже существующие визуальные решения нужно учитывать?»

Ответ должен уже находиться в canonical UX contract.

Сначала выполни repository/document preflight и сообщи коротко:

- какой существующий документ будет обновлён или какой новый документ будет создан;
- canonical area;
- почему;
- какие canonical dependencies прочитаны;
- какие visual references найдены/доступны;
- обнаружены ли прямые конфликты.

После preflight, если нет настоящего blocker, сразу выполняй задачу до конца без ожидания дополнительного подтверждения.