# UX Screen Inventory — Ayla / Provider + Customer Surfaces

| Поле | Значение |
|---|---|
| **Дата** | 2026-05-29 r2 |
| **Статус** | Updated with Provider Onboarding / Activation |
| **Причина обновления** | Добавлен UX приземления мастера/салона: Smart Landing, enrichment, uploaded price extraction, template-based bootstrap |
| **Канонический новый документ** | `docs/screens/provider-onboarding/provider-landing-enrichment-flow.md` |
| **Связанные документы** | `solo-provider-bootstrap.md`, `provider-services-prices-flow.md`, `provider-calendar-schedule-flow.md`, `provider-booking-detail-flow.md`, `provider-messages-flow.md` |
| **Важно** | Provider Landing Enrichment — это не обычный профиль и не dashboard. Это pre-activation onboarding flow до полноценной работы провайдера в Ayla Pro. |

---

## 0. Главная правка r2

В UX Screen Inventory добавлен новый верхнеуровневый раздел:

```text
Provider Onboarding / Activation
```

Туда входит весь процесс “приземления” мастера или салона:

```text
новый мастер/салон пришёл в систему
→ Ayla помогает собрать данные
→ пользователь подтверждает источники или выбирает шаблон
→ система создаёт черновик профиля
→ пользователь проверяет услуги, цены, расписание, фото
→ bootstrap создаёт tenant/profile/services/schedule
→ go-live checklist
→ провайдер готов принимать записи
```

---

## 1. Архитектурное решение

### Decision

```text
Provider Landing Enrichment belongs to Provider Onboarding / Activation layer.

Canonical path:
docs/screens/provider-onboarding/provider-landing-enrichment-flow.md
```

### Не класть в

```text
provider-profile-flow.md
provider-services-prices-flow.md
provider-calendar-schedule-flow.md
salon-main-dashboard-flow.md
solo-provider-bootstrap.md
```

### Почему

`Provider Landing Enrichment` — это стартовый UX до активации провайдера.

Он создаёт:

```text
tenant
provider profile
services
schedule
media drafts
knowledge drafts
go-live readiness
```

А регулярные provider screens уже работают после активации.

---

# 2. Full Screen Inventory

## 2.1. Customer-side surfaces

| ID | Surface | Status | Priority | Document | Notes |
|---|---|---|---|---|---|
| CUS-DASH-1 | Customer main wellness dashboard | Done | P0 | `customer-main-wellness-dashboard` | Главная клиента: food, water, goals, bookings, wellness summary |
| CUS-FOOD-1 | Food scanner flow | Done | P0 | `customer-food-scanner-flow.md` | Скан еды, recap, confidence/portion flow, daily delta |
| CUS-ONB-1 | Customer onboarding bot DM | Done | P0 | `customer-onboarding-flow.md` | First-time bot DM onboarding |
| CUS-BOOK-1 | Booking catalog F1 | Done | P0 | `customer-booking-flow.md` | 3-layer ranking: твои места / Ayla подобрала / исследовать |
| CUS-BOOK-2 | Master detail F2 | Done | P0 | `customer-booking-flow.md` | Мастер, услуги, слоты, reasoning text |
| CUS-BOOK-3 | Date/time F3 | Done | P0 | `customer-booking-flow.md` | Smart suggestions, available slots |
| CUS-BOOK-4 | Confirmation F4 | Done | P0 | `customer-booking-flow.md` | Cancellation policy, loyalty, anonymous gate |
| CUS-BOOK-5 | Success F5 | Done | P0 | `customer-booking-flow.md` | Confirmation, reminders soft copy |
| CUS-CR-1 | Cancellation flow | Done | P0 | `customer-cancellation-reschedule-flow.md` | Отмена, no-fault cascade |
| CUS-CR-2 | Reschedule flow | Done / Updated | P0 | `customer-cancellation-reschedule-flow.md` | Auto-reschedule через Booking Engine when eligible |
| CUS-REC-1 | Records tab | Done | P0 | `customer-records-flow.md` | История записей, карточки, статусы |
| CUS-REM-1 | Reminders voice | Done | P1 | `customer-reminders-voice.md` | Ayla-first reminder tone |
| CUS-PROF-1 | Customer profile tab | Done | P0 | `customer-profile-flow.md` | 152-ФЗ, memory transparency, privacy |
| CUS-AI-1 | Ayla chat / bot DM | Existing | P0 | `ayla-mediated-messaging.md` | Ayla как посредник, не прямой чат |

---

## 2.2. Provider Onboarding / Activation surfaces

Это новый раздел. Он должен идти до Provider Operational screens.

| ID | Surface | Status | Priority | Document | Notes |
|---|---|---|---|---|---|
| PON-0 | Consent / explanation | Added r2 | P0 | `provider-landing-enrichment-flow.md` | Пользователь соглашается на поиск данных из открытых/подключённых источников |
| PON-1 | Minimal input | Added r2 | P0 | `provider-landing-enrichment-flow.md` | Название, телефон, город или ссылка |
| PON-2 | Source search | Added r2 | P0 | `provider-landing-enrichment-flow.md` | Поиск YCLIENTS, Яндекс.Карты, 2ГИС, VK, сайт, прайс |
| PON-3 | Source candidates | Added r2 | P0 | `provider-landing-enrichment-flow.md` | Найденные источники, выбор “это мой бизнес” |
| PON-4 | Source selection | Added r2 | P0 | `provider-landing-enrichment-flow.md` | Использовать всё / выбрать источники / добавить источник |
| PON-5 | Enrichment progress | Added r2 | P0 | `provider-landing-enrichment-flow.md` | “Собираю профиль”: контакты, услуги, фото, часы |
| PON-6 | Draft profile summary | Added r2 | P0 | `provider-landing-enrichment-flow.md` | Готово / нужно проверить / conflicts |
| PON-7 | Main info review | Added r2 | P0 | `provider-landing-enrichment-flow.md` | Название, тип, город, адрес, контакты |
| PON-8 | Services review | Added r2 | P0 | `provider-landing-enrichment-flow.md` | Услуги, цены, длительности, categories, draft/requires_review |
| PON-9 | Schedule review | Added r2 | P0 | `provider-landing-enrichment-flow.md` | Часы работы из источников или template preset |
| PON-10 | Media/legal confirmation | Added r2 | P0 | `provider-landing-enrichment-flow.md` | Фото, право использования, reject/accept |
| PON-11 | Template fallback | Added r2 | P0 | `provider-landing-enrichment-flow.md` | Если источников нет — запуск по шаблону |
| PON-12 | Template specialization selection | Added r2 | P0 | `provider-landing-enrichment-flow.md` | Маникюр, массаж, брови, эпиляция, косметология и т.д. |
| PON-13 | Region price benchmark review | Added r2 | P0 | `provider-landing-enrichment-flow.md` | Рекомендованные диапазоны цен, не final prices |
| PON-14 | Uploaded price extraction | Added r2 | P0 | `provider-landing-enrichment-flow.md` | PDF/XLSX/JPG/PNG/TXT/MD прайсы |
| PON-15 | Conflict resolution | Added r2 | P0 | `provider-landing-enrichment-flow.md` | Разные часы, телефоны, цены, адреса |
| PON-16 | Go-live checklist | Added r2 | P0 | `provider-landing-enrichment-flow.md` | Готовность профиля, тестовая запись |
| PON-17 | Bootstrap result | Added r2 | P0 | `solo-provider-bootstrap.md` | Tenant/profile/services/schedule created |
| PON-18 | First provider launch | Added r2 | P0 | `solo-provider-bootstrap.md` | Провайдер открывает Ayla Pro после bootstrap |

---

## 2.3. Provider operational surfaces

| ID | Surface | Status | Priority | Document | Notes |
|---|---|---|---|---|---|
| PRO-BOOK-1 | Provider booking detail | Done / Updated | P0 | `provider-booking-detail-flow.md` | Карточка записи, timeline, messages, rescheduled_by_ayla |
| PRO-CAL-1 | Provider calendar day view | Done / Updated | P0 | `provider-calendar-schedule-flow.md` | День, записи, свободные окна |
| PRO-CAL-2 | Provider calendar week view | Done | P0 | `provider-calendar-schedule-flow.md` | Неделя, загрузка, статусы |
| PRO-CAL-3 | Manual booking | Done | P0 | `provider-calendar-schedule-flow.md` | Ручная запись с backend slot check |
| PRO-CAL-4 | Time block | Done | P0 | `provider-calendar-schedule-flow.md` | Обед, перерыв, блокировка времени |
| PRO-CAL-5 | Working hours | Done / Updated | P0 | `provider-calendar-schedule-flow.md` | Может быть предзаполнено из enrichment/template |
| PRO-CAL-6 | Day off / sick day | Done | P0 | `provider-calendar-schedule-flow.md` | Impacted bookings queue |
| PRO-SVC-1 | Services list | Done / Updated | P0 | `provider-services-prices-flow.md` | Список услуг, активные/скрытые/архив |
| PRO-SVC-2 | Create service | Done / Updated | P0 | `provider-services-prices-flow.md` | Может быть создано из enrichment/template/uploaded price |
| PRO-SVC-3 | Edit service | Done | P0 | `provider-services-prices-flow.md` | Цена, duration, buffer, visibility |
| PRO-SVC-4 | Assign masters | Done | P0 | `provider-services-prices-flow.md` | Solo auto-assigned, salon manual |
| PRO-SVC-5 | Hide/archive service | Done | P0 | `provider-services-prices-flow.md` | Hidden vs archived |
| PRO-MSG-1 | Provider messages list | Done | P0/P1 | `provider-messages-flow.md` | Сообщения по записи |
| PRO-MSG-2 | Reply via Ayla | Done | P0 | `provider-messages-flow.md` | Не прямой чат |
| PRO-MSG-3 | Escalate to admin | Done | P0 | `provider-messages-flow.md` | Для sensitive/financial/conflict cases |
| PRO-NOT-1 | Provider notification: booking rescheduled by Ayla | Done | P0 | `provider-messages-flow.md`, `provider-booking-detail-flow.md` | Post-action notification, not approval request |
| PRO-DASH-1 | Provider main dashboard | Planned | P1 | TBD | Для solo/master/salon after activation |

---

## 2.4. Salon/team surfaces

| ID | Surface | Status | Priority | Document | Notes |
|---|---|---|---|---|---|
| SAL-DASH-1 | Salon main dashboard | Next | P1 | `salon-main-dashboard-flow.md` | Главный экран салона после активации |
| SAL-TEAM-1 | Team management | Planned | P1 | `salon-team-management-flow.md` | Мастера, роли, услуги, расписание |
| SAL-TEAM-2 | Master profile in team | Planned | P1 | `salon-team-management-flow.md` | Профиль мастера в салоне |
| SAL-TEAM-3 | Master-service mapping | Partial | P1 | `provider-services-prices-flow.md` | Кто какие услуги выполняет |
| SAL-OPS-1 | Admin operational queue | Planned | P1 | TBD | Конфликты, переносы, сообщения, moderation |
| SAL-ONB-1 | Salon landing enrichment | Added r2 | P0 | `provider-landing-enrichment-flow.md` | Приземление салона как tenant type = salon |
| SAL-ONB-2 | Multi-branch selection | P1/P2 | P1/P2 | `provider-landing-enrichment-flow.md` | Филиалы и branch candidates |

---

## 2.5. Runbooks / technical activation

| ID | Runbook | Status | Priority | Document | Notes |
|---|---|---|---|---|---|
| RUN-SPB-1 | Solo provider bootstrap | Done / Updated | P0 | `solo-provider-bootstrap.md` | Manual or Smart Landing bootstrap |
| RUN-SPB-2 | Smart Landing bootstrap | Added r2 | P0 | `solo-provider-bootstrap.md` | external enrichment / uploaded price / template / manual |
| RUN-QA-1 | Provider E2E smoke test | Added | P0 | `solo-provider-bootstrap.md` | Клиентская запись → provider sees booking → reschedule → notification |
| RUN-TEN-1 | Tenant isolation test | Added | P0 | `solo-provider-bootstrap.md` | Чужие записи/услуги/клиенты недоступны |

---

# 3. Provider Onboarding / Activation detailed map

## 3.1. PON-0 — Consent

### Purpose

Объяснить пользователю, что Ayla может собрать черновик профиля из открытых и подключённых источников.

### Copy

```text
Я помогу собрать ваш профиль быстрее.

Могу поискать данные в открытых и подключённых источниках: карты, сайт, соцсети, прайс, YCLIENTS и другие.

Перед публикацией вы всё проверите и подтвердите.

[Начать]
```

### Rules

```text
не публиковать без подтверждения
сохранять source/provenance
дать возможность отказаться от найденных данных
```

---

## 3.2. PON-1 — Minimal input

### Purpose

Получить минимум данных.

### Fields

```text
Название, телефон или ссылка
Город
```

### Examples

```text
Формула тела Пенза
Ольга массаж Пенза
ссылка на YCLIENTS
ссылка на VK
ссылка на Яндекс.Карты
ссылка на 2ГИС
сайт
```

---

## 3.3. PON-2/PON-3 — Source search and candidates

### Purpose

Найти возможные совпадения и дать пользователю выбрать правильные источники.

### Sources P0

```text
YCLIENTS
Яндекс.Карты
2ГИС
VK
сайт
uploaded price
uploaded media
```

### Candidate card

```text
Формула тела — Яндекс.Карты
Пенза, ул. ...
Телефон: +7...

[Это мой бизнес]
[Не использовать]
```

---

## 3.4. PON-5 — Enrichment progress

### Purpose

Показать, что Ayla работает и собирает профиль.

### Progress checklist

```text
✓ Контакты
✓ Адрес
✓ Услуги
✓ Фото
✓ Часы работы
✓ Описание
✓ Мастера
```

---

## 3.5. PON-6 — Draft ready

### Purpose

Показать результат в стиле “готово / нужно проверить”.

### Copy

```text
Я собрала черновик профиля.

Готово:
✓ Название
✓ Адрес
✓ Телефон
✓ 9 услуг
✓ 12 фото

Нужно проверить:
⚠ 3 услуги без длительности
⚠ разные часы работы
⚠ 2 фото требуют подтверждения

[Проверить профиль]
```

---

## 3.6. PON-8 — Services review

### Purpose

Проверить услуги, цены, длительности, буферы.

### Sources

```text
YCLIENTS
website
VK
uploaded price
template
manual
```

### Rules

```text
услуга без цены = draft
услуга без длительности = draft
template-generated service = requires_review
region benchmark price = recommendation, not final
```

---

## 3.7. PON-9 — Schedule review

### Purpose

Проверить рабочие часы.

### Sources

```text
YCLIENTS
Яндекс.Карты
2ГИС
website
template schedule preset
manual input
```

### Conflict example

```text
Часы работы отличаются:

Яндекс.Карты: 10:00–20:00
2ГИС: 10:00–19:00
Сайт: по записи

Что использовать?
```

---

## 3.8. PON-11/PON-12 — Template fallback

### Purpose

Дать старт мастеру без цифрового следа.

### Trigger

```text
источники не найдены
confidence низкий
пользователь выбрал “Создать по шаблону”
```

### Copy

```text
Я пока не нашла готовый профиль.

Могу создать стартовый профиль по шаблону вашей специализации:
✓ услуги
✓ типовые длительности
✓ рекомендованные диапазоны цен
✓ описание
✓ расписание

Вы всё проверите перед публикацией.

[Создать по шаблону]
```

### Template examples

```text
Маникюр и педикюр
Массаж
Брови и ресницы
Эпиляция
Косметология
Парикмахер
```

---

## 3.9. PON-13 — Region price benchmark review

### Purpose

Предложить мастеру ценовые подсказки, но не публиковать их автоматически.

### Copy

```text
По похожим мастерам в вашем регионе цена обычно в диапазоне 1 500–2 300 ₽.

Какую цену поставить?

[1 500 ₽]
[1 800 ₽]
[2 000 ₽]
[Ввести свою]
```

### Rule

```text
benchmark price != final price
final price requires user confirmation
```

---

## 3.10. PON-16 — Go-live checklist

### Purpose

Проверить готовность профиля к публикации.

### Checklist

```text
[ ] Название подтверждено
[ ] Адрес подтвержден
[ ] Телефон подтвержден
[ ] Есть хотя бы одна активная услуга
[ ] У услуги есть цена
[ ] У услуги есть длительность
[ ] У услуги есть назначенный мастер
[ ] Есть рабочие часы
[ ] Есть хотя бы одно свободное окно
[ ] Фото разрешены к использованию
[ ] Описание проверено
[ ] Нет медицинских обещаний
[ ] Provider может открыть Ayla Pro
[ ] Тестовая запись проходит
```

---

# 4. Navigation placement

## Before activation

Provider is in onboarding mode:

```text
Ayla Pro start
→ Provider Landing / Activation
→ Draft review
→ Go-live checklist
→ Bootstrap
```

No full dashboard until activation.

---

## After activation

Provider enters normal Ayla Pro:

```text
Главная
Записи / Календарь
Клиенты
Услуги
Профиль
```

For salon:

```text
Главная
Календарь
Клиенты
Команда
Профиль
```

---

# 5. What changed in related docs

## `provider-landing-enrichment-flow.md`

Added:

```text
canonical Provider Onboarding / Activation placement
source enrichment
template fallback
region price benchmark
uploaded price extraction
go-live checklist
```

## `solo-provider-bootstrap.md`

Added:

```text
Smart Landing Bootstrap
external enrichment
uploaded price extraction
template bootstrap
manual input
```

## `provider-services-prices-flow.md`

Added:

```text
services can be created from enrichment/template/uploaded price
all imported/template services remain draft/requires_review until confirmed
```

## `provider-calendar-schedule-flow.md`

Added:

```text
schedule can be prefilled from YCLIENTS / maps / 2GIS / website / template preset
conflicting schedule values require confirmation
```

---

# 6. New open implementation questions

## Q-INV-1 — Separate provider onboarding route?

**Lean:** yes.

```text
/pro/onboarding/*
```

or Mini App state:

```text
provider_state = onboarding
```

Why:

```text
do not show full dashboard before tenant/profile/services/schedule are ready
```

---

## Q-INV-2 — Can user skip enrichment and go manual?

**Lean:** yes.

Always allow:

```text
Заполнить вручную
```

Ayla should help, not trap.

---

## Q-INV-3 — Can user publish with incomplete profile?

**Lean:** partial yes, but with guardrails.

Minimum publish:

```text
confirmed name
confirmed contact/address or service mode
at least one active service
price
duration
assigned master
working hours
at least one free slot
```

---

## Q-INV-4 — Template-generated prices visibility

**Lean:** never public until confirmed.

```text
RegionPriceBenchmark = recommendation
Service.price = final confirmed value
```

---

## Q-INV-5 — Photo publication

**Lean:** never public until rights confirmed.

---

# 7. Recommended next work

After this inventory update, next document should be:

```text
docs/screens/salon-main-dashboard-flow.md
```

Why:

Provider Onboarding / Activation now covers how a salon/master starts.

Next we need the screen they land on after activation:

```text
today’s bookings
messages
team status
schedule conflicts
profile readiness
service warnings
Ayla auto-reschedule notifications
```

---

# 8. Changelog

## 2026-05-29 r2

Added:

```text
Provider Onboarding / Activation section
PON-0…PON-18 screen inventory
Smart Landing / Enrichment
Template fallback
Region price benchmark review
Uploaded price extraction
Go-live checklist
Smart Landing bootstrap link
```

Updated classification:

```text
Provider Landing Enrichment is pre-activation onboarding, not regular provider profile/dashboard.
```
