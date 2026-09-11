# Ayla — Solo Master Onboarding in MAX
## Implementation Handoff for Orchestrator / Lead Developer

**Status:** OWNER-APPROVED PRODUCT/UX/ENGINEERING HANDOFF
**Date:** 2026-09-11
**Primary entry surface:** salon MAX bot
**Daily work surface after onboarding:** Master Mini App
**Target user:** independent solo service professional
**Goal:** allow a new solo master to register without salon invite, create their own workspace, configure the minimum required booking setup, explicitly publish the profile, and land in the normal Master App ready to operate.

---

## 0. Executive directive

Implement one coherent end-to-end path:

`MAX salon bot → solo registration → Ayla identity link → own tenant → owner+admin+master → setup checklist → explicit publication → Master Mini App`

Do not create:
- a third "Ayla Solo" bot;
- a button in the client bot;
- a second onboarding engine;
- a second master state model;
- a second catalog/mapping authority;
- a separate permanent onboarding application.

Use the existing solo onboarding service and existing Master Mini App. The work is to connect the existing domain machinery into a reliable user path and fill the missing UX/contract gaps.

---

## 1. Existing Linear anchors

Primary implementation epic:
- DRF-1503 — Master connection / invite delivery / unified state / solo registration.

Primary solo task:
- DRF-1509 — solo master registers through the salon bot, without a new bot.

Master onboarding scope:
- DRF-907 — Registration, Profile Setup, Services Setup, Schedule Setup, Portfolio, Publication.
- DRF-930…935 — individual onboarding steps.

Master production UI:
- DRF-1180 — Master MVP Production UI and Engineering Handoff.
- Frozen Master bottom navigation after onboarding:
  `Сегодня | Расписание | Ayla`

Unified "master landed" semantics:
- DRF-1506 — one definition of landed/connected master instead of multiple predicates.

The onboarding must end in the same operational Master App as all other masters. The salon bot is only the entry/orchestration surface; it is not the permanent workspace for a solo master.

---

## 2. Product principles

### 2.1 Registration and onboarding are not the same thing

Registration ends when:
- channel identity is known;
- the user explicitly chooses the solo-master path;
- minimum identity/profile input is collected;
- Ayla creates/links the solo identity;
- own tenant is created;
- roles owner + admin + master exist;
- the master can open their own Master Mini App.

Onboarding continues after registration until booking readiness.

Do not force the entire profile into one long chat session.

### 2.2 Progressive setup

First conversation should get the master inside Ayla quickly.

Minimum path:

`name → city → activity type(s) → explicit account creation → workspace created`

Then Ayla shows an actionable checklist:
- services and prices;
- location / service format;
- working hours;
- publication readiness.

Optional enrichment:
- photo;
- portfolio;
- bio;
- certificates;
- other non-blocking profile details.

### 2.3 Chat for guidance, Mini App for dense editing

Use MAX chat for:
- choosing solo path;
- natural-language questions;
- name/city/activity capture;
- progress explanation;
- reminders about unfinished setup;
- simple confirmations;
- launch/open-app actions.

Use Mini App for:
- choosing multiple service templates;
- price/duration editing;
- location/address;
- weekly schedule;
- photos/portfolio;
- final preview/publication;
- later daily work.

Do not reproduce complex forms as dozens of bot messages.

---

## 3. Entry routing in MAX

Current role-less branch already asks for an invite code.

Replace it with a clear two-path choice.

Bot message:

> Привет! Я Ayla.
> Если салон уже пригласил вас — пришлите код приглашения.
> Если вы работаете самостоятельно — я помогу создать своё рабочее пространство.

Quick actions:
- `У меня есть код`
- `Я работаю самостоятельно`

Semantic actions must be stable IDs, not inferred from label text.

Conceptually:
- `MASTER_ENTRY_HAVE_INVITE`
- `MASTER_ENTRY_SOLO`

Free text remains accepted.

Examples that should map to solo intent:
- "я сам работаю"
- "у меня свой кабинет"
- "я частный мастер"
- "хочу зарегистрироваться как мастер"

Do not let LLM create the account merely from intent detection. Account creation requires explicit controlled action later.

---

## 4. Identity rules

### 4.1 MAX identity

In MAX, do not create a fake "anonymous user" entity when reliable channel identity already exists.

Distinguish:
- identified channel user;
- registered Ayla subject/account.

The bot channel identity is the input for idempotent solo registration.

### 4.2 No foreign salon ownership

The user may physically enter through a salon-specific bot, but the solo onboarding path must ignore the salon tenant for ownership purposes.

The solo master:
- does not become staff of the salon whose bot was used;
- does not enter that salon roster;
- does not enter that salon catalog;
- does not inherit salon settings;
- does not inherit salon permissions.

Use the neutral/bootstrap scope already defined by the existing solo onboarding architecture.

A dedicated leakage test is mandatory.

---

## 5. Minimum conversational flow

### State S0 — role-less entry

Ayla offers:
- invite code path;
- solo path.

### State S1 — solo intent confirmed

Ayla:

> Отлично. Создадим ваше собственное рабочее пространство.
> Для начала — как вас зовут?

Input:
- free text;
- existing channel profile name may be suggested, but user can correct it.

Store provenance:
`user_entered`

### State S2 — city

Ayla:

> В каком городе вы принимаете клиентов?

Input:
- city selection/search/free text.

Do not ask home address at this stage.

### State S3 — activity type

Ayla:

> Чем вы занимаетесь? Можно выбрать несколько направлений.

Examples:
- Маникюр
- Педикюр
- Массаж
- Косметология
- Брови и ресницы
- Парикмахерские услуги
- Другое

This is multi-select.

The activity type must be controlled taxonomy/category semantics where available. LLM may understand free text, but must map into controlled candidates and ask if ambiguous.

### State S4 — explicit account creation

Before creating persistent solo workspace, show exactly what will happen:

> Создам для вас отдельное рабочее пространство Ayla.
> Вы будете его владельцем и мастером.
> После этого настроим услуги, место работы и расписание.

Buttons:
- `Создать мой профиль`
- `Отмена`

Only the explicit confirmation triggers durable account creation.

---

## 6. Account creation transaction

On confirmation, orchestrate the existing solo onboarding service.

Expected durable result:
1. own tenant;
2. Ayla/backend identity linkage;
3. BotUser linked to the new tenant;
4. TenantStaff owner role;
5. TenantStaff admin role;
6. CatalogMaster linked to BotUser;
7. accepted/landed state consistent with unified master-state contract;
8. `identity.solo_provider.created` or canonical equivalent event;
9. cross-system Ayla key/user linkage created during registration, not deferred to an impossible later sync.

Important:
registration must not complete as "success" if the master has been created only in one side of the system and will never be discoverable by Ayla.

If the cross-system link cannot be completed:
- do not silently mark onboarding done;
- return a controlled retryable state such as `SETUP_PENDING`;
- preserve idempotency;
- show a human message, not technical error.

Example:

> Профиль почти готов. Я ещё завершаю подключение к Ayla.
> Ничего повторно создавать не нужно — попробую закончить подключение ещё раз.

---

## 7. Registration idempotency

The same MAX user must never create multiple solo tenants by repeating the flow.

Required invariant:

`(channel, channel_user_id) → at most one solo provider identity`

Repeated:
- button tap;
- webhook redelivery;
- timeout retry;
- double submit;
- reopened onboarding;

must converge to the same account/workspace.

Reuse the existing advisory-lock mechanism unless a real contradiction is found.

---

## 8. Post-registration landing

Immediately after successful creation:

> Готово 🎉
> Я создала ваше рабочее пространство.
>
> Чтобы клиенты могли записываться, осталось настроить:
>
> ○ услуги и цены
> ○ место работы
> ○ расписание
>
> Начнём с услуг?

Actions:
- `Настроить услуги`
- `Продолжить позже`
- `Открыть кабинет`

`Открыть кабинет` must open the real Master Mini App for the solo tenant.

This must work before the task can be considered complete.

---

## 9. Setup checklist model

Use explicit setup readiness, not fake percent completion.

Recommended presentation:

```text
Ваш профиль

✓ Основная информация
✓ Город
○ Услуги и цены
○ Где принимаете
○ Расписание

Осталось настроить 3 пункта
```

Avoid:
- arbitrary completion percentages;
- hidden readiness assumptions;
- one boolean trying to represent every stage.

At minimum, implementation must distinguish:
- account created;
- setup incomplete;
- booking ready;
- published.

Publication is separate from account creation.

---

## 10. Services onboarding

### 10.1 Activity category first

Example:
master selects `Маникюр`.

Then open the service template selection for manicure.

### 10.2 Service template selection

Show controlled service templates, e.g.:
- Классический маникюр
- Аппаратный маникюр
- Комбинированный маникюр
- Маникюр + гель-лак
- Снятие покрытия
- Укрепление ногтей
- Наращивание
- Коррекция
- Дизайн ногтей

Multi-select.

The master should click what they provide, not manually recreate the catalog.

### 10.3 Multiple activities

If the master selected:
- Маникюр
- Педикюр

flow:
`activity selection → manicure templates → pedicure templates → selected services summary`

### 10.4 Own service

Always provide:
`+ Своя услуга`

But custom text must not automatically create a new canonical service.

Flow:
`user entered service → mapping attempt → VERIFIED / REVIEW_REQUIRED / UNMAPPED / NOT_RECOMMENDABLE according to canonical mapping model`

A custom service may still be available for direct catalog/direct booking if allowed by catalog and safety/transaction rules, but must not enter semantic AI Recommendation until canonical requirements are satisfied.

### 10.5 Draft/provisional templates

Onboarding may show templates allowed by current catalog policy even if they are not eligible for AI recommendation yet.

UI must not imply:
`selected = Ayla can recommend it`.

Commercial catalog inclusion and AI Recommendation eligibility are different facts.

---

## 11. Price and duration

After service selection, edit only selected services.

Example:

```text
Маникюр + гель-лак
Цена: [ 1800 ₽ ]
Длительность: [ 90 мин ]

Аппаратный маникюр
Цена: [ 1200 ₽ ]
Длительность: [ 60 мин ]
```

Requirements:
- price editable by master;
- duration editable by master;
- positive validation;
- no zero/negative values;
- inactive services not bookable.

Regional hints may be displayed only as hints, never authoritative pricing.

Example:
`Обычно в вашем городе: 1 200–1 700 ₽`

Ayla must not silently set the price.

---

## 12. Location / service format

Ask where the master receives clients.

Controlled options:
- `У себя / в кабинете`
- `В салоне`
- `На выезде`
- combinations if supported
- `Другое`

Then collect only fields required for selected format.

For a fixed location:
- city;
- address;
- optional location photo later.

Do not force unnecessary home-address disclosure if the master works mobile/on-site.

---

## 13. Schedule setup

Use simple weekly working hours.

Example:

```text
Пн 10:00–19:00
Вт 10:00–19:00
Ср Выходной
Чт 12:00–20:00
...
```

Use the same availability semantics as the normal Master App.

Do not create a special onboarding-only schedule model.

Requirements:
- timezone explicit or reliably resolved;
- no overlapping intervals;
- service duration later used when producing bookable slots;
- schedule editable later from Master App;
- errors/conflicts use existing availability rules.

---

## 14. Profile enrichment

Optional/non-blocking for initial booking readiness unless current policy explicitly requires otherwise:
- photo/avatar;
- work photos / portfolio;
- bio;
- certificates;
- richer description.

Do not block account creation because these are absent.

---

## 15. Publication model

Owner decision:

**A newly registered solo master is not automatically public merely because the account exists.**

Required sequence:

`account created → setup incomplete → booking-ready data completed → preview → explicit publish → PUBLIC`

Before publication:

> Всё необходимое для записи заполнено.
> Проверьте, как выглядит ваш профиль для клиентов.

Actions:
- `Посмотреть профиль`
- `Опубликовать`
- `Изменить`

After explicit publication:
- master becomes visible where allowed;
- direct booking may use active/bookable offers;
- AI Recommendation still obeys VERIFIED mapping and safety requirements.

`PUBLIC ≠ AI_RECOMMENDABLE`

---

## 16. Readiness conditions

### ACCOUNT_CREATED
- solo tenant exists;
- identity link exists;
- owner/admin/master roles exist;
- Master Mini App accessible.

### SETUP_INCOMPLETE
One or more required commercial/booking inputs missing.

### BOOKING_READY
Minimum:
- at least one active bookable service/offer;
- price/duration valid;
- service linked to master;
- usable location/service format;
- working hours/availability configured;
- no mandatory safety/catalog constraint unresolved for direct booking path;
- master has landed/connected identity.

### PUBLIC
- explicit publish action completed;
- readiness gate passed;
- publication policy passed.

Do not infer PUBLIC from `is_active=True` alone if that field has other meanings in current runtime.

Reconcile with DRF-1506 unified master state instead of creating contradictory predicates.

---

## 17. Conversation resume

Onboarding must be resumable.

If the master leaves after account creation and returns later:

> Продолжим настройку?
> Осталось:
> ○ услуги и цены
> ○ расписание

Actions:
- `Продолжить`
- `Открыть кабинет`

Resume state must be based on authoritative stored setup facts, not solely a chat-session flag.

ConversationState may expire; durable onboarding progress must not disappear.

---

## 18. Failures and retries

### Before account creation
If user cancels:
- no tenant created;
- no partial staff/master objects.

### During account creation
If operation times out:
- do not immediately invite a second create;
- check idempotent result;
- return known state.

### After account creation
If service/schedule save fails:
- preserve already saved sections;
- do not restart onboarding;
- show local retry.

Example:
> Не удалось сохранить расписание. Услуги и профиль уже сохранены. Попробуем расписание ещё раз.

### Cross-system identity failure
Use retryable setup state.
Do not claim success until the master can actually land in the system.

---

## 19. Security and tenant isolation

Mandatory tests:
1. Solo user coming through salon X bot does not become salon X staff.
2. Does not appear in salon X roster.
3. Does not inherit salon X catalog.
4. Does not receive salon X admin permissions.
5. Own tenant contains owner/admin/master roles.
6. User cannot open another tenant by changing identifiers.
7. Repeated registration does not create another tenant.
8. Invite-code path for salon employees remains unchanged.
9. Internal service calls enforce object/tenant authorization.
10. No secrets or raw auth tokens in chat/logs.

---

## 20. Analytics / observability

At minimum emit:
- solo_onboarding.started
- solo_onboarding.path_selected
- solo_onboarding.minimum_profile_completed
- solo_onboarding.account_create_requested
- solo_onboarding.account_created
- solo_onboarding.account_create_failed
- solo_onboarding.setup_resumed
- solo_onboarding.services_completed
- solo_onboarding.location_completed
- solo_onboarding.schedule_completed
- solo_onboarding.booking_ready
- solo_onboarding.publish_requested
- solo_onboarding.published
- solo_onboarding.open_master_app

Every create/publish event needs:
- correlation/request id;
- channel user reference;
- tenant id after creation;
- result status;
- idempotency/retry trace where relevant.

Do not place sensitive free text into product analytics unnecessarily.

---

## 21. UX copy baseline

### Entry
> Привет! Я Ayla.
> Если салон уже пригласил вас — пришлите код приглашения.
> Если вы работаете самостоятельно — я помогу создать своё рабочее пространство.

Buttons:
`У меня есть код`
`Я работаю самостоятельно`

### Solo start
> Отлично. Настроим ваш рабочий профиль. Это займёт несколько минут.
> Как вас зовут?

### City
> В каком городе вы принимаете клиентов?

### Activities
> Чем вы занимаетесь? Можно выбрать несколько направлений.

### Create confirmation
> Создам для вас отдельное рабочее пространство Ayla.
> Вы будете его владельцем и мастером.
> После этого настроим услуги, место работы и расписание.

Buttons:
`Создать мой профиль`
`Отмена`

### Created
> Готово 🎉
> Рабочее пространство создано.
> Чтобы клиенты могли записываться, осталось настроить услуги, место работы и расписание.

Buttons:
`Настроить услуги`
`Продолжить позже`
`Открыть кабинет`

### Booking-ready
> Всё необходимое для записи готово.
> Теперь проверьте, как ваш профиль выглядит для клиентов.

Buttons:
`Посмотреть профиль`
`Опубликовать`

### Published
> Готово — профиль опубликован.
> Теперь можно принимать записи и работать в Ayla.

Button:
`Открыть кабинет`

---

## 22. Master Mini App landing

After onboarding, do not invent a new home.

Land in existing Master App:
`Сегодня | Расписание | Ayla`

On first landing, a small setup/status card may show optional unfinished enrichment, but must not replace daily work.

Do not add a permanent Onboarding tab.

---

## 23. What must not be built

Out of scope:
- separate solo bot;
- client-bot solo registration;
- salon approval queue for someone claiming to work in a foreign salon;
- full CRM;
- marketing;
- loyalty;
- earnings;
- advanced analytics;
- automatic AI recommendation eligibility for custom/unverified services;
- automatic publication immediately after account creation;
- arbitrary percentage completion;
- onboarding-specific duplicate schedule/catalog engines;
- long mandatory portfolio/document questionnaire before basic account creation.

---

## 24. Implementation work breakdown

### Track A — Entry & conversation
- role-less MAX routing;
- solo intent action;
- name/city/activity capture;
- explicit create confirmation;
- resume UX.

### Track B — Identity / solo tenant creation
- call existing solo onboarding service;
- cross-system Ayla identity linkage;
- idempotency;
- retryable pending state;
- leakage tests.

### Track C — Master Mini App handoff
- secure open action;
- solo tenant resolution;
- verify Today/Schedule/Ayla work without foreign salon membership.

### Track D — Services
- activity categories;
- template lists;
- multi-select;
- custom service path;
- TenantOffer creation;
- price/duration editing;
- mapping status preservation.

### Track E — Location
- service format;
- city/address;
- privacy-safe conditional fields.

### Track F — Schedule
- weekly working hours;
- availability validation;
- reuse normal schedule APIs/contracts.

### Track G — Readiness & publication
- authoritative checklist;
- preview;
- explicit publish command;
- PUBLIC vs AI_RECOMMENDABLE separation.

### Track H — Observability / E2E
- analytics;
- correlation IDs;
- golden scenarios;
- failure/retry tests.

D/E/F can proceed after account contract is fixed; all converge before publication readiness.

---

## 25. Golden E2E scenarios

1. New MAX user → solo path → account → Mini App opens.
2. Same user repeats onboarding → no duplicate tenant.
3. User through salon X bot → own tenant only, no salon X leakage.
4. Existing invite-code user still joins salon path.
5. User cancels before create → no durable account.
6. Create request times out → retry does not duplicate.
7. Cross-system identity link fails → controlled pending, not false success.
8. Multi-activity master selects manicure + pedicure.
9. Template service selection creates only selected offers.
10. Custom service does not auto-become VERIFIED.
11. Price/duration validation.
12. Fixed-location master adds address.
13. Mobile/on-site master is not forced to publish home address.
14. Weekly schedule saved and visible in normal Master App.
15. User leaves after services, returns later → resumes remaining steps.
16. Booking-ready but unpublished master not shown to clients.
17. Explicit publish → profile becomes public.
18. Public unverified service remains excluded from semantic AI Recommendation.
19. Master opens Today/Schedule/Ayla after publication.
20. Permission/tenant tampering is denied.

---

## 26. Definition of Done

A real new MAX user can:
1. open the salon bot;
2. choose `Я работаю самостоятельно`;
3. provide minimum identity/profile data;
4. explicitly create a solo workspace;
5. receive correct Ayla identity link;
6. get own tenant with owner/admin/master roles;
7. open Master Mini App;
8. select service categories and templates;
9. set prices/durations;
10. set location/service format;
11. set working hours;
12. see authoritative readiness checklist;
13. preview profile;
14. explicitly publish;
15. become visible/bookable according to publication/catalog/safety rules;
16. work in `Сегодня | Расписание | Ayla`;
17. repeat/retry without duplicates or cross-tenant leakage.

No manual DB repair or operator intervention for the normal happy path.

---

## 27. Required first response from the orchestrator

Before coding, return:

```text
SOLO MASTER ONBOARDING — PHASE 0 RECONCILIATION

CURRENT REPOS / SHAS:
...

EXISTING COMPONENTS REUSED:
- solo_onboarding:
- role resolver:
- master landed predicate:
- service catalog/template API:
- availability API:
- publication API:
- Master Mini App entry:
- Ayla identity/key sync:

GAPS:
...

CONFLICTS WITH THIS HANDOFF:
NONE / exact contradictions

PROPOSED TASK/PR SPLIT:
...

DEPENDENCY GRAPH:
...

E2E TEST PLAN:
...

OWNER DECISIONS STILL REQUIRED:
NONE
```

Do not start redesign if current code differs. First classify differences as:
- implementation gap;
- stale spec;
- true contradiction.

Only a true contradiction comes back to the owner.
